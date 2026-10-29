"""邀请海报合成：服务端用 Pillow 渲染 1080×1440 PNG。

为什么放服务端（而不是前端 canvas 合成）：海报是要被大量转发的物料，服务端出图
能保证每个用户拿到的字体、间距、二维码清晰度完全一致，也不受设备字体缺失影响。

设计稿对应「邀请好友海报设计」：深空蓝紫渐变 + 双行大标题 + 白色二维码卡片 +
邀请人信息 + 底部站点信息。二维码内容即邀请链接（扫码注册后由既有注册流程绑定邀请关系）。
"""

from __future__ import annotations

import io
from functools import lru_cache
from urllib.parse import urlparse

import httpx
import qrcode
from loguru import logger
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import settings
from app.core.executors import run_heavy
from app.services import cos_service
from app.services.image_proxy import own_cos_object_key
from app.utils.upload_validation import MAX_DECODE_PIXELS

# 画布尺寸与布局常量（与设计稿一一对应，改一处要同步改设计稿）
WIDTH, HEIGHT = 1080, 1440
MARGIN = 72
CARD_TOP = 676
CARD_HEIGHT = 500
QR_SIZE = 280

# 配色
_BG_TOP = (11, 16, 38)
_BG_BOTTOM = (34, 26, 79)
BLUE = (91, 140, 255)
VIOLET = (140, 92, 246)
INK = (14, 20, 48)
MUTED = (100, 116, 139)
SUB = (168, 184, 232)
FOOT = (143, 162, 216)
FOOT_DIM = (127, 144, 196)
TAG = (157, 176, 224)
LABEL = (148, 163, 184)
WHITE = (255, 255, 255)
# 全图唯一的高饱和色，只用在「奖励积分数」上，充当视觉锚点
AMBER = (251, 191, 36)
LINE_CARD = (232, 237, 245)
LINE_DARK = (255, 255, 255, 44)

# 字体候选：镜像内 apt 安装 fonts-noto-cjk；本地 Windows 回落到微软雅黑
# ⚠️ python:3.11-slim 默认没有任何中文字体，缺字体时 PIL 会把中文画成方块，
# 所以渲染前必须确认命中一个候选（见 _font）。
_FONT_BOLD = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)
_FONT_REGULAR = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)

# 头像下载上限：海报里只有 76px，几 MB 的原图足够，避免拉超大图拖慢出图
_AVATAR_MAX_BYTES = 4 * 1024 * 1024
_AVATAR_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


@lru_cache(maxsize=24)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """按字号取字体（带缓存，Pillow 每次 truetype 都会重新解析字体文件）"""
    import os

    for path in _FONT_BOLD if bold else _FONT_REGULAR:
        if not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise RuntimeError(
        "邀请海报缺少中文字体：镜像需安装 fonts-noto-cjk（见 backend/Dockerfile）"
    )


def invite_url(invite_code: str) -> str:
    """二维码内容：邀请注册链接（与前端邀请页拼法一致）"""
    base = (settings.SITE_BASE_URL or "https://www.wdmao.cn").rstrip("/")
    return f"{base}/login?invite={invite_code}"


def _site_host() -> str:
    try:
        return urlparse(settings.SITE_BASE_URL).hostname or "www.wdmao.cn"
    except ValueError:
        return "www.wdmao.cn"


def _gradient_canvas() -> Image.Image:
    """纵向渐变底：先用 1px 宽画好再拉伸，避免逐像素铺 1080×1440"""
    strip = Image.new("RGB", (1, HEIGHT))
    px = strip.load()
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(_BG_TOP, _BG_BOTTOM))
    return strip.resize((WIDTH, HEIGHT), Image.BILINEAR).convert("RGBA")


def _add_glow(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
    alpha: int,
    blur: int = 90,
) -> None:
    """柔和光晕：实心椭圆 + 高斯模糊（Pillow 没有直接的原生径向渐变）

    ⚠️ 模糊半径偏小时，大椭圆会留下清晰的圆弧边缘（看起来像脏块），
    越大的光晕需要越大的模糊半径。
    """
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=(*color, alpha))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def _fit_font(text: str, size: int, max_width: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """按可用宽度收敛字号（昵称可能很长），不低于 28px"""
    while size > 28 and _font(size, bold).getlength(text) > max_width:
        size -= 2
    return _font(size, bold)


def _circle_avatar(data: bytes, size: int) -> Image.Image | None:
    """头像裁圆：中心方裁 + 4 倍超采样，边缘不会锯齿"""
    try:
        # 先只看头部尺寸再决定要不要解码：Pillow 在 89M~178M 像素之间只告警不抛错，
        # 这一段 convert("RGB") 会真分配几百 MB（容器 mem_limit 768m）→ 必须先自己拦
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
        if width * height > MAX_DECODE_PIXELS:
            logger.warning(
                f"邀请海报：头像像素 {width}x{height} 超过 {MAX_DECODE_PIXELS} 上限，改用首字底色"
            )
            return None
        src = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - 头像坏图/非图片格式都只是降级
        logger.warning(f"邀请海报：头像解析失败，改用首字底色 ({exc})")
        return None
    side = min(src.size)
    left = (src.width - side) // 2
    top = (src.height - side) // 2
    src = src.crop((left, top, left + side, top + side)).resize((size * 4, size * 4), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src.resize((size, size), Image.LANCZOS), (0, 0), mask)
    return out


def _qr_image(url: str, size: int) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0E1430", back_color="white").convert("RGB")
    # NEAREST：二维码是纯二值色块，插值会糊边导致识别率下降
    return img.resize((size, size), Image.NEAREST)


def _gradient_bar(width: int, height: int) -> Image.Image:
    """标题下方的渐变短线：给「标题 → 奖励」之间一个视觉停顿"""
    bar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(bar)
    for i in range(width):
        t = i / max(1, width - 1)
        d.line(
            [(i, 0), (i, height - 1)],
            fill=(
                round(BLUE[0] + (VIOLET[0] - BLUE[0]) * t),
                round(BLUE[1] + (VIOLET[1] - BLUE[1]) * t),
                round(BLUE[2] + (VIOLET[2] - BLUE[2]) * t),
                255,
            ),
        )
    return bar


def _draw_reward(draw: ImageDraw.ImageDraw, x: int, y: int, points: int) -> None:
    """奖励行：文字与数字同一基线，数字放大并点暖色（全图唯一高饱和色）"""
    prefix, suffix = "邀请成功，双方各得 ", " 积分"
    small, num = _font(30), _font(56, True)
    baseline = y + num.getmetrics()[0]
    draw.text((x, baseline), prefix, font=small, fill=SUB, anchor="ls")
    x1 = x + small.getlength(prefix)
    draw.text((x1, baseline), str(points), font=num, fill=AMBER, anchor="ls")
    x2 = x1 + num.getlength(str(points))
    draw.text((x2, baseline), suffix, font=small, fill=SUB, anchor="ls")


def _gradient_text(
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    top_color: tuple[int, int, int],
    bottom_color: tuple[int, int, int],
) -> None:
    """渐变填充文字：先用字形做蒙版，再按蒙版贴一层纵向渐变

    只给主标题第二行用 —— 纯白两行会显得平，白→淡紫能让整块标题有体积感。
    """
    bbox = font.getbbox(text)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).text((-bbox[0], -bbox[1]), text, font=font, fill=255)

    gradient = Image.new("RGB", (1, height))
    px = gradient.load()
    for y in range(height):
        t = y / max(1, height - 1)
        px[0, y] = tuple(round(a + (b - a) * t) for a, b in zip(top_color, bottom_color))
    gradient = gradient.resize((width, height))

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer.paste(gradient, (0, 0), mask)
    canvas.alpha_composite(layer, (xy[0] + bbox[0], xy[1] + bbox[1]))


def _render(
    code: str, nickname: str, avatar_bytes: bytes | None, reward_points: int = 50
) -> bytes:
    canvas = _gradient_canvas()
    _add_glow(canvas, (600, -200, 1300, 500), BLUE, 40, 100)
    _add_glow(canvas, (-280, 780, 360, 1420), VIOLET, 34, 140)
    draw = ImageDraw.Draw(canvas)

    # —— 顶部品牌 ——
    draw.rounded_rectangle((MARGIN, MARGIN, MARGIN + 56, MARGIN + 56), radius=16, fill=BLUE)
    draw.text((MARGIN + 28, MARGIN + 26), "AI", font=_font(30, True), fill=WHITE, anchor="mm")
    draw.text((MARGIN + 72, MARGIN + 2), "AI学习猫", font=_font(40, True), fill=WHITE)
    draw.text(
        (WIDTH - MARGIN, MARGIN + 14), "INVITE · 邀请有礼",
        font=_font(28), fill=TAG, anchor="rt",
    )

    # —— 主标题 ——
    draw.text((MARGIN, 176), "一起学 AI", font=_font(112, True), fill=WHITE)
    _gradient_text(
        canvas, (MARGIN, 304), "把技能学成自己的", _font(112, True),
        WHITE, (165, 180, 252),
    )
    canvas.alpha_composite(_gradient_bar(104, 6), (MARGIN, 460))
    _draw_reward(draw, MARGIN, 486, reward_points)

    # —— 邀请人 ——
    avatar_top = 580
    avatar = _circle_avatar(avatar_bytes, 72) if avatar_bytes else None
    box = (MARGIN, avatar_top, MARGIN + 72, avatar_top + 72)
    if avatar is None:
        draw.ellipse(box, fill=VIOLET)
        draw.text(
            (MARGIN + 36, avatar_top + 36), (nickname or "我")[0],
            font=_font(36, True), fill=WHITE, anchor="mm",
        )
    else:
        canvas.alpha_composite(avatar, (MARGIN, avatar_top))
    name = f"{nickname or '我'} 邀请你"
    draw.text((MARGIN + 92, avatar_top + 2), name, font=_fit_font(name, 34, 820, True), fill=WHITE)
    draw.text((MARGIN + 92, avatar_top + 50), "扫码注册，自动绑定我的邀请码", font=_font(28), fill=SUB)

    # —— 二维码卡片 ——
    top = 690
    draw.rounded_rectangle((MARGIN, top, WIDTH - MARGIN, top + 500), radius=32, fill=WHITE)
    qr_size = 260
    qr_x, qr_y = MARGIN + 64, top + 100
    canvas.alpha_composite(_qr_image(invite_url(code), qr_size).convert("RGBA"), (qr_x, qr_y))
    draw.text(
        (qr_x + qr_size // 2, qr_y + qr_size + 18), "扫码注册",
        font=_font(30, True), fill=INK, anchor="mt",
    )

    col = qr_x + qr_size + 56
    draw.text((col, top + 104), "扫码，自动绑定邀请", font=_font(40, True), fill=INK)
    draw.line([(col, top + 178), (col + 372, top + 178)], fill=LINE_CARD, width=1)
    draw.text((col, top + 202), "我的邀请码", font=_font(28), fill=LABEL)
    draw.text((col, top + 240), code, font=_font(52, True), fill=BLUE)
    draw.text(
        (col, top + 336), f"每邀请 1 人，各得 {reward_points} 积分",
        font=_font(28), fill=MUTED,
    )

    # —— 底部 ——
    draw.line([(MARGIN, 1244), (WIDTH - MARGIN, 1244)], fill=LINE_DARK, width=1)
    draw.text(
        (MARGIN, 1272), f"长按识别二维码 · 或访问 {_site_host()}",
        font=_font(28), fill=FOOT,
    )
    draw.text(
        (WIDTH - MARGIN, 1272), "AI学习猫 · 能学得会的 AI",
        font=_font(28), fill=FOOT_DIM, anchor="rt",
    )

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _load_avatar(url: str | None) -> bytes | None:
    """下载头像：本站 COS 走服务端凭证（匿名可能被桶策略拦成 403），外部地址直连"""
    if not url:
        return None
    try:
        key = own_cos_object_key(url)
        if key:
            data, _ = await cos_service.download_object_bytes(key, _AVATAR_MAX_BYTES)
            return data
        async with httpx.AsyncClient(timeout=_AVATAR_TIMEOUT, follow_redirects=True) as client:
            # 流式读 + 边读边判上限：先 get() 再 len() 的话，超大响应已经整包进内存了
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > _AVATAR_MAX_BYTES:
                    logger.warning("邀请海报：头像响应超出大小上限，改用首字底色")
                    return None
                buf = bytearray()
                async for chunk in resp.aiter_bytes(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > _AVATAR_MAX_BYTES:
                        logger.warning("邀请海报：头像响应超出大小上限，改用首字底色")
                        return None
                return bytes(buf)
    except Exception as exc:  # noqa: BLE001 - 头像拿不到不该让海报整体失败
        logger.warning(f"邀请海报：头像下载失败，改用首字底色 ({exc})")
    return None


async def build_invite_poster(
    *,
    invite_code: str,
    nickname: str = "",
    avatar_url: str | None = None,
    reward_points: int = 50,
) -> bytes:
    """生成邀请海报 PNG 字节（CPU 段在线程池执行，避免阻塞事件循环）"""
    avatar = await _load_avatar(avatar_url)
    return await run_heavy(_render, invite_code, nickname or "", avatar, reward_points)
