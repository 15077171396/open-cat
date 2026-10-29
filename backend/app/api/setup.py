"""首次部署的安装向导接口（薄 IO 层，业务在 `services/setup_service.py`）。

为什么需要它
------------
开源项目最常见的流失点是「clone 下来跑不起来」。原流程要求先 `seed_data.py`
再手动抄日志里的随机口令，用户看不到也记不住。这里把「创建第一个管理员」
变成一次网页操作。

⚠️ 该接口挂在 `/api/v1/setup`，**不在** `/admin` 前缀下：
`_admin_shared.router` 的 `check_route_permission` 是 fail-closed 的，
挂进去会导致「库里还没有管理员时无法初始化」的死锁。

三条安全约束（一次性 / 口令哈希 / 角色降级）见 `services/setup_service.py`。
"""

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import success_response
from app.database import get_db
from app.schemas.admin import ActionAck
from app.schemas.common import ApiResponse
from app.services import setup_service

router = APIRouter()


class SetupStatusData(BaseModel):
    """安装向导需要知道的全部状态。"""

    #: 是否需要初始化（没有管理员账号）
    needs_setup: bool
    #: 站点根地址，前端用来提示「装完从这里进」
    site_base_url: str
    #: 当前存储驱动，用于提示「上传会落到哪」（local 无需云账号）
    storage_driver: str


class SetupInitRequest(BaseModel):
    account: str = Field(..., min_length=3, max_length=100, description="管理员账号（邮箱或用户名）")
    password: str = Field(
        ...,
        min_length=setup_service.MIN_PASSWORD_LEN,
        max_length=setup_service.MAX_PASSWORD_LEN,
        description="登录口令",
    )
    nickname: str | None = Field(None, max_length=50, description="昵称（可选）")


@router.get("/status", response_model=ApiResponse[SetupStatusData])
async def setup_status(db: AsyncSession = Depends(get_db)):
    """是否需要初始化。登录页与 /setup 页都会先问它。"""
    return success_response(
        SetupStatusData(
            needs_setup=await setup_service.needs_setup(db),
            site_base_url=settings.SITE_BASE_URL,
            storage_driver=(settings.STORAGE_DRIVER or "local").strip().lower(),
        )
    )


@router.post("/init", response_model=ApiResponse[ActionAck])
async def setup_init(
    req: SetupInitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """创建第一个管理员并绑定 super_admin 角色（仅当系统里还没有管理员）。"""
    result = await setup_service.create_first_admin(
        db,
        account=req.account,
        password=req.password,
        nickname=req.nickname,
    )

    # 来源 IP 只在这一层记：service 不该知道 HTTP 上下文
    client = request.client.host if request.client else "unknown"
    logger.info(f"安装向导：首个管理员来源 ip={client}")

    message = "初始化完成，请登录"
    if not result.super_admin_bound:
        message += "（RBAC 角色表尚未就绪，请在后台「角色管理」手动分配角色）"
    return success_response(ActionAck(ok=True), message)
