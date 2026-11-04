"""权限点补种服务：把 PERMISSION_CATALOG 里**新增**的码补进已有部署的库。

为什么存在：权限点只在建表迁移时按当时的目录播种过一次，此后新增的码不会
自动出现在已有部署的库里 —— 代码里有接口、路由规则也登记了，但没有任何角色
持有它，管理员点进去直接 403。此前每个新码都要手写一条补种迁移
（0061/0064/0066...），同步逻辑在迁移间复制；收拢到这里后：

- 迁移只写 `asyncio.run(seed_permission_codes(["feedback:reply"]))`，一行；
- 同步语义只有一份（**只增不减**，与 0064 一致）；
- 逻辑在应用层，可被离线测试直接覆盖（迁移本体不参与 pytest）。

同步语义：
- 权限点：缺失则按目录插入；已存在则按目录刷新元信息（名称/模块等以代码为准）；
- 内置角色：只补齐目录声明中缺失的权限，**不回收**多余权限
  （内置角色可能被运营在界面上手动调整过，回收是二次破坏）。

调用方必须跑在迁移环境里（alembic upgrade 时）；自行建连接、自管事务，
结束后由 `upgrade()` 所在连接之外独立提交 —— 迁移链的原子性由 alembic 保证，
本服务的事务只覆盖补种本身。
"""

from __future__ import annotations

import secrets

from sqlalchemy import select

from app.database import async_session
from app.models.rbac import Permission, Role, RolePermission
from app.services.rbac_catalog import PERMISSION_CATALOG, SYSTEM_ROLES


def _gen_id() -> int:
    # 与 database.generate_id 同口径的 10 位随机 ID（防枚举），避免迁移环境
    # 再造一份实现时口径漂移
    return secrets.randbelow(9_000_000_000) + 1_000_000_000


async def seed_permission_codes(codes: tuple[str, ...] | list[str]) -> dict:
    """补种指定权限码并同步内置角色，返回 {inserted, updated, granted} 统计。"""
    wanted = set(codes)
    known = {p.code: p for p in PERMISSION_CATALOG if p.code in wanted}
    unknown = wanted - set(known)
    if unknown:
        raise ValueError(f"权限码不在 PERMISSION_CATALOG 中: {sorted(unknown)}")

    stats = {"inserted": 0, "updated": 0, "granted": 0}
    async with async_session() as db:
        perms = (await db.scalars(select(Permission))).all()
        perm_by_code = {p.code: p for p in perms}

        # 1) 权限点：缺失插入 / 已存在按代码目录刷新元信息
        for p in PERMISSION_CATALOG:
            if p.code not in wanted:
                continue
            row = perm_by_code.get(p.code)
            meta = dict(
                name=p.name, type=p.type, module=p.module,
                module_name=p.module_name, sort=p.sort,
            )
            if row is None:
                db.add(Permission(id=_gen_id(), code=p.code, **meta))
                stats["inserted"] += 1
            elif any(getattr(row, k) != v for k, v in meta.items()):
                for k, v in meta.items():
                    setattr(row, k, v)
                stats["updated"] += 1
        await db.flush()

        perms = (await db.scalars(select(Permission))).all()
        perm_by_code = {p.code: p for p in perms}
        roles = (await db.scalars(select(Role))).all()

        # 2) 内置角色：补齐目录声明中缺失的权限（不回收多余权限）
        for role in roles:
            target = next((r for r in SYSTEM_ROLES if r["code"] == role.code), None)
            if target is None:
                continue
            granted = {
                rp.permission_id
                for rp in (await db.scalars(
                    select(RolePermission).where(RolePermission.role_id == role.id)
                )).all()
            }
            for code in target["permissions"]:
                perm = perm_by_code.get(code)
                if perm is None or perm.id in granted:
                    continue
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
                stats["granted"] += 1
        await db.commit()
    return stats


async def revoke_permission_codes(codes: tuple[str, ...] | list[str]) -> int:
    """删除指定权限点及其角色关联（补种迁移的 downgrade 专用，不动其他权限）。"""
    removed = 0
    async with async_session() as db:
        perms = (
            await db.scalars(select(Permission).where(Permission.code.in_(list(codes))))
        ).all()
        for perm in perms:
            links = (
                await db.scalars(
                    select(RolePermission).where(RolePermission.permission_id == perm.id)
                )
            ).all()
            for link in links:
                await db.delete(link)
            await db.delete(perm)
            removed += 1
        await db.commit()
    return removed
