"""系统设置模型"""

from datetime import datetime
from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemSetting(Base):
    """系统设置（key-value 结构：新增设置项只需加代码定义，无需改表）"""
    __tablename__ = "system_settings"

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True, comment="设置键")
    # NULL = 未配置，接口层回退到代码中的默认值
    value: Mapped[str | None] = mapped_column(Text, nullable=True, comment="设置值（NULL=使用默认值）")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")
