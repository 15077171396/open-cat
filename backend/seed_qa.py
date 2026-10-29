"""QA 各态种子数据（**只在开发/测试库跑**）。

为什么需要：2026-10-29 全链路测试时，库里 6 篇教程只有 2 篇已发布、13 个账号只有 2 个
未注销 —— 付费墙、筛选、分页、审核队列只能验到"接口不报错"，验不到"数据对不对"。
本脚本把「免费 / 会员 / 已购买 / 草稿 / 回收站」五种状态各造一条，用完可一键清理。

用法（在 backend/ 下）：

    ./venv/Scripts/python.exe seed_qa.py            # 预览将要做的事（默认 dry-run）
    ./venv/Scripts/python.exe seed_qa.py --apply    # 真正写入
    ./venv/Scripts/python.exe seed_qa.py --clean    # 删掉本脚本造的数据

约定：
- 所有内容标题以 `【QA】` 开头，便于识别与清理；
- 幂等：已存在（同标题 / 同购买记录）就跳过；
- `APP_ENV=production` 直接拒绝执行（避免误在线上造内容）。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.database import async_session, engine
from app.models.moderation import ContentReport
from app.models.tutorial import Tutorial, TutorialPurchase
from app.models.user import User

PREFIX = "【QA】"
HQ_ACCOUNT = "test@aicat.com"  # 购买态要挂在一个真实存在的普通账号上

UTC = timezone.utc


def _now() -> datetime:
    """写库统一 aware UTC（全仓约定，见 backend/README 时区一节）"""
    return datetime.now(UTC)


def _tutorial_states() -> list[dict]:
    """五种状态。`published_at` 只有已发布的才给值 —— 列表按它排序。"""
    return [
        {
            "title": f"{PREFIX}免费教程（已发布）",
            "description": "免费阅读态：登录即可看全文",
            "is_published": True,
            "status": "completed",
            "access_mode": "free",
            "points_price": 0,
            "published_at": _now() - timedelta(days=3),
        },
        {
            "title": f"{PREFIX}会员教程（已发布·未购买）",
            "description": "会员专享态：非会员只能看试读，可用积分购买",
            "is_published": True,
            "status": "completed",
            "access_mode": "vip",
            "points_price": 100,
            "published_at": _now() - timedelta(days=2),
        },
        {
            "title": f"{PREFIX}会员教程（已发布·已购买）",
            "description": "已购买态：应能直接读全文（购买记录由本脚本补）",
            "is_published": True,
            "status": "completed",
            "access_mode": "vip",
            "points_price": 50,
            "published_at": _now() - timedelta(days=1),
            "_purchase_by": HQ_ACCOUNT,
        },
        {
            "title": f"{PREFIX}草稿教程（未发布）",
            "description": "草稿态：公开列表必须看不到，收藏必须被拒",
            "is_published": False,
            "status": "updating",
            "access_mode": "vip",
            "points_price": 0,
            "published_at": None,
        },
        {
            "title": f"{PREFIX}回收站教程（已删除）",
            "description": "软删态：公开端不可见，后台回收站可见",
            "is_published": False,
            "status": "updating",
            "access_mode": "free",
            "points_price": 0,
            "published_at": None,
            "_deleted": True,
        },
    ]


async def _ensure_user(db, account: str) -> User | None:
    return (
        await db.execute(select(User).where(User.account == account))
    ).scalar_one_or_none()


async def seed(apply: bool) -> None:
    async with async_session() as db:
        buyer = await _ensure_user(db, HQ_ACCOUNT)
        if buyer is None:
            print(f"⚠ 找不到账号 {HQ_ACCOUNT}，'已购买' 态会跳过（先跑 seed_more.py）")

        todo: list[dict] = []
        skipped: list[str] = []
        for state in _tutorial_states():
            exists = (
                await db.execute(select(Tutorial).where(Tutorial.title == state["title"]))
            ).scalar_one_or_none()
            if exists:
                skipped.append(state["title"])
            else:
                todo.append(dict(state))

        report_exists = (
            await db.execute(
                select(ContentReport).where(ContentReport.target_id == "qa-seed")
            )
        ).scalar_one_or_none()
        need_report = report_exists is None and buyer is not None

        if not apply:
            print("== dry-run（加 --apply 才会写库）==")
            for state in todo:
                print("  将创建:", state["title"])
            for title in skipped:
                print("  已存在，跳过:", title)
            if need_report:
                print("  将创建: 待处理举报 ×1")
            print(f"合计：新建 {len(todo)}，跳过 {len(skipped)}")
            return

        for state in todo:
            purchase_by = state.pop("_purchase_by", None)
            deleted = state.pop("_deleted", False)
            tutorial = Tutorial(
                title=state["title"],
                description=state["description"],
                introduction="<p>QA 种子数据</p>",
                category="QA",
                content=state["description"] + "\n\n## 正文\n\n占位内容。",
                cover_image=None,
                view_count=0,
                status=state["status"],
                is_published=state["is_published"],
                access_mode=state["access_mode"],
                points_price=state["points_price"],
                published_at=state["published_at"],
                deleted_at=_now() if deleted else None,
            )
            db.add(tutorial)
            # 主键是客户端默认值（generate_id），flush 之后才落到对象上；
            # 购买记录要用它，所以必须先 flush（真实会话同样如此）
            await db.flush()
            if purchase_by and buyer is not None:
                db.add(
                    TutorialPurchase(
                        user_id=buyer.id,
                        tutorial_id=tutorial.id,
                        points_paid=tutorial.points_price,
                    )
                )

        if need_report:
            db.add(
                ContentReport(
                    reporter_id=buyer.id,
                    content_type="tutorial",
                    target_id="qa-seed",
                    reason="spam",
                    detail="QA 种子举报（可忽略，用于验证审核台）",
                    status="pending",
                )
            )

        await db.commit()
        print(f"✅ 已写入 {len(todo)} 篇教程" + ("、1 条待处理举报" if need_report else ""))
        for state in todo:
            print("  +", state["title"])
        for title in skipped:
            print("  =", title, "（已存在）")


async def clean(apply: bool) -> None:
    async with async_session() as db:
        rows = (
            await db.execute(select(Tutorial).where(Tutorial.title.startswith(PREFIX)))
        ).scalars().all()
        reports = (
            await db.execute(select(ContentReport).where(ContentReport.target_id == "qa-seed"))
        ).scalars().all()

        print(f"匹配到 {len(rows)} 篇 {PREFIX} 教程、{len(reports)} 条 QA 举报")
        if not apply:
            for t in rows:
                print("  将删除:", t.title)
            print("== dry-run（加 --clean 时默认就是 dry-run，加 --apply 才真删）==")
            return

        for t in rows:
            purchases = (
                await db.execute(
                    select(TutorialPurchase).where(TutorialPurchase.tutorial_id == t.id)
                )
            ).scalars().all()
            for p in purchases:
                await db.delete(p)
            await db.delete(t)
        for r in reports:
            await db.delete(r)
        await db.commit()
        print("✅ 已清理")


async def main() -> None:
    parser = argparse.ArgumentParser(description="QA 各态种子数据（仅开发/测试库）")
    parser.add_argument("--apply", action="store_true", help="真正写入 / 真正删除")
    parser.add_argument("--clean", action="store_true", help="清理本脚本造的数据")
    args = parser.parse_args()

    if settings.APP_ENV == "production":
        raise SystemExit("拒绝执行：APP_ENV=production。本脚本只用于开发/测试库。")

    try:
        if args.clean:
            await clean(args.apply)
        else:
            await seed(args.apply)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
