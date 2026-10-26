
"""Discord RP：群主專用測試角色。

- 僅伺服器擁有者可以建立
- 每位群主在每個伺服器最多建立一名
- 直接啟用，不經一般創角審核
- 獨立記錄測試角色身分
- 為後續各模組提供測試豁免判斷

注意：
其他遊戲模組必須呼叫 is_test_character()
才會真正套用測試豁免。
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    create_character,
    get_connection,
    transaction,
)


TEST_STAMINA = 1_000_000


def init_test_db():
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS
            special_test_characters (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                owner_id TEXT NOT NULL,
                unlimited INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, character_id),
                UNIQUE (guild_id, owner_id)
            )
        """)


def is_test_character(
    conn,
    guild_id,
    character_id,
):
    """供其他模組檢查角色是否有測試豁免。"""

    row = conn.execute(
        """
        SELECT unlimited
        FROM special_test_characters
        WHERE guild_id = ?
          AND character_id = ?
        """,
        (
            str(guild_id),
            int(character_id),
        ),
    ).fetchone()

    return bool(row and row["unlimited"])


def get_existing_test_character(
    guild_id,
    owner_id,
):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT character_id
            FROM special_test_characters
            WHERE guild_id = ?
              AND owner_id = ?
            """,
            (
                str(guild_id),
                str(owner_id),
            ),
        ).fetchone()


def create_test_character(
    guild_id,
    owner_id,
    name,
):
    guild_id = str(guild_id)
    owner_id = str(owner_id)
    name = name.strip()

    if not 1 <= len(name) <= 80:
        raise ValueError(
            "測試角色姓名需為1至80字。"
        )

    existing = get_existing_test_character(
        guild_id,
        owner_id,
    )

    if existing:
        return (
            existing["character_id"],
            False,
        )

    character_id = create_character(
        guild_id,
        owner_id,
        name,
        25,
        "系統測試角色",
    )

    with transaction() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(characters)"
            ).fetchall()
        }

        updates = ["status = 'active'"]
        values = []

        if "stamina" in columns:
            updates.append("stamina = ?")
            values.append(TEST_STAMINA)

        if "max_stamina" in columns:
            updates.append("max_stamina = ?")
            values.append(TEST_STAMINA)

        values.extend([
            guild_id,
            character_id,
            owner_id,
        ])

        changed = conn.execute(
            f"""
            UPDATE characters
            SET {", ".join(updates)}
            WHERE guild_id = ?
              AND id = ?
              AND owner_id = ?
            """,
            values,
        )

        if changed.rowcount != 1:
            raise RuntimeError(
                "測試角色建立後，無法更新角色資料。"
            )

        conn.execute(
            """
            INSERT INTO special_test_characters (
                guild_id,
                character_id,
                owner_id,
                unlimited
            )
            VALUES (?, ?, ?, 1)
            """,
            (
                guild_id,
                character_id,
                owner_id,
            ),
        )

    return character_id, True


class TestCharacter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_test_db
        )

    @app_commands.command(
        name="測試角建立",
        description="群主建立專用的測試角色",
    )
    @app_commands.guild_only()
    async def create_test(
        self,
        interaction: discord.Interaction,
        名稱: str = "系統測試員",
    ):
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "只能在 Discord 伺服器使用。",
                ephemeral=True,
            )
            return

        if guild.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "⛔ 只有伺服器擁有者"
                "可以建立無限制測試角。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            cid, created = await asyncio.to_thread(
                create_test_character,
                guild.id,
                interaction.user.id,
                名稱,
            )

            if created:
                text = (
                    "🧪 測試角色已建立！\n\n"
                    f"角色編號：#{cid}\n"
                    f"角色姓名：{名稱}\n"
                    "角色狀態：已啟用\n"
                    "測試標記：無限制模式\n\n"
                    "其他模組的無限制豁免"
                    "仍須逐一接入。"
                )
            else:
                text = (
                    "你已經有一名測試角色："
                    f"#{cid}\n"
                    "不會重複建立。"
                )

        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "建立測試角色失敗"
            )
            text = (
                "⚠️ 建立測試角失敗，"
                "請查看 Railway Logs。"
            )

        await interaction.followup.send(
            text,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        TestCharacter(bot)
    )
