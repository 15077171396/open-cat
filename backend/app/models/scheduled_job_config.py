"""定时任务配置（后台「系统 → 定时任务」可改的那部分）。

**这里只存「跑不跑 / 什么周期」，不存「做什么」** —— 任务体（handler）永远在
`scheduler/tasks.py::JOB_FUNCS` 里，必须是代码 + 测试。理由：
后台能改的行为边界越小，出事的概率越低；而「新增一种任务」本质是一次代码变更，
让它走正常的开发/审查/发布流程比在页面上拼参数安全得多。

一行一个任务，`job_id` 直接对应 `JOB_SPECS` 的 id（自然主键，不用随机 id）：
- 目录里新增任务 → 迁移里跟着插一行默认配置（`INSERT IGNORE`），否则页面会是「库里没这行」的灰区；
- 目录里删掉任务 → 这里的孤儿行不影响调度（对齐循环只按 `JOB_SPECS` 遍历）。

⚠️ 本表是**期望状态**，不是实际状态：改了要等调度器对齐（≤5 秒）才生效。
实际状态看 Redis 的 `ops:jobs:applied`（由 `scheduled_job_service.APPLIED_KEY` 定义）。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduledJobConfig(Base):
    """单个定时任务的配置覆盖（缺行 = 用代码默认值）"""

    __tablename__ = "scheduled_job_configs"

    job_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="任务注册名，与 scheduled_job_service.JOB_SPECS 一致"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False, comment="是否参与调度"
    )
    schedule_kind: Mapped[str] = mapped_column(
        String(16), default="daily", server_default="daily", nullable=False, comment="daily / interval"
    )
    hour: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, comment="daily：执行小时（北京时间 0-23）"
    )
    minute: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, comment="daily：执行分钟（0-59）"
    )
    interval_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="interval：间隔分钟（1-10080）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="最后修改时间"
    )
    # 账号快照而不是 user_id：账号可改名/注销，事后要能回答「当时是谁改的」
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True, comment="最后修改者账号")
