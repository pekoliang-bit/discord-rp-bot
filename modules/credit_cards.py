
"""信用卡：官方審核、週期帳單、手動／自動繳款、零息分期。"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import (
    transaction,
    get_connection,
    get_or_create_account,
    account_balance,
)
from core.economy import _payment, EconomyError
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
    require_admin,
)

LOG = logging.getLogger(__name__)

MIN_AGE = 20
MIN_LIMIT = 50_000
MAX_LIMIT = 3_000_000
PERIOD_DAYS = 14
MAX_TERMS = 12


def now():
    return datetime.now(timezone.utc)


def ts(dt):
    return dt.isoformat(timespec="seconds")


def init_credit_db():
    with transaction() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS credit_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            requested_limit INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            applied_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewer_id TEXT,
            note TEXT NOT NULL DEFAULT '',
            UNIQUE(guild_id, id),
            FOREIGN KEY(guild_id, character_id)
                REFERENCES characters(guild_id, id)
        );

        CREATE TABLE IF NOT EXISTS credit_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            application_id INTEGER NOT NULL,
            tier TEXT NOT NULL DEFAULT 'standard',
            status TEXT NOT NULL DEFAULT 'active',
            credit_limit INTEGER NOT NULL,
            outstanding INTEGER NOT NULL DEFAULT 0,
            due_amount INTEGER NOT NULL DEFAULT 0,
            late_events INTEGER NOT NULL DEFAULT 0,
            next_bill_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, id),
            FOREIGN KEY(guild_id, character_id)
                REFERENCES characters(guild_id, id),
            CHECK(outstanding >= 0),
            CHECK(due_amount >= 0)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS
        one_open_credit_card
        ON credit_cards(guild_id, character_id)
        WHERE status IN ('active', 'frozen');

        CREATE TABLE IF NOT EXISTS credit_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            card_id INTEGER NOT NULL,
            external_key TEXT NOT NULL,
            merchant_type TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            installments INTEGER NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, external_key),
            FOREIGN KEY(guild_id, card_id)
                REFERENCES credit_cards(guild_id, id)
        );

        CREATE TABLE IF NOT EXISTS credit_installments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            card_id INTEGER NOT NULL,
            purchase_id INTEGER NOT NULL,
            due_at TEXT NOT NULL,
            amount INTEGER NOT NULL,
            billed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(guild_id, id),
            FOREIGN KEY(guild_id, card_id)
                REFERENCES credit_cards(guild_id, id)
        );

        CREATE TABLE IF NOT EXISTS credit_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            card_id INTEGER NOT NULL,
            payment_key TEXT NOT NULL,
            amount INTEGER NOT NULL,
            paid_at TEXT NOT NULL,
            UNIQUE(guild_id, payment_key),
            FOREIGN KEY(guild_id, card_id)
                REFERENCES credit_cards(guild_id, id)
        );
        """)


def character(conn, actor, character_id):
    row = conn.execute(
        """
        SELECT *
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (actor.guild_id, character_id),
    ).fetchone()

    if row is None or row["status"] != "active":
        raise InvalidState(
            "角色不存在或尚未啟用。"
        )

    if (
        row["owner_id"] != actor.user_id
        and not actor.is_admin
    ):
        raise PermissionDenied(
            "你沒有操作這名角色的權限。"
        )

    return row


def apply_card(actor, character_id, requested):
    if (
        type(requested) is not int
        or not MIN_LIMIT <= requested <= MAX_LIMIT
    ):
        raise ValueError(
            "一般卡申請額度必須介於5萬至300萬。"
        )

    with transaction() as conn:
        row = character(
            conn,
            actor,
            character_id,
        )

        if (
            row["age"] is None
            or row["age"] < MIN_AGE
        ):
            raise InvalidState(
                "信用卡申請年齡必須滿20歲。"
            )

        existing = conn.execute(
            """
            SELECT 1 FROM credit_cards
            WHERE guild_id = ?
              AND character_id = ?
              AND status IN ('active', 'frozen')
            """,
            (actor.guild_id, character_id),
        ).fetchone()

        if existing:
            raise InvalidState(
                "這名角色已持有信用卡，不能重複申請。"
            )

        pending = conn.execute(
            """
            SELECT 1 FROM credit_applications
            WHERE guild_id = ?
              AND character_id = ?
              AND status = 'pending'
            """,
            (actor.guild_id, character_id),
        ).fetchone()

        if pending:
            raise InvalidState(
                "這名角色已有待審申請。"
            )

        cursor = conn.execute(
            """
            INSERT INTO credit_applications(
                guild_id,
                character_id,
                requested_limit,
                applied_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                character_id,
                requested,
                ts(now()),
            ),
        )

        return cursor.lastrowid


def review_card(
    actor,
    application_id,
    approved,
    amount,
    note,
):
    require_admin(actor)

    if len(note) > 500:
        raise ValueError(
            "審核意見最多500字。"
        )

    with transaction() as conn:
        app = conn.execute(
            """
            SELECT
                a.*,
                c.owner_id,
                c.age,
                c.status AS char_status
            FROM credit_applications a
            JOIN characters c
              ON c.guild_id = a.guild_id
             AND c.id = a.character_id
            WHERE a.guild_id = ?
              AND a.id = ?
            """,
            (actor.guild_id, application_id),
        ).fetchone()

        if app is None or app["status"] != "pending":
            raise InvalidState(
                "申請不存在或已審核。"
            )

        if app["owner_id"] == actor.user_id:
            raise PermissionDenied(
                "不能審核自己的角色。"
            )

        if not approved:
            conn.execute(
                """
                UPDATE credit_applications
                SET status = 'rejected',
                    reviewed_at = ?,
                    reviewer_id = ?,
                    note = ?
                WHERE guild_id = ?
                  AND id = ?
                """,
                (
                    ts(now()),
                    actor.user_id,
                    note,
                    actor.guild_id,
                    application_id,
                ),
            )

            return "申請已拒絕。"

        if (
            app["char_status"] != "active"
            or app["age"] is None
            or app["age"] < MIN_AGE
        ):
            raise InvalidState(
                "申請角色不符合啟用或年齡資格。"
            )

        if (
            type(amount) is not int
            or not MIN_LIMIT <= amount
            <= min(
                MAX_LIMIT,
                app["requested_limit"],
            )
        ):
            raise ValueError(
                "核准額度須介於5萬及申請額度之間。"
            )

        existing = conn.execute(
            """
            SELECT 1 FROM credit_cards
            WHERE guild_id = ?
              AND character_id = ?
              AND status IN ('active', 'frozen')
            """,
            (
                actor.guild_id,
                app["character_id"],
            ),
        ).fetchone()

        if existing:
            raise InvalidState(
                "已有有效信用卡。"
            )

        last = conn.execute(
            """
            SELECT credit_limit, late_events
            FROM credit_cards
            WHERE guild_id = ?
              AND character_id = ?
              AND status = 'closed'
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                actor.guild_id,
                app["character_id"],
            ),
        ).fetchone()

        if (
            last
            and last["late_events"] > 0
            and amount > last["credit_limit"] // 2
        ):
            raise InvalidState(
                "曾有逾期紀錄，下次可核准額度"
                f"不得超過 "
                f"{last['credit_limit'] // 2:,}。"
            )

        current = now()

        conn.execute(
            """
            UPDATE credit_applications
            SET status = 'approved',
                reviewed_at = ?,
                reviewer_id = ?,
                note = ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                ts(current),
                actor.user_id,
                note,
                actor.guild_id,
                application_id,
            ),
        )

        cursor = conn.execute(
            """
            INSERT INTO credit_cards(
                guild_id,
                character_id,
                application_id,
                credit_limit,
                next_bill_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                app["character_id"],
                application_id,
                amount,
                ts(
                    current + timedelta(
                        days=PERIOD_DAYS
                    )
                ),
                ts(current),
            ),
        )

        return (
            f"信用卡 #{cursor.lastrowid} "
            f"已核准，額度 {amount:,} 金幣。"
        )


def charge_purchase(
    conn,
    *,
    guild_id,
    card_id,
    merchant_type,
    merchant_id,
    amount,
    installments,
    external_key,
    description,
):
    """供已驗證的商城或交易模組呼叫。

    必須在外層 transaction() 裡執行。
    不得直接暴露成玩家自行輸入金額的指令。
    """

    guild_id = str(guild_id)

    if (
        type(amount) is not int
        or amount <= 0
        or amount > 10**15
    ):
        raise ValueError(
            "消費金額不合法。"
        )

    if (
        type(installments) is not int
        or not 1 <= installments <= MAX_TERMS
    ):
        raise ValueError(
            "分期必須介於1至12期。"
        )

    if merchant_type not in (
        "organization",
        "production",
        "school",
    ):
        raise ValueError(
            "不支援的商家類型。"
        )

    if not external_key or len(external_key) > 150:
        raise ValueError(
            "消費唯一編號不合法。"
        )

    previous = conn.execute(
        """
        SELECT *
        FROM credit_purchases
        WHERE guild_id = ?
          AND external_key = ?
        """,
        (guild_id, external_key),
    ).fetchone()

    if previous:
        if (
            previous["card_id"],
            previous["merchant_type"],
            previous["merchant_id"],
            previous["amount"],
            previous["installments"],
        ) != (
            card_id,
            merchant_type,
            str(merchant_id),
            amount,
            installments,
        ):
            raise InvalidState(
                "消費編號已被不同交易使用。"
            )

        return previous["id"]

    card = conn.execute(
        """
        SELECT *
        FROM credit_cards
        WHERE guild_id = ?
          AND id = ?
        """,
        (guild_id, card_id),
    ).fetchone()

    if (
        card is None
        or card["status"] != "active"
        or card["tier"] != "standard"
    ):
        raise InvalidState(
            "一般信用卡不存在或已停止消費。"
        )

    if (
        amount
        > card["credit_limit"]
        - card["outstanding"]
    ):
        raise InvalidState(
            "信用卡可用額度不足，交易取消。"
        )

    buyer = conn.execute(
        """
        SELECT status
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (guild_id, card["character_id"]),
    ).fetchone()

    if not buyer or buyer["status"] != "active":
        raise InvalidState(
            "持卡角色未啟用。"
        )

    issuer = get_or_create_account(
        conn,
        guild_id,
        "system",
        "credit_issuance",
        "treasury",
    )

    merchant = get_or_create_account(
        conn,
        guild_id,
        merchant_type,
        str(merchant_id),
        "wallet",
    )

    _payment(
        conn,
        guild_id,
        f"card:purchase:{external_key}",
        issuer,
        merchant,
        amount,
        description[:300],
    )

    cursor = conn.execute(
        """
        INSERT INTO credit_purchases(
            guild_id,
            card_id,
            external_key,
            merchant_type,
            merchant_id,
            amount,
            installments,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            card_id,
            external_key,
            merchant_type,
            str(merchant_id),
            amount,
            installments,
            description[:500],
            ts(now()),
        ),
    )

    purchase_id = cursor.lastrowid

    base, extra = divmod(
        amount,
        installments,
    )

    first_due = datetime.fromisoformat(
        card["next_bill_at"]
    )

    for index in range(installments):
        installment_amount = (
            base + (1 if index < extra else 0)
        )

        if installment_amount == 0:
            continue

        conn.execute(
            """
            INSERT INTO credit_installments(
                guild_id,
                card_id,
                purchase_id,
                due_at,
                amount
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                card_id,
                purchase_id,
                ts(
                    first_due + timedelta(
                        days=PERIOD_DAYS * index
                    )
                ),
                installment_amount,
            ),
        )

    conn.execute(
        """
        UPDATE credit_cards
        SET outstanding = outstanding + ?
        WHERE guild_id = ?
          AND id = ?
        """,
        (amount, guild_id, card_id),
    )

    return purchase_id


def pay_bill(
    conn,
    guild_id,
    card_id,
    amount,
    payment_key,
):
    guild_id = str(guild_id)

    card = conn.execute(
        """
        SELECT *
        FROM credit_cards
        WHERE guild_id = ?
          AND id = ?
        """,
        (guild_id, card_id),
    ).fetchone()

    if (
        card is None
        or card["status"] not in (
            "active",
            "frozen",
        )
    ):
        raise InvalidState(
            "找不到可繳款信用卡。"
        )

    previous = conn.execute(
        """
        SELECT amount
        FROM credit_payments
        WHERE guild_id = ?
          AND payment_key = ?
        """,
        (guild_id, payment_key),
    ).fetchone()

    if previous:
        if previous["amount"] != amount:
            raise InvalidState(
                "同一繳款編號不能使用不同金額。"
            )

        return card["due_amount"]

    if (
        type(amount) is not int
        or amount <= 0
        or amount > card["due_amount"]
    ):
        raise ValueError(
            "本期可繳金額為1至 "
            f"{card['due_amount']:,} 金幣。"
        )

    bank = get_or_create_account(
        conn,
        guild_id,
        "character",
        str(card["character_id"]),
        "bank",
    )

    treasury = get_or_create_account(
        conn,
        guild_id,
        "system",
        "credit_repayment",
        "treasury",
    )

    if account_balance(
        conn,
        guild_id,
        bank,
    ) < amount:
        raise InvalidState(
            "個人活期存款不足，扣款取消。"
        )

    _payment(
        conn,
        guild_id,
        f"card:repay:{payment_key}",
        bank,
        treasury,
        amount,
        "信用卡帳單繳款",
    )

    due_remaining = (
        card["due_amount"] - amount
    )

    new_status = (
        "active"
        if due_remaining == 0
        else card["status"]
    )

    conn.execute(
        """
        UPDATE credit_cards
        SET due_amount = due_amount - ?,
            outstanding = outstanding - ?,
            status = ?
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            amount,
            amount,
            new_status,
            guild_id,
            card_id,
        ),
    )

    conn.execute(
        """
        INSERT INTO credit_payments(
            guild_id,
            card_id,
            payment_key,
            amount,
            paid_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            card_id,
            payment_key,
            amount,
            ts(now()),
        ),
    )

    return due_remaining


def manual_pay(
    actor,
    card_id,
    amount,
    request_id,
):
    with transaction() as conn:
        card = conn.execute(
            """
            SELECT *
            FROM credit_cards
            WHERE guild_id = ?
              AND id = ?
            """,
            (actor.guild_id, card_id),
        ).fetchone()

        if card is None:
            raise InvalidState(
                "找不到信用卡。"
            )

        character(
            conn,
            actor,
            card["character_id"],
        )

        return pay_bill(
            conn,
            actor.guild_id,
            card_id,
            amount,
            f"manual:{request_id}",
        )


def close_card(actor, card_id):
    with transaction() as conn:
        card = conn.execute(
            """
            SELECT *
            FROM credit_cards
            WHERE guild_id = ?
              AND id = ?
            """,
            (actor.guild_id, card_id),
        ).fetchone()

        if card is None:
            raise InvalidState(
                "找不到信用卡。"
            )

        character(
            conn,
            actor,
            card["character_id"],
        )

        if (
            card["outstanding"] != 0
            or card["due_amount"] != 0
        ):
            raise InvalidState(
                "信用卡仍有欠款或未到期分期，"
                "不能銷卡。"
            )

        if card["status"] == "closed":
            return "信用卡已銷戶。"

        conn.execute(
            """
            UPDATE credit_cards
            SET status = 'closed'
            WHERE guild_id = ?
              AND id = ?
            """,
            (actor.guild_id, card_id),
        )

        return "信用卡已銷戶。"


def settle_card(conn, card, current):
    guild_id = card["guild_id"]
    card_id = card["id"]

    cutoff = datetime.fromisoformat(
        card["next_bill_at"]
    )

    if cutoff > current:
        return

    # 補算停機期間已到期的分期。
    due_now = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM credit_installments
        WHERE guild_id = ?
          AND card_id = ?
          AND billed = 0
          AND due_at <= ?
        """,
        (guild_id, card_id, ts(current)),
    ).fetchone()[0]

    conn.execute(
        """
        UPDATE credit_installments
        SET billed = 1
        WHERE guild_id = ?
          AND card_id = ?
          AND billed = 0
          AND due_at <= ?
        """,
        (guild_id, card_id, ts(current)),
    )

    total_due = (
        card["due_amount"] + due_now
    )

    next_cycle = cutoff

    while next_cycle <= current:
        next_cycle += timedelta(
            days=PERIOD_DAYS
        )

    conn.execute(
        """
        UPDATE credit_cards
        SET due_amount = ?,
            next_bill_at = ?
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            total_due,
            ts(next_cycle),
            guild_id,
            card_id,
        ),
    )

    if total_due <= 0:
        return

    account = get_or_create_account(
        conn,
        guild_id,
        "character",
        str(card["character_id"]),
        "bank",
    )

    savings = account_balance(
        conn,
        guild_id,
        account,
    )

    if savings >= total_due:
        pay_bill(
            conn,
            guild_id,
            card_id,
            total_due,
            f"auto:{card_id}:{ts(cutoff)}",
        )

    elif card["status"] == "active":
        conn.execute(
            """
            UPDATE credit_cards
            SET status = 'frozen',
                late_events = late_events + 1
            WHERE guild_id = ?
              AND id = ?
            """,
            (guild_id, card_id),
        )


def settle_all():
    current = now()

    with get_connection() as conn:
        due = conn.execute(
            """
            SELECT guild_id, id
            FROM credit_cards
            WHERE status IN ('active', 'frozen')
              AND next_bill_at <= ?
            ORDER BY id
            LIMIT 500
            """,
            (ts(current),),
        ).fetchall()

    for item in due:
        try:
            with transaction() as conn:
                card = conn.execute(
                    """
                    SELECT *
                    FROM credit_cards
                    WHERE guild_id = ?
                      AND id = ?
                    """,
                    (
                        item["guild_id"],
                        item["id"],
                    ),
                ).fetchone()

                if (
                    card
                    and card["status"] in (
                        "active",
                        "frozen",
                    )
                ):
                    settle_card(
                        conn,
                        card,
                        current,
                    )

        except Exception:
            LOG.exception(
                "信用卡結算失敗：%s",
                item["id"],
            )


def card_summary(actor, character_id):
    with get_connection() as conn:
        character(
            conn,
            actor,
            character_id,
        )

        cards = conn.execute(
            """
            SELECT *
            FROM credit_cards
            WHERE guild_id = ?
              AND character_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (actor.guild_id, character_id),
        ).fetchall()

        if not cards:
            return "目前沒有信用卡。"

        return "\n\n".join(
            (
                f"卡片 #{c['id']}｜"
                f"{c['tier']}｜{c['status']}\n"
                f"額度：{c['credit_limit']:,}\n"
                "可用："
                f"{c['credit_limit'] - c['outstanding']:,}\n"
                f"未清償總額：{c['outstanding']:,}\n"
                f"本期應繳：{c['due_amount']:,}\n"
                f"下次結帳：{c['next_bill_at'][:10]}\n"
                f"歷史逾期：{c['late_events']}次"
            )
            for c in cards
        )


class CreditCards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_credit_db
        )
        self.billing.start()

    async def cog_unload(self):
        self.billing.cancel()

    @tasks.loop(hours=1)
    async def billing(self):
        await asyncio.to_thread(
            settle_all
        )

    @billing.before_loop
    async def wait_ready(self):
        await self.bot.wait_until_ready()

    async def execute(
        self,
        interaction,
        func,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                func
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
        name="信用卡申請",
        description="年滿20歲後申請一般信用卡",
    )
    async def apply(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        申請額度: app_commands.Range[
            int, 50000, 3000000
        ],
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )

            application_id = apply_card(
                actor,
                角色編號,
                申請額度,
            )

            return (
                f"✅ 信用卡申請 "
                f"#{application_id} "
                "已送交官方銀行審核。"
            )

        await self.execute(
            interaction,
            work,
        )

    @app_commands.command(
        name="信用卡待審",
        description="官方銀行查看待審信用卡申請",
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
                rows = conn.execute(
                    """
                    SELECT
                        id,
                        character_id,
                        requested_limit
                    FROM credit_applications
                    WHERE guild_id = ?
                      AND status = 'pending'
                    ORDER BY id
                    LIMIT 20
                    """,
                    (actor.guild_id,),
                ).fetchall()

            return "\n".join(
                (
                    f"申請#{r['id']}｜"
                    f"角色#{r['character_id']}｜"
                    f"{r['requested_limit']:,}金幣"
                )
                for r in rows
            ) or "目前沒有待審信用卡。"

        await self.execute(
            interaction,
            work,
        )

    @app_commands.command(
        name="信用卡審核",
        description="官方銀行審核一般信用卡申請",
    )
    async def review(
        self,
        interaction: discord.Interaction,
        申請編號: int,
        通過: bool,
        核准額度: app_commands.Range[
            int, 0, 3000000
        ] = 0,
        意見: str = "",
    ):
        await self.execute(
            interaction,
            lambda: review_card(
                Actor.from_interaction(
                    interaction
                ),
                申請編號,
                通過,
                核准額度,
                意見,
            ),
        )

    @app_commands.command(
        name="信用卡",
        description="查詢角色卡片、帳單與可用額度",
    )
    async def summary(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        await self.execute(
            interaction,
            lambda: card_summary(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
            ),
        )

    @app_commands.command(
        name="信用卡繳款",
        description="手動繳交信用卡已列帳金額",
    )
    async def payment(
        self,
        interaction: discord.Interaction,
        卡片編號: int,
        金額: app_commands.Range[
            int, 1, 1000000000000000
        ],
    ):
        def work():
            remaining = manual_pay(
                Actor.from_interaction(
                    interaction
                ),
                卡片編號,
                金額,
                interaction.id,
            )

            return (
                "✅ 繳款成功！"
                f"本期剩餘應繳："
                f"{remaining:,}金幣。"
            )

        await self.execute(
            interaction,
            work,
        )

    @app_commands.command(
        name="信用卡銷戶",
        description="結清全部欠款後關閉一般信用卡",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        卡片編號: int,
    ):
        await self.execute(
            interaction,
            lambda: close_card(
                Actor.from_interaction(
                    interaction
                ),
                卡片編號,
            ),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CreditCards(bot)
    )
