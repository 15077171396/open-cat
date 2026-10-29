"""内容评论模型：多态目标 (target_type, target_id) 指向被评论内容，新增内容类型无需加列。

设计要点：
- 只做两级（顶层 + 回复），回复的回复挂到同一顶层父评论下（三级以上移动端无可用展示形态）；
- 父评论被删连带软删其回复，从根上避免孤儿数据（管理员可 restore_comment 恢复）；
- 图片存 URL 列表（JSON），只接受本站 COS/CDN 域名地址（防图床/追踪像素）；
- 软删除（ICP 备案要求删除的 UGC 可追溯），NULL = 正常；
- ⚠️ 内容是硬删除，评论须在内容删除时一并清理（见 comment_service.purge_comments_for_targets）。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 评论正文长度上限（API 校验 + 列宽 + 前端计数三处共用同一个常量，避免漂移）
COMMENT_MAX_LENGTH = 500

# 单条评论最多附几张图（前端限制张数 + 后端校验同源，避免「前端让传、后端拒绝」）
COMMENT_MAX_IMAGES = 3

# 单用户每日配图上传张数上限：上传不产生业务行（可传完不发评论），须独立 Redis 计数；
# 30 条评论 × 3 张为理论峰值，60 张覆盖截图提问的真实用法，同时压住图床式刷量
MAX_COMMENT_IMAGES_PER_USER_PER_DAY = 60

# 单条顶层评论单次返回的回复条数上限。
# 超出的回复不返回（reply_count 仍是真实总数）——防止一条被刷的评论
# 把整个列表接口的响应体撑大。
MAX_REPLIES_PER_COMMENT = 50

# 单用户每日评论上限（防灌水）。评论是公开可见的 UGC，没有上限时
# 一个脚本就能把评论区刷满，且比举报更难事后清理。
MAX_COMMENTS_PER_USER_PER_DAY = 30

# 可评论的内容类型 —— 内容能力的**总登记处**在 `app.core.content_types`
# （EXT-02 之后，评论/收藏/分类/可用性/通知/清理全部由它派生），本常量是其中
# `commentable=True` 的那一组的镜像：
#   ① 本常量；② `core.content_types` 的 `commentable` 标记；
#   ③ 用户端 `CommentSection.tsx` 的 `CommentTargetType`；④ 管理端 `Comments/List.tsx`
#   的 `TARGET_TYPE_META`（跨语言两处只能靠测试比对）。
# ①② 由 `comment_service` 的**导入期断言**锁死（不一致直接启动失败），③④ 由
# `tests/test_comment_target_registry.py` 逐端比对。取值同时被写进 ``content_comments.target_type``。
# ⚠️ 只是**新增一个取值**时不需要数据迁移（列是 VARCHAR，没有 CHECK/ENUM 约束）；
#    只有改名或删除已有取值才需要配套迁移。
COMMENT_TARGET_TUTORIAL = "tutorial"
COMMENT_TARGET_TYPES = (COMMENT_TARGET_TUTORIAL,)

# target_type 列宽：够放未来的类型名，又不至于浪费
COMMENT_TARGET_TYPE_MAX_LENGTH = 20


class ContentComment(Base):
    """内容评论（当前只有教程，新增内容类型时按下方说明同步登记）

    多态目标：``(target_type, target_id)`` 指向被评论的内容。刻意**不加外键**——
    本项目的内容都是硬删除，外键级联会在删除时触发 1451，且不同内容类型分散在多张表，
    单列外键也表达不了。改为在删除流程里同事务清理
    （见 ``comment_service.purge_comments_for_targets``）。

    索引设计：列表查询固定是「某内容下、未删除、按时间倒序」，
    因此建 (target_type, target_id, deleted_at, created_at) 复合索引直接覆盖该查询；
    user_id 单独索引服务于「我发的评论」与每日上限计数。
    """

    __tablename__ = "content_comments"
    __table_args__ = (
        Index("ix_cc_target_deleted_created", "target_type", "target_id", "deleted_at", "created_at"),
        Index("ix_cc_user_created", "user_id", "created_at"),
        Index("ix_cc_deleted_at", "deleted_at"),
        # 列表查询会「按一批顶层评论 id 取回复」，parent_id 必须有索引
        Index("ix_cc_parent", "parent_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id
    )
    # 目标类型：取值见 COMMENT_TARGET_TYPES（'tutorial' / 'node' / …）
    target_type: Mapped[str] = mapped_column(
        String(COMMENT_TARGET_TYPE_MAX_LENGTH), comment="目标类型(tutorial/node/…)"
    )
    target_id: Mapped[int] = mapped_column(BigInteger, comment="目标内容ID")
    user_id: Mapped[int] = mapped_column(BigInteger, comment="评论作者ID")
    # 两级回复：NULL = 顶层评论；非 NULL = 挂在该顶层评论下（不会指向另一条回复）
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None, comment="父评论ID(NULL=顶层, 仅两级)"
    )
    content: Mapped[str] = mapped_column(
        String(COMMENT_MAX_LENGTH), comment=f"评论正文(纯文本, 最多{COMMENT_MAX_LENGTH}字)"
    )
    # 配图 URL 列表（JSON）。只允许本站 COS/CDN 域名下的地址，见 comment_service 校验
    images: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None, comment=f"配图URL列表(最多{COMMENT_MAX_IMAGES}张)"
    )
    # 软删除：NULL = 正常。管理员可恢复，故不用布尔位（无法表达删除时间）
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None, comment="软删除时间(NULL=正常)"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="发布时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="最后更新时间"
    )
