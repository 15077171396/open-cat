"""内容级浏览记录（归因用）。

与 `tutorials.view_count` 的分工：
- `view_count` 是**聚合计数**，回答「这篇有多热」，没有用户维度；
- 本表是**按天去重的用户级记录**，回答「看过它的人里，后来有多少申请了认证」。

只记登录用户（游客没有稳定标识，记了也无法归因）；按天去重控制体量。
保留期由 `scheduler/tasks.py::cleanup_content_views` 清理（180 天）。
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class ContentView(Base):
    """一次（按天去重的）内容浏览"""

    __tablename__ = "content_views"
    __table_args__ = (
        # 按天去重：同一人同一天看同一篇只记一条
        UniqueConstraint("user_id", "target_type", "target_id", "view_date", name="uq_content_view_daily"),
        Index("ix_content_views_target", "target_type", "target_id", "view_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, comment="浏览用户ID")
    target_type: Mapped[str] = mapped_column(String(20), comment="内容类型: tutorial/resource/node/plugin")
    target_id: Mapped[int] = mapped_column(BigInteger, comment="内容ID")
    view_date: Mapped[date] = mapped_column(Date, index=True, comment="浏览日期(按天去重)")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="首次浏览时间")
