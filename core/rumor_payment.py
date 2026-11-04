
"""傳聞公關資源扣費核心。

安裝：core/rumor_payment.py

扣費、行動紀錄及效果寫入在同一筆 SQLite 交易中完成。
案件等級必須已鎖定，並在扣費交易內重新讀取。

暫時只支援角色自己的錢包或付費行動值。
公司銀行金幣代付需公司公關權限及正式財務授權；免費行動值尚未接入。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.database import transaction, get_or_create_account
from core.economy import _payment


PR_COSTS = {
    1: (1000, 1),
    2: (5000, 2),
    3: (15000, 3),
    4: (50000, 5),
    5: (100000, 8),
}

REPEAT_MULTIPLIERS = (
    1.0,
    0.75,
    0.50,
    0.25,
    0.10,
)


class RumorPaymentError(ValueError):
    pass


@dataclass(frozen=True)
class ChargeResult:
    action_id: int
    cost: int
    resource: str
    repeat_count: int
    effectiveness: float
    duplicated: bool


def initialize(conn):
    """建立必要資料表，不刪除舊資料。"""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_pr_charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            request_key TEXT NOT NULL,
            rumor_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            resource TEXT NOT NULL
                CHECK(resource IN ('coins','paid_actions')),
            cost INTEGER NOT NULL CHECK(cost>0),
            repeat_count INTEGER NOT NULL,
            effectiveness REAL NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(guild_id,request_key)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_case_tiers (
            guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL,
            tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 5),
            locked_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id, rumor_id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rumor_pr_charge_repeat
        ON rp_rumor_pr_charges(
            guild_id,rumor_id,character_id,strategy
        )
    """)


def _existing(conn, gid, key):
    return conn.execute(
        """
        SELECT *
        FROM rp_rumor_pr_charges
        WHERE guild_id=? AND request_key=?
        """,
        (gid, key),
    ).fetchone()


def spend_for_pr_action(
    guild_id,
    actor_id,
    character_id,
    rumor_id,
    severity,
    strategy,
    resource,
    request_key,
    *,
    effect_writer: Optional[Callable] = None,
    admin_override: bool = False,
    funding_company: str | None = None,
):
    """執行正式公關行動扣費。

    severity 可為 None，這時一律採用資料庫中的鎖定等級。

    effect_writer(conn, effectiveness, action_id)
    必須只進行同一筆 SQLite 交易內的資料庫操作。

    公司付款由 funding_company 指定；實際扣費與效果同交易。
    """

    gid = str(guild_id)
    uid = str(actor_id)
    cid = int(character_id)
    rid = int(rumor_id)

    key = str(request_key).strip()
    strategy = str(strategy).strip()

    if not key or len(key) > 200:
        raise RumorPaymentError("行動唯一編號不合法。")

    if not strategy or len(strategy) > 80:
        raise RumorPaymentError("公關策略名稱不合法。")

    if severity is not None and (
        type(severity) is not int
        or severity not in PR_COSTS
    ):
        raise RumorPaymentError("案件嚴重度應為 T1～T5。")

    if resource not in ("coins", "paid_actions"):
        raise RumorPaymentError(
            "資源只能選擇 coins 或 paid_actions。"
        )

    if cid <= 0 or rid <= 0:
        raise RumorPaymentError(
            "角色或案件編號不合法。"
        )

    if funding_company is not None and resource != "coins":
        raise RumorPaymentError("公司付款只能使用金幣")

    with transaction() as conn:
        initialize(conn)

        tier_row = conn.execute(
            """
            SELECT tier
            FROM rp_rumor_case_tiers
            WHERE guild_id=? AND rumor_id=?
            """,
            (gid, rid),
        ).fetchone()

        if tier_row is None:
            raise RumorPaymentError(
                "案件尚未鎖定 T1～T5 等級，禁止扣費。"
            )

        locked_tier = int(tier_row["tier"])

        if (
            severity is not None
            and severity != locked_tier
        ):
            raise RumorPaymentError(
                "案件等級與鎖定版本不一致，禁止扣費。"
            )

        cost = PR_COSTS[locked_tier][
            0 if resource == "coins" else 1
        ]

        from core.company_pr_funding import initialize as initialize_company_funding
        initialize_company_funding(conn)
        funding_company = str(funding_company) if funding_company is not None else None
        old = _existing(conn, gid, key)

        if old:
            if (
                int(old["rumor_id"]) != rid
                or int(old["character_id"]) != cid
                or old["strategy"] != strategy
                or old["resource"] != resource
                or int(old["cost"]) != cost
            ):
                raise RumorPaymentError(
                    "相同請求編號已用於不同公關操作。"
                )

            funded = conn.execute("SELECT business_id FROM rp_company_pr_funding WHERE guild_id=? AND request_key=?",(gid,key)).fetchone()
            if (str(funded['business_id']) if funded else None) != funding_company:
                raise RumorPaymentError('相同請求已用於不同公司／個人付款來源')
            if funding_company is not None:
                from core.company_pr_funding import verify_company_payer
                verify_company_payer(conn,gid,funding_company,uid,rid,cost)
            return ChargeResult(
                int(old["id"]),
                int(old["cost"]),
                old["resource"],
                int(old["repeat_count"]),
                float(old["effectiveness"]),
                True,
            )

        character = conn.execute(
            """
            SELECT owner_id,status
            FROM characters
            WHERE guild_id=? AND id=?
            """,
            (gid, cid),
        ).fetchone()

        if (
            not character
            or character["status"] != "active"
        ):
            raise RumorPaymentError(
                "角色不存在或尚未啟用。"
            )

        if (
            not admin_override
            and str(character["owner_id"]) != uid
        ):
            raise PermissionError(
                "只能使用自己的已啟用角色付款。"
            )

        case = conn.execute(
            """
            SELECT id,status,character_id,settle_at
            FROM rp_company_rumors
            WHERE guild_id=? AND id=?
            """,
            (gid, rid),
        ).fetchone()

        if (
            not case
            or case["status"] != "active"
        ):
            raise RumorPaymentError(
                "傳聞不存在或已經結案。"
            )

        if int(case["character_id"]) != cid:
            raise RumorPaymentError(
                "目前僅支援當事角色為自己的案件付款。"
            )

        if int(case["settle_at"]) <= int(time.time()):
            raise RumorPaymentError(
                "傳聞已到結算時間，不能繼續付款。"
            )

        count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM rp_rumor_pr_charges
                WHERE guild_id=?
                  AND rumor_id=?
                  AND character_id=?
                  AND strategy=?
                """,
                (gid, rid, cid, strategy),
            ).fetchone()[0]
        )

        effectiveness = REPEAT_MULTIPLIERS[
            min(count, len(REPEAT_MULTIPLIERS) - 1)
        ]

        if resource == "coins":
            if funding_company is not None:
                from core.company_pr_funding import verify_company_payer
                sender = verify_company_payer(conn,gid,funding_company,uid,rid,cost)
            else:
                sender = get_or_create_account(conn,gid,"character",cid,"wallet")
            receiver = get_or_create_account(
                conn, gid, "system",
                "rumor-pr-expense", "treasury"
            )

            _payment(
                conn,
                gid,
                "rumor-pr:" + key,
                sender,
                receiver,
                cost,
                f"傳聞 #{rid} 公關策略 {strategy}",
            )

        else:
            from modules.zzz_shop_home import (
                spend_paid_actions,
            )

            spend_paid_actions(
                conn, gid, cid, cost
            )

        cur = conn.execute(
            """
            INSERT INTO rp_rumor_pr_charges (
                guild_id,
                request_key,
                rumor_id,
                character_id,
                strategy,
                resource,
                cost,
                repeat_count,
                effectiveness,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                gid,
                key,
                rid,
                cid,
                strategy,
                resource,
                cost,
                count + 1,
                effectiveness,
                int(time.time()),
            ),
        )

        action_id = int(cur.lastrowid)
        if funding_company is not None:
            conn.execute("INSERT INTO rp_company_pr_funding (guild_id,request_key,business_id,actor_id,charge_id,amount) VALUES (?,?,?,?,?,?)",
                (gid,key,funding_company,uid,action_id,cost))

        if effect_writer is not None:
            effect_writer(
                conn,
                effectiveness,
                action_id,
            )

        return ChargeResult(
            action_id,
            cost,
            resource,
            count + 1,
            effectiveness,
            False,
        )
