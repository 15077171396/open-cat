"""种子数据 - 插入教程、资源和管理员账号"""

import asyncio
import os
import secrets
from datetime import datetime, timezone

from app.database import async_session, engine
from app.models.user import User
from app.models.tutorial import Tutorial, TutorialNetdiskLink
from app.models.resource import Resource, ResourceNetdiskLink
from app.core.security import hash_password

# 管理员口令：优先从环境变量读取，未设置则生成随机口令
admin_password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(16)


TUTORIAL_CONTENT = """# ComfyUI 基础教程

## 前言
本教程将带你从零开始学习 ComfyUI，掌握 AI 绘画的核心技能。

## 基础概念与准备
ComfyUI 是一个基于节点的 Stable Diffusion 工作流编辑器，它以模块化、可视化的方式构建 AI 图像生成流程。

### 环境准备
1. 下载并安装 Python 3.10+
2. 安装 ComfyUI 整合包
3. 准备模型文件

## 核心操作实战
### 加载模型
使用 `CheckpointLoaderSimple` 节点加载 SD 模型。

### 编写提示词
```
正向提示词: masterpiece, best quality, 1girl, solo
反向提示词: lowres, bad anatomy, bad hands
```

## 进阶应用与拓展
- 自定义节点开发
- 工作流复用与分享
- 批量生成与后处理

## 总结
通过本教程，你已经掌握了 ComfyUI 的基本使用方法。继续探索更多高级功能吧！
"""


async def seed():
    async with async_session() as db:
        # 创建管理员账号
        from sqlalchemy import select
        result = await db.execute(select(User).where(User.account == "admin@aicat.com"))
        if not result.scalar_one_or_none():
            admin = User(
                account="admin@aicat.com",
                password_hash=hash_password(admin_password),
                role="admin",
                free_used_seconds=0,
                # 必须显式写：该列无 server_default，漏写会落 NULL，
                # 前台只能回退 created_at 算免费时长（时区语义不同源，见 timer_service）
                free_started_at=datetime.now(timezone.utc),
            )
            db.add(admin)
            await db.flush()
            print(f"创建管理员账号: admin@aicat.com / {admin_password}")
            # RBAC：全新部署时迁移尚无存量 admin 可分配，此处为种子管理员
            # 绑定 super_admin 角色（幂等：角色已绑定时跳过）
            try:
                from app.models.rbac import Role, UserRole
                super_role = (
                    await db.execute(select(Role).where(Role.code == "super_admin"))
                ).scalar_one_or_none()
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

        # 创建教程
        tutorials_data = [
            {
                "title": "ComfyUI 零基础入门教程",
                "description": "从零开始学习 ComfyUI，掌握 AI 绘画核心技能，涵盖模型加载、提示词编写、工作流搭建。",
                "category": "ComfyUI",
                "content": TUTORIAL_CONTENT,
                "status": "completed",
                "view_count": 156,
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/example1", "extract_code": "abcd"},
                    {"platform": "quark", "url": "https://pan.quark.cn/s/example1", "extract_code": "efgh"},
                ],
            },
            {
                "title": "Coze 智能体开发实战",
                "description": "学习使用 Coze 平台开发 AI 智能体，从Prompt 设计到扩展集成全流程。",
                "category": "Coze",
                "content": "# Coze 智能体开发\n\n## 前言\n本教程介绍 Coze 平台的智能体开发流程。",
                "status": "updating",
                "view_count": 89,
                "links": [
                    {"platform": "quark", "url": "https://pan.quark.cn/s/example2", "extract_code": "1234"},
                ],
            },
            {
                "title": "Stable Diffusion 提示词进阶技巧",
                "description": "深入解析 SD 提示词编写技巧，包括权重控制、风格混合、质量提升策略。",
                "category": "Stable Diffusion",
                "content": "# SD 提示词进阶\n\n## 权重控制\n使用 (keyword:1.5) 控制提示词权重。",
                "status": "completed",
                "view_count": 234,
                "links": [],
            },
            {
                "title": "AI 视频生成完全指南",
                "description": "掌握 AI 视频生成技术，涵盖 Runway、Pika、可灵等主流工具使用方法。",
                "category": "AI 视频",
                "content": "# AI 视频生成指南\n\n## 主流工具对比\nRunway / Pika / 可灵",
                "status": "updating",
                "view_count": 67,
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/example4", "extract_code": "wxyz"},
                ],
            },
            {
                "title": "Prompt 工程最佳实践",
                "description": "系统学习 Prompt 工程，提升大模型对话质量与输出稳定性。",
                "category": "Prompt",
                "content": "# Prompt 工程实践\n\n## 核心原则\n清晰、具体、有上下文。",
                "status": "completed",
                "view_count": 312,
                "links": [],
            },
            {
                "title": "Midjourney 完全指南",
                "description": "从入门到精通，AI 艺术创作生成惊艳作品，涵盖参数详解、风格控制与商业应用。",
                "category": "Stable Diffusion",
                "content": "# Midjourney 完全指南\n\n## 前言\n本教程带你系统掌握 Midjourney 的核心用法与进阶技巧。\n\n## 基础入门\n1. 注册与订阅\n2. 基本命令 /imagine 与参数\n3. 提示词结构：主体 + 环境 + 风格 + 光影\n\n## 进阶技巧\n- --ar 控制画幅比例\n- --stylize 控制艺术化程度\n- --cref 角色一致性\n\n## 商业应用\n海报设计、插画外包、电商素材等场景实战。",
                "status": "completed",
                "view_count": 121,
                "links": [],
            },
            {
                "title": "AI 办公效率提升工具箱",
                "description": "精选 AI 效率工具，覆盖文档处理、数据分析、演示制作等办公场景。",
                "category": "效率办公",
                "content": "# AI 办公工具箱\n\n## 文档处理\nChatDOC / Kimi",
                "status": "updating",
                "view_count": 198,
                "links": [
                    {"platform": "aliyun", "url": "https://www.aliyundrive.com/s/example6", "extract_code": None},
                ],
            },
        ]

        for t_data in tutorials_data:
            result = await db.execute(select(Tutorial).where(Tutorial.title == t_data["title"]))
            if not result.scalar_one_or_none():
                links = t_data.pop("links")
                tutorial = Tutorial(
                    **t_data,
                    is_published=True,
                    # 全库时间约定存 UTC（app/utils/time.py），本地时间会使前台显示偏移 8 小时
                    published_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                db.add(tutorial)
                await db.flush()

                for link in links:
                    db.add(TutorialNetdiskLink(
                        tutorial_id=tutorial.id,
                        platform=link["platform"],
                        url=link["url"],
                        extract_code=link["extract_code"],
                    ))
                print(f"创建教程: {t_data['title']}")

        # 创建资源
        resources_data = [
            {
                "name": "ComfyUI 整合包 (含常用模型)",
                "description": "一键安装的 ComfyUI 整合包，包含 SD1.5、SDXL 模型及常用自定义节点。",
                "resource_type": "tool",
                "file_size": "12.5 GB",
                "content_list": ["ComfyUI 主程序", "SD 1.5 模型", "SDXL 模型", "常用自定义节点包", "启动脚本"],
                "usage_notes": ["解压到任意目录", "运行 start.bat 启动", "浏览器访问 http://127.0.0.1:8188"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res1", "extract_code": "comf"},
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res1", "extract_code": "ui12"},
                ],
            },
            {
                "name": "Stable Diffusion 模型合集",
                "description": "精选 20+ 高质量 SD 模型，覆盖二次元、写实、国风等风格。",
                "resource_type": "model",
                "file_size": "35.2 GB",
                "content_list": ["二次元模型 x5", "写实模型 x8", "国风模型 x3", "特殊风格模型 x4"],
                "usage_notes": ["选择需要的模型下载", "放入 models/checkpoints 目录", "重启 ComfyUI"],
                "links": [
                    {"platform": "quark", "url": "https://pan.quark.cn/s/res2", "extract_code": "sdml"},
                ],
            },
            {
                "name": "ComfyUI 工作流源码合集",
                "description": "100+ 精选 ComfyUI 工作流 JSON 文件，可直接导入使用。",
                "resource_type": "source",
                "file_size": "15.8 MB",
                "content_list": ["文生图工作流 x30", "图生图工作流 x25", "局部重绘工作流 x20", "视频生成工作流 x15", "后处理工作流 x10"],
                "usage_notes": ["解压文件", "在 ComfyUI 中选择 Load", "导入对应的 JSON 文件"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res3", "extract_code": "flow"},
                    {"platform": "github", "url": "https://github.com/example/workflows", "extract_code": None},
                ],
            },
            {
                "name": "AI 绘画 Prompt 速查手册",
                "description": "系统整理的 AI 绘画提示词参考文档，含中英文对照和效果示例。",
                "resource_type": "doc",
                "file_size": "5.6 MB",
                "content_list": ["提示词分类索引", "风格关键词 500+", "质量提升词 200+", "反向提示词模板"],
                "usage_notes": ["下载 PDF 文档", "按分类查找关键词", "组合使用效果更佳"],
                "links": [
                    {"platform": "aliyun", "url": "https://www.aliyundrive.com/s/res4", "extract_code": None},
                ],
            },
            {
                "name": "LoRA 模型训练工具包",
                "description": "完整的 LoRA 训练工具包，含训练脚本、数据集模板和预设参数。",
                "resource_type": "tool",
                "file_size": "2.3 GB",
                "content_list": ["训练脚本", "数据预处理工具", "预设参数配置", "训练教程文档"],
                "usage_notes": ["安装依赖环境", "准备训练数据集", "修改配置参数", "运行训练脚本"],
                "links": [
                    {"platform": "baidu", "url": "https://pan.baidu.com/s/res5", "extract_code": "lora"},
                    {"platform": "mobile", "url": "https://yun.139.com/s/res5", "extract_code": "tr01"},
                ],
            },
        ]

        for r_data in resources_data:
            result = await db.execute(select(Resource).where(Resource.name == r_data["name"]))
            if not result.scalar_one_or_none():
                links = r_data.pop("links")
                resource = Resource(**r_data)
                db.add(resource)
                await db.flush()

                for idx, link in enumerate(links):
                    db.add(ResourceNetdiskLink(
                        resource_id=resource.id,
                        platform=link["platform"],
                        url=link["url"],
                        extract_code=link["extract_code"],
                        sort_order=idx,
                    ))
                print(f"创建资源: {r_data['name']}")

        await db.commit()
        print("\n种子数据创建完成!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
