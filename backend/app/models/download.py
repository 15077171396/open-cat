"""下载记录模型 + 工具使用日志"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class Download(Base):
    """下载记录 (用户获取网盘链接的行为记录)"""
    __tablename__ = "downloads"
    # 下载历史高频按 user_id 排序 created_at 倒序分页，复合索引避免 filesort
    __table_args__ = (
        Index("ix_downloads_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    resource_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="资源ID")
    resource_name: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="资源名称快照")
    file_size: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="资源大小")
    file_type: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="文件类型: pdf/archive/exe/other")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True, comment="下载时间")


class ToolUsageLog(Base):
    """工具使用日志 (计时+统计)"""
    __tablename__ = "tool_usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="用户ID")
    tool_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="工具ID")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=10, comment="本次使用时长")
    # 索引：stats_service 统计「今日工具调用数」按 created_at 过滤；
    # 本表随每次 AI 工具运行**单调增长**，无索引即全表扫描（见迁移 add_list_sort_indexes）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
