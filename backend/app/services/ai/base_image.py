"""图像 Provider 抽象。与文本 Provider 分开：入参、计费单位（张）与返回结构都不同。"""

from abc import ABC, abstractmethod


class ImageProvider(ABC):
    config_fingerprint: tuple = ()
    model_key: str = ""
    image_price: float = 0.0

    @abstractmethod
    async def generate(
        self, prompt: str, *, model: str | None = None, size: str = "1024*1024",
        n: int = 1, negative_prompt: str | None = None, watermark: bool = False,
    ) -> list[str]:
        """返回临时图片 URL 列表；调用方负责下载与转存。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        ...
