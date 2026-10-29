
"""公司、品牌、集團的獨立金庫與資金調度。

整合既有 accounts / ledger_transactions / ledger_entries。
不建立平行的餘額系統，避免兩份餘額不一致。

安全規則：
- 持有人可以設定財務管理層的支出上限。
- 管理層需要 finance 權限。
- 未設定上限的管理層不得支出。
- 每筆轉帳都有唯一交易鍵與審計紀錄。
- 僅允許轉往同伺服器有效公司或角色。
"""

from __future__ import annotations

import json
import time
import uuid

from core.database import (
    account_balance,
    get_connection,
    get_or_create_account,
    transaction,
)
from core.economy import _payment
from modules.company_business import (
    has_permission,
    initialize as initialize_business,
)


def initialize_treasury():
    initialize_business()

    with get_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_company_finance_limits (
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                manager_user_id TEXT NOT NULL,
                max_per_transaction INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (
                    guild_id, business_id, manager_user_id
                )
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_company_finance_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reference_key TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                target_type TEXT,
                target_id TEXT,
                note TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                UNIQUE (guild_id, reference_key)
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_company_finance_audit_recent
            ON rp_company_finance_audit (
                guild_id, business_id, created_at
            )
        """)


def _entity(db, guild_id, business_id):
    row = db.execute("""
        SELECT *
        FROM rp_business_entities
        WHERE guild_id = ?
          AND business_id = ?
          AND status = 'active'
    """, (
        str(guild_id),
        str(business_id),
    )).fetchone()

    if row is None:
        raise ValueError("公司不存在或目前不可操作。")

    return row


def _is_owner(db, guild_id, business_id, user_id):
    entity = _entity(db, guild_id, business_id)
    return str(entity["owner_id"]) == str(user_id)


def _finance_permission(db, guild_id, business_id, user_id):
    if _is_owner(db, guild_id, business_id, user_id):
        return True

    return has_permission(
        db,
        str(guild_id),
        str(business_id),
        str(user_id),
        "finance",
    )


def _manager_limit(db, guild_id, business_id, user_id):
    row = db.execute("""
        SELECT max_per_transaction
        FROM rp_company_finance_limits
        WHERE guild_id = ?
          AND business_id = ?
          AND manager_user_id = ?
    """, (
        str(guild_id),
        str(business_id),
        str(user_id),
    )).fetchone()

    return int(row["max_per_transaction"]) if row else 0


def _treasury_account(db, guild_id, business_id):
    _entity(db, guild_id, business_id)

    return get_or_create_account(
        db,
        str(guild_id),
        "organization",
        str(business_id),
        "treasury",
    )


def treasury_balance(guild_id, business_id, actor_id):
    """只有持有人或具 finance 權限者能查看完整帳務。"""

    initialize_treasury()

    with get_connection() as db:
        if not _finance_permission(
            db, guild_id, business_id, actor_id
        ):
            raise PermissionError("沒有財務查看權限。")

        account_id = _treasury_account(
            db, guild_id, business_id
        )

        balance = int(
            account_balance(db, str(guild_id), account_id)
        )

        return balance


def company_bank_summary(guild_id, business_id, actor_id):
    """只顯示已獲官方核准開立的公司銀行帳戶。"""
    initialize_treasury()
    from modules.application_extras import init_db
    init_db()
    with get_connection() as db:
        if not _finance_permission(db, guild_id, business_id, actor_id):
            raise PermissionError("沒有公司銀行帳戶查看權限。")
        record = db.execute(
            "SELECT account_id,initial_deposit,opened_at "
            "FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?",
            (str(guild_id), str(business_id)),
        ).fetchone()
        if not record:
            raise ValueError("此公司尚未通過銀行帳戶申請。")
        bank = int(account_balance(db, guild_id, record["account_id"]))
        treasury = int(account_balance(db, guild_id, _treasury_account(db, guild_id, business_id)))
        return (
            f"🏦 **公司銀行帳戶｜{business_id}**\n"
            f"銀行餘額：**{bank:,} 金幣**\n"
            f"公司金庫：{treasury:,} 金幣\n"
            f"核准開戶首存：{record['initial_deposit']:,} 金幣\n"
            f"開戶時間：{record['opened_at']}"
        )


def move_company_bank(guild_id, business_id, actor_id, direction, amount, unique_key):
    """僅在同一家公司金庫與已開立的銀行帳戶間轉帳。"""
    from modules.application_extras import init_db
    init_db()
    initialize_treasury()
    amount = int(amount)
    if amount <= 0:
        raise ValueError("轉帳金額必須大於零。")
    if direction not in ("deposit", "withdraw"):
        raise ValueError("銀行操作類型不正確。")
    with transaction() as db:
        if not _finance_permission(db, guild_id, business_id, actor_id):
            raise PermissionError("沒有公司財務操作權限。")
        if not _is_owner(db, guild_id, business_id, actor_id):
            limit = _manager_limit(db, guild_id, business_id, actor_id)
            if limit <= 0 or amount > limit:
                raise PermissionError("超過管理員單筆額度，或尚未設定支出上限。")
        record = db.execute(
            "SELECT account_id FROM rp_company_bank_accounts "
            "WHERE guild_id=? AND company_id=?",
            (str(guild_id), str(business_id)),
        ).fetchone()
        if not record:
            raise ValueError("此公司尚未開立銀行帳戶。")
        treasury = _treasury_account(db, guild_id, business_id)
        bank = int(record["account_id"])
        sender, receiver = (treasury, bank) if direction == "deposit" else (bank, treasury)
        result = _payment(
            db, str(guild_id), str(unique_key), sender, receiver, amount,
            f"公司 {business_id} 銀行{'存入' if direction == 'deposit' else '提取'}",
        )
        if result.duplicated:
            return "ℹ️ 此筆銀行轉帳已執行，不會重複扣款。"
        db.execute(
            "INSERT INTO rp_company_finance_audit "
            "(guild_id,business_id,actor_id,action,reference_key,amount,"
            "target_type,target_id,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(guild_id), str(business_id), str(actor_id),
             f"bank_{direction}", str(unique_key), amount, "organization",
             str(business_id), "公司金庫與銀行帳戶間移轉", int(time.time())),
        )
        remaining = int(account_balance(db, guild_id, bank))
    return f"✅ 銀行{'存入' if direction == 'deposit' else '提取'} {amount:,} 金幣成功。\n銀行餘額：{remaining:,} 金幣"


def set_manager_spending_limit(
    guild_id,
    business_id,
    actor_id,
    manager_user_id,
    amount,
):
    """只有公司持有人可設定管理層單筆支出上限。"""

    initialize_treasury()

    amount = int(amount)

    if amount < 0:
        raise ValueError("支出上限不能是負數。")

    now = int(time.time())

    with transaction() as db:
        if not _is_owner(
            db, guild_id, business_id, actor_id
        ):
            raise PermissionError(
                "只有公司持有人能設定財務額度。"
            )

        manager = db.execute("""
            SELECT 1
            FROM rp_business_managers
            WHERE guild_id = ?
              AND business_id = ?
              AND user_id = ?
              AND status = 'active'
            LIMIT 1
        """, (
            str(guild_id),
            str(business_id),
            str(manager_user_id),
        )).fetchone()

        if not manager:
            raise ValueError(
                "指定玩家不是這間公司已接受任命的管理層。"
            )

        db.execute("""
            INSERT INTO rp_company_finance_limits (
                guild_id, business_id,
                manager_user_id,
                max_per_transaction,
                updated_by, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                guild_id, business_id, manager_user_id
            )
            DO UPDATE SET
                max_per_transaction =
                    excluded.max_per_transaction,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
        """, (
            str(guild_id),
            str(business_id),
            str(manager_user_id),
            amount,
            str(actor_id),
            now,
        ))

        db.execute("""
            INSERT INTO rp_company_finance_audit (
                guild_id, business_id, actor_id,
                action, reference_key,
                amount, target_type, target_id,
                note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(guild_id),
            str(business_id),
            str(actor_id),
            "set_spending_limit",
            f"limit:{uuid.uuid4().hex}",
            amount,
            "user",
            str(manager_user_id),
            "設定管理層單筆支出上限",
            now,
        ))

    return (
        f"✅ 已設定 <@{manager_user_id}> 的"
        f"單筆支出上限為 {amount:,} 金幣。"
    )


def _receiver_account(
    db,
    guild_id,
    target_type,
    target_id,
):
    """驗證收款對象，避免任意建立虛構付款對象。"""

    if target_type == "organization":
        _entity(db, guild_id, target_id)

        return get_or_create_account(
            db,
            str(guild_id),
            "organization",
            str(target_id),
            "treasury",
        )

    if target_type == "character":
        try:
            character_id = int(target_id)
        except (TypeError, ValueError):
            raise ValueError("角色編號必須是數字。")

        character = db.execute("""
            SELECT id
            FROM characters
            WHERE guild_id = ?
              AND id = ?
        """, (
            str(guild_id),
            character_id,
        )).fetchone()

        if not character:
            raise ValueError("找不到收款角色。")

        return get_or_create_account(
            db,
            str(guild_id),
            "character",
            str(character_id),
            "wallet",
        )

    raise ValueError("收款類型只能是公司或角色。")


def transfer_treasury(
    guild_id,
    business_id,
    actor_id,
    target_type,
    target_id,
    amount,
    note,
    unique_key=None,
):
    """實際轉帳並寫入雙向帳本。

    呼叫方應在已確認操作的情境傳入穩定 unique_key，
    避免 Discord 重試時重複付款。
    """

    initialize_treasury()

    amount = int(amount)
    note = str(note).strip()

    if amount <= 0:
        raise ValueError("轉帳金額必須大於零。")

    if not note:
        raise ValueError("必須填寫資金用途。")

    if len(note) > 300:
        raise ValueError("資金用途不能超過 300 字。")

    if target_type not in ("organization", "character"):
        raise ValueError("收款類型只能是公司或角色。")

    reference = (
        str(unique_key).strip()
        if unique_key
        else f"company-transfer:{uuid.uuid4().hex}"
    )

    with transaction() as db:
        if not _finance_permission(
            db, guild_id, business_id, actor_id
        ):
            raise PermissionError("你沒有資金調度權限。")

        is_owner = _is_owner(
            db, guild_id, business_id, actor_id
        )

        if not is_owner:
            spending_limit = _manager_limit(
                db, guild_id, business_id, actor_id
            )

            if spending_limit <= 0:
                raise PermissionError(
                    "持有人尚未設定你的支出上限。"
                )

            if amount > spending_limit:
                raise PermissionError(
                    f"單筆支出不得超過 "
                    f"{spending_limit:,} 金幣。"
                )

        sender_account = _treasury_account(
            db, guild_id, business_id
        )

        receiver_account = _receiver_account(
            db, guild_id, target_type, target_id
        )

        if sender_account == receiver_account:
            raise ValueError("不能轉帳給自己的金庫。")

        existing = db.execute("""
            SELECT id
            FROM rp_company_finance_audit
            WHERE guild_id = ?
              AND reference_key = ?
        """, (
            str(guild_id),
            reference,
        )).fetchone()

        if existing:
            return "ℹ️ 這筆轉帳已執行，不會再次扣款。"

        result = _payment(
            db,
            str(guild_id),
            reference,
            sender_account,
            receiver_account,
            amount,
            note,
        )

        if result.duplicated:
            return "ℹ️ 這筆轉帳已執行，不會再次扣款。"

        db.execute("""
            INSERT INTO rp_company_finance_audit (
                guild_id, business_id, actor_id,
                action, reference_key, amount,
                target_type, target_id,
                note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(guild_id),
            str(business_id),
            str(actor_id),
            "transfer",
            reference,
            amount,
            target_type,
            str(target_id),
            note,
            int(time.time()),
        ))

        balance = int(
            account_balance(
                db, str(guild_id), sender_account
            )
        )

    return (
        f"✅ 資金調度成功\n"
        f"來源公司：{business_id}\n"
        f"收款對象：{target_type} #{target_id}\n"
        f"金額：{amount:,} 金幣\n"
        f"用途：{note}\n"
        f"公司剩餘金庫：{balance:,} 金幣"
    )


def recent_treasury_records(
    guild_id,
    business_id,
    actor_id,
    limit=10,
):
    initialize_treasury()

    limit = max(1, min(int(limit), 20))

    with get_connection() as db:
        if not _finance_permission(
            db, guild_id, business_id, actor_id
        ):
            raise PermissionError("沒有帳務查看權限。")

        records = db.execute("""
            SELECT action, amount, target_type,
                   target_id, note, created_at,
                   actor_id
            FROM rp_company_finance_audit
            WHERE guild_id = ?
              AND business_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (
            str(guild_id),
            str(business_id),
            limit,
        )).fetchall()

    return [dict(row) for row in records]


def finance_summary(guild_id, business_id, actor_id):
    balance = treasury_balance(
        guild_id, business_id, actor_id
    )

    records = recent_treasury_records(
        guild_id, business_id, actor_id
    )

    lines = [
        f"🏦 **公司金庫｜{business_id}**",
        f"目前餘額：**{balance:,} 金幣**",
        "",
        "**最近財務異動**",
    ]

    if not records:
        lines.append("目前沒有財務調度紀錄。")
    else:
        for record in records:
            if record["action"] == "transfer":
                lines.append(
                    f"• 支出 {record['amount']:,}｜"
                    f"{record['target_type']} "
                    f"{record['target_id']}｜"
                    f"{record['note']}"
                )
            elif record["action"] == "set_spending_limit":
                lines.append(
                    f"• 額度設定 {record['amount']:,}｜"
                    f"管理員 {record['target_id']}"
                )

    return "\n".join(lines)[:1900]


async def setup(bot):
    """Discord 擴充模組入口；保留既有金庫函式供公司面板呼叫。"""
    import asyncio

    await asyncio.to_thread(initialize_treasury)
