"""自定义图像供应商：任何 OpenAI 兼容的 `/images/generations` 服务。

和文本 `custom` 的边界一致：Provider 是协议适配，不是品牌清单。
支持返回 `url`，也支持只返回 `b64_json` 的网关（转成 data URL 交给下载校验层）。
非 OpenAI 协议（原生豆包/即梦/SD WebUI 等）仍需单独实现 Provider，不能只靠配置。
"""

import base64

import httpx
from loguru import logger

from app.config import settings
from app.core.exceptions import AppException
from app.services.ai.base_image import ImageProvider
from app.services.ai.registry import register_provider


def _sniff_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    raise AppException(code=502, message="自定义图像接口返回了无法识别的图片格式")


@register_provider("custom_image", settings_prefix="ai_custom_image", env_prefix="CUSTOM", modality="image")
class CustomImageProvider(ImageProvider):
    """自定义图像（OpenAI 兼容）"""

    provider_name = "custom_image"
    display_name = "自定义图像（OpenAI 兼容）"

    def __init__(self, config=None):
        # 复用文本自定义供应商的 CUSTOM_* 环境变量作为兜底：两者通常指向同一个网关。
        self.api_key = config.api_key if config else settings.CUSTOM_API_KEY
        self.base_url = (config.base_url if config else settings.CUSTOM_BASE_URL).rstrip("/")
        self.default_model = config.default_model if config else settings.CUSTOM_MODEL
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60,
        )

    async def generate(
        self, prompt, *, model=None, size="1024*1024", n=1,
        negative_prompt=None, watermark=False,
    ):
        if not self.api_key or not self.base_url or not self.default_model:
            raise AppException(
                code=503,
                message="自定义图像模型未配置完整：请在模型管理填写 Base URL、上游模型名与 API Key",
            )
        if n < 1 or n > 4:
            raise AppException(code=400, message="单次生成数量必须在 1 到 4 之间")

        text = prompt
        if negative_prompt:
            text += f"\n避免出现：{negative_prompt}"
        payload = {
            "model": model or self.default_model,
            "prompt": text,
            "n": n,
            "size": size.replace("*", "x"),
        }
        try:
            response = await self.client.post("/images/generations", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("自定义图像接口超时")
            raise AppException(code=504, message="图像生成超时，请重试")
        except httpx.HTTPStatusError as exc:
            logger.error(f"自定义图像接口返回异常状态: {exc.response.status_code}")
            raise AppException(code=503, message="图像服务暂时不可用，请稍后重试")
        except httpx.HTTPError as exc:
            logger.error(f"自定义图像接口请求失败: {exc}")
            raise AppException(code=503, message="图像服务暂时不可用，请稍后重试")

        try:
            data = response.json().get("data") or []
        except ValueError:
            raise AppException(code=503, message="图像服务返回格式不正确")
        urls: list[str] = []
        for item in data:
            if item.get("url"):
                urls.append(str(item["url"]))
            elif item.get("b64_json"):
                try:
                    raw = base64.b64decode(item["b64_json"])
                except (ValueError, TypeError):
                    continue
                mime = _sniff_mime(raw)
                urls.append(f"data:{mime};base64,{base64.b64encode(raw).decode()}")
        if not urls:
            raise AppException(code=503, message="图像服务未返回可用图片")
        return urls

    async def close(self):
        await self.client.aclose()
