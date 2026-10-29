"""Alembic 环境配置"""

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from app.config import settings
from app.database import Base

# 导入全部模型，确保 Alembic 能发现。
#
# ⚠️ 清单维护在 app/models/__init__.py（**唯一事实来源**），此处不再重复列举：
#    这里曾手写 import 清单，两次因漏登记导致 autogenerate 生成 DROP TABLE
#    （5 张表；以及 TutorialPurchase）。改为整包导入后，新增模型只需在
#    app/models/__init__.py 登记一次。
import app.models  # noqa: F401

config = context.config
# ConfigParser 会把 % 当插值语法解析（密码经 quote_plus 编码后含 %2B/%2F 等），
# 写入前必须转义为 %%，读取时 ConfigParser 自动还原为单个 %
config.set_main_option("sqlalchemy.url", settings.database_url_sync.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 由迁移用原生 SQL 创建、**刻意不写进模型**的数据库对象（autogenerate 不比较）。
# 这些索引依赖 MySQL 专属子句（FULLTEXT ... WITH PARSER ngram），SQLAlchemy 的
# Index() 无法表达；若放进 __table_args__，autogenerate 会尝试生成不带 parser 的
# 索引定义，反而更错。不放进模型则会被判为「多余索引」→ 生成 DROP INDEX，
# 谁跑一次 autogenerate 就静默失去全文检索。两头都不行，只能显式排除。
_UNMANAGED_INDEXES = frozenset(
    {
        "ft_tutorials_search",
        "ft_resources_search",
        # 知识百科 / 插件全文索引（台账 P1-17）：同理由原生 SQL 维护
        "ft_nodes_search",
        "ft_plugins_search",
        # 工具全文索引（第二轮复审 C-01）：0028/0032 漏掉了 tools，导致搜一次工具
        # 就把全站全文检索熔断（已改为按列组熔断，索引本身仍必须补上）
        "ft_tools_search",
    }
)


def include_object(object_, name, type_, reflected, compare_to):
    """autogenerate 的对象过滤器：跳过不由模型管理的索引。"""
    if type_ == "index" and name in _UNMANAGED_INDEXES:
        return False
    return True


def run_migrations_offline():
    """离线模式"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """在线模式"""
    # 迁移同样要锁定会话时区为 UTC：数据迁移里的时间戳（如 P2-7 回填 free_started_at）
    # 必须与应用写入同源，否则会在迁移这一步把偏移带进库里。
    # 复用 app.database 的常量，避免两处各写一份 SQL 而漂移。
    from app.database import MYSQL_SESSION_TZ_INIT

    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"init_command": MYSQL_SESSION_TZ_INIT},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
