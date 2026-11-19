"""核心指标快照模型：每小时把一帧「系统体检读数」落一行。

为什么是快照而不是现算：看板已有「内容趋势 / 注册趋势」（按 created_at 现算），
但**总量增长、待办积压、登录失败数**这类读数要么跨多张表、要么有窗口语义
（近 1 小时失败数），现算既慢又无法回看「上周三凌晨是什么水平」。快照表
把读数钉在时间轴上，趋势面板就能回看任意区间。

每行一个 JSON（metrics），好处是加指标**不用改表结构**（新增读数只需在
`ops_metric_service.collect_metrics` 里加一个键，历史行缺键时前端按缺失展示）。

保留期 180 天，由 `scheduler/tasks.py::ops_metric_snapshot` 写入前顺带清理
（一小时一帧 ≈ 8.8k 行/年，量很小，但让表自清理是白赚的卫生习惯）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id

# 快照保留期（天）：趋势回看用不到半年前的读数
OPS_SNAPSHOT_RETENTION_DAYS = 180


class OpsMetricSnapshot(Base):
    """一帧核心指标读数"""

    __tablename__ = "ops_metric_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, comment="采样时间（UTC）"
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, comment="指标键值对（键定义见 ops_metric_service.METRIC_LABELS）")
