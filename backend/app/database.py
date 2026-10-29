"""数据库连接管理 - SQLAlchemy 2.0 异步引擎"""

import secrets

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# 会话时区锁定 UTC：created_at 等由 MySQL 按会话时区求值，与应用侧 UTC 比较不同源会静默偏差数小时；
# 必须按连接显式 SET（生产 compose 设 TZ=UTC 恰好正常，本地 MySQL 跟随系统时区 +08:00 无任何报错）；
# 用数字偏移 +00:00 而非命名时区 UTC，不依赖 mysql 时区表已导入
MYSQL_SESSION_TZ_INIT = "SET time_zone='+00:00'"


def generate_id() -> int:
    """生成 10 位随机数字 ID（1000000000 ~ 9999999999），避免自增 ID 被枚举爬取"""
    return secrets.randbelow(9_000_000_000) + 1_000_000_000

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_dev,
    # 池容量按「单机 2核4G + gunicorn -w 2」核算：3 进程峰值 45 条连接，
    # 避免逼近 MySQL 默认 max_connections=151
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
    # 每条物理连接建立时执行一次（连接池复用期间会话变量保持）
    connect_args={"init_command": MYSQL_SESSION_TZ_INIT},
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """释放引擎连接池（应用关闭时调用，避免悬空连接与事件循环引用）"""
    await engine.dispose()
