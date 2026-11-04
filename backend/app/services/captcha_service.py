"""登录图形验证码（SVG 自绘，零第三方依赖）

为什么需要：全局限流只按 IP，`login_fail` 只锁单账号 —— 跨大量 IP 的慢速撞库
两条都拦不住（auth_service 预留的「人机挑战」方向）。验证码把「是否真人」变成
每次密码登录的前置问题，把爆破成本前移到请求侧。

设计要点：
- **默认关闭**（`login_captcha_enabled`，「登录管理」页可开）：内容 API 是本项目的
  公开交付面，第三方自建的登录页不感知这个开关；开启后能力位经
  `GET /auth/capabilities` 的 `captcha_enabled` 下发，前端据此渲染验证码框。
- 答案存 Redis（一次性消费 + TTL）：Redis 挂掉时**拒绝校验**（fail-closed），
  与限流中间件对敏感端点的降级方向一致 —— 人机验证失效绝不能退化成「全部放行」。
- 字符集剔除 `0O1I` 等易混字符；SVG 由服务端自绘：进入文本节点的只有白名单字符，
  其余属性全部由常量与数值拼出 —— 生成物交给前端 `<img>` 渲染，不进 DOM。
"""

from __future__ import annotations

import secrets
import uuid

from redis.exceptions import RedisError

from app.core.exceptions import AppException
from app.redis import redis_client
from app.services.integration_settings_service import get_auth_config

CAPTCHA_TTL_SECONDS = 300
CAPTCHA_LENGTH = 4

# 剔除 0/O/1/I 等易混字符：验证码本来就是给「看不清」准备的，别再雪上加霜
_CAPTCHA_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

# 与 login_fail / totp_lock 同属登录安全的 Redis 键域
_KEY_PREFIX = "login_captcha:"

# SVG 画布与字形参数：字符旋转角、基线偏移都随机化，拼图式逐字符变换
# 让简单的模板 OCR 也难以一次性切出固定网格
_SVG_WIDTH = 120
_SVG_HEIGHT = 40
_CHAR_ROTATIONS = (-24, -16, -8, 8, 16, 24)
_CHAR_SIZES = (22, 24, 26)
_CHAR_COLORS = ("#1f2933", "#3b4a5f", "#543d7a", "#1d5c4f", "#7a3b3b")
_NOISE_COLORS = ("#c3ccd9", "#d5cde3", "#cfe0d8")


def _pick(seq: tuple) -> object:
    return secrets.choice(seq)


def _build_svg(code: str) -> str:
    """把 4 位验证码画成 SVG。

    只做字符串拼接：code 出自白名单字符集（无 XML 特殊字符），数值参数全部
    来自常量与 `secrets` 随机数，不存在注入面；前端以 `<img src>` 消费，
    SVG 不进 DOM，进一步收窄潜在风险。
    """
    step = _SVG_WIDTH / (CAPTCHA_LENGTH + 1)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_SVG_WIDTH}" '
        f'height="{_SVG_HEIGHT}" viewBox="0 0 {_SVG_WIDTH} {_SVG_HEIGHT}" '
        f'role="img" aria-label="login captcha">',
        f'<rect width="{_SVG_WIDTH}" height="{_SVG_HEIGHT}" fill="#f4f6f9"/>',
    ]
    # 噪声曲线：3 条贝塞尔弧线打乱字符轮廓，位置每次随机
    for _ in range(3):
        x1, x2 = _pick((10, 30, 60, 90)), _pick((30, 60, 90, 110))
        y1, y2 = _pick((4, 12, 24, 34)), _pick((4, 12, 24, 34))
        cx = (x1 + x2) / 2 + _pick((-18, -9, 9, 18))
        parts.append(
            f'<path d="M {x1} {y1} Q {cx:.0f} {(y1 + y2) / 2:.0f} {x2} {y2}" '
            f'stroke="{_pick(_NOISE_COLORS)}" stroke-width="2" fill="none"/>'
        )
    for i, ch in enumerate(code):
        x = step * (i + 1)
        y = _pick((26, 28, 30))
        rot = _pick(_CHAR_ROTATIONS)
        parts.append(
            f'<text x="{x:.0f}" y="{y}" '
            f'transform="rotate({rot} {x:.0f} {y})" '
            f'font-family="Verdana, Geneva, sans-serif" '
            f'font-size="{_pick(_CHAR_SIZES)}" font-weight="700" '
            f'fill="{_pick(_CHAR_COLORS)}" text-anchor="middle">{ch}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


async def generate_captcha() -> dict:
    """生成一枚验证码：返回 id / SVG / 有效期，答案只进 Redis 不回传。

    Redis 故障时抛 503 而不是降级放行 —— 该端点本身在 SENSITIVE_PATHS 里
    （限流层已对 Redis 故障 fail-closed），这里保持同一方向：拿不到存储就
    不签发，避免「看起来发了码、实际验不了」的假可用。
    """
    code = "".join(secrets.choice(_CAPTCHA_ALPHABET) for _ in range(CAPTCHA_LENGTH))
    captcha_id = uuid.uuid4().hex
    try:
        await redis_client.set(_KEY_PREFIX + captcha_id, code, ex=CAPTCHA_TTL_SECONDS)
    except RedisError as exc:
        # 只捕 Redis 异常：编程错误直接 500 暴露，不伪装成「验证码不可用」
        raise AppException(code=503, message="验证码服务暂不可用，请稍后再试") from exc
    return {
        "captcha_id": captcha_id,
        "svg": _build_svg(code),
        "expires_in": CAPTCHA_TTL_SECONDS,
    }


async def verify_captcha(captcha_id: str | None, code: str | None) -> bool:
    """校验并**一次性消费**一枚验证码（大小写不敏感）。

    GETDEL 原子取值并删除：天然防重放（同一枚码第二次必失败）。
    Redis 故障抛 503（fail-closed）—— 人机验证失效绝不能退化成「全部放行」。
    """
    if not captcha_id or not code:
        return False
    try:
        stored = await redis_client.getdel(_KEY_PREFIX + captcha_id)
    except RedisError as exc:
        raise AppException(code=503, message="验证码服务暂不可用，请稍后再试") from exc
    return bool(stored) and stored.upper() == code.strip().upper()


async def enforce_login_captcha(captcha_id: str | None, captcha_code: str | None) -> None:
    """登录链路的人机挑战收口：开关关闭直接放行；开启则必须先过验证码。

    读取走 `get_auth_config()`（Redis 60s + 进程内 30s 缓存），与手机号/微信
    登录开关同一数据面；「登录管理」页保存时已有缓存失效逻辑，开关即时生效。
    """
    cfg = await get_auth_config()
    if not cfg.bool("login_captcha_enabled"):
        return
    if await verify_captcha(captcha_id, captcha_code):
        return
    raise AppException(
        code=400,
        message="图形验证码不正确或已过期，请刷新后重试",
        data={"captcha_required": True},
    )
