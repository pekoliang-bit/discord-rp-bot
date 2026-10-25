
"""Discord RP 共用財務核心。

功能：
- 各伺服器獨立帳戶
- 雙向分錄
- 原子轉帳
- 不允許一般帳戶透支
- 相同交易編號防止重複結算
- 薪資、合約及其他款項託管
- 交易紀錄查詢

注意：
這是內部服務，不是供玩家直接呼叫的發錢指令。
金額與付款資格由其他業務模組判定。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from core.database import (
    get_connection,
    get_or_create_account,
    account_balance,
    transaction,
)


MAX_AMOUNT = 10**15


class EconomyError(ValueError):
    """財務操作無法執行。"""


class InsufficientFunds(EconomyError):
    """帳戶餘額不足。"""


class DuplicateTransaction(EconomyError):
    """交易編號已被其他交易使用。"""


@dataclass(frozen=True)
class PaymentResult:
    transaction_id: int
    amount: int
    duplicated: bool
    sender_balance: int
    receiver_balance: int


def _guild_id(value) -> str:
    result = str(value).strip()

    if not result or len(result) > 100:
        raise EconomyError(
            "伺服器編號不合法。"
        )

    return result


def _owner_type(value: str) -> str:
    allowed = {
        "character",
        "player",
        "organization",
        "production",
        "school",
        "system",
        "escrow",
    }

    if value not in allowed:
        raise EconomyError(
            "不支援的帳戶擁有者類型。"
        )

    return value


def _account_type(value: str) -> str:
    allowed = {
        "wallet",
        "bank",
        "treasury",
        "escrow",
        "reserve",
    }

    if value not in allowed:
        raise EconomyError(
            "不支援的帳戶種類。"
        )

    return value


def _amount(value) -> int:
    # bool 在 Python 中是 int 的子類，
    # 因此必須使用 type 進行檢查。
    if type(value) is not int:
        raise EconomyError(
            "金額必須是整數。"
        )

    if value <= 0 or value > MAX_AMOUNT:
        raise EconomyError(
            "金額必須是合法的正整數。"
        )

    return value


def _key(value: str) -> str:
    result = str(value).strip()

    if not result or len(result) > 200:
        raise EconomyError(
            "交易唯一編號不可空白且不能過長。"
        )

    return result


def _account(
    conn,
    guild_id: str,
    account_id: int,
):
    row = conn.execute(
        """
        SELECT
            id,
            guild_id,
            owner_type,
            owner_id,
            account_type
        FROM accounts
        WHERE guild_id = ?
          AND id = ?
        """,
        (guild_id, account_id),
    ).fetchone()

    if row is None:
        raise EconomyError(
            "找不到此伺服器的指定帳戶。"
        )

    return row


def _fingerprint(
    sender_id: int,
    receiver_id: int,
    amount: int,
) -> str:
    payload = json.dumps(
        [
            int(sender_id),
            int(receiver_id),
            amount,
        ],
        separators=(",", ":"),
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def _description(
    text: str,
    fingerprint: str,
) -> str:
    # 在既有 ledger_transactions.description 欄位
    # 儲存交易摘要及請求指紋，不需修改資料表。
    return json.dumps(
        {
            "text": str(text)[:500],
            "fingerprint": fingerprint,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _existing_payment(
    conn,
    guild_id: str,
    unique_key: str,
    fingerprint: str,
):
    row = conn.execute(
        """
        SELECT id, description
        FROM ledger_transactions
        WHERE guild_id = ?
          AND unique_key = ?
        """,
        (guild_id, unique_key),
    ).fetchone()

    if row is None:
        return None

    try:
        details = json.loads(
            row["description"]
        )
    except (TypeError, ValueError):
        raise DuplicateTransaction(
            "此交易編號已被舊格式交易占用。"
        )

    if (
        not isinstance(details, dict)
        or details.get("fingerprint")
        != fingerprint
    ):
        raise DuplicateTransaction(
            "同一交易編號不可用於不同付款。"
        )

    return row["id"]


def _payment(
    conn,
    guild_id: str,
    unique_key: str,
    sender_id: int,
    receiver_id: int,
    amount: int,
    description: str = "",
) -> PaymentResult:
    """所有付款共用的原子交易操作。"""

    guild_id = _guild_id(guild_id)
    unique_key = _key(unique_key)
    amount = _amount(amount)

    sender_id = int(sender_id)
    receiver_id = int(receiver_id)

    if sender_id == receiver_id:
        raise EconomyError(
            "不能轉帳到同一個帳戶。"
        )

    sender = _account(
        conn, guild_id, sender_id
    )

    _account(
        conn, guild_id, receiver_id
    )

    fingerprint = _fingerprint(
        sender_id,
        receiver_id,
        amount,
    )

    previous_id = _existing_payment(
        conn,
        guild_id,
        unique_key,
        fingerprint,
    )

    if previous_id is not None:
        return PaymentResult(
            transaction_id=previous_id,
            amount=amount,
            duplicated=True,
            sender_balance=account_balance(
                conn,
                guild_id,
                sender_id,
            ),
            receiver_balance=account_balance(
                conn,
                guild_id,
                receiver_id,
            ),
        )

    sender_balance = account_balance(
        conn,
        guild_id,
        sender_id,
    )

    if (
        sender["owner_type"] != "system"
        and sender_balance < amount
    ):
        raise InsufficientFunds(
            f"帳戶餘額不足。"
            f"目前有 {sender_balance} 金幣，"
            f"需要 {amount} 金幣。"
        )

    cursor = conn.execute(
        """
        INSERT INTO ledger_transactions (
            guild_id,
            unique_key,
            description
        )
        VALUES (?, ?, ?)
        """,
        (
            guild_id,
            unique_key,
            _description(
                description,
                fingerprint,
            ),
        ),
    )

    transaction_id = cursor.lastrowid

    # 支出與收入合計為零。
    conn.executemany(
        """
        INSERT INTO ledger_entries (
            guild_id,
            transaction_id,
            account_id,
            amount
        )
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                guild_id,
                transaction_id,
                sender_id,
                -amount,
            ),
            (
                guild_id,
                transaction_id,
                receiver_id,
                amount,
            ),
        ],
    )

    return PaymentResult(
        transaction_id=transaction_id,
        amount=amount,
        duplicated=False,
        sender_balance=account_balance(
            conn,
            guild_id,
            sender_id,
        ),
        receiver_balance=account_balance(
            conn,
            guild_id,
            receiver_id,
        ),
    )


def ensure_account(
    guild_id,
    owner_type: str,
    owner_id,
    account_type: str = "wallet",
) -> int:
    """建立或取得指定帳戶。"""

    guild_id = _guild_id(guild_id)
    owner_type = _owner_type(owner_type)
    account_type = _account_type(
        account_type
    )

    with transaction() as conn:
        return get_or_create_account(
            conn,
            guild_id,
            owner_type,
            str(owner_id),
            account_type,
        )


def get_balance(
    guild_id,
    owner_type: str,
    owner_id,
    account_type: str = "wallet",
) -> int:
    """查詢帳戶餘額；不存在的帳戶視為零。"""

    guild_id = _guild_id(guild_id)
    owner_type = _owner_type(owner_type)
    account_type = _account_type(
        account_type
    )

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM accounts
            WHERE guild_id = ?
              AND owner_type = ?
              AND owner_id = ?
              AND account_type = ?
            """,
            (
                guild_id,
                owner_type,
                str(owner_id),
                account_type,
            ),
        ).fetchone()

        if row is None:
            return 0

        return account_balance(
            conn,
            guild_id,
            row["id"],
        )


def transfer(
    guild_id,
    unique_key: str,
    sender_type: str,
    sender_owner_id,
    receiver_type: str,
    receiver_owner_id,
    amount: int,
    description: str = "",
    sender_account_type: str = "wallet",
    receiver_account_type: str = "wallet",
) -> PaymentResult:
    """指定帳戶間轉帳，不允許玩家自行發幣。"""

    guild_id = _guild_id(guild_id)

    sender_type = _owner_type(
        sender_type
    )
    receiver_type = _owner_type(
        receiver_type
    )

    if (
        sender_type == "system"
        or receiver_type == "system"
    ):
        raise EconomyError(
            "一般轉帳不可直接操作系統帳戶。"
        )

    sender_account_type = _account_type(
        sender_account_type
    )
    receiver_account_type = _account_type(
        receiver_account_type
    )

    with transaction() as conn:
        sender_id = get_or_create_account(
            conn,
            guild_id,
            sender_type,
            str(sender_owner_id),
            sender_account_type,
        )

        receiver_id = get_or_create_account(
            conn,
            guild_id,
            receiver_type,
            str(receiver_owner_id),
            receiver_account_type,
        )

        return _payment(
            conn,
            guild_id,
            unique_key,
            sender_id,
            receiver_id,
            amount,
            description,
        )


def issue_system_reward(
    guild_id,
    unique_key: str,
    receiver_type: str,
    receiver_owner_id,
    amount: int,
    description: str,
) -> PaymentResult:
    """由可信任的結算程式發放系統獎勵。

    此函式不得直接暴露成玩家可指定金額的指令。
    呼叫前必須先驗證獎勵資格與核准規則。
    """

    guild_id = _guild_id(guild_id)

    receiver_type = _owner_type(
        receiver_type
    )

    if receiver_type in {
        "system",
        "escrow",
    }:
        raise EconomyError(
            "系統獎勵收款帳戶類型不合法。"
        )

    with transaction() as conn:
        system_id = get_or_create_account(
            conn,
            guild_id,
            "system",
            "issuance",
            "treasury",
        )

        receiver_id = get_or_create_account(
            conn,
            guild_id,
            receiver_type,
            str(receiver_owner_id),
            "wallet",
        )

        return _payment(
            conn,
            guild_id,
            unique_key,
            system_id,
            receiver_id,
            amount,
            description,
        )


def escrow_deposit(
    guild_id,
    source_key: str,
    payer_type: str,
    payer_owner_id,
    amount: int,
) -> PaymentResult:
    """預先將款項存入專用託管帳戶。

    source_key 必須是穩定且唯一的合約／工作編號。
    """

    source_key = _key(source_key)

    return transfer(
        guild_id=guild_id,
        unique_key=(
            f"escrow:deposit:{source_key}"
        ),
        sender_type=payer_type,
        sender_owner_id=payer_owner_id,
        receiver_type="escrow",
        receiver_owner_id=source_key,
        amount=amount,
        description=(
            f"託管款存入：{source_key}"
        ),
        receiver_account_type="escrow",
    )


def escrow_release(
    guild_id,
    source_key: str,
    payout_key: str,
    receiver_type: str,
    receiver_owner_id,
    amount: int,
) -> PaymentResult:
    """由託管帳戶支付已核准的報酬。

    payout_key 應包含合約編號及受益人識別，
    防止重複結算。
    """

    source_key = _key(source_key)
    payout_key = _key(payout_key)

    return transfer(
        guild_id=guild_id,
        unique_key=(
            f"escrow:payout:{source_key}:"
            f"{payout_key}"
        ),
        sender_type="escrow",
        sender_owner_id=source_key,
        receiver_type=receiver_type,
        receiver_owner_id=receiver_owner_id,
        amount=amount,
        description=(
            f"託管款支付：{source_key}"
        ),
        sender_account_type="escrow",
    )


def escrow_refund(
    guild_id,
    source_key: str,
    refund_key: str,
    receiver_type: str,
    receiver_owner_id,
    amount: int,
) -> PaymentResult:
    """退款到原付款帳戶。

    呼叫者必須先核對託管合約的原付款人，
    本函式不會自行判定退款資格。
    """

    source_key = _key(source_key)
    refund_key = _key(refund_key)

    return transfer(
        guild_id=guild_id,
        unique_key=(
            f"escrow:refund:{source_key}:"
            f"{refund_key}"
        ),
        sender_type="escrow",
        sender_owner_id=source_key,
        receiver_type=receiver_type,
        receiver_owner_id=receiver_owner_id,
        amount=amount,
        description=(
            f"託管款退款：{source_key}"
        ),
        sender_account_type="escrow",
    )


def get_transaction_history(
    guild_id,
    owner_type: str,
    owner_id,
    account_type: str = "wallet",
    limit: int = 20,
) -> list[dict]:
    """取得指定帳戶的最近交易紀錄。"""

    guild_id = _guild_id(guild_id)
    owner_type = _owner_type(owner_type)
    account_type = _account_type(
        account_type
    )

    limit = max(
        1,
        min(int(limit), 100),
    )

    with get_connection() as conn:
        account = conn.execute(
            """
            SELECT id
            FROM accounts
            WHERE guild_id = ?
              AND owner_type = ?
              AND owner_id = ?
              AND account_type = ?
            """,
            (
                guild_id,
                owner_type,
                str(owner_id),
                account_type,
            ),
        ).fetchone()

        if account is None:
            return []

        rows = conn.execute(
            """
            SELECT
                t.id AS transaction_id,
                t.unique_key,
                t.description,
                t.created_at,
                e.amount
            FROM ledger_entries AS e
            JOIN ledger_transactions AS t
              ON t.guild_id = e.guild_id
             AND t.id = e.transaction_id
            WHERE e.guild_id = ?
              AND e.account_id = ?
            ORDER BY t.id DESC, e.id DESC
            LIMIT ?
            """,
            (
                guild_id,
                account["id"],
                limit,
            ),
        ).fetchall()

        result = []

        for row in rows:
            try:
                details = json.loads(
                    row["description"]
                )
                description = details.get(
                    "text", ""
                )
            except (TypeError, ValueError):
                description = row[
                    "description"
                ]

            result.append(
                {
                    "transaction_id": row[
                        "transaction_id"
                    ],
                    "unique_key": row[
                        "unique_key"
                    ],
                    "amount": row["amount"],
                    "description": description,
                    "created_at": row[
                        "created_at"
                    ],
                }
            )

        return result


def get_escrow_balance(
    guild_id,
    source_key: str,
) -> int:
    """查詢一筆合約目前尚在託管的金額。"""

    return get_balance(
        guild_id,
        "escrow",
        _key(source_key),
        "escrow",
    )
