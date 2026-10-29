"""AI 模型目录（后台「模型管理」的唯一事实来源）。

与 `services/ai/registry.py` 的分工：
- registry 只声明「有哪些协议实现」（deepseek / openai / qwen 及各自环境变量前缀）；
- 本表声明「有哪些可调用模型」：同一实现下可以有多条模型（如 deepseek-chat /
  deepseek-reasoner），各自持有 Key、BaseURL、上游模型名与单价。

`code` 是工具 `ai_config.model` / 翻译等调用方引用的稳定标识，创建后不可修改；
旧数据迁移时三条种子记录的 code 直接取供应商名（deepseek/openai/qwen），
保证升级前工具里已有的值继续命中。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class AiModel(Base):
    """可调用的 AI 模型"""

    __tablename__ = "ai_models"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, default=generate_id, comment="主键"
    )
    code: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, comment="稳定标识（工具/翻译引用），创建后不可改"
    )
    display_name: Mapped[str] = mapped_column(String(100), comment="后台与看板展示名")
    provider: Mapped[str] = mapped_column(
        String(30), index=True, comment="协议实现名（必须来自 Provider 注册表）"
    )
    modality: Mapped[str] = mapped_column(
        String(10), default="text", server_default="text", index=True,
        comment="模态：text / image",
    )
    image_price: Mapped[float] = mapped_column(
        Numeric(12, 4), default=0, server_default="0",
        comment="图片单价（元/张，0=不计价）",
    )
    model_name: Mapped[str] = mapped_column(
        String(100), default="", server_default="", comment="上游模型名，空=回退供应商 .env"
    )
    base_url: Mapped[str] = mapped_column(
        String(500), default="", server_default="", comment="接口地址，空=回退供应商 .env"
    )
    api_key_enc: Mapped[str] = mapped_column(
        String(1000), default="", server_default="", comment="加密后的 API Key，空=回退供应商 .env"
    )
    input_price: Mapped[float] = mapped_column(
        Numeric(12, 6), default=0, server_default="0", comment="输入单价（元/百万 tokens，0=回退兜底单价）"
    )
    output_price: Mapped[float] = mapped_column(
        Numeric(12, 6), default=0, server_default="0", comment="输出单价（元/百万 tokens，0=回退兜底单价）"
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", index=True, comment="是否启用"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", index=True, comment="是否全局默认（最多一条）"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", comment="排序（越小越靠前）"
    )
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True, comment="更新时间"
    )
