"""Models 包——全部 ORM 模型的唯一注册入口：清单只此一份，alembic/env.py 只 import app.models。
（手写 import 清单曾两次因漏登记导致 autogenerate 生成 DROP TABLE。）
约定：新增模型必须在本文件导入并加入 __all__，否则 Alembic 会把它判定为多余的表。"""

from app.models.admin_audit_log import AdminAuditLog
from app.models.ai_model import AiModel
from app.models.ai_writing_prompt import AiWritingPrompt
from app.models.announcement import Announcement, NotificationSetting
from app.models.auth_request import AuthRequest
from app.models.category import Category
from app.models.comment import ContentComment
from app.models.content_view import ContentView
from app.models.download import Download
from app.models.favorite import Favorite
from app.models.feedback import Feedback
from app.models.image_asset import ImageAsset
from app.models.invite_relation import InviteRelation
from app.models.learning import LearningRecord
from app.models.message import Message
from app.models.moderation import ContentModerationLog, ContentReport
from app.models.payment import PaymentOrder, PaymentProduct
from app.models.point_transaction import PointTransaction
from app.models.rbac import Department, Permission, Role, RolePermission, UserRole
from app.models.redemption_code import RedemptionCode, RedemptionRecord
from app.models.scheduled_job_config import ScheduledJobConfig
from app.models.search_log import SearchLog
from app.models.system_setting import SystemSetting
from app.models.tutorial import Tutorial, TutorialNetdiskLink, TutorialPurchase
from app.models.tutorial_node import TutorialNode
from app.models.user import User

__all__ = [
    "AdminAuditLog",
    "AiModel",
    "AiWritingPrompt",
    "Announcement",
    "NotificationSetting",
    "AuthRequest",
    "Category",
    "ContentComment",
    "ContentView",
    "Download",
    "Favorite",
    "Feedback",
    "ImageAsset",
    "InviteRelation",
    "LearningRecord",
    "Message",
    "ContentModerationLog",
    "ContentReport",
    "PaymentOrder",
    "PaymentProduct",
    "PointTransaction",
    "Department",
    "Permission",
    "Role",
    "RolePermission",
    "UserRole",
    "RedemptionCode",
    "RedemptionRecord",
    "ScheduledJobConfig",
    "SearchLog",
    "SystemSetting",
    "Tutorial",
    "TutorialNetdiskLink",
    "TutorialPurchase",
    "TutorialNode",
    "User",
]
