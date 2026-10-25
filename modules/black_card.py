
"""Discord RP 黑卡 VIP 制度。

申請：
- 角色年滿20歲
- 個人銀行活期＋有效定存本金至少1億
- 官方銀行人工審核
- 須已有一般信用卡

保級：
- 個人銀行存款至少9000萬
- 跌破門檻，降級為一般信用卡
- 既有信用卡債務不消失

福利：
- 每完整7天200通用自由點數
- 每完整24小時2個免費行動值
- 有效黑卡才能進入隱藏商城

實際商城與屬性成長由對應模組接入。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import (
    get_connection,
    transaction,
    account_balance,
)
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
    require_admin,
)

from modules.credit_cards import init_credit_db


LOG = logging.getLogger(__name__)

APPLICATION_MINIMUM = 100_000_000
RETENTION_MINIMUM = 90_000_000
MIN_AGE = 20

WEEKLY_POINTS = 200
DAILY_ACTIONS = 2

DAY = timedelta(days=1)
WEEK = timedelta(days=7)


def now_utc():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.isoformat(
        timespec="seconds"
    )


def parse_time(value):
    return datetime.fromisoformat(value)


def init_black_card_db():
    """先確認一般信用卡資料表已建立。"""

    init_credit_db()

    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS
            black_card_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                review_note TEXT NOT NULL DEFAULT '',
                UNIQUE(guild_id, id),
                FOREIGN KEY(guild_id, character_id)
                    REFERENCES characters(guild_id, id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS
            black_card_memberships (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                card_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                activated_at TEXT NOT NULL,
                downgraded_at TEXT,
                next_weekly_at TEXT NOT NULL,
                next_daily_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, character_id),
                FOREIGN KEY(guild_id, character_id)
                    REFERENCES characters(guild_id, id),
                FOREIGN KEY(guild_id, card_id)
                    REFERENCES credit_cards(guild_id, id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS
            vip_balances (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                free_points INTEGER NOT NULL DEFAULT 0,
                bonus_actions INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id, character_id),
                CHECK(free_points >= 0),
                CHECK(bonus_actions >= 0),
                FOREIGN KEY(guild_id, character_id)
                    REFERENCES characters(guild_id, id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS
            vip_reward_ledger (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                reward_key TEXT NOT NULL,
                reward_type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, reward_key),
                FOREIGN KEY(guild_id, character_id)
                    REFERENCES characters(guild_id, id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS
            vip_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            )
        """)


def personal_savings(conn, guild_id, character_id):
    """只計算個人銀行存款，不計入公司或其他財產。"""

    guild_id = str(guild_id)

    row = conn.execute(
        """
        SELECT id
        FROM accounts
        WHERE guild_id = ?
          AND owner_type = 'character'
          AND owner_id = ?
          AND account_type = 'bank'
        """,
        (
            guild_id,
            str(character_id),
        ),
    ).fetchone()

    savings = 0

    if row is not None:
        savings = account_balance(
            conn,
            guild_id,
            row["id"],
        )

    term = conn.execute(
        """
        SELECT COALESCE(
            SUM(principal), 0
        ) AS total
        FROM bank_deposits
        WHERE guild_id = ?
          AND character_id = ?
          AND status = 'active'
        """,
        (
            guild_id,
            character_id,
        ),
    ).fetchone()

    return savings + term["total"]


def controlled_character(
    conn,
    actor,
    character_id,
):
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
            "找不到已啟用的角色。"
        )

    if row["character_type"] == "official_npc":
        allowed = actor.is_admin
    else:
        allowed = (
            row["owner_id"] == actor.user_id
            or actor.is_admin
        )

    if not allowed:
        raise PermissionDenied(
            "無權操作這名角色。"
        )

    return row


def active_standard_card(
    conn,
    guild_id,
    character_id,
):
    return conn.execute(
        """
        SELECT *
        FROM credit_cards
        WHERE guild_id = ?
          AND character_id = ?
          AND tier = 'standard'
          AND status = 'active'
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            guild_id,
            character_id,
        ),
    ).fetchone()


def apply_black_card(actor, character_id):
    """送出黑卡申請，不會立即核發。"""

    with transaction() as conn:
        character = controlled_character(
            conn,
            actor,
            character_id,
        )

        if (
            character["age"] is None
            or character["age"] < MIN_AGE
        ):
            raise InvalidState(
                "黑卡申請角色必須年滿20歲。"
            )

        savings = personal_savings(
            conn,
            actor.guild_id,
            character_id,
        )

        if savings < APPLICATION_MINIMUM:
            raise InvalidState(
                "黑卡申請需個人銀行存款"
                "至少100,000,000金幣。\n"
                f"目前：{savings:,}"
            )

        card = active_standard_card(
            conn,
            actor.guild_id,
            character_id,
        )

        if card is None:
            raise InvalidState(
                "請先申請並取得有效的一般信用卡，"
                "才能提出黑卡升級申請。"
            )

        pending = conn.execute(
            """
            SELECT 1
            FROM black_card_applications
            WHERE guild_id = ?
              AND character_id = ?
              AND status = 'pending'
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        if pending:
            raise InvalidState(
                "角色已有待審核的黑卡申請。"
            )

        member = conn.execute(
            """
            SELECT status
            FROM black_card_memberships
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        if member and member["status"] == "active":
            raise InvalidState(
                "已經持有有效黑卡。"
            )

        cursor = conn.execute(
            """
            INSERT INTO black_card_applications (
                guild_id,
                character_id,
                applied_at
            )
            VALUES (?, ?, ?)
            """,
            (
                actor.guild_id,
                character_id,
                timestamp(now_utc()),
            ),
        )

        return cursor.lastrowid


def review_black_card(
    actor,
    application_id,
    approve,
    reason,
):
    require_admin(actor)

    if len(reason) > 500:
        raise ValueError(
            "審核意見不得超過500字。"
        )

    with transaction() as conn:
        application = conn.execute(
            """
            SELECT
                a.*,
                c.owner_id,
                c.age,
                c.status AS character_status
            FROM black_card_applications a
            JOIN characters c
              ON c.guild_id = a.guild_id
             AND c.id = a.character_id
            WHERE a.guild_id = ?
              AND a.id = ?
            """,
            (
                actor.guild_id,
                application_id,
            ),
        ).fetchone()

        if (
            application is None
            or application["status"] != "pending"
        ):
            raise InvalidState(
                "黑卡申請不存在或已完成審核。"
            )

        if application["owner_id"] == actor.user_id:
            raise PermissionDenied(
                "不能審核自己持有的角色。"
            )

        current = now_utc()

        if not approve:
            conn.execute(
                """
                UPDATE black_card_applications
                SET status = 'rejected',
                    reviewed_at = ?,
                    reviewed_by = ?,
                    review_note = ?
                WHERE guild_id = ?
                  AND id = ?
                """,
                (
                    timestamp(current),
                    actor.user_id,
                    reason,
                    actor.guild_id,
                    application_id,
                ),
            )

            return "黑卡申請已拒絕。"

        if (
            application["character_status"] != "active"
            or application["age"] is None
            or application["age"] < MIN_AGE
        ):
            raise InvalidState(
                "角色已不符合黑卡申請資格。"
            )

        savings = personal_savings(
            conn,
            actor.guild_id,
            application["character_id"],
        )

        if savings < APPLICATION_MINIMUM:
            raise InvalidState(
                "審核當下個人銀行存款"
                "已低於1億，無法核發黑卡。"
            )

        card = active_standard_card(
            conn,
            actor.guild_id,
            application["character_id"],
        )

        if card is None:
            raise InvalidState(
                "沒有可升級的一般信用卡。"
            )

        previous = conn.execute(
            """
            SELECT *
            FROM black_card_memberships
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                application["character_id"],
            ),
        ).fetchone()

        if previous and previous["status"] == "active":
            raise InvalidState(
                "該角色已持有黑卡。"
            )

        conn.execute(
            """
            UPDATE credit_cards
            SET tier = 'black'
            WHERE guild_id = ?
              AND id = ?
              AND tier = 'standard'
              AND status = 'active'
            """,
            (
                actor.guild_id,
                card["id"],
            ),
        )

        conn.execute(
            """
            INSERT INTO black_card_memberships (
                guild_id,
                character_id,
                card_id,
                status,
                activated_at,
                next_weekly_at,
                next_daily_at
            )
            VALUES (?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(guild_id, character_id)
            DO UPDATE SET
                card_id = excluded.card_id,
                status = 'active',
                activated_at = excluded.activated_at,
                downgraded_at = NULL,
                next_weekly_at = excluded.next_weekly_at,
                next_daily_at = excluded.next_daily_at
            """,
            (
                actor.guild_id,
                application["character_id"],
                card["id"],
                timestamp(current),
                timestamp(current + WEEK),
                timestamp(current + DAY),
            ),
        )

        conn.execute(
            """
            INSERT OR IGNORE INTO vip_balances (
                guild_id,
                character_id
            )
            VALUES (?, ?)
            """,
            (
                actor.guild_id,
                application["character_id"],
            ),
        )

        conn.execute(
            """
            UPDATE black_card_applications
            SET status = 'approved',
                reviewed_at = ?,
                reviewed_by = ?,
                review_note = ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                timestamp(current),
                actor.user_id,
                reason,
                actor.guild_id,
                application_id,
            ),
        )

        return (
            "🖤 黑卡升級成功！\n"
            f"個人銀行存款：{savings:,}\n"
            "VIP福利將依每日及每週週期發放。\n"
            "信用卡原有欠款與分期繼續有效。"
        )


def downgrade_if_needed(conn, guild_id, character_id):
    """檢查保級，必要時自動轉回一般卡。"""

    member = conn.execute(
        """
        SELECT *
        FROM black_card_memberships
        WHERE guild_id = ?
          AND character_id = ?
          AND status = 'active'
        """,
        (
            guild_id,
            character_id,
        ),
    ).fetchone()

    if member is None:
        return False

    savings = personal_savings(
        conn,
        guild_id,
        character_id,
    )

    if savings >= RETENTION_MINIMUM:
        return False

    conn.execute(
        """
        UPDATE credit_cards
        SET tier = 'standard'
        WHERE guild_id = ?
          AND id = ?
          AND tier = 'black'
        """,
        (
            guild_id,
            member["card_id"],
        ),
    )

    conn.execute(
        """
        UPDATE black_card_memberships
        SET status = 'downgraded',
            downgraded_at = ?
        WHERE guild_id = ?
          AND character_id = ?
          AND status = 'active'
        """,
        (
            timestamp(now_utc()),
            guild_id,
            character_id,
        ),
    )

    message = (
        "🖤【銀行黑卡通知】\n"
        "您的個人銀行存款已低於"
        "90,000,000金幣。\n"
        "黑卡已自動降級為一般信用卡，"
        "後續VIP福利停止發放。\n"
        "已有點數、物品和信用卡債務"
        "不會因此消失。\n"
        "重新達到1億後可再次申請。"
    )

    conn.execute(
        """
        INSERT INTO vip_notifications (
            guild_id,
            character_id,
            message,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            guild_id,
            character_id,
            message,
            timestamp(now_utc()),
        ),
    )

    return True


def is_black_card_active(
    conn,
    guild_id,
    character_id,
):
    """商城及其他模組可共用此資格檢查。

    須在可寫入的 transaction() 內呼叫，
    因為資格不符時可能觸發降級。
    """

    downgrade_if_needed(
        conn,
        str(guild_id),
        character_id,
    )

    row = conn.execute(
        """
        SELECT 1
        FROM black_card_memberships m
        JOIN credit_cards c
          ON c.guild_id = m.guild_id
         AND c.id = m.card_id
        WHERE m.guild_id = ?
          AND m.character_id = ?
          AND m.status = 'active'
          AND c.tier = 'black'
          AND c.status = 'active'
        """,
        (
            str(guild_id),
            character_id,
        ),
    ).fetchone()

    return row is not None


def grant_reward(
    conn,
    guild_id,
    character_id,
    key,
    kind,
    amount,
):
    """同一個週期不能領取第二次。"""

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO vip_reward_ledger (
            guild_id,
            character_id,
            reward_key,
            reward_type,
            amount,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            character_id,
            key,
            kind,
            amount,
            timestamp(now_utc()),
        ),
    )

    if cursor.rowcount == 0:
        return False

    if kind == "free_points":
        column = "free_points"
    elif kind == "bonus_actions":
        column = "bonus_actions"
    else:
        raise ValueError(
            "未知的VIP獎勵種類。"
        )

    conn.execute(
        f"""
        UPDATE vip_balances
        SET {column} = {column} + ?
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (
            amount,
            guild_id,
            character_id,
        ),
    )

    return True


def distribute_vip_rewards():
    """處理所有有效黑卡的週期福利。"""

    current = now_utc()

    with get_connection() as conn:
        members = conn.execute(
            """
            SELECT guild_id, character_id
            FROM black_card_memberships
            WHERE status = 'active'
            """
        ).fetchall()

    for item in members:
        try:
            with transaction() as conn:
                guild_id = item["guild_id"]
                character_id = item[
                    "character_id"
                ]

                if not is_black_card_active(
                    conn,
                    guild_id,
                    character_id,
                ):
                    continue

                member = conn.execute(
                    """
                    SELECT *
                    FROM black_card_memberships
                    WHERE guild_id = ?
                      AND character_id = ?
                    """,
                    (
                        guild_id,
                        character_id,
                    ),
                ).fetchone()

                weekly = parse_time(
                    member["next_weekly_at"]
                )

                daily = parse_time(
                    member["next_daily_at"]
                )

                while weekly <= current:
                    grant_reward(
                        conn,
                        guild_id,
                        character_id,
                        (
                            f"black:weekly:"
                            f"{character_id}:"
                            f"{timestamp(weekly)}"
                        ),
                        "free_points",
                        WEEKLY_POINTS,
                    )

                    weekly += WEEK

                while daily <= current:
                    grant_reward(
                        conn,
                        guild_id,
                        character_id,
                        (
                            f"black:daily:"
                            f"{character_id}:"
                            f"{timestamp(daily)}"
                        ),
                        "bonus_actions",
                        DAILY_ACTIONS,
                    )

                    daily += DAY

                conn.execute(
                    """
                    UPDATE black_card_memberships
                    SET next_weekly_at = ?,
                        next_daily_at = ?
                    WHERE guild_id = ?
                      AND character_id = ?
                    """,
                    (
                        timestamp(weekly),
                        timestamp(daily),
                        guild_id,
                        character_id,
                    ),
                )

        except Exception:
            LOG.exception(
                "黑卡VIP福利處理失敗：%s",
                item["character_id"],
            )


def spend_free_points(
    conn,
    guild_id,
    character_id,
    amount,
    unique_key,
):
    """供屬性成長及商城模組呼叫。"""

    if type(amount) is not int or amount <= 0:
        raise ValueError(
            "使用點數必須為正整數。"
        )

    existing = conn.execute(
        """
        SELECT amount
        FROM vip_reward_ledger
        WHERE guild_id = ?
          AND reward_key = ?
          AND reward_type = 'points_spend'
        """,
        (
            str(guild_id),
            unique_key,
        ),
    ).fetchone()

    if existing is not None:
        if existing["amount"] != -amount:
            raise InvalidState(
                "交易編號與原本點數金額不符。"
            )
        return False

    balance = conn.execute(
        """
        SELECT free_points
        FROM vip_balances
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (
            str(guild_id),
            character_id,
        ),
    ).fetchone()

    if (
        balance is None
        or balance["free_points"] < amount
    ):
        raise InvalidState(
            "通用自由點數不足。"
        )

    conn.execute(
        """
        UPDATE vip_balances
        SET free_points = free_points - ?
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (
            amount,
            str(guild_id),
            character_id,
        ),
    )

    conn.execute(
        """
        INSERT INTO vip_reward_ledger (
            guild_id,
            character_id,
            reward_key,
            reward_type,
            amount,
            created_at
        )
        VALUES (?, ?, ?, 'points_spend', ?, ?)
        """,
        (
            str(guild_id),
            character_id,
            unique_key,
            -amount,
            timestamp(now_utc()),
        ),
    )

    return True


def vip_status(actor, character_id):
    with transaction() as conn:
        controlled_character(
            conn,
            actor,
            character_id,
        )

        active = is_black_card_active(
            conn,
            actor.guild_id,
            character_id,
        )

        savings = personal_savings(
            conn,
            actor.guild_id,
            character_id,
        )

        balance = conn.execute(
            """
            SELECT free_points, bonus_actions
            FROM vip_balances
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        points = (
            balance["free_points"]
            if balance else 0
        )

        actions = (
            balance["bonus_actions"]
            if balance else 0
        )

        return (
            f"🖤 **角色 #{character_id} VIP資料**\n"
            f"黑卡狀態：{'有效' if active else '未持有'}\n"
            f"個人銀行存款：{savings:,}\n"
            f"通用自由點數：{points:,}\n"
            f"免費行動值：{actions:,}\n"
            "VIP隱藏商城："
            f"{'已解鎖' if active else '未解鎖'}"
        )


class BlackCard(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_black_card_db
        )

        self.vip_scheduler.start()

    async def cog_unload(self):
        self.vip_scheduler.cancel()

    @tasks.loop(hours=1)
    async def vip_scheduler(self):
        await asyncio.to_thread(
            distribute_vip_rewards
        )

        await self.send_notifications()

    @vip_scheduler.before_loop
    async def before_vip_scheduler(self):
        await self.bot.wait_until_ready()

    async def send_notifications(self):
        """向可辨識的角色擁有者發送黑卡通知。"""

        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    n.id,
                    n.guild_id,
                    n.message,
                    c.owner_id
                FROM vip_notifications n
                JOIN characters c
                  ON c.guild_id = n.guild_id
                 AND c.id = n.character_id
                WHERE n.delivered_at IS NULL
                ORDER BY n.id
                LIMIT 30
                """
            ).fetchall()

        for notice in rows:
            try:
                user_id = int(
                    notice["owner_id"]
                )

                user = (
                    self.bot.get_user(user_id)
                    or await self.bot.fetch_user(user_id)
                )

                await user.send(
                    notice["message"]
                )

            except (
                ValueError,
                discord.Forbidden,
                discord.NotFound,
                discord.HTTPException,
            ):
                # 若無法私訊，通知保留在資料庫，
                # 之後可由管理員查看。
                continue

            with transaction() as conn:
                conn.execute(
                    """
                    UPDATE vip_notifications
                    SET delivered_at = ?
                    WHERE id = ?
                      AND delivered_at IS NULL
                    """,
                    (
                        timestamp(now_utc()),
                        notice["id"],
                    ),
                )

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
            sqlite3.Error,
        ) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="黑卡申請",
        description="申請億元存款黑卡VIP",
    )
    async def apply(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )

            application_id = apply_black_card(
                actor,
                角色編號,
            )

            return (
                f"🖤 黑卡申請 #{application_id} "
                "已送交官方銀行審核。"
            )

        await self.execute(
            interaction,
            work,
        )

    @app_commands.command(
        name="黑卡待審",
        description="官方查看待審黑卡申請",
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
                    SELECT id, character_id, applied_at
                    FROM black_card_applications
                    WHERE guild_id = ?
                      AND status = 'pending'
                    ORDER BY id
                    LIMIT 20
                    """,
                    (actor.guild_id,),
                ).fetchall()

            if not rows:
                return "目前沒有待審黑卡申請。"

            return "\n".join(
                (
                    f"申請#{row['id']}｜"
                    f"角色#{row['character_id']}｜"
                    f"{row['applied_at'][:10]}"
                )
                for row in rows
            )

        await self.execute(
            interaction,
            work,
        )

    @app_commands.command(
        name="黑卡審核",
        description="官方銀行核准或拒絕黑卡",
    )
    async def review(
        self,
        interaction: discord.Interaction,
        申請編號: int,
        通過: bool,
        審核意見: str = "",
    ):
        await self.execute(
            interaction,
            lambda: review_black_card(
                Actor.from_interaction(
                    interaction
                ),
                申請編號,
                通過,
                審核意見,
            ),
        )

    @app_commands.command(
        name="黑卡",
        description="查詢黑卡資格、點數及行動值",
    )
    async def status(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        await self.execute(
            interaction,
            lambda: vip_status(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
            ),
        )

    @app_commands.command(
        name="黑卡通知",
        description="查看角色尚未送達的銀行通知",
    )
    async def notifications(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )

            with get_connection() as conn:
                controlled_character(
                    conn,
                    actor,
                    角色編號,
                )

                rows = conn.execute(
                    """
                    SELECT message
                    FROM vip_notifications
                    WHERE guild_id = ?
                      AND character_id = ?
                      AND delivered_at IS NULL
                    ORDER BY id DESC
                    LIMIT 5
                    """,
                    (
                        actor.guild_id,
                        角色編號,
                    ),
                ).fetchall()

            if not rows:
                return "目前沒有未送達通知。"

            return "\n\n".join(
                row["message"]
                for row in rows
            )

        await self.execute(
            interaction,
            work,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        BlackCard(bot)
    )
