"""tutorial nodes table + split legacy content

Revision ID: add_tutorial_nodes
Revises: add_tool_categories
Create Date: 2026-08-30 00:00:00

教程正文章节化（PandaWiki 文档节点模式）:
- 新建 tutorial_nodes 自关联树表（tutorial_id/parent_id/sort/title/content）
- 存量 tutorials.content 按 h1-h6 标题切分为章节树写入新表
  （复用 content_parser.parse_sections，与前后端切分规则一致；
   首个标题之前的前导内容补「前言」顶级章节，避免内容丢失）
- tutorials.content 原字段保留不删除，作为回滚备份；业务读写改走 tutorial_nodes
"""
from typing import Sequence, Union
import re

from alembic import op
import sqlalchemy as sa

from app.services.content_parser import parse_sections, is_html_content


revision: str = 'add_tutorial_nodes'
down_revision: Union[str, None] = 'add_tool_categories'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 content_parser 保持一致的标题定位正则（用于提取首标题前的前导内容）
_HTML_HEADING_RE = re.compile(r"<(h[1-6])([^>]*)>(.*?)</\1>", re.IGNORECASE)
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+.", re.MULTILINE)


def _build_section_tree(content: str) -> list[dict]:
    """把整篇正文切分为章节树（栈算法按标题层级构树）。

    返回嵌套 dict: {title, content, children: [...]}。
    - parse_sections 返回扁平 [{id,title,level,content}]，按 level 挂父
    - 首个标题之前的前导内容（如"<p>前言…</p>"）补为「前言」顶级章节
    """
    sections = parse_sections(content)
    if not sections:
        return []

    roots: list[dict] = []
    stack: list[dict] = []

    # 前导内容：正则定位首个标题标签位置，其之前的部分补「前言」章节。
    # 前言为独立顶级节点（不入构树栈，避免首个真实标题被挂到它下面）
    heading_match = (
        _HTML_HEADING_RE.search(content) if is_html_content(content) else _MARKDOWN_HEADING_RE.search(content)
    )
    if heading_match and heading_match.start() > 0:
        preamble = content[:heading_match.start()].strip()
        if preamble:
            roots.append({"title": "前言", "content": preamble, "children": []})

    for s in sections:
        node = {"title": s["title"], "content": s["content"], "level": s["level"], "children": []}
        while stack and s["level"] <= stack[-1]["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def _insert_nodes(conn: sa.Connection, table: sa.Table, tutorial_id: int, nodes: list[dict], parent_id: int | None) -> None:
    """递归插入章节树，返回无值（层级通过闭包 parent_id 传递）"""
    for sort, node in enumerate(nodes):
        result = conn.execute(
            table.insert().values(
                tutorial_id=tutorial_id,
                parent_id=parent_id,
                sort=sort,
                title=node["title"][:200],
                content=node["content"],
            )
        )
        node_id = result.inserted_primary_key[0]
        if node["children"]:
            _insert_nodes(conn, table, tutorial_id, node["children"], node_id)


def upgrade() -> None:
    # 1. 建表（与 app/models/tutorial_node.py 的 ORM 定义一致）
    table = op.create_table(
        'tutorial_nodes',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tutorial_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('sort', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content', sa.Text(length=16777215), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tutorial_id'], ['tutorials.id'], ),
        sa.ForeignKeyConstraint(['parent_id'], ['tutorial_nodes.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tutorial_nodes_tutorial_id', 'tutorial_nodes', ['tutorial_id'])
    op.create_index('ix_tutorial_nodes_parent_id', 'tutorial_nodes', ['parent_id'])
    op.create_index('ix_tutorial_nodes_tutorial_sort', 'tutorial_nodes', ['tutorial_id', 'sort'])

    # 2. 存量教程正文切分为章节树（幂等：仅当该教程尚无章节时切分）
    conn = op.get_bind()
    tutorials = conn.execute(
        sa.text("SELECT id, content FROM tutorials WHERE content IS NOT NULL AND content != ''")
    ).fetchall()
    for tid, content in tutorials:
        existing = conn.execute(
            sa.text("SELECT COUNT(*) FROM tutorial_nodes WHERE tutorial_id = :tid"),
            {"tid": tid},
        ).scalar()
        if existing:
            continue
        tree = _build_section_tree(content)
        if tree:
            _insert_nodes(conn, table, tid, tree, None)


def downgrade() -> None:
    # 章节数据无法无损合回 tutorials.content（各章标题层级信息已展平为树），
    # 降级仅删除章节表；tutorials.content 自迁移起未被修改，回滚后恢复旧行为
    #
    # ⚠️ 刻意**不逐个 drop_index**（2026-10-10 由 CI 的迁移可逆性校验发现）：
    # `parent_id` 上有自引用外键（见上方 ForeignKeyConstraint），MySQL 会复用
    # `ix_tutorial_nodes_parent_id` 作为该外键的支撑索引，先 drop 它会报
    # errno 1553「Cannot drop index ... needed in a foreign key constraint」，
    # 使整条 downgrade 链在此中断 —— 也就是说**回滚能力实际不可用**。
    # DROP TABLE 会连带删除表上的全部索引与外键，无需也不能逐个先删。
    op.drop_table('tutorial_nodes')
