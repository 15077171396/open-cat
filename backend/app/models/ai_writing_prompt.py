"""AI 写作操作配置：提示词、模型与生成参数。

提示词放数据库的理由：改文案、换模型、调温度不应发版；代码里的
DEFAULT_PROMPTS 只作为首次种子与「恢复默认」的事实来源。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class AiWritingPrompt(Base):
    __tablename__ = "ai_writing_prompts"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False,
        default=generate_id, comment="主键",
    )
    operation: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, comment="操作标识（rewrite/polish/...）"
    )
    display_name: Mapped[str] = mapped_column(String(50), comment="面板展示名")
    description: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="用途说明")
    system_prompt: Mapped[str] = mapped_column(Text, comment="系统提示词")
    user_template: Mapped[str] = mapped_column(Text, comment="用户模板，{{var}} 占位")
    output_type: Mapped[str] = mapped_column(
        String(10), default="markdown", server_default="markdown", comment="markdown / json"
    )
    model_code: Mapped[str] = mapped_column(
        String(64), default="", server_default="", comment="空=全局默认文本模型"
    )
    temperature: Mapped[float] = mapped_column(
        Numeric(3, 2), default=0.7, server_default="0.7", comment="采样温度"
    )
    max_tokens: Mapped[int] = mapped_column(
        Integer, default=2000, server_default="2000", comment="最大输出 tokens"
    )
    max_input_chars: Mapped[int] = mapped_column(
        Integer, default=12000, server_default="12000", comment="上下文最大字符数"
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", index=True, comment="是否启用"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="排序")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
