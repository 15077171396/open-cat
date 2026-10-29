"""扩展正文 LLM 翻译服务：管理后台「翻译为中文」实现，把英文 README（Markdown/HTML）
经 DeepSeek 翻为简体中文后存库（导入阶段一次性翻译）。设计要点：
1. 代码保护：代码块/行内代码替换为 %%CODE_n%% 占位符，译文占位符数量不一致时该块保留原文（宁可不译，不可译坏）；
2. 分块并发：按块级边界切分（约 1800 字符/块）并发翻译，规避 max_tokens 截断与请求超时，单块失败保留原文；
3. 格式保留：提示词约束仅翻译自然语言文本，URL/标签/属性原样保留。
"""

import asyncio
import json
import os
import re
import time
import uuid

from loguru import logger

from app.redis import redis_client

from app.core.exceptions import AppException
from app.services import ai_usage_service
from app.services.ai.factory import get_provider

# 单块字符数上限（约 450 token 输入 / 1500 token 输出，兼顾
# AI_TIMEOUT_SECONDS=30s 超时与 deepseek-chat 输出上限）
_CHUNK_CHAR_LIMIT = 1800

# 并发翻译块数上限（避免触发 API 速率限制）
_CONCURRENCY = 3

# 翻译输入总字符数上限（约 200KB，超过提示手动精简）
_MAX_INPUT_CHARS = 200_000

# 代码占位符：%%CODE_0%%、%%CODE_1%%…（提示词要求原样保留）。
# 注意用 str.format 而非 % 格式化——% 会把 %% 转义成单个 %
_PLACEHOLDER_FMT = "%%CODE_{}%%"
_PLACEHOLDER_RE = re.compile(r"%%CODE_(\d+)%%")

# Markdown 围栏代码块（``` 或 ~~~）
_MD_FENCE_RE = re.compile(r"(```+|~~~+)[\s\S]*?\1")
# Markdown 行内代码 `...`
_MD_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# HTML 代码块与行内代码
_HTML_PRE_RE = re.compile(r"<pre\b[^>]*>[\s\S]*?</pre>", re.IGNORECASE)
_HTML_CODE_RE = re.compile(r"<code\b[^>]*>[\s\S]*?</code>", re.IGNORECASE)

# 块级边界（HTML 块级标签前），用于把超长段落二次切分
_HTML_BLOCK_SPLIT_RE = re.compile(
    r"(?=<(?:p|div|h[1-6]|li|ul|ol|pre|blockquote|table|tr|section|article)\b)"
)

_SYSTEM_PROMPT = (
    "你是专业技术文档翻译引擎。把用户提供的 Markdown/HTML 内容翻译成简体中文，规则：\n"
    "1. 只翻译自然语言文本；代码、命令、URL、HTML/XML 标签及属性、Markdown 语法符号保持原样\n"
    "2. 形如 %%CODE_数字%% 的占位符必须原样保留，不得翻译、删改或增减\n"
    "3. 常见技术名词（如 Docker、API、LLM、ComfyUI、Workflow）保留英文\n"
    "4. 直接输出翻译后的完整内容，不要任何解释、前言或代码块包裹"
)


def _mask_code(text: str, fmt: str) -> tuple[str, list[str]]:
    """把代码内容替换为占位符，返回 (替换后文本, 代码原文列表)"""
    segments: list[str] = []

    def _repl(m: re.Match) -> str:
        segments.append(m.group(0))
        return _PLACEHOLDER_FMT.format(len(segments) - 1)

    patterns = [_HTML_PRE_RE, _HTML_CODE_RE]
    if fmt == "markdown":
        patterns = [_MD_FENCE_RE, _MD_INLINE_CODE_RE, *patterns]
    for pat in patterns:
        text = pat.sub(_repl, text)
    return text, segments


def _unmask_code(text: str, segments: list[str]) -> str:
    """把占位符还原为代码原文"""

    def _repl(m: re.Match) -> str:
        idx = int(m.group(1))
        return segments[idx] if idx < len(segments) else m.group(0)

    return _PLACEHOLDER_RE.sub(_repl, text)


def _split_into_chunks(text: str) -> list[str]:
    """按块级边界把长文切成不超过 _CHUNK_CHAR_LIMIT 的块

    Markdown 以空行分段；超长段落再按 HTML 块级标签前缀二次切分；
    仍超长的单元（如超长单行）按行聚合硬拆。
    """
    if len(text) <= _CHUNK_CHAR_LIMIT:
        return [text]

    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        if len(para) > _CHUNK_CHAR_LIMIT:
            units.extend(u for u in _HTML_BLOCK_SPLIT_RE.split(para) if u)
        else:
            units.append(para)

    # 仍超长的单元按行聚合硬拆
    flattened: list[str] = []
    for unit in units:
        if len(unit) <= _CHUNK_CHAR_LIMIT:
            flattened.append(unit)
            continue
        buf = ""
        for line in unit.splitlines(keepends=True):
            # 单行本身超长（如超长 URL）按字符硬切
            while len(line) > _CHUNK_CHAR_LIMIT:
                if buf:
                    flattened.append(buf)
                    buf = ""
                flattened.append(line[:_CHUNK_CHAR_LIMIT])
                line = line[_CHUNK_CHAR_LIMIT:]
            if buf and len(buf) + len(line) > _CHUNK_CHAR_LIMIT:
                flattened.append(buf)
                buf = line
            else:
                buf += line
        if buf:
            flattened.append(buf)

    chunks: list[str] = []
    buf = ""
    for unit in flattened:
        if buf and len(buf) + len(unit) + 2 > _CHUNK_CHAR_LIMIT:
            chunks.append(buf)
            buf = unit
        else:
            buf = f"{buf}\n\n{unit}" if buf else unit
    if buf:
        chunks.append(buf)
    return chunks


async def _translate_chunk(chunk: str) -> str:
    """翻译单个文本块，失败或校验不通过时返回原文"""
    provider = await get_provider()
    # 全站成本护栏：翻译同样是计费 AI 调用，必须计入全站额度；超限时该块保留原文、任务继续跑完
    ticket = None
    # 用量出参：provider 是进程级单例且并发分片共用，靠引用传出避免记账互相覆盖
    usage_sink: dict = {}
    try:
        ticket = await ai_usage_service.reserve_quota()
        result = await provider.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": chunk},
            ],
            max_tokens=4000,
            temperature=0.1,
            usage_out=usage_sink,
        )
    except Exception as exc:  # noqa: BLE001 - 单块失败保留原文，不阻塞整体
        # 调用未成功 → 归还全站占位（内部吞异常，不会掩盖原始错误）
        await ai_usage_service.release_quota(ticket)
        logger.warning(f"AI 翻译块失败，保留原文: {exc}")
        return chunk

    # 记账本次 token 用量（统计失败不影响业务）
    await ai_usage_service.record_provider_usage(provider, usage=usage_sink)

    result = (result or "").strip()
    # 剥离模型偶尔添加的代码块包裹
    if result.startswith("```"):
        result = re.sub(r"^```[\w-]*\n?", "", result)
        result = re.sub(r"\n?```\s*$", "", result)
    if not result:
        return chunk
    # 占位符数量不一致说明模型改动了代码占位，保留原文更安全
    if len(_PLACEHOLDER_RE.findall(result)) != len(_PLACEHOLDER_RE.findall(chunk)):
        logger.warning("AI 译文占位符数量不一致，该块保留原文")
        return chunk
    return result


async def validate_translation_input(content: str) -> None:
    """翻译前置校验（尽早暴露配置/输入问题）

    Key 不再只看 .env：模型管理里配置的独立 Key 同样有效（DB 优先、env 兜底）。
    """
    from app.services.ai_config_service import get_runtime_config

    cfg = await get_runtime_config()
    if not cfg.api_key:
        raise AppException(
            code=503,
            message="AI 翻译未配置：请在「系统 → 模型管理」为默认模型配置 API Key（或服务端 .env）",
        )

    text = (content or "").strip()
    if not text:
        raise AppException(code=400, message="正文内容为空，无需翻译")
    if len(text) > _MAX_INPUT_CHARS:
        raise AppException(code=400, message="正文超过 20 万字符上限，请精简后再翻译")


async def translate_content(
    content: str,
    fmt: str = "markdown",
    progress_cb=None,
) -> str:
    """把扩展正文翻译为简体中文

    :param content: Markdown 或 HTML 文本
    :param fmt: "markdown" | "html"（决定代码保护规则与还原拼接方式）
    :param progress_cb: 可选异步回调 ``async (done, total) -> None``，每完成一块触发
    :return: 翻译后的同格式文本
    """
    await validate_translation_input(content)

    text = (content or "").strip()
    masked, segments = _mask_code(text, fmt)
    chunks = _split_into_chunks(masked)

    total = len(chunks)
    done = 0
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _run(chunk: str) -> str:
        nonlocal done
        async with sem:
            result = await _translate_chunk(chunk)
            done += 1
            if progress_cb:
                try:
                    await progress_cb(done, total)
                except Exception as exc:  # noqa: BLE001 - 进度回调失败不影响翻译
                    logger.debug(f"翻译进度回调失败（已忽略）：{exc}")
            return result

    translated = await asyncio.gather(*(_run(c) for c in chunks))
    joiner = "" if fmt == "html" else "\n\n"
    result = _unmask_code(joiner.join(translated), segments)

    # 粗略判断翻译是否产生效果：中文字符占比显著提升才算成功，
    # 避免模型整体未翻译时把"成功"假象返回给管理端
    zh_before = len(re.findall(r"[\u4e00-\u9fff]", text))
    zh_after = len(re.findall(r"[\u4e00-\u9fff]", result))
    if zh_after <= zh_before and len(re.findall(r"[A-Za-z]", text)) > 200:
        raise AppException(
            code=503, message="AI 翻译未生效（译文与原文一致），请稍后重试或手动翻译"
        )
    return result


# 异步翻译任务管理：长文翻译需 1-2 分钟，同步等待会被 CDN/网关超时掐断（524），
# 故提交任务立即返回 task_id、前端轮询。任务状态存 Redis 而非进程内存：
# 多 worker 下提交（POST）与轮询（GET）可能落在不同 worker，进程内存互相不可见。

_TASK_TTL_SECONDS = 600
_TASK_KEY_FMT = "translate:task:{task_id}"

# 进度陈旧判定：执行体是接单 worker 本地的 asyncio.Task，worker 被 OOM/发版杀掉时
# 没有人写终态，Redis 里的 running 会悬到 TTL；正常进度间隔远小于 180s，超时即判执行体已消失
_TASK_STALE_SECONDS = 180

# 单 worker 同时在跑的翻译任务上限：每个任务内部还会 fan-out 3 路并发计费 AI 调用，
# 无上限时管理员连点 N 次即 3N 路并发 + 3N 份 200KB 文本驻留内存（per-worker 上限）
_MAX_CONCURRENT_TASKS_PER_WORKER = 2

# task_id -> (Task, state)。state 与任务闭包**共享同一对象**：关停排收要读它的实时进度，
# 而写终态必须由关停协程来做（见 shutdown_translate_tasks 的注释）
_translate_tasks: dict[str, tuple[asyncio.Task, dict]] = {}


async def _save_task(task_id: str, state: dict) -> None:
    """把任务状态写入 Redis（TTL 期间可轮询；写入失败仅记日志不影响翻译）"""
    payload = json.dumps(
        {
            "status": state["status"],
            "done": state["done"],
            "total": state["total"],
            "content": state["result"],
            "error": state["error"],
            # 进度即心跳：get_translate_task 用它判陈旧
            "updated_at": int(time.time()),
            # 归属 worker（多 worker 部署下排查「谁在执行这个任务」用）
            "worker": os.getpid(),
        },
        ensure_ascii=False,
    )
    try:
        await redis_client.set(
            _TASK_KEY_FMT.format(task_id=task_id), payload, ex=_TASK_TTL_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - Redis 异常不应中断翻译任务
        logger.warning(f"写入翻译任务状态失败 {task_id}: {exc}")


async def submit_translate_task(content: str, fmt: str = "markdown") -> str:
    """提交异步翻译任务，立即返回 task_id（输入校验失败同步抛出）"""
    await validate_translation_input(content)
    if len(_translate_tasks) >= _MAX_CONCURRENT_TASKS_PER_WORKER:
        raise AppException(
            code=429,
            message=(
                f"翻译任务繁忙（本机已有 {len(_translate_tasks)} 个在跑），"
                "请等当前任务完成后再提交"
            ),
        )
    task_id = uuid.uuid4().hex
    state = {
        "status": "pending",  # pending | running | completed | failed
        "done": 0,
        "total": 0,
        "result": None,
        "error": None,
    }
    # 先落 Redis 再启动后台任务，确保前端首次轮询一定能读到 pending 状态
    await _save_task(task_id, state)
    # create_task 与登记之间不能插入 await：否则两个提交会同时通过上面的上限判定
    task = asyncio.create_task(_run_translate_task(task_id, state, content, fmt))
    _translate_tasks[task_id] = (task, state)
    return task_id


async def get_translate_task(task_id: str) -> dict | None:
    """查询任务进度，不存在/已过期返回 None"""
    try:
        payload = await redis_client.get(_TASK_KEY_FMT.format(task_id=task_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取翻译任务状态失败 {task_id}: {exc}")
        return None
    if payload is None:
        return None
    try:
        state = json.loads(payload)
    except (TypeError, ValueError):
        return None

    if state.get("status") in ("pending", "running"):
        # 陈旧即判死：宁可给前端一个明确终态让它停止轮询，也不要让它盯一个
        # 永远不会推进的任务直到 TTL 到期
        # （旧格式条目没有 updated_at，按 0 处理 → 直接判陈旧；最长影响 10 分钟）
        if time.time() - int(state.get("updated_at") or 0) > _TASK_STALE_SECONDS:
            state["status"] = "failed"
            state["stale"] = True
            state["error"] = "任务长时间无进度（执行体可能已随服务重启消失），请重新提交"
    return state


async def shutdown_translate_tasks() -> int:
    """应用关停时取消在飞翻译任务，并把中断写成终态（main.lifespan 调用）。
    ⚠️ 终态必须由关停协程写：被取消的任务里再 await Redis 会撞上第二次取消，写入发不出去。"""
    snapshot = dict(_translate_tasks)
    for task, _state in snapshot.values():
        task.cancel()
    if snapshot:
        await asyncio.gather(*(t for t, _ in snapshot.values()), return_exceptions=True)

    interrupted = 0
    for task_id, (_task, state) in snapshot.items():
        _translate_tasks.pop(task_id, None)
        if state["status"] in ("pending", "running"):
            state["status"] = "failed"
            state["error"] = "服务正在关闭，翻译任务已中断，请重新提交"
            await _save_task(task_id, state)
            interrupted += 1
    return interrupted


async def _run_translate_task(task_id: str, state: dict, content: str, fmt: str) -> None:
    """后台执行翻译并回写任务状态到 Redis（state 由 submit 创建、与关停排收共享）"""
    cancelled = False
    try:
        state["status"] = "running"
        await _save_task(task_id, state)

        async def _progress(done: int, total: int) -> None:
            state["done"] = done
            state["total"] = total
            await _save_task(task_id, state)

        result = await translate_content(content, fmt, progress_cb=_progress)
        state["result"] = result
        state["status"] = "completed"
    except asyncio.CancelledError:
        # 状态交给 shutdown_translate_tasks 统一写（那里能安全 await）
        cancelled = True
        raise
    except Exception as exc:  # noqa: BLE001 - 任务失败转为状态而非抛出
        state["error"] = getattr(exc, "message", None) or str(exc)
        state["status"] = "failed"
        logger.warning(f"异步翻译任务 {task_id} 失败: {state['error']}")
    finally:
        if not cancelled:
            await _save_task(task_id, state)
        # 结束后释放强引用，避免长期运行的进程内存增长（状态已持久化到 Redis）
        _translate_tasks.pop(task_id, None)
