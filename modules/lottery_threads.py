
"""Discord RP｜討論串／論壇帖子抽獎系統。

功能：
- 一般文字頻道的討論串可抽獎
- 論壇頻道的帖子可抽獎
- 按討論串／帖子名稱匹配獎單
- 同一玩家對同一獎單只能抽一次
- 依剩餘份數隨機抽取
- 金幣獎勵自動發放
- 商品／禮物中獎後等待領取
- 中獎紀錄永久保存
- 金幣重試防止重複入帳

依賴：
- core.database
- core.economy
- modules.lottery_fulfillment
"""

from __future__ import annotations

import logging
import random
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from core.economy import issue_system_reward


LOG = logging.getLogger(__name__)


# ==========================================
# 資料庫初始化
# ==========================================

def init_lottery_db():
    with transaction() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_lottery_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, name)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_lottery_prizes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lottery_id INTEGER NOT NULL
                    REFERENCES rp_lottery_lists(id)
                    ON DELETE CASCADE,
                name TEXT NOT NULL,
                prize_type TEXT NOT NULL
                    CHECK(prize_type IN ('money', 'gift')),
                amount INTEGER NOT NULL DEFAULT 0,
                remaining INTEGER NOT NULL DEFAULT 0,
                CHECK(amount >= 0),
                CHECK(remaining >= 0)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_lottery_draws (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                lottery_id INTEGER NOT NULL
                    REFERENCES rp_lottery_lists(id),
                user_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                prize_id INTEGER NOT NULL
                    REFERENCES rp_lottery_prizes(id),
                state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, lottery_id, user_id)
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_lottery_draw_user
            ON rp_lottery_draws(guild_id, user_id)
        """)


# ==========================================
# 權限與訊息
# ==========================================

def is_owner(interaction: discord.Interaction) -> bool:
    return bool(
        interaction.guild
        and interaction.guild.owner_id == interaction.user.id
    )


async def tell(
    interaction: discord.Interaction,
    content: str,
):
    content = content[:1900]

    if interaction.response.is_done():
        await interaction.followup.send(
            content,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            content,
            ephemeral=True,
        )


def safe_text(value: str) -> str:
    return discord.utils.escape_mentions(str(value))


# ==========================================
# 獎單查詢
# ==========================================

def find_list(guild_id, name: str):
    """只尋找啟用中的同名獎單。"""

    with get_connection() as db:
        return db.execute(
            """
            SELECT *
            FROM rp_lottery_lists
            WHERE guild_id=?
              AND name=?
              AND enabled=1
            """,
            (
                str(guild_id),
                name.strip(),
            ),
        ).fetchone()


def find_any_list(guild_id, name: str):
    """官方管理用途，包含停用的獎單。"""

    with get_connection() as db:
        return db.execute(
            """
            SELECT *
            FROM rp_lottery_lists
            WHERE guild_id=?
              AND name=?
            """,
            (
                str(guild_id),
                name.strip(),
            ),
        ).fetchone()


# ==========================================
# 抽獎核心
# ==========================================

def make_draw(
    guild_id,
    user_id,
    thread_id,
    lottery_id,
):
    """同一交易內完成選獎、扣除庫存和建立中獎紀錄。"""

    with transaction() as db:

        existing = db.execute(
            """
            SELECT *
            FROM rp_lottery_draws
            WHERE guild_id=?
              AND lottery_id=?
              AND user_id=?
            """,
            (
                str(guild_id),
                int(lottery_id),
                str(user_id),
            ),
        ).fetchone()

        if existing:
            return dict(existing), False

        # 再次確認獎單啟用狀態，避免已關閉獎單被抽取。
        lottery = db.execute(
            """
            SELECT id
            FROM rp_lottery_lists
            WHERE id=?
              AND guild_id=?
              AND enabled=1
            """,
            (
                int(lottery_id),
                str(guild_id),
            ),
        ).fetchone()

        if lottery is None:
            raise ValueError("此獎單尚未建立")

        prizes = db.execute(
            """
            SELECT id, remaining
            FROM rp_lottery_prizes
            WHERE lottery_id=?
              AND remaining>0
            ORDER BY id
            """,
            (int(lottery_id),),
        ).fetchall()

        total = sum(
            int(prize["remaining"])
            for prize in prizes
        )

        if total <= 0:
            raise ValueError(
                "這張獎單的獎品已經抽完了。"
            )

        # 每一份剩餘獎品都擁有相同的中獎機會。
        index = random.randrange(total)

        chosen_id = None

        for prize in prizes:
            count = int(prize["remaining"])

            if index < count:
                chosen_id = int(prize["id"])
                break

            index -= count

        if chosen_id is None:
            raise RuntimeError(
                "無法選出獎品，請重新嘗試。"
            )

        updated = db.execute(
            """
            UPDATE rp_lottery_prizes
            SET remaining=remaining-1
            WHERE id=?
              AND lottery_id=?
              AND remaining>0
            """,
            (
                chosen_id,
                int(lottery_id),
            ),
        )

        if updated.rowcount != 1:
            raise ValueError(
                "獎品庫存已變動，請重新嘗試。"
            )

        inserted = db.execute(
            """
            INSERT INTO rp_lottery_draws (
                guild_id,
                lottery_id,
                user_id,
                thread_id,
                prize_id,
                state
            )
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (
                str(guild_id),
                int(lottery_id),
                str(user_id),
                str(thread_id),
                chosen_id,
            ),
        )

        return {
            "id": inserted.lastrowid,
            "guild_id": str(guild_id),
            "lottery_id": int(lottery_id),
            "user_id": str(user_id),
            "thread_id": str(thread_id),
            "prize_id": chosen_id,
            "state": "pending",
        }, True


def draw_detail(draw_id):
    """取得中獎結果及獎品資訊。"""

    with get_connection() as db:
        return db.execute(
            """
            SELECT
                d.*,
                p.name AS prize_name,
                p.prize_type,
                p.amount
            FROM rp_lottery_draws d
            JOIN rp_lottery_prizes p
              ON p.id=d.prize_id
            WHERE d.id=?
            """,
            (int(draw_id),),
        ).fetchone()


def mark_done(draw_id):
    """僅在金幣成功發放後，標記已完成。

    禮物也可標記 done，代表抽獎結果已確認；
    實際是否入背包須另外檢查領取紀錄。
    """

    with transaction() as db:
        db.execute(
            """
            UPDATE rp_lottery_draws
            SET state='done'
            WHERE id=?
              AND state!='done'
            """,
            (int(draw_id),),
        )


# ==========================================
# Discord 抽獎指令
# ==========================================

class LotteryCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_lottery_db()

    @app_commands.command(
        name="獎單建立",
        description="群主建立討論串／論壇帖子對應獎單",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        名稱="必須與討論串或論壇帖子名稱相同",
    )
    async def create_list(
        self,
        interaction: discord.Interaction,
        名稱: str,
    ):
        if not is_owner(interaction):
            return await tell(
                interaction,
                "⛔ 只有群主能建立獎單。",
            )

        name = 名稱.strip()

        if not 1 <= len(name) <= 100:
            return await tell(
                interaction,
                "⚠️ 獎單名稱須為 1～100 字。",
            )

        try:
            with transaction() as db:
                db.execute(
                    """
                    INSERT INTO rp_lottery_lists (
                        guild_id,
                        name,
                        created_by
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        str(interaction.guild_id),
                        name,
                        str(interaction.user.id),
                    ),
                )

        except sqlite3.IntegrityError:
            return await tell(
                interaction,
                "⚠️ 同一伺服器已有同名獎單。",
            )

        except sqlite3.Error:
            LOG.exception("建立獎單失敗")

            return await tell(
                interaction,
                "⚠️ 建立獎單失敗，請查看 Railway 日誌。",
            )

        await tell(
            interaction,
            (
                f"✅ 已建立獎單「{safe_text(name)}」。\n"
                "接著使用 `/獎單獎品` 加入獎品，"
                "或從 `/官方` 的內容建立中心進行管理。"
            ),
        )

    @app_commands.command(
        name="獎單獎品",
        description="群主為指定獎單新增獎品及數量",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        獎單名稱="獎單名稱",
        獎品名稱="獎品顯示名稱",
        類型="金幣或禮物／商品",
        數量="可抽出的獎品份數",
        金額="每份金幣獎勵，禮物請填 0",
    )
    @app_commands.choices(
        類型=[
            app_commands.Choice(
                name="金幣（自動發放）",
                value="money",
            ),
            app_commands.Choice(
                name="禮物／商品（中獎後領取）",
                value="gift",
            ),
        ]
    )
    async def add_prize(
        self,
        interaction: discord.Interaction,
        獎單名稱: str,
        獎品名稱: str,
        類型: app_commands.Choice[str],
        數量: app_commands.Range[int, 1, 100000],
        金額: app_commands.Range[int, 0, 100000000] = 0,
    ):
        if not is_owner(interaction):
            return await tell(
                interaction,
                "⛔ 只有群主能設定獎品。",
            )

        lottery = find_any_list(
            interaction.guild_id,
            獎單名稱,
        )

        if lottery is None:
            return await tell(
                interaction,
                "此獎單尚未建立",
            )

        name = 獎品名稱.strip()

        if not 1 <= len(name) <= 100:
            return await tell(
                interaction,
                "⚠️ 獎品名稱須為 1～100 字。",
            )

        kind = 類型.value

        if kind == "money" and 金額 <= 0:
            return await tell(
                interaction,
                "⚠️ 金幣獎勵金額必須大於 0。",
            )

        if kind == "gift" and 金額 != 0:
            return await tell(
                interaction,
                "⚠️ 禮物／商品類型的金額必須填 0。",
            )

        try:
            with transaction() as db:
                db.execute(
                    """
                    INSERT INTO rp_lottery_prizes (
                        lottery_id,
                        name,
                        prize_type,
                        amount,
                        remaining
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(lottery["id"]),
                        name,
                        kind,
                        int(金額),
                        int(數量),
                    ),
                )

        except sqlite3.Error:
            LOG.exception("新增獎單獎品失敗")

            return await tell(
                interaction,
                "⚠️ 無法新增獎品，請查看 Railway 日誌。",
            )

        await tell(
            interaction,
            (
                f"✅ 已為「{safe_text(獎單名稱)}」"
                f"新增「{safe_text(name)}」"
                f" ×{數量}。"
            ),
        )

    @app_commands.command(
        name="獎單查看",
        description="查看獎單獎品與剩餘數量",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        獎單名稱="要查詢的獎單名稱",
    )
    async def show_list(
        self,
        interaction: discord.Interaction,
        獎單名稱: str,
    ):
        lottery = find_any_list(
            interaction.guild_id,
            獎單名稱,
        )

        if lottery is None:
            return await tell(
                interaction,
                "此獎單尚未建立",
            )

        with get_connection() as db:
            prizes = db.execute(
                """
                SELECT
                    id,
                    name,
                    prize_type,
                    amount,
                    remaining
                FROM rp_lottery_prizes
                WHERE lottery_id=?
                ORDER BY id
                """,
                (int(lottery["id"]),),
            ).fetchall()

        lines = [
            f"🎟️ 獎單：{safe_text(lottery['name'])}",
            (
                "狀態：啟用"
                if lottery["enabled"]
                else "狀態：停用"
            ),
        ]

        if not prizes:
            lines.append(
                "目前這張獎單尚未加入獎品。"
            )

        for prize in prizes:
            if prize["prize_type"] == "money":
                detail = (
                    f"每份 {prize['amount']} 金幣"
                )
            else:
                detail = "商品／禮物（待領取）"

            lines.append(
                f"• #{prize['id']} "
                f"{safe_text(prize['name'])}"
                f" ×{prize['remaining']}｜{detail}"
            )

        await tell(
            interaction,
            "\n".join(lines),
        )

    @app_commands.command(
        name="抽獎",
        description="在討論串或論壇帖子內抽取同名獎單",
    )
    @app_commands.guild_only()
    async def draw(
        self,
        interaction: discord.Interaction,
    ):
        # Discord 的論壇帖子本質上也是 Thread。
        if (
            interaction.guild_id is None
            or not isinstance(
                interaction.channel,
                discord.Thread,
            )
        ):
            return await tell(
                interaction,
                "⚠️ 請在討論串或論壇帖子內使用 `/抽獎`。",
            )

        thread_name = (
            interaction.channel.name or ""
        ).strip()

        lottery = find_list(
            interaction.guild_id,
            thread_name,
        )

        if lottery is None:
            return await tell(
                interaction,
                "此獎單尚未建立",
            )

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            draw, is_new = make_draw(
                interaction.guild_id,
                interaction.user.id,
                interaction.channel.id,
                lottery["id"],
            )

            item = draw_detail(
                draw["id"]
            )

            if item is None:
                raise ValueError(
                    "找不到中獎紀錄，請聯絡官方。"
                )

            prize_name = safe_text(
                item["prize_name"]
            )

            # 已抽過且完成金幣發獎的情況。
            if (
                item["prize_type"] == "money"
                and item["state"] == "done"
            ):
                return await tell(
                    interaction,
                    (
                        "🎟️ 你已經抽過這張獎單。\n"
                        f"中獎獎品：{prize_name}"
                    ),
                )

            # 禮物中獎紀錄不應再次抽取或重複扣庫存。
            if item["prize_type"] == "gift":
                if not is_new:
                    return await tell(
                        interaction,
                        (
                            "🎟️ 你已經抽過這張獎單。\n"
                            f"中獎獎品：{prize_name}\n"
                            "可使用 `/抽獎紀錄` "
                            "查詢實際領取狀態。"
                        ),
                    )

                # gift 的 done 只代表抽獎結果已確認，
                # 不代表商品已進入角色背包。
                mark_done(
                    item["id"]
                )

                return await tell(
                    interaction,
                    (
                        f"🎉 恭喜抽中「{prize_name}」！\n"
                        "✅ 已保存中獎紀錄。\n"
                        "🎒 如官方已綁定商城商品，"
                        "請使用 `/抽獎紀錄` 查詢編號，"
                        "再用 `/抽獎商品領取` 領獎。"
                    ),
                )

            # 金幣中獎時，允許 pending 紀錄重試發放。
            if item["prize_type"] == "money":

                issue_system_reward(
                    str(interaction.guild_id),
                    (
                        f"lottery:"
                        f"{interaction.guild_id}:"
                        f"{item['id']}"
                    ),
                    # 財務核心要求 player，
                    # 不可使用 user。
                    "player",
                    str(interaction.user.id),
                    int(item["amount"]),
                    (
                        f"抽獎獎勵：{lottery['name']}"
                        f"／{item['prize_name']}"
                    ),
                )

                # 只有成功入帳後才標記完成。
                mark_done(
                    item["id"]
                )

                return await tell(
                    interaction,
                    (
                        f"🎉 恭喜抽中「{prize_name}」！\n"
                        f"💰 已發放 "
                        f"{item['amount']:,} 金幣。"
                    ),
                )

            raise ValueError(
                "未知的抽獎獎品類型。"
            )

        except (ValueError, sqlite3.Error) as exc:
            LOG.exception(
                "抽獎資料或結算失敗"
            )

            await tell(
                interaction,
                f"⚠️ 抽獎失敗：{safe_text(exc)}",
            )

        except Exception:
            LOG.exception(
                "抽獎發獎失敗："
                "中獎紀錄保留供重試"
            )

            await tell(
                interaction,
                (
                    "⚠️ 獎勵暫時無法入帳。\n"
                    "中獎結果已保留，"
                    "不會重新抽取或重複扣除庫存。\n"
                    "請稍後在同一獎單重新使用 `/抽獎`。"
                ),
            )


# ==========================================
# Discord 模組載入入口
# ==========================================

async def setup(
    bot: commands.Bot,
):
    await bot.add_cog(
        LotteryCog(bot)
    )

    LOG.info(
        "討論串／論壇抽獎系統已載入。"
    )
