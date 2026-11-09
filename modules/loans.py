"""Discord RP 官方銀行貸款系統。

提供：
- 個人、事業、資產貸款申請
- 官方人工審核
- 核准額度與固定年利率
- 原子撥款與還款
- 提前清償
- 到期自動扣款
- 逾期紀錄
- 角色與伺服器隔離

時間均為現實時間，使用 UTC 儲存。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import (
    get_connection,
    get_or_create_account,
    account_balance,
    transaction,
)
from core.economy import (
    _payment,
    EconomyError,
    InsufficientFunds,
)
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
    require_admin,
)


LOG = logging.getLogger(__name__)

MAX_LOAN = 10**12

LOAN_TYPES = {
    "personal": ("個人貸款", 30),
    "business": ("事業貸款", 60),
    "asset": ("資產貸款", 90),
}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def init_loans():
    """建立貸款專用資料表。"""

    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                loan_type TEXT NOT NULL,
                requested_amount INTEGER NOT NULL,
                approved_amount INTEGER,
                annual_bps INTEGER,
                interest_amount INTEGER,
                total_due INTEGER,
                repaid INTEGER NOT NULL DEFAULT 0,
                purpose TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                review_note TEXT NOT NULL DEFAULT '',
                funded_at TEXT,
                due_at TEXT,
                closed_at TEXT,
                UNIQUE(guild_id, id),
                FOREIGN KEY (guild_id, character_id)
                    REFERENCES characters(guild_id, id),
                CHECK(requested_amount > 0),
                CHECK(repaid >= 0)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_loan_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                loan_id INTEGER NOT NULL,
                unique_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                payment_type TEXT NOT NULL,
                paid_at TEXT NOT NULL,
                UNIQUE(guild_id, unique_key),
                FOREIGN KEY (guild_id, loan_id)
                    REFERENCES bank_loans(guild_id, id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_bank_loans_status
            ON bank_loans(guild_id, status, due_at)
        """)


def audit(conn, guild_id, actor_id, action, loan_id, data):
    conn.execute(
        """
        INSERT INTO audit_events (
            guild_id,
            actor_id,
            action,
            target_key,
            details_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(guild_id),
            str(actor_id),
            action,
            f"loan:{loan_id}",
            json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )


def loan_character(conn, actor, character_id):
    """確認角色存在且操作者有操作權。"""

    row = conn.execute(
        """
        SELECT *
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            actor.guild_id,
            character_id,
        ),
    ).fetchone()

    if row is None or row["status"] != "active":
        raise InvalidState(
            "角色不存在或尚未正式啟用。"
        )

    if row["character_type"] == "official_npc":
        permitted = actor.is_admin
    else:
        permitted = (
            row["owner_id"] == actor.user_id
            or actor.is_admin
        )

    if not permitted:
        raise PermissionDenied(
            "你沒有操作這名角色的權限。"
        )

    return row


def calc_interest(principal, annual_bps, days):
    """貸款利息在核准時固定，不採複利。"""

    return (
        principal * annual_bps * days
        // (10000 * 365)
    )


def new_application(actor, character_id, kind, amount, purpose):
    """提出一筆貸款申請，不會自動撥款。"""

    if kind not in LOAN_TYPES:
        raise ValueError("不支援的貸款種類。")

    if type(amount) is not int or not 1 <= amount <= MAX_LOAN:
        raise ValueError("貸款金額不合法。")

    purpose = purpose.strip()

    if not 5 <= len(purpose) <= 500:
        raise ValueError(
            "貸款用途必須介於5至500字。"
        )

    with transaction() as conn:
        loan_character(
            conn,
            actor,
            character_id,
        )

        existing = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM bank_loans
            WHERE guild_id = ?
              AND character_id = ?
              AND status = 'pending'
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()["total"]

        if existing > 0:
            raise InvalidState(
                "這名角色仍有待審核的貸款申請。"
            )

        cursor = conn.execute(
            """
            INSERT INTO bank_loans (
                guild_id,
                character_id,
                loan_type,
                requested_amount,
                purpose,
                requested_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                character_id,
                kind,
                amount,
                purpose,
                iso(now_utc()),
            ),
        )

        loan_id = cursor.lastrowid

        audit(
            conn,
            actor.guild_id,
            actor.user_id,
            "bank.loan.apply",
            loan_id,
            {
                "kind": kind,
                "amount": amount,
            },
        )

        return loan_id


def review_application(
    actor,
    loan_id,
    approve,
    approved_amount,
    annual_bps,
    reason,
):
    """官方銀行核准或拒絕貸款。

    核准與發放金幣必須在同一交易內完成。
    """

    require_admin(actor)

    if len(reason) > 500:
        raise ValueError(
            "審核意見最多500字。"
        )

    with transaction() as conn:
        loan = conn.execute(
            """
            SELECT l.*, c.owner_id, c.status AS character_status
            FROM bank_loans AS l
            JOIN characters AS c
              ON c.guild_id = l.guild_id
             AND c.id = l.character_id
            WHERE l.guild_id = ?
              AND l.id = ?
            """,
            (
                actor.guild_id,
                loan_id,
            ),
        ).fetchone()

        if loan is None:
            raise InvalidState(
                "找不到貸款申請。"
            )

        if loan["status"] != "pending":
            raise InvalidState(
                "這筆申請已經處理。"
            )

        if loan["owner_id"] == actor.user_id:
            raise PermissionDenied(
                "銀行審核者不得審核自己的角色。"
            )

        reviewed_at = now_utc()

        if not approve:
            conn.execute(
                """
                UPDATE bank_loans
                SET status = 'rejected',
                    reviewed_at = ?,
                    reviewed_by = ?,
                    review_note = ?
                WHERE guild_id = ?
                  AND id = ?
                """,
                (
                    iso(reviewed_at),
                    actor.user_id,
                    reason,
                    actor.guild_id,
                    loan_id,
                ),
            )

            audit(
                conn,
                actor.guild_id,
                actor.user_id,
                "bank.loan.reject",
                loan_id,
                {"reason": reason},
            )

            return "已拒絕貸款申請。"

        if loan["character_status"] != "active":
            raise InvalidState(
                "借款角色已不是正式啟用狀態。"
            )

        if (
            type(approved_amount) is not int
            or approved_amount < 1
            or approved_amount > loan["requested_amount"]
        ):
            raise ValueError(
                "核貸金額必須介於1及申請金額之間。"
            )

        if (
            type(annual_bps) is not int
            or not 0 <= annual_bps <= 10000
        ):
            raise ValueError(
                "貸款年利率必須介於0%至100%。"
            )

        duration = LOAN_TYPES[
            loan["loan_type"]
        ][1]

        interest = calc_interest(
            approved_amount,
            annual_bps,
            duration,
        )

        total = approved_amount + interest
        due_at = reviewed_at + timedelta(
            days=duration
        )

        # 有明確抵押標記的資產貸款：在同一原子事務中查核產權、貸款成數和既有抵押。
        # 無標記的既有貸款不自動以角色資產作擔保。
        mortgage_id = None
        if loan["loan_type"] == "asset":
            from core.asset_completions import mortgage_asset_id, validate_mortgage
            mortgage_id = mortgage_asset_id(loan["purpose"])
            if mortgage_id is not None:
                validate_mortgage(conn, actor.guild_id, loan["character_id"], mortgage_id, approved_amount)

        # 直接從系統放款帳戶轉入角色銀行。
        # 系統帳戶只能由內部核貸流程操作。
        issuer = get_or_create_account(
            conn,
            actor.guild_id,
            "system",
            "bank_loans_issuance",
            "treasury",
        )

        borrower = get_or_create_account(
            conn,
            actor.guild_id,
            "character",
            str(loan["character_id"]),
            "bank",
        )

        _payment(
            conn,
            actor.guild_id,
            f"bank:loan:fund:{loan_id}",
            issuer,
            borrower,
            approved_amount,
            f"官方銀行貸款#{loan_id}撥款",
        )

        conn.execute(
            """
            UPDATE bank_loans
            SET status = 'active',
                approved_amount = ?,
                annual_bps = ?,
                interest_amount = ?,
                total_due = ?,
                reviewed_at = ?,
                reviewed_by = ?,
                review_note = ?,
                funded_at = ?,
                due_at = ?
            WHERE guild_id = ?
              AND id = ?
              AND status = 'pending'
            """,
            (
                approved_amount,
                annual_bps,
                interest,
                total,
                iso(reviewed_at),
                actor.user_id,
                reason,
                iso(reviewed_at),
                iso(due_at),
                actor.guild_id,
                loan_id,
            ),
        )

        if mortgage_id is not None:
            from core.asset_completions import bind_bank_loan
            bind_bank_loan(conn, actor.guild_id, loan_id, loan["character_id"], mortgage_id, approved_amount)

        audit(
            conn,
            actor.guild_id,
            actor.user_id,
            "bank.loan.approve",
            loan_id,
            {
                "approved_amount": approved_amount,
                "annual_bps": annual_bps,
                "interest": interest,
                "total_due": total,
                "due_at": iso(due_at),
            },
        )

        return (
            f"✅ 貸款#{loan_id}已核准並撥款。\n"
            f"本金：{approved_amount:,}\n"
            f"利息：{interest:,}\n"
            f"應還總額：{total:,}\n"
            f"到期日：{due_at:%Y-%m-%d}"
        )


def repay_in_transaction(
    conn,
    guild_id,
    loan_id,
    amount,
    unique_key,
    *,
    actor_id="system",
    automatic=False,
):
    """付款、更新負債、留存紀錄，同時提交。"""

    loan = conn.execute(
        """
        SELECT *
        FROM bank_loans
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            str(guild_id),
            loan_id,
        ),
    ).fetchone()

    if loan is None:
        raise InvalidState(
            "找不到貸款。"
        )

    if loan["status"] not in (
        "active",
        "overdue",
    ):
        raise InvalidState(
            "此貸款目前不可還款。"
        )

    outstanding = (
        loan["total_due"] - loan["repaid"]
    )

    if (
        type(amount) is not int
        or amount <= 0
        or amount > outstanding
    ):
        raise ValueError(
            f"還款金額必須介於1至"
            f"{outstanding:,}金幣。"
        )

    borrower = get_or_create_account(
        conn,
        guild_id,
        "character",
        str(loan["character_id"]),
        "bank",
    )

    treasury = get_or_create_account(
        conn,
        guild_id,
        "system",
        "bank_loans_repayment",
        "treasury",
    )

    if account_balance(
        conn,
        guild_id,
        borrower,
    ) < amount:
        raise InsufficientFunds(
            "活期存款不足，無法扣款。"
        )

    result = _payment(
        conn,
        str(guild_id),
        unique_key,
        borrower,
        treasury,
        amount,
        f"官方銀行貸款#{loan_id}還款",
    )

    if result.duplicated:
        raise InvalidState(
            "這筆還款已經處理。"
        )

    closed = amount == outstanding
    new_status = (
        "closed"
        if closed
        else loan["status"]
    )

    conn.execute(
        """
        UPDATE bank_loans
        SET repaid = repaid + ?,
            status = ?,
            closed_at = ?
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            amount,
            new_status,
            iso(now_utc()) if closed else None,
            str(guild_id),
            loan_id,
        ),
    )

    conn.execute(
        """
        INSERT INTO bank_loan_payments (
            guild_id,
            loan_id,
            unique_key,
            amount,
            payment_type,
            paid_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(guild_id),
            loan_id,
            unique_key,
            amount,
            "automatic" if automatic else "manual",
            iso(now_utc()),
        ),
    )

    # 同一 SQL 交易更新資產抵押餘額；非抵押貸款不新增抵押。
    from core.asset_completions import update_bank_loan_balance
    update_bank_loan_balance(
        conn, guild_id, loan_id,
        loan['approved_amount'], loan['total_due'], loan['repaid'] + amount,
    )

    audit(
        conn,
        guild_id,
        actor_id,
        "bank.loan.repay",
        loan_id,
        {
            "amount": amount,
            "automatic": automatic,
            "closed": closed,
        },
    )

    return {
        "paid": amount,
        "remaining": outstanding - amount,
        "closed": closed,
    }


def repay_loan(actor, loan_id, amount, request_id):
    """玩家手動還款，可提前或部分清償。"""

    with transaction() as conn:
        loan = conn.execute(
            """
            SELECT character_id
            FROM bank_loans
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                actor.guild_id,
                loan_id,
            ),
        ).fetchone()

        if loan is None:
            raise InvalidState(
                "找不到貸款。"
            )

        loan_character(
            conn,
            actor,
            loan["character_id"],
        )

        return repay_in_transaction(
            conn,
            actor.guild_id,
            loan_id,
            amount,
            f"bank:loan:manual:{request_id}",
            actor_id=actor.user_id,
        )


def process_due_loans():
    """到期自動扣款，不足額則保留債務並標記逾期。"""

    current_time = now_utc()

    with get_connection() as conn:
        loans = conn.execute(
            """
            SELECT guild_id, id
            FROM bank_loans
            WHERE status = 'active'
              AND due_at <= ?
            ORDER BY id
            LIMIT 500
            """,
            (iso(current_time),),
        ).fetchall()

    for item in loans:
        try:
            with transaction() as conn:
                loan = conn.execute(
                    """
                    SELECT *
                    FROM bank_loans
                    WHERE guild_id = ?
                      AND id = ?
                      AND status = 'active'
                    """,
                    (
                        item["guild_id"],
                        item["id"],
                    ),
                ).fetchone()

                if loan is None:
                    continue

                outstanding = (
                    loan["total_due"] - loan["repaid"]
                )

                if outstanding <= 0:
                    conn.execute(
                        """
                        UPDATE bank_loans
                        SET status = 'closed',
                            closed_at = ?
                        WHERE guild_id = ?
                          AND id = ?
                        """,
                        (
                            iso(now_utc()),
                            item["guild_id"],
                            item["id"],
                        ),
                    )
                    continue

                account = get_or_create_account(
                    conn,
                    item["guild_id"],
                    "character",
                    str(loan["character_id"]),
                    "bank",
                )

                savings = account_balance(
                    conn,
                    item["guild_id"],
                    account,
                )

                if savings < outstanding:
                    conn.execute(
                        """
                        UPDATE bank_loans
                        SET status = 'overdue'
                        WHERE guild_id = ?
                          AND id = ?
                        """,
                        (
                            item["guild_id"],
                            item["id"],
                        ),
                    )

                    audit(
                        conn,
                        item["guild_id"],
                        "system",
                        "bank.loan.overdue",
                        item["id"],
                        {
                            "remaining": outstanding,
                        },
                    )
                    continue

                repay_in_transaction(
                    conn,
                    item["guild_id"],
                    item["id"],
                    outstanding,
                    (
                        f"bank:loan:auto:"
                        f"{item['id']}"
                    ),
                    automatic=True,
                )

        except Exception:
            LOG.exception(
                "貸款到期結算失敗：%s",
                item["id"],
            )


def loan_summary(actor, character_id):
    with get_connection() as conn:
        loan_character(
            conn,
            actor,
            character_id,
        )

        loans = conn.execute(
            """
            SELECT *
            FROM bank_loans
            WHERE guild_id = ?
              AND character_id = ?
            ORDER BY id DESC
            LIMIT 15
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchall()

        if not loans:
            return "目前沒有貸款紀錄。"

        lines = []

        for loan in loans:
            remaining = (
                (loan["total_due"] or 0)
                - loan["repaid"]
            )

            lines.append(
                f"#{loan['id']}｜"
                f"{LOAN_TYPES[loan['loan_type']][0]}\n"
                f"狀態：{loan['status']}\n"
                f"申請：{loan['requested_amount']:,}\n"
                f"剩餘應還：{remaining:,}\n"
                f"到期：{loan['due_at'] or '待審核'}"
            )

        return "\n\n".join(lines)


class Loans(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(init_loans)
        self.loan_scheduler.start()

    async def cog_unload(self):
        self.loan_scheduler.cancel()

    @tasks.loop(hours=1)
    async def loan_scheduler(self):
        await asyncio.to_thread(
            process_due_loans
        )

    @loan_scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    async def respond(self, interaction, operation):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                operation
            )

            await interaction.followup.send(
                str(result)[:1900],
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            EconomyError,
            sqlite3.Error,
        ) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="貸款申請",
        description="向官方銀行提出貸款申請",
    )
    @app_commands.choices(
        種類=[
            app_commands.Choice(
                name="個人貸款｜30天",
                value="personal",
            ),
            app_commands.Choice(
                name="事業貸款｜60天",
                value="business",
            ),
            app_commands.Choice(
                name="資產貸款｜90天",
                value="asset",
            ),
        ]
    )
    async def apply(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        種類: app_commands.Choice[str],
        金額: app_commands.Range[
            int, 1, 1000000000000
        ],
        用途: str,
    ):
        def work():
            loan_id = new_application(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
                種類.value,
                金額,
                用途,
            )

            return (
                f"✅ 貸款申請#{loan_id}已提交。\n"
                "目前等待官方銀行審核。\n"
                "在核准前不會撥款。"
            )

        await self.respond(
            interaction,
            work,
        )

    @app_commands.command(
        name="貸款審核",
        description="官方銀行審核貸款",
    )
    async def review(
        self,
        interaction: discord.Interaction,
        貸款編號: int,
        通過: bool,
        核准金額: app_commands.Range[
            int, 0, 1000000000000
        ] = 0,
        年利率百分比: app_commands.Range[
            float, 0, 100
        ] = 5.0,
        意見: str = "",
    ):
        def work():
            return review_application(
                Actor.from_interaction(
                    interaction
                ),
                貸款編號,
                通過,
                核准金額,
                round(年利率百分比 * 100),
                意見,
            )

        await self.respond(
            interaction,
            work,
        )

    @app_commands.command(
        name="貸款還款",
        description="償還貸款，可提前或部分還款",
    )
    async def repayment(
        self,
        interaction: discord.Interaction,
        貸款編號: int,
        金額: app_commands.Range[
            int, 1, 1000000000000
        ],
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )

            result = repay_loan(
                actor,
                貸款編號,
                金額,
                interaction.id,
            )

            return (
                "✅ 還款成功。\n"
                f"本次還款：{result['paid']:,}\n"
                f"剩餘欠款：{result['remaining']:,}\n"
                f"是否結清："
                f"{'是' if result['closed'] else '否'}"
            )

        await self.respond(
            interaction,
            work,
        )

    @app_commands.command(
        name="貸款紀錄",
        description="查看角色的貸款與還款狀況",
    )
    async def history(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        def work():
            return loan_summary(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
            )

        await self.respond(
            interaction,
            work,
        )

    @app_commands.command(
        name="貸款待審",
        description="官方查看待審貸款申請",
    )
    async def pending(
        self,
        interaction: discord.Interaction,
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )

            require_admin(actor)

            with get_connection() as conn:
                loans = conn.execute(
                    """
                    SELECT id, character_id,
                           loan_type,
                           requested_amount
                    FROM bank_loans
                    WHERE guild_id = ?
                      AND status = 'pending'
                    ORDER BY id
                    LIMIT 20
                    """,
                    (actor.guild_id,),
                ).fetchall()

            if not loans:
                return "目前沒有待審貸款。"

            return "\n".join(
                (
                    f"#{row['id']}｜"
                    f"角色#{row['character_id']}｜"
                    f"{LOAN_TYPES[row['loan_type']][0]}｜"
                    f"{row['requested_amount']:,}"
                )
                for row in loans
            )

        await self.respond(
            interaction,
            work,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Loans(bot))
