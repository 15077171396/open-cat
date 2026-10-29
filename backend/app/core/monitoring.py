"""错误监控接入 - Sentry / GlitchTip 门控初始化（未配置 DSN 零开销，凭据递归脱敏）"""

from typing import Any, Dict, List, Optional
import re

from loguru import logger

from app.config import settings

# 需要脱敏的字段名（小写子串匹配：命中即替换为 ***）
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "authorization",
    "cookie",
    "session",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "code",  # 邮箱/短信验证码
    "credential",
)

_REDACTED = "***"

# 自由文本里的凭据（key=value / key: value / "key": "value"）靠正则兜住；
# 键名用 (?<![A-Za-z0-9]) 前缀匹配而非 \b，否则 refresh_token 这类带前缀的键会漏掉
_TEXT_CREDENTIAL_RE = re.compile(
    r"(?i)"
    r"(?P<key>"
    r"(?<![A-Za-z0-9])[A-Za-z0-9_]*"
    r"(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|private[_-]?key|credential|authorization|code)"
    r"|[\"'](?:password|passwd|pwd|token|secret|api[_-]?key|code)[\"']"
    r")"
    r"(?P<sep>\s*[=:]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<val>[^\s,;&\"'}]+)"
)

# 递归脱敏的深度上限，防止畸形深层结构拖垮事件上报
_MAX_DEPTH = 6

# 初始化结果（供 /healthz 或启动日志查询，避免重复初始化）
_initialized = False
_enabled = False


def _is_sensitive(key: Any) -> bool:
    """判断字段名是否命中敏感词。"""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _scrub_text(text: str) -> str:
    """擦除自由文本中的凭据（可能误伤 status code: 200 之类，刻意取舍：漏擦一次就是凭据外泄）"""
    return _TEXT_CREDENTIAL_RE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{m.group('quote')}{_REDACTED}",
        text,
    )


def _scrub(value: Any, depth: int = 0) -> Any:
    """递归擦除 dict/list/str 中的敏感内容。"""
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive(k) else _scrub(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _scrub_frames(frames: Any) -> None:
    """就地脱敏堆栈帧的 vars（局部变量）与 context_line 等源码行（默认都不被 SDK 过滤）"""
    if not isinstance(frames, list):
        return
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        if "vars" in frame:
            frame["vars"] = _scrub(frame["vars"])
        for field in ("context_line", "pre_context", "post_context"):
            value = frame.get(field)
            if isinstance(value, str):
                frame[field] = _scrub_text(value)
            elif isinstance(value, list):
                frame[field] = [
                    _scrub_text(item) if isinstance(item, str) else item for item in value
                ]


def _scrub_stacktrace_containers(event: Dict[str, Any]) -> None:
    """遍历事件里所有承载堆栈的容器并脱敏其帧。"""
    containers = [event]
    for key in ("exception", "threads"):
        node = event.get(key)
        if isinstance(node, dict):
            containers.append(node)

    for container in containers:
        stacktrace = container.get("stacktrace")
        if isinstance(stacktrace, dict):
            _scrub_frames(stacktrace.get("frames"))
        for item in container.get("values") or []:
            if isinstance(item, dict) and isinstance(item.get("stacktrace"), dict):
                _scrub_frames(item["stacktrace"].get("frames"))


def _before_send(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """上报前钩子：擦除请求、日志与堆栈中的凭据类信息。"""
    request = event.get("request")
    if isinstance(request, dict):
        # query string 形如 "?email=x&password=y"，整体丢弃（价值低、风险高）
        request.pop("query_string", None)
        request.pop("cookies", None)
        if "data" in request:
            request["data"] = _scrub(request["data"])
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: (_REDACTED if _is_sensitive(k) else v) for k, v in headers.items()
            }

    if "extra" in event:
        event["extra"] = _scrub(event["extra"])

    # 消息正文（loguru logger.error 走的就是这条路径）
    if isinstance(event.get("message"), str):
        event["message"] = _scrub_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for field in ("message", "formatted"):
            if isinstance(logentry.get(field), str):
                logentry[field] = _scrub_text(logentry[field])

    # 异常描述（异常消息里常拼接口令）
    exception = event.get("exception")
    if isinstance(exception, dict):
        for item in exception.get("values") or []:
            if isinstance(item, dict) and isinstance(item.get("value"), str):
                item["value"] = _scrub_text(item["value"])

    # 堆栈帧的局部变量与源码行
    _scrub_stacktrace_containers(event)

    # 面包屑（出错前的日志轨迹，同样可能含凭据）
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        for item in breadcrumbs.get("values") or []:
            if isinstance(item, dict) and isinstance(item.get("message"), str):
                item["message"] = _scrub_text(item["message"])

    # 用户信息只保留 ID，去掉邮箱/IP 等可直接定位到自然人的字段
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user.get("id")} if user.get("id") else {}

    return event


def init_monitoring() -> bool:
    """初始化错误监控，返回是否真正启用（幂等，应在 create_app() 中调用一次）"""
    global _initialized, _enabled

    if _initialized:
        return _enabled

    _initialized = True

    dsn = (settings.SENTRY_DSN or "").strip()
    if not dsn:
        logger.info("未配置 SENTRY_DSN，错误监控未启用（线上异常不会自动上报）")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.loguru import LoguruIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError as e:
        logger.warning(f"SENTRY_DSN 已配置但 sentry-sdk 未安装，错误监控不可用: {e}")
        return False

    # 客户端主动断开（用户刷新/关页面）会抛 CancelledError，属正常现象，
    # 上报会淹没真实故障。KeyboardInterrupt/SystemExit 同理。
    from asyncio import CancelledError

    ignore_errors: List[type] = [CancelledError, KeyboardInterrupt, SystemExit]

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.SENTRY_ENVIRONMENT or settings.APP_ENV,
            release=settings.APP_RELEASE or None,
            # 隐私：不采集 IP、请求头中的用户信息等默认 PII
            send_default_pii=False,
            # 采集范围收口（源头不采集 + before_send 纵深防御）：
            # 明文口令、邮箱验证码、token 与 AI 对话 prompt 均不应外送
            data_collection={
                "user_info": False,  # 不回填 user.email / ip_address
                "cookies": {"mode": "off"},
                "http_headers": {
                    "request": {
                        "mode": "denylist",
                        "terms": [
                            "authorization",
                            "cookie",
                            "x-api-key",
                            "x-auth-token",
                            "x-real-ip",
                            "x-forwarded-for",
                        ],
                    }
                },
                "http_bodies": [],  # 请求体一律不采集（登录/注册的明文口令在此）
                "url_query_params": {"mode": "off"},
                "graphql": {"document": False, "variables": False},
                "gen_ai": {"inputs": False, "outputs": False},  # AI 对话 prompt 属用户内容
                "database_query_data": False,
                # 局部变量对排查有价值，password/token 等由 before_send 按名擦除
                "stack_frame_variables": True,
                "frame_context_lines": 0,  # 不附带源码行（源码可能写死测试凭据）
            },
            # 性能追踪默认关闭（0.0）；需要时用环境变量开启，避免自带采样开销
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            # 只上报 ERROR 及以上级别日志，WARNING 仅作面包屑
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
                SqlalchemyIntegration(),
                # level=INFO → 以上记为面包屑；event_level=ERROR → 以上生成事件；
                # sentry_logs_level=None 关闭 Sentry Logs 产品（INFO 日志量大，全量投递消耗配额）
                LoguruIntegration(
                    level=20,  # INFO
                    event_level=40,  # ERROR
                    sentry_logs_level=None,
                ),
            ],
            ignore_errors=ignore_errors,
            before_send=_before_send,
            # 附加进程内上下文（含环境名、日志路径等），便于多容器排查
            attach_stacktrace=True,
        )
    except Exception as e:
        # DSN 格式错误等不应阻塞启动
        logger.warning(f"Sentry 初始化失败，错误监控不可用: {e}")
        return False

    _enabled = True
    logger.info(
        f"错误监控已启用: env={settings.SENTRY_ENVIRONMENT or settings.APP_ENV}, "
        f"traces_sample_rate={settings.SENTRY_TRACES_SAMPLE_RATE}"
    )
    return True


def is_monitoring_enabled() -> bool:
    """错误监控当前是否启用（供健康检查展示）。"""
    return _enabled
