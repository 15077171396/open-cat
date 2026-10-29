"""配图流水线：调用图像 Provider → 下载临时 URL → 校验 → 转存 COS。

万相返回的 URL 24 小时过期，且属于第三方域名；正文只允许站内 COS URL。
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import socket
import time
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.core.degrade import degrade_closed, degrade_guard
from app.core.exceptions import AppException
from app.schemas.ai_writing import ImageRequest
from app.services import ai_usage_service
from app.services.storage import storage
from app.services.image_search.asset_recorder import record_generated_asset
from app.services.ai import factory
from app.utils.upload_validation import is_allowed_image, validate_image_pixels

_STYLE_PREFIX = {
    "flat": "扁平插画风格，干净的矢量质感，",
    "photo": "写实摄影风格，自然光，",
    "render": "3D 渲染风格，柔和材质，",
    "line": "黑白线稿风格，简洁线条，",
}
_MAX_DOWNLOAD = settings.COS_IMAGE_MAX_SIZE
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}

# 整条图片链路（提交 + 轮询 + 逐张下载 + 转存）的总预算。必须明显小于 API 层
# 100s 的 SSE 总预算：留出把「超时」翻译成 SSE error(504) 与归还票据的时间，
# 绝不能让 gunicorn 在 120s 直接杀掉 worker。
IMAGE_TOTAL_BUDGET = 90.0


def _timeout_error() -> AppException:
    return AppException(code=504, message="图像生成超时，请重试")


def _host_is_public(host: str) -> bool:
    """自定义图床可能返回任意 CDN；只允许解析到公网地址的 https 主机，挡 SSRF。"""
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(infos)


async def _download_image(url: str, *, allow_public: bool = False) -> tuple[bytes, str, int, int]:
    # 部分 OpenAI 兼容网关只返回 b64_json；Provider 已转成 data URL。
    # 这里跳过 host 下载，但仍走完整的体积/魔数/像素校验。
    if url.startswith("data:image/"):
        try:
            header, encoded = url.split(",", 1)
            content_type = header[5:].split(";")[0].lower()
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise AppException(code=502, message="图像数据格式不正确") from exc
        if len(data) > _MAX_DOWNLOAD or not is_allowed_image(content_type, data):
            raise AppException(code=502, message="生成图片校验失败")
        try:
            width, height = validate_image_pixels(data)
        except ValueError as exc:
            raise AppException(code=502, message=str(exc)) from exc
        return data, content_type, width, height

    host = (urlparse(url).hostname or "").lower()
    if allow_public:
        if not url.lower().startswith("https://") or not _host_is_public(host):
            raise AppException(code=502, message="图像地址必须是公网 https 地址")
    elif not host.endswith(".aliyuncs.com"):
        raise AppException(code=502, message="图像地址来源不可信")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        # 跟随重定向后必须再校验最终主机：否则 DashScope 结果地址跳转到任意域名时，
        # 上面的首次 host 校验会被绕过（SSRF）。
        final_host = (urlparse(str(response.url)).hostname or "").lower()
        if allow_public:
            if not str(response.url).lower().startswith("https://") or not _host_is_public(final_host):
                raise AppException(code=502, message="图像地址跳转到了不可信域名")
        elif not final_host.endswith(".aliyuncs.com"):
            raise AppException(code=502, message="图像地址跳转到了不可信域名")
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type not in _EXT:
            raise AppException(code=502, message="图像格式不受支持")
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > _MAX_DOWNLOAD:
            raise AppException(code=502, message="生成图片过大")
        data = response.content
    if len(data) > _MAX_DOWNLOAD or not is_allowed_image(content_type, data):
        raise AppException(code=502, message="生成图片校验失败")
    try:
        width, height = validate_image_pixels(data)
    except ValueError as exc:
        raise AppException(code=502, message=str(exc)) from exc
    return data, content_type, width, height


async def iter_images(
    image_req: ImageRequest, *, provider=None, usage_out: dict | None = None,
    admin_id: int | None = None,
):
    # 先查 COS 再提交生成任务：生图是付费调用，配置缺失必须在花钱之前失败。
    # 这里刻意放在逐张降级守卫之外 —— 配置错误不能被「单张转存失败只跳过」吞掉，
    # 否则会白烧一次生成费用，最后只得到「配图生成失败」这种误导性提示。
    if not storage.is_configured():
        raise AppException(code=503, message="COS 未配置，无法保存插图")
    provider = provider or await factory.get_image_provider(None)
    prompt = _STYLE_PREFIX.get(image_req.style, "") + image_req.prompt.strip()
    deadline = time.monotonic() + IMAGE_TOTAL_BUDGET
    yield "status", {"stage": "generating"}
    try:
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            urls = await provider.generate(
                prompt, size=image_req.size, n=image_req.count,
                negative_prompt="文字, 水印, logo, 低质量",
            )
    except TimeoutError:
        raise _timeout_error()
    except AppException:
        raise
    except Exception as exc:
        # Provider 的异常类型随供应商/网络栈而变，必须统一翻译成可重试的 503；
        # 方向是「拒绝」——不能让用户拿到「成功但没有任何配图」的正文。
        degrade_closed(
            "ai_writing.image_generate", exc, status=503, message="图像生成失败，请稍后重试"
        )
    saved = 0
    model_key = getattr(provider, "model_key", "") or "image"
    unit_price = float(getattr(provider, "image_price", 0) or 0)
    # 内置万相只信任 aliyuncs 结果域；自定义图像供应商的 CDN 不固定，
    # 改为「公网 https + 非内网解析」的 SSRF 边界。
    allow_public = getattr(provider, "provider_name", "") == "custom_image"
    for index, url in enumerate(urls):
        # 生成可能已经吃掉大部分预算：每张下载/转存都只能用剩下的时间，
        # 否则 4 张图各自 30s 超时会把整条链路拖过 gunicorn 的 120s。
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _timeout_error()
        yield "status", {"stage": "downloading", "index": index}
        cos_url = None
        # 单张失败只跳过该张（COS/下载/校验都可能失败），不能拖垮整批；
        # 全部失败会在循环结束后统一报错。
        try:
            async with asyncio.timeout(remaining):
                with degrade_guard(
                    "ai_writing.image_transfer",
                    detail=f"第 {index + 1} 张配图转存失败，跳过",
                ):
                    data, content_type, width, height = await _download_image(
                        url, allow_public=allow_public
                    )
                    cos_url = await storage.upload_bytes(
                        data, folder="ai-writing", ext=_EXT[content_type],
                        content_type=content_type,
                    )
        except TimeoutError:
            raise _timeout_error()
        if cos_url is None:
            continue
        if admin_id is not None:
            with degrade_guard(
                "ai_writing.asset_record",
                detail=f"第 {index + 1} 张 AI 图写入素材库失败（不影响出图）",
            ):
                await record_generated_asset(
                    url=cos_url, data=data, content_type=content_type,
                    width=width, height=height, prompt=image_req.prompt,
                    alt=image_req.alt or "", admin_id=admin_id,
                )
        saved += 1
        # 逐张记账而不是循环后汇总：SSE 客户端可能在收到前几张后断开，
        # 生成器在 yield 处被关闭，循环之后的记账不会执行 ——
        # 已转存成功的图片就会「文件在、额度与费用没记」。放在 yield 之前，
        # 保证每张已宣告成功的图片在发出事件前就已经落账。
        await ai_usage_service.record_image_usage(model_key, 1, unit_price)
        if usage_out is not None:
            usage_out["images_cost"] = round(
                float(usage_out.get("images_cost") or 0) + unit_price, 4
            )
        yield "image", {
            "index": index, "url": cos_url, "width": width, "height": height,
            "alt": image_req.alt or "", "prompt": image_req.prompt,
        }
    if saved == 0:
        raise AppException(code=503, message="配图生成失败，请重试")
