"""邀请关系模型"""

from datetime import datetime
from sqlalchemy import BigInteger, Integer, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, generate_id


class InviteRelation(Base):
    """邀请关系表

    inviter_id: 邀请人（通过邀请链接/邀请码获得积分的一方）
    invitee_id: 被邀请人（注册时填入/携带邀请码的新用户）
    reward_points: 发放积分快照（当时系统配置的奖励值，便于追溯）

    每个被邀请人仅可绑定一位邀请人（invitee_id 唯一约束兜底并发重复请求）；
    被邀请人账号注销后复活不清理本关系、不重复发奖（防「注销-复活换码」刷分）。
    """
    __tablename__ = "invite_relations"
    __table_args__ = (
        UniqueConstraint("invitee_id", name="uq_invite_relations_invitee"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=generate_id)
    inviter_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="邀请人ID")
    invitee_id: Mapped[int] = mapped_column(BigInteger, index=True, comment="被邀请人ID(一人仅可被邀请一次)")
    reward_points: Mapped[int] = mapped_column(Integer, server_default="0", comment="邀请人获得的积分")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="绑定时间")
