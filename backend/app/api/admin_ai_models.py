"""管理后台 - AI 模型管理

路由级权限见 core/rbac.py：
- 列表/写操作走 ai_model:*；options 只暴露非密钥信息，任何 admin 可读
  （工具编辑页需要它，内容管理员不一定持有 ai_model:view）。

2026-10-27：全站成本护栏（日/月调用上限 + 兜底单价）从「系统设置 → AI 成本护栏」
并入本模块。它和单模型单价是同一件事的两半 —— 单价填 0 就是「回退兜底单价」，
兜底单价填 0 就是「不估算金额」；放在两个页面时，改任一侧都看不到另一侧。
"""

from typing import Any, Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._admin_shared import router
from app.core.exceptions import AppException, success_response
from app.core.permissions import require_admin
from app.database import get_db
from app.models.user import User
from app.schemas.admin import ActionAck, AffectedResult, CreatedResult, SettingsPayload
from app.schemas.ai_model import AiModelListResult, AiModelOptionsResult, AiModelProbeResult
from app.schemas.common import ApiResponse
from app.services import ai_cost_settings_service, ai_model_probe_service, ai_model_service
from app.services.admin_audit_service import record_admin_action
from app.services.ai_usage_service import invalidate_quota_config_cache


class CostSettingsPayload(SettingsPayload):
    """成本护栏载荷：通用设置载荷 + 当前生效的限额与实际用量"""

    quota: Optional[Any] = None


class AiModelCreateRequest(BaseModel):
    code: Optional[str] = Field(None, max_length=64, description="稳定标识；留空自动生成")
    display_name: str = Field(..., min_length=1, max_length=100)
    provider: str = Field(..., min_length=1, max_length=30)
    model_name: str = Field("", max_length=100)
    base_url: str = Field("", max_length=500)
    api_key: str = Field("", max_length=1000)
    modality: str = Field("text", pattern="^(text|image)$")
    image_price: float = Field(0, ge=0, le=1_000_000)
    input_price: float = Field(0, ge=0, le=1_000_000)
    output_price: float = Field(0, ge=0, le=1_000_000)
    is_enabled: bool = True
    is_default: bool = False
    sort_order: int = Field(0, ge=0, le=1_000_000)
    remark: str = Field("", max_length=500)


class AiModelUpdateRequest(BaseModel):
    # code 刻意不在更新字段里：工具/翻译按它引用，改标识等于悄悄切断调用
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    provider: Optional[str] = Field(None, min_length=1, max_length=30)
    model_name: Optional[str] = Field(None, max_length=100)
    base_url: Optional[str] = Field(None, max_length=500)
    # None / 掩码 = 不修改；空串 = 清除并回退 .env；其它 = 替换
    api_key: Optional[str] = Field(None, max_length=1000)
    # 模态创建后不可修改（接口保留字段只为让前端整表单回传不报 422）
    modality: Optional[str] = Field(None, pattern="^(text|image)$")
    image_price: Optional[float] = Field(None, ge=0, le=1_000_000)
    input_price: Optional[float] = Field(None, ge=0, le=1_000_000)
    output_price: Optional[float] = Field(None, ge=0, le=1_000_000)
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    sort_order: Optional[int] = Field(None, ge=0, le=1_000_000)
    remark: Optional[str] = Field(None, max_length=500)


@router.get("/ai-models/options", response_model=ApiResponse[AiModelOptionsResult])
async def admin_ai_model_options(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """工具编辑下拉：全部模型的非密钥信息（含禁用项，前端置灰）"""
    return success_response(await ai_model_service.list_model_options(db))


class CostSettingsUpdateRequest(BaseModel):
    # 批量更新：{key: value}；value 为 null/空串表示清除该项恢复默认（回退 .env）
    settings: dict[str, Optional[str]] = Field(..., min_length=1)


# ⚠️ 这两条必须声明在任何 `/ai-models/{model_id}` 之前：
# FastAPI 按声明顺序匹配，`{model_id}` 会把字面量 "cost-settings" 当成主键，
# 结果是 PUT 走错处理函数（报「模型不存在」而不是保存设置）。
@router.get("/ai-models/cost-settings", response_model=ApiResponse[CostSettingsPayload])
async def admin_get_ai_cost_settings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """AI 成本护栏：设置项定义 + 已配置值 + **当前生效的限额与实际用量**

    带上用量不是为了好看：只给「上限 1000 次/天」时，运营没法判断该不该调；
    给出「今天已用 830 次、还剩 170」才是一次可决策的阅读。
    """
    return success_response(await ai_cost_settings_service.overview(db))


@router.put("/ai-models/cost-settings", response_model=ApiResponse[AffectedResult])
async def admin_update_ai_cost_settings(
    req: CostSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """保存 AI 成本护栏（空值=恢复默认）。写通道按 surface 限定键范围。"""
    try:
        changed = await ai_cost_settings_service.update(db, req.settings)
    except ValueError as exc:
        raise AppException(code=400, message=str(exc))
    record_admin_action(
        db,
        admin,
        "settings_update",
        target_type="setting",
        summary=f"更新 AI 成本护栏 {changed} 项",
        detail={"keys": sorted(req.settings.keys()), "changed": changed},
    )
    # 先 commit 再失效缓存：否则并发读会把旧阈值回填进 Redis（60s TTL），
    # 表现为「保存成功，但另一个 worker 仍在用旧上限」
    await db.commit()
    # 限额/单价由 ai_usage_service 缓存（ai_quota:cfg），与看板读取同一份
    await invalidate_quota_config_cache()
    return success_response({"updated": changed}, "成本护栏已保存")


@router.get("/ai-models", response_model=ApiResponse[AiModelListResult])
async def admin_list_ai_models(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """模型列表（Key 只回显掩码 / 来源，绝不返回密文或明文）"""
    return success_response(await ai_model_service.list_models(db))


@router.post("/ai-models", response_model=ApiResponse[CreatedResult])
async def admin_create_ai_model(
    req: AiModelCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await ai_model_service.create_model(db, req.model_dump(), admin)
    return success_response(result, "模型已创建")


@router.put("/ai-models/{model_id}", response_model=ApiResponse[ActionAck])
async def admin_update_ai_model(
    model_id: int,
    req: AiModelUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await ai_model_service.update_model(
        db, model_id, req.model_dump(exclude_unset=True), admin
    )
    return success_response({"ok": True}, "模型已更新")


@router.delete("/ai-models/{model_id}", response_model=ApiResponse[ActionAck])
async def admin_delete_ai_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    await ai_model_service.delete_model(db, model_id, admin)
    return success_response({"ok": True}, "模型已删除")


@router.post("/ai-models/{model_id}/test", response_model=ApiResponse[AiModelProbeResult])
async def admin_probe_ai_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """测试连接：对该模型发一次极短的请求（约几十 token），返回延迟与回复片段。

    为什么值得一个接口：Key / Base URL / 上游模型名填错，管理员在后台看不出来 ——
    错的表现是「用户在工具页看到 AI 服务暂时不可用」。这里把它变成一次点击。

    ⚠️ 两点刻意设计：
    ① **不记用量、不占全站限额**：这是运维体检不是业务调用，记进去只会污染看板；
    ② 失败返回 200 + `ok=false`：连不上是这个接口的正常结论，原因是给页面显示用的，
       不该变成红色 toast。
    """
    result = await ai_model_probe_service.probe_model(db, model_id)
    # 记审计（改名/删模型都不会丢证据）：这类「谁在什么时候把某个模型打到多少钱」的问题
    # 事后只能靠这条记录回答
    record_admin_action(
        db,
        admin,
        "ai_model_test",
        target_type="ai_model",
        target_id=model_id,
        summary=f"测试 AI 模型「{result['display_name']}」（{'成功' if result['ok'] else '失败'}）",
        detail={
            "ok": result["ok"],
            "latency_ms": result["latency_ms"],
            "error": (result.get("error") or "")[:200],
        },
    )
    await db.commit()
    return success_response(result)
