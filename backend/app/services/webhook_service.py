"""Webhook 出站订阅服务：内容事件 → HTTP 回调（2026-11-20，开放 API 第二块）。

可靠性取舍（与 webhook.py 模型口径一致）：投递是**尽力而为** —— 每次投递最多
3 次尝试、指数退避（0.5s/2s），最终失败只把状态写回端点行并累加 failure_streak，
不进队列、不补投。emit 全程 degrade_guard 吞异常，绝不影响发布主流程；
失败不可怕，**看得见**就行：last_status / last_error / failure_streak 落在端点行上。

安全：签名密钥只在创建响应里出现一次，库里只存 Fernet 加密版（core/crypto）；
每次投递对请求体做 HMAC-SHA256 签名（X-OpenCat-Signature），订阅方据密钥验签；
回调地址仅接受 https。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import uuid
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.degrade import degrade_guard
from app.core.exceptions import AppException
from app.models.user import User
from app.models.webhook import (
    WEBHOOK_DELIVERY_STATUSES,
    WEBHOOK_EVENTS,
    WEBHOOK_RETRY_DELAYS,
    WEBHOOK_TIMEOUT_SECONDS,
    WebhookEndpoint,
)
from app.database import async_session
from app.services.admin_audit_service import record_admin_action
from app.utils.time import utcnow

# 投递结果取值（与模型白名单一致）：最近一次的状态只可能是 success / failed
_RESULT_OK, _RESULT_FAIL = WEBHOOK_DELIVERY_STATUSES

_URL_MAX = 500
_NAME_MAX = 100
_ERROR_MAX = 255


def validate_events(events: list[str] | None) -> list[str]:
    """校验订阅事件白名单子集；未提供时默认订阅全部事件（不静默丢弃未知事件）"""
    checked = list(events or WEBHOOK_EVENTS)
    unknown = [e for e in checked if e not in WEBHOOK_EVENTS]
    if unknown:
        raise AppException(code=400, message=f"未知的事件：{', '.join(unknown)}")
    return list(dict.fromkeys(checked))


def _validate_url(url: str) -> str:
    """回调地址只接受 https（明文 http 会把签名密钥暴露给中间人）。"""
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.netloc:
        raise AppException(code=400, message="回调地址必须以 https:// 开头")
    return cleaned[:_URL_MAX]


async def create_webhook(
    db: AsyncSession, admin: User, name: str, url: str, events: list[str] | None
) -> dict:
    """新增订阅：签名密钥本次明文返回一次，库里只存 Fernet 加密版。"""
    checked = validate_events(events)
    plain_secret = secrets.token_hex(24)
    row = WebhookEndpoint(
        name=name.strip()[:_NAME_MAX] or "未命名订阅",
        url=_validate_url(url),
        secret_encrypted=encrypt_secret(plain_secret),
        events=checked,
        active=True,
    )
    db.add(row)
    await db.flush()
    record_admin_action(
        db,
        admin,
        "webhook_create",
        target_type="webhook",
        target_id=row.id,
        summary=f"新增 Webhook 订阅「{row.name}」",
        detail={"url": row.url, "events": checked},
    )
    await db.commit()
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "events": checked,
        "active": bool(row.active),
        "secret": plain_secret,
    }


async def list_webhooks(db: AsyncSession) -> list[dict]:
    """订阅清单（只有展示与运维字段，不含签名密钥；最多 200 条，创建时间倒序）。"""
    result = await db.execute(
        select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc()).limit(200)
    )
    rows = result.scalars().all()
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "url": row.url,
                "events": list(row.events or []),
                "active": bool(row.active),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "last_status": row.last_status,
                "last_event": row.last_event,
                "last_delivered_at": (
                    row.last_delivered_at.isoformat() if row.last_delivered_at else None
                ),
                "last_error": row.last_error,
                "failure_streak": int(row.failure_streak or 0),
            }
        )
    return items


async def set_active(db: AsyncSession, admin: User, webhook_id: int, active: bool) -> dict:
    """启用 / 停用（停用后不再投递，但保留配置）。"""
    row = await db.get(WebhookEndpoint, webhook_id)
    if row is None:
        raise AppException(code=404, message="Webhook 不存在")
    row.active = bool(active)
    record_admin_action(
        db,
        admin,
        "webhook_set_active",
        target_type="webhook",
        target_id=row.id,
        summary=f"{'启用' if active else '停用'} Webhook 订阅「{row.name}」",
        detail={"active": bool(active)},
    )
    await db.commit()
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "events": list(row.events or []),
        "active": bool(row.active),
    }


async def delete_webhook(db: AsyncSession, admin: User, webhook_id: int) -> bool:
    """删除订阅（配置数据直接删除，不进回收站）。"""
    row = await db.get(WebhookEndpoint, webhook_id)
    if row is None:
        raise AppException(code=404, message="Webhook 不存在")
    detail = {"url": row.url, "events": list(row.events or [])}
    await db.delete(row)
    record_admin_action(
        db,
        admin,
        "webhook_delete",
        target_type="webhook",
        target_id=webhook_id,
        summary=f"删除 Webhook 订阅「{row.name}」",
        detail=detail,
    )
    await db.commit()
    return True


async def test_delivery(db: AsyncSession, admin: User, webhook_id: int) -> dict:
    """手动试投一次（管理端「测试」按钮）：真实走 _deliver，能暴露 URL/签名/超时问题。

    与 emit 的区别：定向单端点、不受订阅事件集限制（没订阅也能试投）、写审计。
    投递结果照常回写 last_status/failure_streak —— 状态面板反映的是真实连通性。
    """
    row = await db.get(WebhookEndpoint, webhook_id)
    if row is None:
        raise AppException(code=404, message="Webhook 不存在")
    ok, error = await _deliver(row, WEBHOOK_EVENTS[0], {"source": "admin-test"})
    record_admin_action(
        db,
        admin,
        "webhook_test",
        target_type="webhook",
        target_id=row.id,
        summary=f"试投 Webhook 订阅「{row.name}」",
        detail={"ok": bool(ok), "error": (error or "")[:_ERROR_MAX]},
    )
    await db.commit()
    return {"id": row.id, "name": row.name, "ok": bool(ok), "error": (error or "")[:_ERROR_MAX]}


async def emit(event: str, payload: dict) -> None:
    """把一个内容事件推给所有订阅了它的启用端点（绝不抛异常、绝不影响发布主流程）。

    投递与状态回写用独立会话（与 login_log_service.record 同向）：发布事务已提交，
    webhook 的成败不该也不能反过来牵连业务事务。未注册的事件名直接跳过。
    """
    if event not in WEBHOOK_EVENTS:
        return
    with degrade_guard("webhook.emit", detail="Webhook 投递失败（不影响发布主流程）"):
        async with async_session() as session:
            result = await session.execute(
                select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))
            )
            rows = result.scalars().all()
            targets = [row for row in rows if event in list(row.events or [])]
            if not targets:
                return
            results = await asyncio.gather(
                *[_deliver(row, event, payload) for row in targets]
            )
            delivered_at = utcnow().replace(tzinfo=None)
            for row, (ok, error) in zip(targets, results):
                row.last_event = event
                row.last_delivered_at = delivered_at
                row.last_status = _RESULT_OK if ok else _RESULT_FAIL
                row.last_error = None if ok else (error or "")[:_ERROR_MAX]
                row.failure_streak = 0 if ok else int(row.failure_streak or 0) + 1
            await session.commit()


async def _deliver(row: WebhookEndpoint, event: str, payload: dict) -> tuple[bool, str]:
    """单个端点的尽力投递：3 次尝试、指数退避，返回 (是否成功, 失败原因)。

    签名 = HMAC-SHA256(签名密钥, 请求体)，订阅方拿创建时下发的密钥可完整验签；
    任何异常都收拢成 (False, 原因)，不让单个端点的网络故障外溢。
    """
    secret = decrypt_secret(row.secret_encrypted) or ""
    body = json.dumps(
        {"event": event, "payload": payload, "timestamp": utcnow().isoformat()},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-OpenCat-Event": event,
        "X-OpenCat-Delivery": uuid.uuid4().hex,
        "X-OpenCat-Signature": f"sha256={signature}",
    }
    error = ""
    for attempt, delay in enumerate((0.0,) + WEBHOOK_RETRY_DELAYS):
        if attempt:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
                response = await client.post(row.url, content=body, headers=headers)
            if 200 <= response.status_code < 300:
                return True, ""
            error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            # 网络层故障（超时 / 连接失败 / 传输中断）按预期失败收窄捕获；
            # 其余意外异常交由 emit 外层 degrade_guard 记 [降级-放行] 并跳过本次投递
            error = f"{type(exc).__name__}: {exc}"[:_ERROR_MAX]
    return False, error
