"""工具模型"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, DateTime, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON

from app.database import Base, generate_id


class Tool(Base):
    """在线工具"""
    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    name: Mapped[str] = mapped_column(String(100), comment="工具名称")
    description: Mapped[str] = mapped_column(String(500), comment="简介")
    category: Mapped[str] = mapped_column(String(30), index=True, comment="prompt/image/comfyui/calc")
    tool_type: Mapped[str] = mapped_column(String(30), default="ai", comment="ai/frontend")
    ai_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="AI模型配置(prompt模板/模型/参数)")
    form_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="表单字段定义")
    usage_count: Mapped[int] = mapped_column(Integer, default=0, comment="使用次数")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # 使用权限：free=免费使用(登录即可，不计时) / auth=需认证(默认，消耗免费时长，耗尽需认证) / points=需积分(每次消耗 points_cost 积分)
    access_mode: Mapped[str] = mapped_column(String(20), default="auth", index=True, comment="使用权限 free/auth/points")
    points_cost: Mapped[int] = mapped_column(Integer, default=1, server_default="1", comment="积分模式下每次运行消耗的积分")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # updated_at 建索引：SEO 增量推送（scheduler/tasks.py::collect_recent_urls）每天按
    # `updated_at >= 26 小时前 ORDER BY updated_at DESC LIMIT 500` 扫这张表，无索引即全表 + filesort（P2-43）
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)
