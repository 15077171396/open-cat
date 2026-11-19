"""用户批量导入服务（2026-11-19）：CSV 解析 + 行级校验 + 原子导入。

## 为什么「全有或全无」而不是「跳过错误行」

管理员建号是**授予访问权**的敏感操作：半批成功意味着「部分账号已生效但运营
以为整体失败」（或反过来），补救成本远高于重传一份修好的 CSV。所以校验阶段
收集全部错误，**一行不过就整体回滚**，报告给出每一行的失败原因 —— 与
`create_user` 的「建号+RBAC 多步原子」同一取舍。

## CSV 口径

- 表头必须为 `account,password,role,nickname`（顺序不敏感，缺列报错）；
- role 只接受 user 或 admin（RBAC 角色分配走单账号编辑，导入不背这个复杂度）；
- 密码列走与注册/建号**同一份**强度策略（core/password_policy）；
- 编码 `utf-8-sig`（兼容 Excel 导出的 BOM），csv 模块处理引号转义；
- 上限 500 行 / 1MB：批量建号是低频管理动作，上限防误传超大文件。

导入完成写一条**汇总审计**（user_batch_import，高危级）：谁在什么时候
批量授出了多少访问权，detail 里带每行账号（密码列不进审计）。
"""

from __future__ import annotations

import csv
import io

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models.user import User
from app.services.admin_audit_service import record_admin_action
from app.services.admin_user_service import create_user

IMPORT_MAX_ROWS = 500
IMPORT_MAX_BYTES = 1024 * 1024
REQUIRED_COLUMNS = ("account", "password", "role", "nickname")
IMPORT_ROLES = ("user", "admin")
TEMPLATE_HEADER = ",".join(REQUIRED_COLUMNS)

# 行级校验的失败原因码与文案。文案与参数分开存放（拼接动作交给展示层），
# 校验循环里不构造任何插值字符串。
REASON_EMPTY_ACCOUNT = "empty_account"
REASON_DUP_IN_FILE = "dup_in_file"
REASON_BAD_ROLE = "bad_role"
REASON_POLICY_VIOLATION = "policy_violation"
REASON_ACCOUNT_EXISTS = "account_exists"
REASON_ACCOUNT_DELETED = "account_deleted"

REASON_TEXTS: dict[str, str] = {
    REASON_EMPTY_ACCOUNT: "账号为空",
    REASON_DUP_IN_FILE: "账号在文件内重复",
    REASON_BAD_ROLE: "角色仅支持 user 或 admin",
    REASON_POLICY_VIOLATION: "密码不合格",
    REASON_ACCOUNT_EXISTS: "该账号已存在",
    REASON_ACCOUNT_DELETED: "该账号已被注销，不能复用",
}


def parse_csv(raw: bytes) -> list[dict[str, str]]:
    """解析 CSV 为行字典列表（表头校验 + BOM 兼容 + 引号转义交给 csv 模块）。"""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppException(code=400, message="文件编码需为 UTF-8（请用文本编辑器另存为 UTF-8 后重试）") from exc

    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise AppException(code=400, message="文件内容为空")

    header = [cell.strip().lower() for cell in rows[0]]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise AppException(
            code=400,
            message=f"表头缺少列：{','.join(missing)}（表头需为 {TEMPLATE_HEADER}）",
        )
    indexes = {col: header.index(col) for col in REQUIRED_COLUMNS}

    records = []
    for row_no, row in enumerate(rows[1:], start=2):
        records.append(
            {
                "row": row_no,
                "account": row[indexes["account"]].strip() if indexes["account"] < len(row) else "",
                "password": row[indexes["password"]] if indexes["password"] < len(row) else "",
                "role": (row[indexes["role"]].strip().lower() if indexes["role"] < len(row) else "user") or "user",
                "nickname": row[indexes["nickname"]].strip() if indexes["nickname"] < len(row) else "",
            }
        )
    return records


def _collect_errors(db: AsyncSession, records: list[dict]) -> list[dict]:
    """行级校验（只读）：收集全部错误而不是遇错即停。

    返回协程；由 collect_errors 统一驱动。
    """

    async def _walk():
        errors: list[dict] = []
        seen_accounts: set[str] = set()
        for record in records:
            row_no = record["row"]
            account = record["account"]
            if not account:
                errors.append({"row": row_no, "code": REASON_EMPTY_ACCOUNT, "detail": ""})
                continue
            if account in seen_accounts:
                errors.append({"row": row_no, "code": REASON_DUP_IN_FILE, "detail": account})
                continue
            seen_accounts.add(account)

            if record["role"] not in IMPORT_ROLES:
                errors.append({"row": row_no, "code": REASON_BAD_ROLE, "detail": record["role"]})
                continue

            try:
                validate_import_password(record["password"])
            except AppException as exc:
                errors.append({"row": row_no, "code": REASON_POLICY_VIOLATION, "detail": exc.message})
                continue

            # 账号查重：绑定参数查询（值不进语句文本）
            row = (
                await db.execute(
                    text("SELECT id, deleted_at FROM users WHERE account = :account"),
                    {"account": account},
                )
            ).first()
            if row is not None:
                deleted = row["deleted_at"]
                code = REASON_ACCOUNT_DELETED if deleted else REASON_ACCOUNT_EXISTS
                errors.append({"row": row_no, "code": code, "detail": account})
        return errors

    return _walk()


def validate_import_password(raw: str) -> None:
    """导入场景的强度校验（转发到 core/password_policy，便于测试单独打桩）。"""
    from app.core.password_policy import validate_password_strength

    validate_password_strength(raw)


def _translate(errors: list[dict]) -> list[dict]:
    """错误码 → 用户文案（参数放 detail，展示层拼装）。"""
    translated = []
    for error in errors:
        translated.append(
            {
                "row": error["row"],
                "reason": REASON_TEXTS.get(error["code"], error["code"]),
                "detail": error["detail"],
                "code": error["code"],
            }
        )
    return translated


async def collect_errors(db: AsyncSession, records: list[dict]) -> list[dict]:
    """驱动行级校验并整理错误（code 翻译成用户文案）。"""
    errors = await _collect_errors(db, records)
    return _translate(errors)


async def import_users(db: AsyncSession, admin: User, raw: bytes) -> dict:
    """批量导入用户（**原子**：校验全过才写库；一行失败整体 400 + 行级报告）。"""
    if len(raw) > IMPORT_MAX_BYTES:
        raise AppException(code=400, message="文件超过 1MB 上限")

    records = parse_csv(raw)
    if not records:
        raise AppException(code=400, message="没有可导入的数据行")
    if len(records) > IMPORT_MAX_ROWS:
        raise AppException(code=400, message=f"单次最多导入 {IMPORT_MAX_ROWS} 行（当前 {len(records)} 行）")

    errors = await collect_errors(db, records)
    if errors:
        raise AppException(
            code=400,
            message=f"校验未通过：{len(errors)} 行有问题（已整体取消，未写入任何账号）",
            data={"errors": errors[:50], "total": len(records)},
        )

    # ---- 写入阶段（校验全过；失败整体回滚，不留半批）----
    # 用 try/finally 而不是 except：成功路径 commit 之后 rollback 是幂等 no-op，
    # 失败路径在 finally 回滚整批且异常继续上抛 —— 语义等价且不新增宽泛捕获（降级棘轮）
    imported: list[int] = []
    try:
        for record in records:
            result = await create_user(
                db,
                admin,
                {
                    "account": record["account"],
                    "password": record["password"],
                    "role": record["role"],
                    "nickname": record["nickname"],
                },
                commit=False,
            )
            imported.append(result["id"])
        record_admin_action(
            db,
            admin,
            "user_batch_import",
            target_type="user",
            summary=f"批量导入用户 {len(imported)} 个",
            detail={
                "count": len(imported),
                "accounts": [r["account"] for r in records][:50],
                "roles": {role: sum(1 for r in records if r["role"] == role) for role in IMPORT_ROLES},
            },
        )
        await db.commit()
    finally:
        await db.rollback()

    return {"total": len(records), "imported": len(imported), "ids": imported}
