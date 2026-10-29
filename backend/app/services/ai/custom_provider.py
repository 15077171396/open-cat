"""自定义供应商：任何 **OpenAI 兼容** 的模型服务都能接（缺口：此前只有 deepseek/openai/qwen 三家）。

为什么不是「随便填个供应商名」：Provider 是**协议**，不是品牌。
本项目所有内置供应商都继承 `OpenAICompatProvider`（POST {base_url}/chat/completions），
所以「再接一家」本来就不需要新代码 —— 缺的只是一个允许自定义 base_url 的入口。
新增这一个 Provider 后，Moonshot/Kimi、智谱 GLM、SiliconFlow、百川、MiniMax、
Ollama / vLLM / LM Studio、OneAPI 网关等都能直接配进「模型管理」。

不做的部分（文档里也没承诺）：**非 OpenAI 协议**的服务（原生 Gemini、原生 Anthropic
Messages API、原生 DashScope 协议）仍然需要新写一个 Provider 类并注册 ——
那不是「配置项」，是协议适配。

约束：走这条路必须填 Base URL 与上游模型名（没有 base_url 无从拼请求），
校验在 `ai_model_service._require_custom_endpoint`，与创建/编辑接口同一处。
"""

from app.config import settings
from app.services.ai.openai_compat import OpenAICompatProvider
from app.services.ai.registry import register_provider


@register_provider("custom", settings_prefix="ai_custom", env_prefix="CUSTOM")
class CustomProvider(OpenAICompatProvider):
    """自定义（OpenAI 兼容）"""

    provider_name = "custom"
    display_name = "自定义（OpenAI 兼容）"

    def __init__(self, config=None):
        """config 为 ProviderConfig（模型管理里逐条配置的 base_url / key / model）；
        None 时回退 CUSTOM_* 环境变量。"""
        super().__init__(
            api_key=config.api_key if config else settings.CUSTOM_API_KEY,
            base_url=config.base_url if config else settings.CUSTOM_BASE_URL,
            default_model=config.default_model if config else settings.CUSTOM_MODEL,
        )
