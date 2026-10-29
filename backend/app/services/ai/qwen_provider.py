"""通义千问（阿里云百炼 / DashScope）模型适配（缺口台账 P2-4）

走 DashScope 的 **OpenAI 兼容模式**端点，而不是原生 dashscope 协议：
本项目只需要 chat/completions，兼容端点能复用同一份实现，
不必为它单独引 SDK 或再写一套请求/错误处理。
"""

from app.config import settings
from app.services.ai.openai_compat import OpenAICompatProvider
from app.services.ai.registry import register_provider


@register_provider("qwen", settings_prefix="ai_qwen", env_prefix="QWEN")
class QwenProvider(OpenAICompatProvider):
    """通义千问（DashScope 兼容模式）"""

    provider_name = "qwen"
    display_name = "通义千问"

    def __init__(self, config=None):
        """config 为 ProviderConfig（后台配置优先）；None 时回退环境变量。"""
        super().__init__(
            api_key=config.api_key if config else settings.QWEN_API_KEY,
            base_url=config.base_url if config else settings.QWEN_BASE_URL,
            default_model=config.default_model if config else settings.QWEN_MODEL,
        )
