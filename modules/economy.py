
"""Discord RP 銀行、活期利息、定存與角色轉帳。"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import get_connection, transaction
from core.economy import (
    EconomyError,
    get_balance,
    transfer,
    issue_system_reward,
)
from core.permissions import (
    Actor,
    PermissionDenied,
    InvalidState,
    require_admin,
)


LOG = logging.getLogger(__name__)
DAY = 86400
YEAR = 365


def utcnow():
    return datetime.now(timezone.utc)


def stamp(dt):
    return dt.isoformat(timespec="seconds")


def parse_stamp(value):
    return datetime.fromisoformat(value)


def init_bank():
    """建立銀行專用資料表。"""

    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_rates (
                guild_id TEXT PRIMARY KEY,
                annual_bps INTEGER NOT NULL
                    CHECK(annual_bps BETWEEN 0 AND 10000),
                changed_at TEXT NOT NULL,
                changed_by TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                principal INTEGER NOT NULL
                    CHECK(principal > 0),
                annual_bps INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                matures_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                settled_at TEXT,
                UNIQUE(guild_id, id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bank_interest_days (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                last_day TEXT NOT NULL,
                PRIMARY KEY(guild_id, character_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_bank_deposits_due
            ON bank_deposits(status, matures_at)
        """)


def character_access(actor, character_id):
    """驗證操作者有權使用角色的銀行帳戶。"""

    with get_connection() as conn:
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

    if not row or row["status"] != "active":
        raise InvalidState(
            "角色不存在或尚未啟用。"
        )

    owner = row["owner_id"] == actor.user_id

    if row["character_type"] == "official_npc":
        allowed = actor.is_admin
    else:
        allowed = owner or actor.is_admin

    if not allowed:
        raise PermissionDenied(
            "沒有操作該角色銀行帳戶的權限。"
        )

    return row



def resolve_actor_character(actor, identifier):
    """解析角色編號或完整名稱；不更改玩家的全域角色狀態。"""
    value = str(identifier).strip()
    if not value:
        raise ValueError("請輸入角色編號或完整名稱。")
    if value.isdecimal():
        cid = int(value)
        character_access(actor, cid)
        return cid
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM characters WHERE guild_id=? AND name=? AND status='active'",
            (actor.guild_id, value),
        ).fetchall()
    valid = []
    for row in rows:
        try:
            character_access(actor, int(row['id']))
        except (PermissionDenied, InvalidState):
            continue
        valid.append(int(row['id']))
    if not valid:
        raise ValueError("找不到可操作的已啟用角色，請檢查編號或完整名稱。")
    if len(valid) > 1:
        raise ValueError("角色名稱重複，請改用角色編號。")
    return valid[0]

def target_character(guild_id, character_id):
    """確認收款角色存在並已啟用。"""

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, status
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                str(guild_id),
                character_id,
            ),
        ).fetchone()

    if not row or row["status"] != "active":
        raise InvalidState(
            "收款角色不存在或尚未啟用。"
        )

    return row


def rate_for(guild_id):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT annual_bps
            FROM bank_rates
            WHERE guild_id = ?
            """,
            (str(guild_id),),
        ).fetchone()

    return (
        None
        if row is None
        else row["annual_bps"]
    )


def annual_interest(principal, bps, days=1):
    """按年利率基點計算整數金幣利息。"""

    return (
        principal * bps * days
        // (10000 * YEAR)
    )


def bank_move(actor, character_id, amount, direction):
    """活存和現金之間轉移資金。"""

    character_access(actor, character_id)

    if type(amount) is not int or amount <= 0:
        raise ValueError(
            "金額必須是正整數。"
        )

    if direction == "deposit":
        source = "wallet"
        destination = "bank"
    else:
        source = "bank"
        destination = "wallet"

    previous_bank_balance = get_balance(
        actor.guild_id,
        "character",
        character_id,
        "bank",
    )

    result = transfer(
        guild_id=actor.guild_id,
        unique_key=f"bank:{uuid4().hex}",
        sender_type="character",
        sender_owner_id=character_id,
        receiver_type="character",
        receiver_owner_id=character_id,
        amount=amount,
        description=f"銀行{direction}",
        sender_account_type=source,
        receiver_account_type=destination,
    )

    # 首次存入活存時建立計息起點，
    # 不允許新存入的金錢領取過去的利息。
    if (
        direction == "deposit"
        and previous_bank_balance == 0
    ):
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bank_interest_days (
                    guild_id,
                    character_id,
                    last_day
                )
                VALUES (?, ?, ?)
                """,
                (
                    actor.guild_id,
                    character_id,
                    utcnow().date().isoformat(),
                ),
            )

    return result


def free_transfer(
    actor,
    sender_character,
    receiver_character,
    amount,
):
    """角色間零手續費轉帳。"""

    character_access(
        actor,
        sender_character,
    )

    target_character(
        actor.guild_id,
        receiver_character,
    )

    if sender_character == receiver_character:
        raise ValueError(
            "不能轉帳給同一個角色。"
        )

    return transfer(
        guild_id=actor.guild_id,
        unique_key=(
            f"character_transfer:{uuid4().hex}"
        ),
        sender_type="character",
        sender_owner_id=sender_character,
        receiver_type="character",
        receiver_owner_id=receiver_character,
        amount=amount,
        description="角色之間免費轉帳",
    )


def create_term(
    actor,
    character_id,
    amount,
    days,
):
    """建立一筆定期存款。"""

    character_access(
        actor,
        character_id,
    )

    if days not in (14, 30):
        raise ValueError(
            "定存期限只提供14天或30天。"
        )

    if type(amount) is not int or amount <= 0:
        raise ValueError(
            "存入金額必須是正整數。"
        )

    rate = rate_for(actor.guild_id)

    if rate is None:
        raise InvalidState(
            "銀行尚未設定官方利率。"
        )

    if get_balance(
        actor.guild_id,
        "character",
        character_id,
        "bank",
    ) < amount:
        raise InvalidState(
            "活期存款不足。"
        )

    opened = utcnow()

    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO bank_deposits (
                guild_id,
                character_id,
                principal,
                annual_bps,
                opened_at,
                matures_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                character_id,
                amount,
                rate,
                stamp(opened),
                stamp(
                    opened + timedelta(days=days)
                ),
            ),
        )

        deposit_id = cursor.lastrowid

    try:
        transfer(
            guild_id=actor.guild_id,
            unique_key=(
                f"term:deposit:{deposit_id}"
            ),
            sender_type="character",
            sender_owner_id=character_id,
            receiver_type="escrow",
            receiver_owner_id=(
                f"term:{deposit_id}"
            ),
            amount=amount,
            description=(
                f"定期存款#{deposit_id}"
            ),
            sender_account_type="bank",
            receiver_account_type="escrow",
        )

    except Exception:
        with transaction() as conn:
            conn.execute(
                """
                DELETE FROM bank_deposits
                WHERE guild_id = ?
                  AND id = ?
                  AND status = 'active'
                """,
                (
                    actor.guild_id,
                    deposit_id,
                ),
            )
        raise

    return deposit_id


def settle_term(
    guild_id,
    deposit_id,
    *,
    early=False,
    actor=None,
):
    """結算到期定存或提前解約。"""

    guild_id = str(guild_id)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM bank_deposits
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                guild_id,
                deposit_id,
            ),
        ).fetchone()

    if row is None:
        raise InvalidState(
            "定存不存在。"
        )

    if row["status"] == "closed":
        return False

    if actor is not None:
        if guild_id != actor.guild_id:
            raise PermissionDenied(
                "不能跨伺服器操作定存。"
            )

        character_access(
            actor,
            row["character_id"],
        )

    now = utcnow()

    maturity = parse_stamp(
        row["matures_at"]
    )

    if not early and now < maturity:
        return False

    ended = min(now, maturity)

    full_days = max(
        0,
        int(
            (
                ended
                - parse_stamp(row["opened_at"])
            ).total_seconds() // DAY
        ),
    )

    earned = annual_interest(
        row["principal"],
        row["annual_bps"],
        full_days,
    )

    if early and now < maturity:
        paid_interest = earned // 5
    else:
        paid_interest = earned

    # 結算交易使用固定識別碼，
    # 避免排程重試時重複發放本金。
    transfer(
        guild_id=guild_id,
        unique_key=(
            f"term:principal:return:{deposit_id}"
        ),
        sender_type="escrow",
        sender_owner_id=(
            f"term:{deposit_id}"
        ),
        receiver_type="character",
        receiver_owner_id=row["character_id"],
        amount=row["principal"],
        description=(
            f"定存#{deposit_id}退回本金"
        ),
        sender_account_type="escrow",
        receiver_account_type="bank",
    )

    if paid_interest:
        issue_system_reward(
            guild_id,
            f"term:interest:{deposit_id}",
            "character",
            row["character_id"],
            paid_interest,
            f"定存#{deposit_id}利息",
        )

        transfer(
            guild_id=guild_id,
            unique_key=(
                f"term:interest:bank:{deposit_id}"
            ),
            sender_type="character",
            sender_owner_id=row["character_id"],
            receiver_type="character",
            receiver_owner_id=row["character_id"],
            amount=paid_interest,
            description="定存利息入帳",
            sender_account_type="wallet",
            receiver_account_type="bank",
        )

    with transaction() as conn:
        conn.execute(
            """
            UPDATE bank_deposits
            SET status = 'closed',
                settled_at = ?
            WHERE guild_id = ?
              AND id = ?
              AND status = 'active'
            """,
            (
                stamp(now),
                guild_id,
                deposit_id,
            ),
        )

    return True


def accrue_daily(
    guild_id,
    character_id,
    day,
):
    """每個日期最多發放一次活期利息。"""

    guild_id = str(guild_id)

    rate = rate_for(guild_id)

    if rate is None:
        return

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT last_day
            FROM bank_interest_days
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                guild_id,
                character_id,
            ),
        ).fetchone()

    if row and row["last_day"] >= day:
        return

    principal = get_balance(
        guild_id,
        "character",
        character_id,
        "bank",
    )

    amount = annual_interest(
        principal,
        rate,
    )

    if amount:
        issue_system_reward(
            guild_id,
            (
                f"bank:interest:"
                f"{character_id}:{day}"
            ),
            "character",
            character_id,
            amount,
            "活期每日利息",
        )

        transfer(
            guild_id=guild_id,
            unique_key=(
                f"bank:interest:deposit:"
                f"{character_id}:{day}"
            ),
            sender_type="character",
            sender_owner_id=character_id,
            receiver_type="character",
            receiver_owner_id=character_id,
            amount=amount,
            description="活期每日利息入帳",
            sender_account_type="wallet",
            receiver_account_type="bank",
        )

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO bank_interest_days (
                guild_id,
                character_id,
                last_day
            )
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, character_id)
            DO UPDATE SET
                last_day = excluded.last_day
            """,
            (
                guild_id,
                character_id,
                day,
            ),
        )


class BankOperationModal(discord.ui.Modal):
    """銀行面板操作：沿用既有交易與定存核心。"""

    def __init__(self, view, operation):
        titles = {
            "deposit": "存入活期", "withdraw": "提領現金",
            "transfer": "角色轉帳", "term": "建立定存",
            "close": "定存提前解約",
        }
        super().__init__(title=titles[operation], timeout=300)
        self.bank_view = view
        self.operation = operation
        if operation != "close":
            self.amount = discord.ui.TextInput(
                label="金額（金幣）", placeholder="請輸入正整數",
                min_length=1, max_length=16,
            )
            self.add_item(self.amount)
        if operation == "transfer":
            self.recipient = discord.ui.TextInput(
                label="收款角色編號", placeholder="例如 123",
                min_length=1, max_length=16,
            )
            self.add_item(self.recipient)
        elif operation == "term":
            self.days = discord.ui.TextInput(
                label="定存期限", placeholder="14 或 30",
                min_length=2, max_length=2,
            )
            self.add_item(self.days)
        elif operation == "close":
            self.deposit_id = discord.ui.TextInput(
                label="定存編號", placeholder="可在銀行帳戶列表查看",
                min_length=1, max_length=16,
            )
            self.add_item(self.deposit_id)

    async def on_submit(self, interaction: discord.Interaction):
        v = self.bank_view
        if (interaction.guild_id != v.guild_id
                or interaction.user.id != v.user_id):
            await interaction.response.send_message(
                "⛔ 這不是你的銀行操作面板。", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        def work():
            actor = Actor.from_interaction(interaction)
            character_access(actor, v.character_id)
            if self.operation == "close":
                did = int(self.deposit_id.value)
                if did <= 0:
                    raise ValueError("定存編號必須為正整數。")
                done = settle_term(actor.guild_id, did, early=True, actor=actor)
                return "✅ 定存已結算。" if done else "這筆定存已結清。"
            amount = int(self.amount.value)
            if amount <= 0:
                raise ValueError("金額必須為正整數。")
            if self.operation in ("deposit", "withdraw"):
                bank_move(actor, v.character_id, amount, self.operation)
                return f"✅ 銀行操作成功：{amount:,} 金幣。"
            if self.operation == "transfer":
                receiver = int(self.recipient.value)
                result = free_transfer(actor, v.character_id, receiver, amount)
                return (f"✅ 角色 #{v.character_id} → #{receiver}，"
                        f"{amount:,} 金幣；交易編號：{result.transaction_id}")
            if self.operation == "term":
                days = int(self.days.value)
                if days not in (14, 30):
                    raise ValueError("定存僅提供 14 天或 30 天。")
                did = create_term(actor, v.character_id, amount, days)
                return f"✅ 定存 #{did} 已建立：{amount:,} 金幣／{days} 天。"
            raise ValueError("不支援的銀行操作。")

        try:
            message = await asyncio.to_thread(work)
        except (ValueError, EconomyError, PermissionDenied,
                InvalidState, sqlite3.Error) as exc:
            message = f"⚠️ {exc}"
        except Exception:
            LOG.exception("銀行面板操作失敗：%s", self.operation)
            message = "⚠️ 銀行操作異常，請通知管理員查閱日誌。"
        await interaction.followup.send(message[:1900], ephemeral=True)


class BankAccountView(discord.ui.View):
    """與指定角色綁定的銀行面板；無需更改全域目前角色。"""

    def __init__(self, interaction: discord.Interaction, character_id: int):
        super().__init__(timeout=600)
        self.user_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.character_id = character_id

    async def interaction_check(self, interaction: discord.Interaction):
        if (interaction.guild_id != self.guild_id
                or interaction.user.id != self.user_id):
            await interaction.response.send_message(
                "⛔ 請自行使用 /銀行 開啟帳戶。", ephemeral=True,
            )
            return False
        try:
            await asyncio.to_thread(
                character_access, Actor.from_interaction(interaction),
                self.character_id,
            )
        except (PermissionDenied, InvalidState) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="存款", style=discord.ButtonStyle.primary, row=0)
    async def deposit_button(self, interaction, button):
        await interaction.response.send_modal(BankOperationModal(self, "deposit"))

    @discord.ui.button(label="提款", style=discord.ButtonStyle.primary, row=0)
    async def withdraw_button(self, interaction, button):
        await interaction.response.send_modal(BankOperationModal(self, "withdraw"))

    @discord.ui.button(label="轉帳", style=discord.ButtonStyle.secondary, row=0)
    async def transfer_button(self, interaction, button):
        await interaction.response.send_modal(BankOperationModal(self, "transfer"))

    @discord.ui.button(label="建立定存", style=discord.ButtonStyle.success, row=1)
    async def term_button(self, interaction, button):
        await interaction.response.send_modal(BankOperationModal(self, "term"))

    @discord.ui.button(label="定存解約", style=discord.ButtonStyle.danger, row=1)
    async def close_button(self, interaction, button):
        await interaction.response.send_modal(BankOperationModal(self, "close"))


class Economy(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(init_bank)
        self.settlement.start()

    async def cog_unload(self):
        self.settlement.cancel()

    @tasks.loop(hours=1)
    async def settlement(self):
        try:
            await asyncio.to_thread(
                self._settle_all
            )
        except Exception:
            LOG.exception(
                "銀行排程失敗，下次將重試。"
            )

    @settlement.before_loop
    async def before_settlement(self):
        await self.bot.wait_until_ready()

    def _settle_all(self):
        today = (
            utcnow() - timedelta(days=1)
        ).date().isoformat()

        with get_connection() as conn:
            terms = conn.execute(
                """
                SELECT guild_id, id
                FROM bank_deposits
                WHERE status = 'active'
                  AND matures_at <= ?
                """,
                (stamp(utcnow()),),
            ).fetchall()

            banks = conn.execute(
                """
                SELECT DISTINCT guild_id, owner_id
                FROM accounts
                WHERE owner_type = 'character'
                  AND account_type = 'bank'
                """
            ).fetchall()

        for row in terms:
            try:
                settle_term(
                    row["guild_id"],
                    row["id"],
                )
            except Exception:
                LOG.exception(
                    "定存到期結算失敗：%s",
                    row["id"],
                )

        for row in banks:
            try:
                accrue_daily(
                    row["guild_id"],
                    row["owner_id"],
                    today,
                )
            except Exception:
                LOG.exception(
                    "活期利息結算失敗：%s",
                    row["owner_id"],
                )

    async def handle(
        self,
        interaction,
        func,
        *args,
    ):
        try:
            result = await asyncio.to_thread(
                func,
                *args,
            )

            await interaction.followup.send(
                str(result),
                ephemeral=True,
            )

        except (
            ValueError,
            EconomyError,
            PermissionDenied,
            InvalidState,
            sqlite3.Error,
        ) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="銀行",
        description="查看角色的現金與銀行存款",
    )
    async def bank(
        self,
        interaction: discord.Interaction,
        角色編號: str,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        def work():
            actor = Actor.from_interaction(
                interaction
            )

            character_id = resolve_actor_character(actor, 角色編號)

            cash = get_balance(
                actor.guild_id,
                "character",
                character_id,
                "wallet",
            )

            savings = get_balance(
                actor.guild_id,
                "character",
                character_id,
                "bank",
            )

            with get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, principal, matures_at
                    FROM bank_deposits
                    WHERE guild_id = ?
                      AND character_id = ?
                      AND status = 'active'
                    ORDER BY id DESC
                    LIMIT 10
                    """,
                    (
                        actor.guild_id,
                        character_id,
                    ),
                ).fetchall()

            terms = "\n".join(
                (
                    f"#{row['id']}："
                    f"{row['principal']:,}"
                    f"（{row['matures_at'][:10]}到期）"
                )
                for row in rows
            ) or "無"

            bps = rate_for(actor.guild_id)

            rate = (
                f"{bps / 100:.2f}%"
                if bps is not None
                else "尚未設定"
            )

            return (
                f"🏦 **角色 #{character_id} 銀行**\n"
                f"現金：{cash:,}\n"
                f"活期存款：{savings:,}\n"
                f"目前年利率：{rate}\n"
                f"**定期存款**\n{terms}"
            )

        try:
            actor = Actor.from_interaction(interaction)
            selected_id = await asyncio.to_thread(
                resolve_actor_character, actor, 角色編號,
            )
            content = await asyncio.to_thread(work)
            await interaction.followup.send(
                content[:1900],
                view=BankAccountView(interaction, selected_id),
                ephemeral=True,
            )
        except (ValueError, EconomyError, PermissionDenied,
                InvalidState, sqlite3.Error) as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)

    @app_commands.command(
        name="存款",
        description="把角色現金存入銀行",
    )
    async def deposit(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        金額: app_commands.Range[
            int, 1, 1000000000000000
        ],
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        def work():
            result = bank_move(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
                金額,
                "deposit",
            )

            return (
                f"✅ 存入 {金額:,} 金幣。\n"
                f"活期餘額："
                f"{result.receiver_balance:,}"
            )

        await self.handle(
            interaction,
            work,
        )

    @app_commands.command(
        name="提款",
        description="從銀行提領角色現金",
    )
    async def withdraw(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        金額: app_commands.Range[
            int, 1, 1000000000000000
        ],
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        def work():
            result = bank_move(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
                金額,
                "withdraw",
            )

            return (
                f"✅ 提領 {金額:,} 金幣。\n"
                f"活期餘額："
                f"{result.sender_balance:,}"
            )

        await self.handle(
            interaction,
            work,
        )

    @app_commands.command(
        name="角色轉帳",
        description="角色之間零手續費轉帳",
    )
    async def pay(
        self,
        interaction: discord.Interaction,
        付款角色: int,
        收款角色: int,
        金額: app_commands.Range[
            int, 1, 1000000000000000
        ],
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        def work():
            result = free_transfer(
                Actor.from_interaction(
                    interaction
                ),
                付款角色,
                收款角色,
                金額,
            )

            return (
                f"✅ #{付款角色} → "
                f"#{收款角色}\n"
                f"金額：{金額:,} 金幣\n"
                "手續費：0\n"
                f"交易編號："
                f"{result.transaction_id}"
            )

        await self.handle(
            interaction,
            work,
        )

    @app_commands.command(
        name="銀行利率",
        description="官方設定銀行年利率",
    )
    async def set_rate(
        self,
        interaction: discord.Interaction,
        年利率百分比: app_commands.Range[
            float, 0, 100
        ],
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        def work():
            actor = Actor.from_interaction(
                interaction
            )

            require_admin(actor)

            bps = round(
                年利率百分比 * 100
            )

            now = utcnow()

            with transaction() as conn:
                row = conn.execute(
                    """
                    SELECT changed_at
                    FROM bank_rates
                    WHERE guild_id = ?
                    """,
                    (actor.guild_id,),
                ).fetchone()

                if row:
                    changed = parse_stamp(
                        row["changed_at"]
                    )

                    if (
                        now - changed
                        < timedelta(days=7)
                    ):
                        raise InvalidState(
                            "銀行利率每7天"
                            "最多修改一次。"
                        )

                conn.execute(
                    """
                    INSERT INTO bank_rates (
                        guild_id,
                        annual_bps,
                        changed_at,
                        changed_by
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id)
                    DO UPDATE SET
                        annual_bps =
                            excluded.annual_bps,
                        changed_at =
                            excluded.changed_at,
                        changed_by =
                            excluded.changed_by
                    """,
                    (
                        actor.guild_id,
                        bps,
                        stamp(now),
                        actor.user_id,
                    ),
                )

            return (
                "✅ 官方年利率已設定為 "
                f"{bps / 100:.2f}%。\n"
                "7天內不可再次修改。"
            )

        await self.handle(
            interaction,
            work,
        )

    @app_commands.command(
        name="定存",
        description="辦理14天或30天定期存款",
    )
    @app_commands.choices(
        期限=[
            app_commands.Choice(
                name="14天",
                value=14,
            ),
            app_commands.Choice(
                name="30天",
                value=30,
            ),
        ]
    )
    async def term(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        金額: app_commands.Range[
            int, 1, 1000000000000000
        ],
        期限: app_commands.Choice[int],
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        def work():
            deposit_id = create_term(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
                金額,
                期限.value,
            )

            return (
                f"✅ 定存 #{deposit_id} 已建立。\n"
                f"金額：{金額:,}\n"
                f"期限：{期限.value}天\n"
                "到期後自動轉入活期。"
            )

        await self.handle(
            interaction,
            work,
        )

    @app_commands.command(
        name="定存解約",
        description="提前解約並領回本金及部分利息",
    )
    async def close_term(
        self,
        interaction: discord.Interaction,
        定存編號: int,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        def work():
            actor = Actor.from_interaction(
                interaction
            )

            completed = settle_term(
                actor.guild_id,
                定存編號,
                early=True,
                actor=actor,
            )

            if completed:
                return (
                    "✅ 定存已結算。\n"
                    "本金及依規則計算的利息"
                    "已匯入活期。"
                )

            return "這筆定存已經結清。"

        await self.handle(
            interaction,
            work,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
