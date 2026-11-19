"""种子数据 - 插入示例教程和管理员账号（幂等，可重复执行）"""

import asyncio
import os
import secrets
from datetime import datetime, timezone

from app.database import async_session, engine
from app.models.user import User
from app.models.tutorial import Tutorial
from app.core.security import hash_password

# 管理员口令：优先从环境变量读取，未设置则生成随机口令
admin_password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(16)


TUTORIAL_CONTENT = """# 后台使用入门

## 前言
本教程用于演示「教程」业务域的完整能力：列表、树形章节、富文本编辑、审核与回收站。
接入自己的业务模块时，照着本模块抄一遍即可（见开发文档 §五）。

## 基础概念与准备
教程由「主行 + 章节树」组成：主行承载标题/简介/封面/SEO，正文按章节存放在 tutorial_nodes 表。

### 环境准备
1. 完成安装向导（首次访问 /admin 自动进入）
2. 在「系统设置」配置站点名称与存储驱动
3. 在「角色管理」按需分配权限

## 核心操作实战
### 新建教程
使用「教程管理 → 新建」，填写标题、简介与分类后保存。

### 编辑章节
在编辑页左栏维护章节树，支持多级嵌套与拖拽排序。

## 进阶应用与拓展
- 富文本编辑器支持表格、代码块与图注
- 编辑器内置 AI 改写/续写/插图（需在「模型管理」配置模型）
- 素材库统一管理上传与图库导入的图片

## 总结
通过本教程，你已经掌握了后台内容管理的基本方法。
"""


async def seed():
    async with async_session() as db:
        # 创建管理员账号
        from sqlalchemy import select
        result = await db.scalars(select(User).where(User.account == "admin@example.com"))
        if result.one_or_none() is None:
            admin = User(
                account="admin@example.com",
                password_hash=hash_password(admin_password),
                role="admin",
            )
            db.add(admin)
            await db.flush()
            print(f"创建管理员账号: admin@example.com / {admin_password}")
            # RBAC：全新部署时迁移尚无存量 admin 可分配，此处为种子管理员
            # 绑定 super_admin 角色（幂等：角色已绑定时跳过）
            try:
                from app.models.rbac import Role, UserRole
                super_role = (
                    await db.scalars(select(Role).where(Role.code == "super_admin"))
                ).one_or_none()
                if super_role:
                    existed = (
                        await db.execute(
                            select(UserRole.user_id).where(
                                UserRole.user_id == admin.id,
                                UserRole.role_id == super_role.id,
                            )
                        )
                    ).first()
                    if not existed:
                        db.add(UserRole(user_id=admin.id, role_id=super_role.id))
                        print("已为种子管理员绑定 super_admin 角色")
            except Exception as e:
                # RBAC 表尚未迁移（旧库先跑 seed 再迁移的场景）：跳过绑定，
                # 迁移中的存量 admin 分配逻辑会补上
                print(f"跳过 super_admin 角色绑定（RBAC 表未就绪）: {e}")

        # 创建示例教程
        tutorials_data = [
            {
                "title": "后台使用入门",
                "description": "从安装向导到发布第一篇内容，演示教程业务域的完整能力。",
                "category": "入门",
                "content": TUTORIAL_CONTENT,
                "status": "completed",
                "view_count": 156,
            },
            {
                "title": "权限与审计最佳实践",
                "description": "利用按钮级 RBAC 与全量操作审计，安全地多人协作运营内容站。",
                "category": "运营",
                "content": "# 权限与审计\n\n## 权限码\n权限在「角色管理」按按钮级分配。",
                "status": "updating",
                "view_count": 89,
            },
            {
                "title": "内容审核工作流",
                "description": "机审词库 + 人工复核 + 举报处理的完整 UGC 审核链路。",
                "category": "运营",
                "content": "# 内容审核\n\n## 词库\n在「系统设置 → 内容审核」维护追加词库。",
                "status": "completed",
                "view_count": 234,
            },
            {
                "title": "AI 写作配置指南",
                "description": "在「模型管理」配置供应商与模型，再在「AI 写作」页维护提示词。",
                "category": "进阶",
                "content": "# AI 写作\n\n## 模型配置\n支持 DeepSeek / OpenAI / Qwen 等供应商。",
                "status": "updating",
                "view_count": 67,
            },
            {
                "title": "部署与备份手册",
                "description": "Docker Compose 一键部署、备份管理与部署体检。",
                "category": "运维",
                "content": "# 部署\n\n## 快速开始\n`cp .env.example .env && docker compose up -d`",
                "status": "completed",
                "view_count": 312,
            },
        ]

        for t_data in tutorials_data:
            result = await db.scalars(select(Tutorial).where(Tutorial.title == t_data["title"]))
            if result.one_or_none() is None:
                tutorial = Tutorial(
                    **t_data,
                    is_published=True,
                    # 全库时间约定存 UTC（app/utils/time.py），本地时间会使前台显示偏移 8 小时
                    published_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                db.add(tutorial)
                print(f"创建教程: {t_data['title']}")

        await db.commit()
        print("\n种子数据创建完成!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
