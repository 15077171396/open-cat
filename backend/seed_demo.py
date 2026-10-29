"""演示数据：让开源版跑起来「每个列表都有数据可看」。

与 `seed_data.py` 的分工
------------------------
- `seed_data.py`：最小可用集（一个管理员 + 几条内容），给真实部署用；
- `seed_demo.py`：**灌满演示数据**（本文件），给「clone 下来先看看长什么样」用。

三个设计约束
------------
1. **幂等**：以「标题带【示例】前缀的教程」作为标记，已存在就整体跳过。
2. **不污染真实库**：库里已有非示例内容时，必须显式 `--yes` 才继续（默认拒绝）。
3. **图片真的能显示**：封面/头像用 Pillow 现场生成，走 `storage.upload_bytes`，
   因此 local 与 cos 两种驱动下都能看到图——而不是一堆 404 占位。

用法：
    python seed_demo.py            # 空库：直接灌；有真实内容：拒绝并提示 --yes
    python seed_demo.py --yes      # 确认在已有数据的库里追加演示数据
"""

from __future__ import annotations

import argparse
import asyncio
import io
import random
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw
from sqlalchemy import func, select

from app.core.security import hash_password_async
from app.database import async_session, engine
from app.models.announcement import Announcement
from app.models.category import Category
from app.models.comment import ContentComment
from app.models.feedback import Feedback
from app.models.node import Node, Plugin
from app.models.point_transaction import PointTransaction
from app.models.resource import Resource
from app.models.tool import Tool
from app.models.tutorial import Tutorial
from app.models.tutorial_node import TutorialNode
from app.models.user import User
from app.services.storage import storage

MARK = "【示例】"
DEMO_PASSWORD = "Demo@123456"

random.seed(20261030)  # 固定随机：两次跑出来的演示数据一致，便于截图比对


def _png(label: str, color: tuple[int, int, int], size: tuple[int, int] = (640, 360)) -> bytes:
    """生成带文字的纯色占位图（不依赖任何外部图床）。"""
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    # 用矩形代替字体渲染：字体缺失时 Pillow 会退化，反而更不可控
    draw.rectangle([24, size[1] // 2 - 34, size[0] - 24, size[1] // 2 + 34], fill=(255, 255, 255))
    draw.rectangle([40, size[1] // 2 - 20, 40 + min(20 * len(label), size[0] - 80), size[1] // 2 + 20], fill=color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def _upload_demo_image(
    label: str,
    rgb: tuple[int, int, int],
    size: tuple[int, int] = (640, 360),
) -> str:
    """生成占位图并写入当前存储驱动（local / cos 都适用）。

    :param size: 封面用 640×360（16:9），头像传 (128, 128) —— 方图在圆形裁切下不变形
    """
    return await storage.upload_bytes(
        _png(label, rgb, size), folder="demo", ext=".png", content_type="image/png"
    )


CATEGORIES = [
    ("tutorial", ["入门基础", "提示词", "工作流", "本地部署"]),
    ("resource", ["模型", "工作流文件", "素材包"]),
    ("node", ["图像生成", "文本处理", "视频"]),
    ("tool", ["图像", "文本", "效率"]),
    ("plugin", ["ComfyUI", "Coze", "其他"]),
]

TUTORIALS = [
    ("ComfyUI 零基础入门", "从安装到跑通第一个工作流", "入门基础", ["环境准备", "认识节点", "第一个工作流"]),
    ("提示词工程实战", "让模型听懂你的意图", "提示词", ["结构拆解", "权重与否定", "风格控制"]),
    ("LoRA 训练从零到一", "素材整理、打标、训练与验证", "工作流", ["素材与打标", "参数设置", "效果验证"]),
    ("本地部署的性能调优", "显存、量化与批处理", "本地部署", ["显存规划", "量化选择", "吞吐测试"]),
    ("图生视频工作流", "让静态图动起来的三种做法", "工作流", ["关键帧", "运动控制", "后期合成"]),
]

NODES = [
    ("KSampler 采样器", "图像生成", "控制采样步数、CFG 与采样器类型"),
    ("CLIP Text Encode", "文本处理", "把提示词编码成模型可读的条件向量"),
    ("VAE Decode", "图像生成", "把潜空间张量解码为像素图"),
    ("ControlNet 应用", "图像生成", "用边缘/深度约束构图"),
    ("AnimateDiff 加载器", "视频", "为静态模型注入时序运动"),
]

RESOURCES = [
    ("常用 LoRA 合集", "12 个风格化 LoRA", "模型"),
    ("高质量工作流包", "10 个可直接运行的 JSON", "工作流文件"),
    ("图标素材包", "300+ 扁平图标", "素材包"),
]

TOOLS = [
    ("图片压缩", "批量压缩与格式转换", "图像", "frontend"),
    ("提示词优化", "把口语描述改写成结构化提示词", "文本", "ai"),
    ("尺寸计算器", "按比例换算常用分辨率", "效率", "frontend"),
]

PLUGINS = [
    ("ComfyUI Manager", "ComfyUI", "插件与模型的可视化管理器"),
    ("ControlNet 预处理器", "ComfyUI", "常用预处理器集合"),
    ("Coze 助手", "Coze", "把 Coze 智能体接入工作流"),
]

FEEDBACKS = [
    ("教程第三章的截图看不清，能换一张吗？", "reader01@example.com"),
    ("希望增加一个「批量导出」的功能", None),
    ("搜索有时候搜不到刚发布的文章", None),
]

ANNOUNCEMENTS = [
    ("欢迎使用管理后台", "这是一个演示公告：公告支持定时发布与批量操作。", "系统"),
    ("演示数据说明", "当前内容均为演示用途，可在「系统设置」里按需清空。", "提示"),
]

USERS = [
    ("demo01@example.com", "小林"),
    ("demo02@example.com", "阿哲"),
    ("demo03@example.com", "Momo"),
    ("demo04@example.com", "老陈"),
    ("demo05@example.com", "Nina"),
]


async def _has_demo(db) -> bool:
    stmt = select(func.count()).select_from(Tutorial).where(Tutorial.title.like(f"{MARK}%"))
    return int((await db.execute(stmt)).scalar() or 0) > 0


async def _has_real_content(db) -> bool:
    """库里已有非示例内容 → 说明不是全新环境，需要显式确认。"""
    for model in (Tutorial, Resource, Node, Tool, Plugin):
        total = int((await db.execute(select(func.count()).select_from(model))).scalar() or 0)
        demo = 0
        if model is Tutorial:
            demo = int(
                (
                    await db.execute(
                        select(func.count()).select_from(model).where(model.title.like(f"{MARK}%"))
                    )
                ).scalar()
                or 0
            )
        if total - demo > 0:
            return True
    return False


async def seed(check_only: bool = False) -> None:
    async with async_session() as db:
        if await _has_demo(db):
            print(f"检测到已有演示数据（标题以「{MARK}」开头），跳过。")
            print("如需重灌：先删除这些示例内容，或换一个空库。")
            return

        if await _has_real_content(db) and not check_only:
            raise SystemExit(
                "库里已有真实内容，拒绝灌入演示数据。\n"
                "确认要追加吗？加 --yes 再跑一次。"
            )

        now = datetime.now(timezone.utc)

        # ---------- 分类 ----------
        # 逐个跳过已存在的（module + name 有唯一索引）。
        # 不能假设「跑到这里就一定是空库」：上一次可能在中途失败退出，
        # 分类已经 commit 了 —— 那时重跑会直接撞唯一键，用户只能手动清库。
        existing_categories = {
            (c.module, c.name)
            for c in (await db.execute(select(Category))).scalars().all()
        }
        created_categories = 0
        for module, names in CATEGORIES:
            for idx, name in enumerate(names):
                if (module, name) in existing_categories:
                    continue
                db.add(Category(module=module, name=name, sort_order=idx))
                created_categories += 1
        print(f"分类：新增 {created_categories} 个，已存在 {len(existing_categories)} 个（跳过）")

        # ---------- 图片（真实落盘，不是 404 占位） ----------
        cover_urls = [
            await _upload_demo_image(f"cover {i + 1}", c)
            for i, c in enumerate([(66, 99, 235), (109, 91, 255), (23, 146, 124), (217, 119, 6)])
        ]
        avatar_urls = [
            await _upload_demo_image(f"U{i + 1}", c, size=(128, 128))
            for i, c in enumerate([(66, 99, 235), (16, 163, 74), (217, 119, 6), (207, 19, 34), (109, 91, 255)])
        ]
        print(f"图片：{len(cover_urls) + len(avatar_urls)} 张已写入存储（driver={storage.driver}）")

        # ---------- 用户 ----------
        pwd_hash = await hash_password_async(DEMO_PASSWORD)
        users: list[User] = []
        for idx, (account, nickname) in enumerate(USERS):
            user = User(
                account=account,
                password_hash=pwd_hash,
                role="user",
                nickname=nickname,
                avatar_url=avatar_urls[idx % len(avatar_urls)],
                points=random.randint(80, 600),
                free_used_seconds=0,
                free_started_at=now - timedelta(days=random.randint(3, 120)),
            )
            db.add(user)
            users.append(user)
        await db.flush()
        print(f"用户：{len(users)} 个（口令统一 {DEMO_PASSWORD}）")

        # ---------- 教程 + 章节 ----------
        admin = (
            await db.execute(select(User).where(User.role == "admin").limit(1))
        ).scalar_one_or_none()

        tutorials: list[Tutorial] = []
        for idx, (title, desc, category, chapters) in enumerate(TUTORIALS):
            tutorial = Tutorial(
                title=f"{MARK}{title}",
                description=desc,
                introduction=f"## {title}\n\n{desc}。这是演示内容，可直接编辑或删除。",
                category=category,
                content="<p>正文已迁移到章节节点。</p>",
                cover_image=cover_urls[idx % len(cover_urls)],
                status="completed" if idx % 2 == 0 else "updating",
                is_published=True,
                published_at=now - timedelta(days=idx * 3),
                view_count=random.randint(120, 4200),
                is_recommended=idx < 2,
                seo_title=f"{title} - 演示教程",
                seo_description=desc[:80],
            )
            db.add(tutorial)
            tutorials.append(tutorial)
        await db.flush()

        for t_idx, tutorial in enumerate(tutorials):
            chapters = TUTORIALS[t_idx][3]
            for c_idx, chapter in enumerate(chapters):
                db.add(
                    TutorialNode(
                        tutorial_id=tutorial.id,
                        parent_id=None,
                        sort=c_idx,
                        title=f"{c_idx + 1}. {chapter}",
                        content=(
                            f"<h2>{chapter}</h2>"
                            f"<p>这是《{tutorial.title}》第 {c_idx + 1} 章的演示正文。</p>"
                            "<p>支持表格、代码块、图注与目录，可以在编辑器里直接改。</p>"
                        ),
                    )
                )
        print(f"教程：{len(tutorials)} 篇 / {sum(len(t[3]) for t in TUTORIALS)} 个章节")

        # ---------- 百科 ----------
        for idx, (name, category, desc) in enumerate(NODES):
            db.add(
                Node(
                    name=f"{MARK}{name}",
                    category=category,
                    description=desc,
                    content=f"<p>{desc}。演示节点，可编辑。</p>",
                    view_count=random.randint(50, 900),
                    is_published=True,
                    cover_image=cover_urls[idx % len(cover_urls)],
                )
            )
        print(f"百科：{len(NODES)} 条")

        # ---------- 资源 ----------
        for idx, (name, desc, rtype) in enumerate(RESOURCES):
            db.add(
                Resource(
                    name=f"{MARK}{name}",
                    description=desc,
                    content=f"<p>{desc}。演示资源。</p>",
                    resource_type=rtype,
                    # 非空列：该列没有默认值，漏填在真实库上是 IntegrityError
                    file_size=f"{random.randint(1, 40)}.{random.randint(0, 9)}GB",
                    download_count=random.randint(10, 800),
                    view_count=random.randint(100, 2000),
                    is_published=True,
                    cover_image=cover_urls[idx % len(cover_urls)],
                )
            )
        print(f"资源：{len(RESOURCES)} 条")

        # ---------- 工具 ----------
        for name, desc, category, tool_type in TOOLS:
            db.add(
                Tool(
                    name=f"{MARK}{name}",
                    description=desc,
                    category=category,
                    tool_type=tool_type,
                    usage_count=random.randint(0, 500),
                    is_published=True,
                    access_mode="free",
                    points_cost=0,
                )
            )
        print(f"工具：{len(TOOLS)} 条")

        # ---------- 扩展 ----------
        for name, category, desc in PLUGINS:
            db.add(
                Plugin(
                    name=f"{MARK}{name}",
                    category=category,
                    description=desc,
                    content=f"<p>{desc}。演示扩展。</p>",
                    view_count=random.randint(20, 400),
                    is_published=True,
                )
            )
        print(f"扩展：{len(PLUGINS)} 条")

        # ---------- 评论 ----------
        comments = [
            "写得很清楚，跟着做一遍就通了。",
            "第三章的截图能再清晰一点吗？",
            "有没有配套的工作流文件？",
            "按这个参数跑出来效果很稳，感谢。",
            "希望能补充一下显存不足时的降级方案。",
        ]
        for idx, tutorial in enumerate(tutorials):
            for c_idx in range(3):
                db.add(
                    ContentComment(
                        target_type="tutorial",
                        target_id=tutorial.id,
                        user_id=users[(idx + c_idx) % len(users)].id,
                        content=comments[(idx + c_idx) % len(comments)],
                    )
                )
        print(f"评论：{len(tutorials) * 3} 条")

        # ---------- 反馈 ----------
        for idx, (content, contact) in enumerate(FEEDBACKS):
            db.add(
                Feedback(
                    user_id=users[idx % len(users)].id,
                    content=content,
                    contact=contact,
                    status="pending",
                )
            )
        print(f"反馈：{len(FEEDBACKS)} 条")

        # ---------- 公告 ----------
        for idx, (title, content, tag) in enumerate(ANNOUNCEMENTS):
            db.add(
                Announcement(
                    title=f"{MARK}{title}",
                    content=f"<p>{content}</p>",
                    tag=tag,
                    is_published=True,
                    published_at=now - timedelta(days=idx),
                )
            )
        print(f"公告：{len(ANNOUNCEMENTS)} 条")

        # ---------- 积分流水 ----------
        tx_types = ["consume", "redeem", "admin_set", "register"]
        for idx, user in enumerate(users):
            balance = 200
            for k in range(6):
                delta = random.choice([20, -10, 50, -5, 100])
                balance += delta
                db.add(
                    PointTransaction(
                        user_id=user.id,
                        points=delta,
                        balance_after=balance,
                        type=tx_types[(idx + k) % len(tx_types)],
                        description="演示流水：工具消耗" if delta < 0 else "演示流水：签到奖励",
                        operator_id=admin.id if admin and tx_types[(idx + k) % len(tx_types)] == "admin_set" else None,
                        created_at=now - timedelta(days=k, hours=idx),
                    )
                )
        print(f"积分流水：{len(users) * 6} 条")

        await db.commit()

    await engine.dispose()
    print("\n✅ 演示数据灌入完成。")
    print(f"   演示用户：{USERS[0][0]} 等 {len(USERS)} 个，口令 {DEMO_PASSWORD}")
    print("   管理员请用安装向导创建的账号登录。")


def main() -> None:
    parser = argparse.ArgumentParser(description="灌入演示数据（幂等，可安全重复执行）")
    parser.add_argument("--yes", action="store_true", help="确认在已有真实内容的库中追加演示数据")
    args = parser.parse_args()
    asyncio.run(seed(check_only=args.yes))


if __name__ == "__main__":
    main()
