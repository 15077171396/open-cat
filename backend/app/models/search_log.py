"""站内搜索词日志模型。

只用于「热度 / 零结果 / 模块分布」三类聚合，**不存用户标识** ——
搜索词常含隐私意图，能少存就少存；按人下钻不是本表的目的。

保留期 90 天，由 `scheduler/tasks.py::cleanup_search_logs` 每日清理。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class SearchLog(Base):
    """一次站内搜索"""

    __tablename__ = "search_logs"
    __table_args__ = (
        # 按词聚合（热搜榜 / 零结果榜）：先按 keyword 定位再按时间过滤
        Index("ix_search_logs_keyword_created", "keyword", "created_at"),
        Index("ix_search_logs_result_count", "result_count"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    keyword: Mapped[str] = mapped_column(String(64), comment="搜索词(清洗后原始输入)")
    module: Mapped[str] = mapped_column(String(20), comment="检索模块: tutorial/resource/node/plugin/tool")
    result_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", comment="本次命中条数(0=零结果)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, comment="搜索时间"
    )
