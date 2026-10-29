"""模型连通性探测：后台「测试连接」按钮的服务端实现。

为什么要有它：模型管理里能填 Key / Base URL / 上游模型名，但填错的表现是
**用户在工具页看到「AI 服务暂时不可用」** —— 管理员在后台完全看不出错在哪一项。
一次几十 token 的探测请求能把这段「只能靠现象反推」的排查变成一次点击。

三条刻意的取舍：
1. **不记用量、不占全站限额**：这是运维体检，不是业务调用。记进去只会污染看板口径
   （而且用户会看到「明明没人用，额度却少了」）。
2. **失败返回 200 + `ok=false`**，不用 4xx/5xx：连不上是这个接口的**正常结论**，
   前端要在结果行里就地展示原因，而不是弹一个红色 toast 然后什么也没留下。
3. **同模型 5 秒冷却**：按钮连点会真的连发请求（每次都花钱），也容易被上游当成异常流量。
"""

from __future__ import annotations

import time

import httpx
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models.ai_model import AiModel
from app.services.ai.factory import get_provider
from sqlalchemy.ext.asyncio import AsyncSession

#: 探测提示词：短、确定、不需要推理能力，能区分「通」与「不通」即可
PROBE_PROMPT = "只回复 pong 两个字母，不要任何解释。"
PROBE_MAX_TOKENS = 16

#: 同一模型两次探测的最小间隔（秒）
COOLDOWN_SECONDS = 5.0
_last_probe_at: dict[int, float] = {}


def reset_cooldown() -> None:
    """测试用：清掉冷却记录"""
    _last_probe_at.clear()


def check_cooldown(model_id: int) -> None:
    now = time.monotonic()
    last = _last_probe_at.get(model_id)
    if last is not None and now - last < COOLDOWN_SECONDS:
        wait = int(COOLDOWN_SECONDS - (now - last)) + 1
        raise AppException(code=429, message=f"测试太频繁，请 {wait} 秒后再试")
    _last_probe_at[model_id] = now


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, AppException):
        return exc.message
    if isinstance(exc, httpx.HTTPStatusError):
        # 上游报错正文里通常是「模型不存在 / Key 无效 / 余额不足」，比状态码有用得多；
        # 截断到 200 字，避免把整页 HTML 塞进响应（也不含我们自己的 Key）
        detail = (exc.response.text or "").strip().replace("\n", " ")[:200]
        return f"HTTP {exc.response.status_code}" + (f"：{detail}" if detail else "")
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时：上游没在超时时间内响应（检查 Base URL 与网络出口）"
    if isinstance(exc, httpx.InvalidURL):
        return f"Base URL 不合法：{exc}"
    return f"{type(exc).__name__}: {exc}"[:200]


async def probe_model(db: AsyncSession, model_id: int) -> dict:
    """对一条模型做一次真实调用，返回结构化结果（不抛业务异常）"""
    check_cooldown(model_id)

    row = (await db.execute(select(AiModel).where(AiModel.id == model_id))).scalar_one_or_none()
    if row is None:
        raise AppException(code=404, message="AI 模型不存在")

    base = {
        "model": row.code,
        "display_name": row.display_name,
        "provider": row.provider,
        "upstream_model": row.model_name or "",
    }

    if row.modality != "text":
        # 探测只发 chat 请求：图像模型没有这条接口。继续走 get_provider 会按
        # **文本**模态解析，图像 code 查不到后回退全局文本默认模型 —— 用文本模型
        # 的成功结果冒充图像行的连通性，管理员看到「测试通过」却仍出不了图。
        return {
            **base,
            "ok": False,
            "latency_ms": 0,
            "error": "图像模型暂不支持测试连接",
        }

    started = time.perf_counter()
    usage: dict = {}
    try:
        provider = await get_provider(row.code)
        # provider 是进程级单例：这里只借它发一次请求，不缓存任何结果，
        # 因此「测试通过 / 失败」永远反映**当前**配置
        reply = await provider.chat(
            [{"role": "user", "content": PROBE_PROMPT}],
            max_tokens=PROBE_MAX_TOKENS,
            usage_out=usage,
        )
    except (AppException, httpx.HTTPError, httpx.InvalidURL, OSError, ValueError) as exc:
        return {
            **base,
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": _error_text(exc),
        }

    return {
        **base,
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "reply": (reply or "").strip()[:100],
        "usage": {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        },
    }
