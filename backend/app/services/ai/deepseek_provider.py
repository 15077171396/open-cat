"""DeepSeek 模型适配 - 默认 AI 提供者

协议细节（请求结构、错误映射、用量旁路）全部在 :class:`OpenAICompatProvider`，
这里只声明差异。
"""

from app.config import settings
from app.services.ai.openai_compat import OpenAICompatProvider
from app.services.ai.registry import register_provider


@register_provider("deepseek", settings_prefix="ai_deepseek", env_prefix="DEEPSEEK")
class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek API 适配（OpenAI 兼容格式）"""

    provider_name = "deepseek"
    display_name = "DeepSeek"

    def __init__(self, config=None):
        """config 为 ProviderConfig（后台配置优先）；None 时回退环境变量，
        以兼容直接构造（测试 / 脚本）。"""
        super().__init__(
            api_key=config.api_key if config else settings.DEEPSEEK_API_KEY,
            base_url=config.base_url if config else settings.DEEPSEEK_BASE_URL,
            default_model=config.default_model if config else settings.DEEPSEEK_MODEL,
        )
