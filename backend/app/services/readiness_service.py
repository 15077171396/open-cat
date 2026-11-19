"""部署体检：回答「这套系统还差什么配置才能真正用起来」。

和 `/admin/system-status` 的分工：
- system-status 看**依赖是否活着**（MySQL / Redis / 日志 / 备份 / 任务心跳）——运维视角；
- 本模块看**能力是否可用**（存储 / 邮件 / AI / 站点地址）——部署视角。

为什么单独做：这几项的共同特征是「不配也不报错，只是静默不生效」。
用户点「获取验证码」只会看到发送失败，上传图片只会看到「存储失败」，
积分流水永远空着——而界面上没有任何地方告诉他「去填 SMTP 授权码」。
开源版第一次部署时，这份清单就是唯一的向导。

判定口径尽量复用既有实现（`mail_settings_service.channel_status`、
`integration_settings_service`），避免"体检说好、实际报错"的分裂。
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.degrade import degrade_guard
from app.models.ai_model import AiModel
from app.services import mail_settings_service, setting_service
from app.services.storage import storage

# 状态语义
# ready        配好了，能用
# unconfigured 没配（required=True 时算「阻塞首发」）
# error        配了但不完整 / 不可用（比没配更危险，因为用户以为自己配好了）
# off          功能明确关闭（不是问题）
_STATUS_ORDER = {"error": 0, "unconfigured": 1, "ready": 2, "off": 3}

# 站点根地址仍指向本机时，邮件链接、sitemap、分享卡都会指向别人打不开的地址
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


def _item(
    *,
    key: str,
    label: str,
    status: str,
    required: bool,
    detail: str,
    action: str = "",
    link: str = "",
    extra: dict[str, Any] | None = None,
) -> dict:
    data = {
        "key": key,
        "label": label,
        "status": status,
        "required": required,
        "detail": detail,
        "action": action,
        "link": link,
    }
    if extra:
        data.update(extra)
    return data


def _check_storage() -> dict:
    """存储：local 需要目录可写（实测），cos / s3 需要凭据齐备。"""
    driver = storage.driver
    if driver == "local":
        root = Path(settings.STORAGE_LOCAL_DIR)
        try:
            root.mkdir(parents=True, exist_ok=True)
            # 真写一个文件再删：只判断 os.access 在容器里会误判
            # （UID 对、挂载只读时 access 仍返回 True）
            with tempfile.NamedTemporaryFile(dir=root, prefix=".probe-", delete=False) as fh:
                fh.write(b"probe")
                probe = fh.name
            os.unlink(probe)
        except OSError as exc:
            return _item(
                key="storage",
                label="文件存储",
                status="error",
                required=True,
                detail=f"local 驱动目录不可写：{root}（{exc.__class__.__name__}）",
                action="检查 uploads 卷是否只读，或把 STORAGE_LOCAL_DIR 指到可写目录",
                link="/settings",
                extra={"driver": driver},
            )
        return _item(
            key="storage",
            label="文件存储",
            status="ready",
            required=True,
            detail=f"local 驱动，写入 {root}（卷已挂载且可写）",
            action="想用 CDN 时把 STORAGE_DRIVER 改成 cos 或 s3 并填对应凭据",
            link="/settings",
            extra={"driver": driver},
        )

    # cos / s3：凭据清单不同，收口同一份缺项提示
    if driver == "s3":
        credential_items = (
            ("STORAGE_S3_ACCESS_KEY", settings.STORAGE_S3_ACCESS_KEY),
            ("STORAGE_S3_SECRET_KEY", settings.STORAGE_S3_SECRET_KEY),
            ("STORAGE_S3_BUCKET", settings.STORAGE_S3_BUCKET),
        )
        bucket_label = settings.STORAGE_S3_BUCKET
    else:
        credential_items = (
            ("COS_SECRET_ID", settings.COS_SECRET_ID),
            ("COS_SECRET_KEY", settings.COS_SECRET_KEY),
            ("COS_BUCKET", settings.COS_BUCKET),
        )
        bucket_label = settings.COS_BUCKET

    missing = [
        name for name, value in credential_items if not (value or "").strip()
    ]
    if missing:
        return _item(
            key="storage",
            label="文件存储",
            status="error",
            required=True,
            detail=f"{driver} 驱动缺 {len(missing)} 项：{'、'.join(missing)}",
            action=f"补齐 {driver} 凭据，或把 STORAGE_DRIVER 改回 local",
            link="/settings",
            extra={"driver": driver, "missing": missing},
        )
    return _item(
        key="storage",
        label="文件存储",
        status="ready",
        required=True,
        detail=f"{driver} 驱动，bucket={bucket_label}（未做网络连通测试）",
        action="上传一张图验证：正文配图能显示即通",
        link="/settings",
        extra={"driver": driver},
    )


def _check_site_base_url() -> dict:
    """站点根地址：还是 localhost 时，邮件链接与 sitemap 都会指向外部打不开的地址。"""
    raw = (settings.SITE_BASE_URL or "").strip()
    if not raw:
        return _item(
            key="site_base_url",
            label="站点地址",
            status="unconfigured",
            required=True,
            detail="SITE_BASE_URL 为空，邮件链接与 sitemap 无法生成绝对地址",
            action="在 .env 里填 SITE_BASE_URL=https://你的域名",
        )

    host = raw.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].strip().lower()
    if host in _LOCAL_HOSTS:
        return _item(
            key="site_base_url",
            label="站点地址",
            status="unconfigured",
            required=True,
            detail=f"当前是 {raw}（本机地址），发出去的邮件链接在别人电脑上打不开",
            action="上线前改成真实域名：SITE_BASE_URL=https://你的域名",
        )

    if raw.startswith("http://"):
        return _item(
            key="site_base_url",
            label="站点地址",
            status="ready",
            required=True,
            detail=f"{raw}（明文 HTTP；Cookie 的 Secure 标记会被跳过）",
            action="配好 HTTPS 后改成 https://",
        )

    return _item(
        key="site_base_url",
        label="站点地址",
        status="ready",
        required=True,
        detail=raw,
        action="",
    )


def _check_mail() -> dict:
    """邮件：直接复用邮件设置页的通道判定，避免两处口径不一致。"""
    channel = mail_settings_service.channel_status()
    if channel["ready"]:
        tls = channel.get("tls")
        note = "" if tls and tls != "none" else "（未启用加密，多数邮箱商会拒收）"
        return _item(
            key="mail",
            label="邮件 SMTP",
            status="ready",
            required=True,
            detail=f"{channel.get('endpoint')} · 发件人 {channel.get('sender') or '未设置'}{note}",
            action="在「邮件通知」页可发送测试邮件",
            link="/mail-settings",
        )
    return _item(
        key="mail",
        label="邮件 SMTP",
        status="unconfigured" if channel.get("missing") else "error",
        required=True,
        detail="缺：" + "、".join(channel.get("missing") or []) if channel.get("missing") else "配置不完整",
        action="未配齐时生产环境发不出验证码，注册与找回密码都走不通",
        link="/mail-settings",
        extra={"missing": channel.get("missing") or []},
    )


async def _check_ai(db: AsyncSession) -> dict:
    """AI：有启用且有 Key 的模型才算可用；启用但没 Key 是 error（调用必失败）。"""
    rows = (await db.execute(select(AiModel.is_enabled, AiModel.api_key_enc))).all()
    if not rows:
        return _item(
            key="ai",
            label="AI 能力",
            status="off",
            required=False,
            detail="还没有配置任何模型；AI 写作与 AI 工具会提示未配置",
            action="在「模型管理」里加一个 Provider（OpenAI 兼容协议均可）",
            link="/ai-models",
        )

    enabled = [r for r in rows if r[0]]
    with_key = [r for r in enabled if (r[1] or "").strip()]
    if with_key:
        # 有可用的，也有启用却没 Key 的 —— 这才是真「配了一半」，最容易在调用时才炸
        if len(with_key) < len(enabled):
            return _item(
                key="ai",
                label="AI 能力",
                status="error",
                required=False,
                detail=(
                    f"{len(enabled) - len(with_key)} 个已启用的模型缺 API Key"
                    f"（可用 {len(with_key)} 个，共 {len(enabled)} 个启用）"
                ),
                action="给它们补 Key，或先停用 —— 选中缺 Key 的模型会直接失败",
                link="/ai-models",
            )
        return _item(
            key="ai",
            label="AI 能力",
            status="ready",
            required=False,
            detail=f"{len(with_key)} 个模型可用（共 {len(rows)} 个，启用 {len(enabled)} 个）",
            action="",
            link="/ai-models",
        )
    if enabled:
        # 一个 Key 都没配：这是「还没配」，不是「配错了」。
        # 迁移会预置几个占位模型（默认启用），若判成 error，开源用户第一次登录
        # 就看到一片红 —— 而事实是他从没打算用 AI。
        return _item(
            key="ai",
            label="AI 能力",
            status="unconfigured",
            required=False,
            detail=f"{len(enabled)} 个模型已启用但都还没填 API Key（AI 写作与 AI 工具会提示未配置）",
            action="在「模型管理」里给要用的模型填 Key；不用的先停用",
            link="/ai-models",
        )
    return _item(
        key="ai",
        label="AI 能力",
        status="off",
        required=False,
        detail=f"{len(rows)} 个模型全部停用",
        action="需要 AI 写作时在「模型管理」里启用一个",
        link="/ai-models",
    )


def _summarize(items: list[dict]) -> dict:
    blocking = [i for i in items if i["required"] and i["status"] != "ready"]
    return {
        "total": len(items),
        "ready": sum(1 for i in items if i["status"] == "ready"),
        "off": sum(1 for i in items if i["status"] == "off"),
        "error": sum(1 for i in items if i["status"] == "error"),
        # 只统计「必填项尚未就绪」，可选功能没配不算阻塞
        "blocking": len(blocking),
        "blocking_keys": [i["key"] for i in blocking],
        "deployable": not blocking,
    }


async def collect(db: AsyncSession) -> dict:
    """体检清单。**只读**，数据库故障时 AI 一项会降级为「未知」而不是整体报错。"""
    items = [_check_storage(), _check_site_base_url(), _check_mail()]

    # 这三项要读库读缓存；单项失败不该让整页打不开，各自兜成 error 项。
    # 用 degrade_guard 而不是裸 except：捕获点收口在 core/degrade.py 一处，
    # 才不会因为「每加一个观测模块就推高一次棘轮基线」而稀释降级纪律。
    for key, label, runner in (
        ("ai", "AI 能力", _check_ai),
    ):
        item: dict | None = None
        with degrade_guard(f"readiness.{key}", detail="单项检查失败只标该项，其余照常给结论"):
            item = await runner(db)
        if item is None:
            item = _item(
                key=key,
                label=label,
                status="error",
                required=False,
                detail="检查失败（详情见后端日志，可搜 [降级-放行]）",
                action="点「重新检查」重试；持续失败时确认数据库与缓存是否正常",
            )
        items.append(item)

    items.sort(key=lambda i: _STATUS_ORDER[i["status"]])
    return {
        "ts": int(time.time()),
        "items": items,
        "summary": _summarize(items),
        "storage_driver": storage.driver,
        "app_env": settings.APP_ENV,
    }


async def stored_settings_hint(db: AsyncSession) -> dict:
    """给前端的一句话提示：哪些键还没保存过（只报键名，不报值）。"""
    values = await setting_service.get_stored_settings(db, mask_secrets=True)
    return {"configured_keys": len(values)}
