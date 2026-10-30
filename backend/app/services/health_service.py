"""深度健康探针：/health 浅检查仅证明进程存活（容器编排与 nginx 用）；
/health/deep 实际探测 MySQL + Redis（含会话时区与 alembic 版本两项附加校验），供 uptime 监控告警。
alembic head 由 alembic.ini 推导（不硬编码目录层级），读不到时打 WARNING 标记「未知」。"""

import json
import time
from pathlib import Path

from loguru import logger

from app.config import settings
from app.core.degrade import degrade_guard
from app.redis import log_redis_degraded, redis_client

_HEALTH_DEEP_CACHE_KEY = "health:deep"

# alembic 的唯一权威入口（相对它推导 script_location，而不是猜目录层级）
_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


async def _read_session_tz_offset(session) -> int | None:
    """读取 MySQL 会话时区相对 UTC 的偏移（秒）；读不到返回 None（附加校验不能把连通性探测判成失败）"""
    from sqlalchemy import text

    try:
        value = (
            await session.execute(
                text("SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())")
            )
        ).scalar()
        return int(value)
    except Exception as exc:  # noqa: BLE001 附加校验不可用时结论变「未知」，但必须留痕
        # 与本文件 `_code_head_revision` 同一口径（第四轮 P1-49② 补的就是它，这两条漏了）：
        # 静默 return None 会让「会话时区是否为 UTC」这项判定整体失效而无人察觉 ——
        # 而它正是本轮在动的 naive-UTC 约定唯一的运行时护栏。
        logger.warning(f"health.deep: 读取 MySQL 会话时区偏移失败，该项判定跳过（{type(exc).__name__}）")
        return None


def _load_script_directory():
    """经 alembic.ini 解析迁移脚本目录；不可用时返回 None（调用方降级为「未知」）。"""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        if not _INI_PATH.is_file():
            logger.warning(f"未找到 {_INI_PATH}，schema 漂移检查退化为「未知」")
            return None
        cfg = Config(str(_INI_PATH))
        # script_location 允许相对 ini 所在目录（容器与本地布局一致时也可直接解析），
        # 这里显式归一化为绝对路径，避免依赖进程 cwd
        script_location = cfg.get_main_option("script_location") or ""
        if script_location and not Path(script_location).is_absolute():
            resolved = (_INI_PATH.parent / script_location).resolve()
            if resolved.is_dir():
                cfg.set_main_option("script_location", str(resolved))
        return ScriptDirectory.from_config(cfg)
    except Exception as exc:  # noqa: BLE001 附加校验不得把连通性探测打成故障
        logger.warning(f"读取 alembic 脚本目录失败，schema 漂移检查退化为「未知」: {exc}")
        return None


def _code_head_revision() -> str | None:
    """代码侧的迁移 head（经 alembic.ini 解析脚本目录）；读不到打 WARNING 并返回 None。

    为什么单独封装并吞掉异常：本函数是**附加**校验，脚本目录缺失 / alembic
    不可用只应让结论变成「未知」，不能把一次正常的连通性探测判成失败。
    但「未知」必须**留痕**（WARNING）—— 否则路径一变，检查静默失效而无人察觉。
    """
    script = _load_script_directory()
    if script is None:
        return None
    head: str | None = None
    with degrade_guard(
        "health.code_head", detail="读取 alembic head 失败，schema 漂移检查退化为「未知」"
    ):
        head = script.get_current_head()
    return head


_CODE_HEAD_CACHE: dict[str, str] = {}


def _code_head_cached() -> str | None:
    """`_code_head_revision()` 的进程内记忆（第四轮全量审查 P1-64）。

    它要在事件循环上**同步**构建 alembic `Config` + `ScriptDirectory` 并遍历解析
    `alembic/versions/` 全部迁移文件（本机热测 13.0ms/次），而 head 取值只在
    重新部署时才会变 —— 每次探活重算纯属浪费，还会给并发请求加抖动。
    只缓存**成功**结果：读不到（alembic 缺失等）保持「未知」语义并允许下次重试。
    """
    if "head" not in _CODE_HEAD_CACHE:
        head = _code_head_revision()
        if head is not None:
            _CODE_HEAD_CACHE["head"] = head
        return head
    return _CODE_HEAD_CACHE["head"]


def code_head_cached() -> str | None:
    """公开入口：供「版本与升级」接口复用同一份进程内 head 缓存。"""
    return _code_head_cached()


def script_directory():
    """公开入口：迁移链遍历（版本接口用它数「待执行迁移」）。"""
    return _load_script_directory()


async def read_db_revision(session) -> str | None:
    """公开入口：读库里的 alembic 版本号；读不到返回 None。"""
    return await _read_db_revision(session)


async def _read_db_revision(session) -> str | None:
    """读库里的 alembic 版本号；读不到（表不存在 / 权限不足）返回 None。"""
    from sqlalchemy import text

    try:
        row = (
            await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        ).first()
    except Exception as exc:  # noqa: BLE001 同上：结论「未知」可以，无声跳过不行
        # 「库版本 == 代码 head」是 P2-14 立的唯一能发现「库落后于代码」的判定，
        # 而迁移出问题恰恰是它最该响的时候 —— 静默 None 等于在最关键的场景里撤掉护栏
        logger.warning(f"health.deep: 读取 alembic_version 失败，库版本校验跳过（{type(exc).__name__}）")
        return None
    return row[0] if row else None


async def probe_mysql() -> dict:
    """探测 MySQL 连通性（执行 SELECT 1）+ 会话时区是否为 UTC + 库版本是否为代码 head。

    只回传异常类名，不回传异常消息：驱动异常消息常包含
    主机/端口/账号等连接信息，不应出现在对外可访问的响应里。

    ⚠️ 为什么要校验会话时区（台账 P2-7 / 代码审查 D5）：
    全库 naive 时间戳「按 UTC 写入」是本项目的**代码级约定**——统计日界
    （stats_service）、VIP 到期（is_vip_effective）、免费时长倒计时
    （timer_service）全部直接拿 naive 值当 UTC 比较。而 `created_at` /
    `updated_at` 这类列由数据库 `func.now()` 写入，其语义完全取决于会话时区：
    一旦它不是 UTC，DB 写本地时间、应用写 UTC，两者混用会**静默**产生小时级的
    时间差（可能白送几小时权益，也可能瞬间判为耗尽）。这种「不报错但算错」的
    配置错误必须让它可见，因此判定为探测失败（``MySQLSessionNotUTC``），
    由 uptime 监控告警。注意：它不会导致容器重启（编排健康检查用的是 ``/``）。

    ⚠️ 为什么要校验**库版本 == 代码 head**（``SchemaOutOfDate``）：
    「库落后于代码」同样是一种**只在部分接口上暴露**的静默失败 —— 应用能正常启动、
    ``/health`` 也是 ok，但凡是 select 到新列（或查询新表）的接口都会 500
    （``Unknown column 'xxx' in 'field list'``），表现为「某几个页面加载失败」而
    不是「服务挂了」，排查时极易被当成业务 bug。生产靠 compose 的 ``migrate``
    服务先跑 ``alembic upgrade head``；本机开发则完全可能忘了跑。放在这里断言，
    一次请求就能指出「库版本 ≠ 代码版本」。
    """
    from sqlalchemy import text

    from app.database import async_session

    started = time.perf_counter()
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
            tz_offset = await _read_session_tz_offset(session)
            db_revision = await _read_db_revision(session)
        result: dict = {"ok": True, "ms": round((time.perf_counter() - started) * 1000, 1)}

        # 收集所有「附加校验」的问题，最后统一决定 ok；error 取第一个（保持既有契约）
        problems: list[str] = []

        if tz_offset is None:
            # 读不到时区：连通性已证明，时区结论标记为「未知」，不据此判失败
            result["utc"] = None
        else:
            result["utc"] = tz_offset == 0
            result["tz_offset_seconds"] = tz_offset
            if tz_offset != 0:
                problems.append("MySQLSessionNotUTC")

        head = _code_head_cached()
        if db_revision is not None and head is not None:
            result["schema_revision"] = db_revision
            result["schema_head"] = head
            if db_revision != head:
                problems.append("SchemaOutOfDate")
        else:
            # 任一侧读不到 → 结论标记为「未知」，不据此判失败（同 utc 的处理）
            result["schema_revision"] = db_revision
            result["schema_head"] = head

        if problems:
            result["ok"] = False
            result["error"] = problems[0]
        return result
    except Exception as e:
        return {
            "ok": False,
            "error": type(e).__name__,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }


async def probe_redis() -> dict:
    """探测 Redis 连通性（PING）。"""
    started = time.perf_counter()
    try:
        await redis_client.ping()
        return {"ok": True, "ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as e:
        return {
            "ok": False,
            "error": type(e).__name__,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }


async def get_deep_health() -> tuple[dict, bool]:
    """深度健康检查的结果与判定（返回 ``(payload, ok)``）。

    - 依赖全部正常 → ``ok=True``（HTTP 200）
    - 任一依赖异常 → ``ok=False``（HTTP 503，uptime 监控据此告警）

    结果按 ``HEALTH_DEEP_CACHE_SECONDS`` 缓存，吸收探活的高频请求；
    缓存读写失败（Redis 不可用）时退化为每次直连探测，判定结果不受影响。
    """
    cache_ttl = max(0, settings.HEALTH_DEEP_CACHE_SECONDS)

    # 1) 尝试读缓存（Redis 不可用则直接跳过，不影响探测）
    if cache_ttl:
        try:
            cached = await redis_client.get(_HEALTH_DEEP_CACHE_KEY)
            if cached:
                payload = json.loads(cached)
                return payload, payload.get("status") == "ok"
        except Exception as exc:  # noqa: BLE001 降级不阻断主流程
            log_redis_degraded("health_service.get_deep_health", exc)

    # 2) 实际探测
    mysql = await probe_mysql()
    redis = await probe_redis()
    checks = {"mysql": mysql, "redis": redis}
    ok = all(item["ok"] for item in checks.values())
    payload = {"status": "ok" if ok else "degraded", "checks": checks, "ts": int(time.time())}

    # 3) 写缓存（失败无妨，下次继续直连探测）
    if cache_ttl:
        try:
            await redis_client.set(_HEALTH_DEEP_CACHE_KEY, json.dumps(payload), ex=cache_ttl)
        except Exception as exc:  # noqa: BLE001 降级不阻断主流程
            log_redis_degraded("health_service.get_deep_health", exc)

    return payload, ok