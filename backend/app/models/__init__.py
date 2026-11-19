"""Models 包——全部 ORM 模型的唯一注册入口：清单只此一份，alembic/env.py 只 import app.models。
（手写 import 清单曾两次因漏登记导致 autogenerate 生成 DROP TABLE。）
约定：新增模型必须在本文件导入并加入 __all__，否则 Alembic 会把它判定为多余的表。"""

from app.models.admin_audit_log import AdminAuditLog
from app.models.ai_model import AiModel
from app.models.ai_writing_prompt import AiWritingPrompt
from app.models.announcement import Announcement, NotificationSetting
from app.models.api_token import PersonalAccessToken
from app.models.category import Category, CategoryGroup
from app.models.chat import ChatConversation, ChatMember, ChatMessage
from app.models.comment import ContentComment
from app.models.dictionary import DictItem, DictType
from app.models.favorite import Favorite
from app.models.feedback import Feedback
from app.models.feedback_reply import FeedbackReply
from app.models.image_asset import ImageAsset
from app.models.learning import LearningRecord
from app.models.login_log import LoginLog
from app.models.message import Message
from app.models.moderation import ContentModerationLog, ContentReport
from app.models.ops_metric_snapshot import OpsMetricSnapshot
from app.models.position import Position
from app.models.rbac import Department, Permission, Role, RolePermission, UserRole
from app.models.scheduled_job_config import ScheduledJobConfig
from app.models.search_log import SearchLog
from app.models.system_setting import SystemSetting
from app.models.tutorial import Tutorial
from app.models.tutorial_node import TutorialNode
from app.models.user import User
from app.models.user_ui_preference import UserUiPreference
from app.models.webhook import WebhookEndpoint

__all__ = [
    "AdminAuditLog",
    "AiModel",
    "AiWritingPrompt",
    "Announcement",
    "NotificationSetting",
    "PersonalAccessToken",
    "WebhookEndpoint",
    "Category",
    "ChatConversation",
    "ChatMember",
    "ChatMessage",
    "CategoryGroup",
    "ContentComment",
    "DictItem",
    "DictType",
    "Favorite",
    "Feedback",
    "FeedbackReply",
    "ImageAsset",
    "LearningRecord",
    "LoginLog",
    "Message",
    "OpsMetricSnapshot",
    "ContentModerationLog",
    "ContentReport",
    "Position",
    "Department",
    "Permission",
    "Role",
    "RolePermission",
    "UserRole",
    "ScheduledJobConfig",
    "SearchLog",
    "SystemSetting",
    "Tutorial",
    "TutorialNode",
    "User",
    "UserUiPreference",
]
