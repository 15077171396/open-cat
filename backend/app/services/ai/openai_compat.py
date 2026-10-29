"""OpenAI 兼容协议的通用 Provider 基类：DeepSeek / OpenAI / 通义千问（DashScope 兼容模式）
实现同一套 POST {base_url}/chat/completions 协议，差异只在 base_url、API Key、默认模型与报错文案。
协议细节里藏着踩过一次才知道的坑（限流以 200 返回 error、usage 可能缺失等），收敛为一份实现
避免各家复制后漂移；各家只声明差异（provider_name 等类属性）。"""

import json

from loguru import logger
import httpx

from app.config import settings
from app.core.exceptions import AppException
from app.services.ai.base import AIProvider


class OpenAICompatProvider(AIProvider):
    """OpenAI 兼容对话接口的通用实现。

    子类必须声明：
        provider_name: 注册名，与工具 `ai_config.model` 的取值一致
        display_name:  报错文案里的可读名（面向管理员的排障信息）
    """

    provider_name: str = ""
    display_name: str = "AI 模型"

    def __init__(self, *, api_key: str, base_url: str, default_model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        # 最近一次调用的 token 用量（响应里的 usage 字段）。
        #
        # ⚠️ **新代码请用 `chat(..., usage_out=...)` 而不是读这个属性**（第三轮增量审查 P1-44）。
        # 它是进程级单例上的共享可变状态，只有在「`await chat()` 返回后**立刻**读取」
        # 时才正确 —— 中间插入任何 await（或把调用包进 `asyncio.wait_for` 之类的
        # 新 Task），都会被并发调用覆盖。保留它仅为向后兼容既有测试与外部引用。
        self.last_usage: dict = {}
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=settings.AI_TIMEOUT_SECONDS,
        )

    @property
    def is_configured(self) -> bool:
        """是否已配置 API Key。未配置时调用会明确报错，而不是发出必然 401 的请求。"""
        return bool(self.api_key)

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        usage_out: dict | None = None,
        **kwargs,
    ) -> str:
        """调用对话接口，返回回复文本。

        :param model: 具体模型名（如 ``deepseek-chat`` / ``gpt-4o-mini``）；
            不传则用该 Provider 的默认模型。
        :param usage_out: **推荐的用量取回方式**（第三轮增量审查 P1-44）。
            传入一个调用方自有的 dict，本方法会把本次响应的 ``usage`` 写进去
            （无 usage 时清空）。相比读 `self.last_usage`，它不依赖「读得够快」：
            provider 是进程级单例，而调用方可能并发复用同一实例，
            甚至把调用包进新 Task（Python ≤3.11 的 `asyncio.wait_for` 就是这么做的）
            —— 那种情况下共享属性会被覆盖、而按引用传递的出参不会。
        """
        if not self.is_configured:
            # 明确报错而不是「静默回退到其它模型」：换模型会改变成本与输出质量，
            # 必须让配置者看见。日志里带上 provider 名，便于从监控定位。
            logger.error(f"{self.display_name} 未配置 API Key，拒绝调用（provider={self.provider_name}）")
            raise AppException(code=503, message=f"{self.display_name} 未配置，请联系管理员")

        try:
            response = await self.client.post(
                "/chat/completions",
                json={
                    "model": model or self.default_model,
                    "messages": messages,
                    "max_tokens": kwargs.get("max_tokens", 2000),
                    "temperature": kwargs.get("temperature", 0.7),
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            logger.warning(f"{self.display_name} API 超时")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"{self.display_name} API 返回异常状态: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"{self.display_name} API 调用失败: {e}")
            raise

        # 响应结构校验：限流/风控时服务端以 HTTP 200 返回 {"error": {...}} 结构，
        # 裸取 data["choices"][0] 会抛 KeyError 落入全局 500，用户无法得知真实原因
        choices = data.get("choices") or []
        if not choices:
            err = data.get("error") or {}
            logger.warning(
                f"{self.display_name} 返回无 choices: "
                f"code={err.get('code')}, message={err.get('message')}"
            )
            raise AppException(code=503, message="AI 服务暂时不可用，请稍后再试")
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise AppException(code=503, message="AI 服务返回空内容，请稍后重试")
        # 记录本次用量（台账 P1-2）。放在内容校验之后：没有正文的响应不产生记账，
        # 避免把失败的调用也算成「成功消耗」。usage 缺失时置空而非保留上一次的值，
        # 否则一次「无 usage 的成功响应」会被误记成上一条的 token 数。
        usage = data.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        self.last_usage = usage
        if usage_out is not None:
            # 出参：调用方持有引用，不依赖「读得够快」（见 docstring）
            usage_out.clear()
            usage_out.update(usage)
        return content

    async def stream_chat(self, messages, usage_out=None, **kwargs):
        if not self.is_configured:
            raise AppException(code=503, message=f"{self.display_name} 未配置，请联系管理员")
        payload = {
            "model": kwargs.get("model") or self.default_model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 2000),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            async with self.client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:500]
                    logger.error(f"{self.display_name} 流式返回 {response.status_code}: {body}")
                    raise AppException(code=503, message="AI 服务暂时不可用，请稍后再试")
                # 限流/风控时部分上游以 HTTP 200 + 纯 JSON 响应体（非 SSE）返回错误。
                # 这种响应没有任何 `data:` 帧，若继续按 SSE 解析会静默结束：既不产出内容
                # 也不报错，调用方拿不到任何失败信号。正常流式响应的 content-type 是
                # text/event-stream，不会进入下面的分支，因此不会缓冲正常的流式数据。
                content_type = response.headers.get("content-type", "").lower()
                if "application/json" in content_type:
                    raw = (await response.aread()).decode("utf-8", "replace")
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        body = None
                    if isinstance(body, dict) and body.get("error"):
                        logger.warning(
                            f"{self.display_name} 流式返回 200 JSON 错误体: {body['error']}"
                        )
                    elif isinstance(body, dict) and body.get("choices"):
                        # 上游忽略了 stream=True，回了普通对话响应；流式接口无法消费，按错误处理
                        logger.warning(
                            f"{self.display_name} 流式返回 200 JSON choices（非流式响应）"
                        )
                    else:
                        logger.warning(
                            f"{self.display_name} 流式返回 200 非 SSE 响应: {raw[:200]}"
                        )
                    raise AppException(code=503, message="AI 服务暂时不可用，请稍后再试")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        err = chunk["error"]
                        logger.warning(f"{self.display_name} 流式错误: {err}")
                        raise AppException(code=503, message="AI 服务暂时不可用，请稍后再试")
                    usage = chunk.get("usage")
                    if isinstance(usage, dict) and usage_out is not None:
                        # usage 帧（choices 为空）通常出现在 finish 帧之后：clear() 不能
                        # 把已捕获的 finish_reason 一起抹掉，否则 done 事件永远只能报 stop。
                        finish_reason = usage_out.get("finish_reason")
                        usage_out.clear()
                        usage_out.update(usage)
                        if finish_reason:
                            usage_out["finish_reason"] = finish_reason
                    choices = chunk.get("choices") or []
                    if choices:
                        # 上游的结束原因（stop/length/content_filter/...）必须原样带出：
                        # 只说 done 而不说是「写满了」还是「正常结束」，前端无法判断结果完整性。
                        finish_reason = choices[0].get("finish_reason")
                        if finish_reason and usage_out is not None:
                            usage_out["finish_reason"] = str(finish_reason)
                        text = (choices[0].get("delta") or {}).get("content")
                        if text:
                            yield text
        except httpx.TimeoutException:
            logger.warning(f"{self.display_name} 流式超时")
            raise
        except httpx.HTTPError as exc:
            logger.error(f"{self.display_name} 流式请求失败: {exc}")
            raise AppException(code=503, message="AI 服务暂时不可用，请稍后再试")

    async def close(self):
        await self.client.aclose()
