"""迁移脚本：扫描所有 content 字段中的 base64 图片，上传到 COS 并以 URL 替换。

适用模型：Tutorial / Resource / Node / Plugin

用法（在 backend 目录下）：
    python migrate_base64_v2.py            # 仅扫描，不修改（dry-run，打印报告）
    python migrate_base64_v2.py --apply    # 实际执行迁移

环境要求：
    - 已配置 .env 中的 COS 凭证（COS_SECRET_ID / COS_SECRET_KEY / COS_BUCKET）
    - 数据库连接可用（DATABASE_URL 或 MySQL 单独配置）
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import re
import sys
from typing import Optional

# 让脚本既能直接运行（python migrate_base64_v2.py），也能在容器内运行
# 通过相对路径导入 app 包
sys.path.insert(0, '.')

from sqlalchemy import select, update

from app.database import AsyncSessionLocal, engine
from app.models.tutorial import Tutorial
from app.models.resource import Resource
from app.models.node import Node
from app.models.plugin import Plugin
from app.services.cos_service import upload_bytes, CosError
from loguru import logger


# 匹配 HTML 中的 base64 图片标签：<img ... src="data:image/png;base64,xxxxx" ...>
BASE64_IMG_PATTERN = re.compile(
    r'<img([^>]+)src="data:image/([a-zA-Z+]+);base64,([^"]+)"([^>]*)>',
    re.IGNORECASE,
)

# MIME 类型 → 文件扩展名映射
MIME_TO_EXT = {
    "png": (".png", "image/png"),
    "jpeg": (".jpg", "image/jpeg"),
    "jpg": (".jpg", "image/jpeg"),
    "gif": (".gif", "image/gif"),
    "webp": (".webp", "image/webp"),
    "x-icon": (".ico", "image/x-icon"),
    "svg+xml": (".svg", "image/svg+xml"),
}


def _build_replacement_img(pre_attrs: str, url: str, post_attrs: str) -> str:
    """重建 img 标签，仅保留 src、alt，舍弃其他可能携带风险的属性。"""
    return f'<img{pre_attrs}src="{url}" alt=""{post_attrs}>'


async def _migrate_one_text(text: Optional[str], dry_run: bool, model_name: str, row_id) -> tuple[int, Optional[str]]:
    """处理单条 content 字段。

    :return: (替换的图片数量, 新 content；若无需替换，返回 (0, None))
    """
    if not text:
        return 0, None

    matches = list(BASE64_IMG_PATTERN.finditer(text))
    if not matches:
        return 0, None

    if dry_run:
        logger.info(f"  [{model_name} id={row_id}] 发现 {len(matches)} 张 base64 图片（dry-run，不修改）")
        return len(matches), None

    # 串行上传并替换，保证顺序一致
    new_text = text
    replaced = 0
    for m in matches:
        pre_attrs, ext_raw, b64, post_attrs = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        ext, content_type = MIME_TO_EXT.get(ext_raw, (".png", "image/png"))

        try:
            # base64 解码为二进制
            data = base64.b64decode(b64)
        except Exception as exc:
            logger.warning(f"  [{model_name} id={row_id}] base64 解码失败，跳过: {exc}")
            continue

        try:
            url = await upload_bytes(data, folder="migrate", ext=ext, content_type=content_type)
        except CosError as exc:
            logger.error(f"  [{model_name} id={row_id}] COS 上传失败: {exc}")
            continue
        except Exception as exc:
            logger.error(f"  [{model_name} id={row_id}] 上传异常: {exc}")
            continue

        # 替换原 base64 标签为远程 URL 标签
        new_text = new_text.replace(m.group(0), _build_replacement_img(pre_attrs, url, post_attrs))
        replaced += 1
        logger.info(f"  [{model_name} id={row_id}] 上传成功: {len(data)} bytes → {url}")

    if replaced == 0:
        return 0, None
    return replaced, new_text


async def _migrate_model(session, model, model_name: str, dry_run: bool) -> tuple[int, int]:
    """迁移单个模型的所有行。

    :return: (扫描行数, 替换图片总数)
    """
    result = await session.execute(select(model))
    rows = result.scalars().all()
    logger.info(f"\n[{model_name}] 扫描 {len(rows)} 行...")

    scanned = 0
    total_replaced = 0

    for row in rows:
        scanned += 1
        if not getattr(row, "content", None):
            continue

        replaced, new_content = await _migrate_one_text(row.content, dry_run, model_name, row.id)
        total_replaced += replaced

        if not dry_run and new_content is not None:
            await session.execute(
                update(model).where(model.id == row.id).values(content=new_content)
            )

    if not dry_run:
        await session.commit()

    logger.info(f"[{model_name}] 扫描完成: {scanned} 行, 替换 {total_replaced} 张图片")
    return scanned, total_replaced


async def main(dry_run: bool):
    logger.info("=" * 60)
    logger.info(f"base64 图片迁移脚本 v2 ({'DRY-RUN' if dry_run else 'APPLY'})")
    logger.info("=" * 60)

    async with AsyncSessionLocal() as session:
        models = [
            (Tutorial, "Tutorial"),
            (Resource, "Resource"),
            (Node, "Node"),
            (Plugin, "Plugin"),
        ]

        total_scanned = 0
        total_replaced = 0

        for model, name in models:
            scanned, replaced = await _migrate_model(session, model, name, dry_run)
            total_scanned += scanned
            total_replaced += replaced

        logger.info("\n" + "=" * 60)
        logger.info(f"汇总: 扫描 {total_scanned} 行, 替换 {total_replaced} 张 base64 图片")
        if dry_run:
            logger.info("（dry-run 模式，未实际修改数据；确认无误后用 --apply 重新运行）")
        else:
            logger.info("迁移完成。")
        logger.info("=" * 60)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="迁移 content 字段中的 base64 图片到 COS")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行迁移（默认为 dry-run 模式，仅扫描不修改）",
    )
    args = parser.parse_args()

    asyncio.run(main(dry_run=not args.apply))
