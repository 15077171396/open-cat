"""通义万相（DashScope）文生图 Provider。

万相用异步任务协议：提交拿 task_id，再轮询 /api/v1/tasks/{id}。
返回的 URL 只有 24 小时有效期，调用方必须立即转存 COS，不能写进正文。
"""

import asyncio
import time

import httpx
from loguru import logger

from app.config import settings
from app.core.exceptions import AppException
from app.services.ai.base_image import ImageProvider
from app.services.ai.registry import register_provider

_ALLOWED_SIZES = {"1024*1024", "1280*720", "720*1280", "1024*768"}
_POLL_INTERVAL = 2.0
# 轮询预算 60s（原先 100s）：加上提交请求自身的超时，必须给后续的下载/转存
# 留下时间，并让整条图片链路落在 API 层 100s SSE 预算与 gunicorn 120s 之内。
_POLL_BUDGET = 60.0


@register_provider(
    "dashscope_image", settings_prefix="ai_dashscope_image",
    env_prefix="DASHSCOPE_IMAGE", modality="image",
)
class DashScopeImageProvider(ImageProvider):
    provider_name = "dashscope_image"
    display_name = "通义万相"

    def __init__(self, config=None):
        self.api_key = (
            (config.api_key if config else settings.DASHSCOPE_IMAGE_API_KEY)
            or settings.QWEN_API_KEY
        )
        self.base_url = (
            (config.base_url if config else settings.DASHSCOPE_IMAGE_BASE_URL)
            or settings.DASHSCOPE_IMAGE_BASE_URL
        ).rstrip("/")
        # 复用 Qwen 配置时 base_url 常被填成兼容模式地址；万相原生任务 API 在根域下，
        # 留着 /compatible-mode/v1 会稳定 404。
        if "/compatible-mode" in self.base_url:
            self.base_url = self.base_url.split("/compatible-mode")[0]
        self.default_model = (
            (config.default_model if config else settings.DASHSCOPE_IMAGE_MODEL)
            or settings.DASHSCOPE_IMAGE_MODEL
        )
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )

    async def generate(self, prompt, *, model=None, size="1024*1024", n=1,
                       negative_prompt=None, watermark=False):
        if not self.api_key:
            raise AppException(code=503, message="通义万相未配置 API Key，请联系管理员")
        if size not in _ALLOWED_SIZES:
            raise AppException(code=400, message=f"不支持的图片尺寸: {size}")
        if n < 1 or n > 4:
            raise AppException(code=400, message="单次生成数量必须在 1 到 4 之间")

        payload = {
            "model": model or self.default_model,
            "input": {"prompt": prompt},
            "parameters": {
                "size": size, "n": n, "prompt_extend": True, "watermark": watermark,
            },
        }
        if negative_prompt:
            payload["input"]["negative_prompt"] = negative_prompt

        try:
            response = await self.client.post(
                "/api/v1/services/aigc/text2image/image-synthesis",
                headers={"X-DashScope-Async": "enable"}, json=payload,
            )
            response.raise_for_status()
            task_id = ((response.json().get("output") or {}).get("task_id") or "")
            if not task_id:
                raise AppException(code=503, message="图像服务未返回任务 ID，请稍后重试")
            return await self._poll(task_id)
        except httpx.TimeoutException:
            logger.warning("万相提交超时")
            raise AppException(code=504, message="图像生成超时，请重试")
        except httpx.HTTPStatusError as exc:
            logger.error(f"万相返回异常状态: {exc.response.status_code}")
            raise AppException(code=503, message="图像服务暂时不可用，请稍后重试")

    async def _poll(self, task_id: str) -> list[str]:
        deadline = time.monotonic() + _POLL_BUDGET
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            response = await self.client.get(f"/api/v1/tasks/{task_id}")
            response.raise_for_status()
            output = response.json().get("output") or {}
            status = output.get("task_status")
            if status == "SUCCEEDED":
                urls = [item.get("url") for item in (output.get("results") or []) if item.get("url")]
                if not urls:
                    raise AppException(code=503, message="图像服务未返回图片")
                return urls
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                logger.warning(f"万相任务失败: {status} {output.get('message', '')}")
                raise AppException(code=503, message="图像生成失败，请换一个描述重试")
        raise AppException(code=504, message="图像生成超时，请重试")

    async def close(self):
        await self.client.aclose()
