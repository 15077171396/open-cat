"""OpenAI 模型适配（缺口台账 P2-4）"""

from app.config import settings
from app.services.ai.openai_compat import OpenAICompatProvider
from app.services.ai.registry import register_provider


@register_provider("openai", settings_prefix="ai_openai", env_prefix="OPENAI")
class OpenAIProvider(OpenAICompatProvider):
    """OpenAI 官方 API（chat/completions）"""

    provider_name = "openai"
    display_name = "OpenAI"

    def __init__(self, config=None):
        """config 为 ProviderConfig（后台配置优先）；None 时回退环境变量。"""
        super().__init__(
            api_key=config.api_key if config else settings.OPENAI_API_KEY,
            base_url=config.base_url if config else settings.OPENAI_BASE_URL,
            default_model=config.default_model if config else settings.OPENAI_MODEL,
        )
