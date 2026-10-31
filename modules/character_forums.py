"""Discord RP｜角色二審完成自動建立角色論壇。

指令：
/角色論壇設定
/角色論壇補建

功能：
- 群主指定角色論壇分類
- 偵測 approved / active 角色
- 自動建立角色專屬 ForumChannel
- 建立通知、詢問、劇情討論三個貼文
- 記錄 ID，防止重複建立
- 機器人重啟後自動補建
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)

POSTS = (
    (
        "notice",
        "通知",
        "📢 角色公告與劇情解鎖消息將發布在這裡。",
    ),
    (
        "question",
        "詢問",
        "❓ 在此提出關於角色的問題。",
    ),
    (
        "story",
        "劇情討論",
        "📖 在此討論角色的劇情。",
    ),
    (
        "bot",
        "機器人使用",
        "🤖 角色機器人操作專用帖子。／惡意只可在此使用。",
    ),
)


# ==========================================
# 資料庫
# ==========================================

def initialize():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            character_forum_settings (
                guild_id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            character_forum_links (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                forum_id TEXT,
                notice_id TEXT,
                question_id TEXT,
                story_id TEXT,
                bot_id TEXT,
                PRIMARY KEY(guild_id, character_id)
            )
        """)
        cols = {row["name"] for row in db.execute("PRAGMA table_info(character_forum_links)")}
        if "bot_id" not in cols:
            db.execute("ALTER TABLE character_forum_links ADD COLUMN bot_id TEXT")


def get_category(guild_id):
    with get_connection() as db:
        row = db.execute(
            """
            SELECT category_id
            FROM character_forum_settings
            WHERE guild_id=?
            """,
            (str(guild_id),),
        ).fetchone()

    return int(row["category_id"]) if row else None


def get_characters(guild_id):
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT
                id,
                name,
                owner_id,
                character_type
            FROM characters
            WHERE guild_id=?
              AND status = 'active'
            ORDER BY id ASC
            """,
            (str(guild_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def get_link(guild_id, character_id):
    with get_connection() as db:
        row = db.execute(
            """
            SELECT *
            FROM character_forum_links
            WHERE guild_id=?
              AND character_id=?
            """,
            (
                str(guild_id),
                character_id,
            ),
        ).fetchone()

    return dict(row) if row else {}


def update_link(
    guild_id,
    character_id,
    column,
    value,
):
    allowed = (
        "forum_id",
        "notice_id",
        "question_id",
        "story_id",
        "bot_id",
    )

    if column not in allowed:
        raise ValueError("欄位不合法。")

    with transaction() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO character_forum_links(
                guild_id,
                character_id
            ) VALUES (?,?)
            """,
            (
                str(guild_id),
                character_id,
            ),
        )

        db.execute(
            f"""
            UPDATE character_forum_links
            SET {column}=?
            WHERE guild_id=?
              AND character_id=?
            """,
            (
                str(value),
                str(guild_id),
                character_id,
            ),
        )


def marker(guild_id, character_id):
    return (
        f"RP_CHARACTER_FORUM:"
        f"{guild_id}:{character_id}"
    )


# ==========================================
# 角色論壇 Cog
# ==========================================

class CharacterForums(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self._locks = {}
        self._cycle_lock = asyncio.Lock()

    async def cog_load(self):
        initialize()
        self.scan.start()

    def cog_unload(self):
        self.scan.cancel()

    # --------------------------------------
    # 建立單一角色論壇
    # --------------------------------------

    async def ensure_forum(self, guild, character):
        character_id = character["id"]

        category_id = get_category(guild.id)

        category = (
            guild.get_channel(category_id)
            if category_id
            else None
        )

        if not isinstance(
            category,
            discord.CategoryChannel,
        ):
            raise ValueError(
                "尚未設定有效的角色論壇分類。"
            )

        key = (guild.id, character_id)

        lock = self._locks.setdefault(
            key,
            asyncio.Lock(),
        )

        async with lock:
            data = get_link(
                guild.id,
                character_id,
            )

            forum = (
                guild.get_channel(
                    int(data["forum_id"])
                )
                if data.get("forum_id")
                else None
            )

            if not isinstance(
                forum,
                discord.ForumChannel,
            ):
                # 透過專屬標記恢復已建立的論壇，
                # 不單純依角色名稱猜測，避免同名誤判。
                forum = next(
                    (
                        channel
                        for channel in category.channels
                        if isinstance(
                            channel,
                            discord.ForumChannel,
                        )
                        and channel.topic == marker(
                            guild.id,
                            character_id,
                        )
                    ),
                    None,
                )

                if forum is None:
                    suffix = (
                        "_npc"
                        if character["character_type"]
                        != "player"
                        else ""
                    )

                    forum = await guild.create_forum(
                        name=(
                            character["name"] + suffix
                        )[:100],
                        category=category,
                        topic=marker(
                            guild.id,
                            character_id,
                        ),
                        reason=(
                            f"角色 #{character_id} "
                            "二審完成，自動建立論壇"
                        ),
                    )

                update_link(
                    guild.id,
                    character_id,
                    "forum_id",
                    forum.id,
                )

            data = get_link(
                guild.id,
                character_id,
            )

            # --------------------------------
            # 建立三個固定貼文
            # --------------------------------

            fields = {
                "notice": "notice_id",
                "question": "question_id",
                "story": "story_id",
                "bot": "bot_id",
            }

            for code, title, body in POSTS:
                field = fields[code]
                thread = None

                if data.get(field):
                    thread = guild.get_thread(
                        int(data[field])
                    )

                    if thread is None:
                        try:
                            thread = (
                                await guild.fetch_channel(
                                    int(data[field])
                                )
                            )
                        except discord.NotFound:
                            thread = None

                if (
                    not isinstance(
                        thread,
                        discord.Thread,
                    )
                    or thread.parent_id != forum.id
                ):
                    thread = next(
                        (
                            item
                            for item in forum.threads
                            if item.name == title
                        ),
                        None,
                    )

                    if thread is None:
                        content = body

                        if code == "notice":
                            content = (
                                f"<@{character['owner_id']}>\n"
                                f"{body}"
                            )

                        result = await forum.create_thread(
                            name=title,
                            content=content,
                            allowed_mentions=(
                                discord.AllowedMentions(
                                    users=True,
                                    roles=False,
                                    everyone=False,
                                )
                            ),
                            reason=(
                                f"角色 #{character_id} "
                                "固定貼文"
                            ),
                        )

                        thread = result.thread

                    update_link(
                        guild.id,
                        character_id,
                        field,
                        thread.id,
                    )

                if code == "bot":
                    # 以實際論壇、帖子 ID 登記，不因名稱相同授權。
                    from core.malicious_forum_permissions import register_bot_thread
                    register_bot_thread(
                        guild.id, int(character_id), forum.id, thread.id,
                        setter_id=int(guild.owner_id), owner_authorized=True,
                    )

                # 通知貼文首篇釘選
                if code == "notice":
                    try:
                        starter = (
                            await thread.fetch_message(
                                thread.id
                            )
                        )

                        if not starter.pinned:
                            await starter.pin(
                                reason=(
                                    "角色通知固定貼文"
                                )
                            )

                    except (
                        discord.Forbidden,
                        discord.NotFound,
                    ):
                        LOG.warning(
                            "無法釘選角色 #%s 的通知首篇",
                            character_id,
                        )

            return forum

    # --------------------------------------
    # 批次掃描
    # --------------------------------------

    async def run_scan(self, guild, limit=20):
        processed = 0
        failed = []

        for character in get_characters(guild.id):
            if processed >= limit:
                break

            data = get_link(
                guild.id,
                character["id"],
            )

            fields = (
                "forum_id",
                "notice_id",
                "question_id",
                "story_id",
                "bot_id",
            )

            if all(
                data.get(field)
                for field in fields
            ):
                forum = guild.get_channel(
                    int(data["forum_id"])
                )

                if isinstance(
                    forum,
                    discord.ForumChannel,
                ):
                    # 舊角色可能已有帖子，卻因當時尚未啟用而綁定失敗。
                    # 只有論壇 ID、bot 帖子 ID、惡意系統綁定三者一致才跳過。
                    from core.database import get_connection
                    with get_connection() as db:
                        bound = db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name='rp_malicious_bot_threads'"
                        ).fetchone()
                        record = (
                            db.execute(
                                """SELECT forum_id, thread_id FROM rp_malicious_bot_threads
                                    WHERE guild_id=? AND character_id=?""",
                                (str(guild.id), int(character["id"])),
                            ).fetchone()
                            if bound else None
                        )
                    if (record is not None
                            and str(record["forum_id"]) == str(forum.id)
                            and str(record["thread_id"]) == str(data["bot_id"])):
                        continue

            try:
                await self.ensure_forum(
                    guild,
                    character,
                )

                processed += 1

            except (
                discord.HTTPException,
                ValueError,
                PermissionError,
                sqlite3.Error,
            ) as exc:
                failed.append(
                    f"#{character['id']} "
                    f"{character['name']}: "
                    f"{type(exc).__name__}"
                )

                LOG.exception(
                    "角色論壇建立失敗："
                    "guild=%s character=%s",
                    guild.id,
                    character["id"],
                )

        return processed, failed

    # --------------------------------------
    # 自動掃描
    # --------------------------------------

    @tasks.loop(seconds=90)
    async def scan(self):
        async with self._cycle_lock:
            for guild in self.bot.guilds:
                if get_category(guild.id):
                    await self.run_scan(guild)

    @scan.before_loop
    async def before_scan(self):
        await self.bot.wait_until_ready()

    # --------------------------------------
    # 群主設定分類
    # --------------------------------------

    @app_commands.command(
        name="角色論壇設定",
        description="指定二審通過後的角色論壇分類",
    )
    @app_commands.guild_only()
    async def set_category(
        self,
        interaction: discord.Interaction,
        分類: discord.CategoryChannel,
    ):
        if (
            interaction.guild.owner_id
            != interaction.user.id
        ):
            await interaction.response.send_message(
                "⛔ 只有群主可以設定。",
                ephemeral=True,
            )
            return

        me = interaction.guild.me

        if (
            me is None
            or not me.guild_permissions.manage_channels
        ):
            await interaction.response.send_message(
                "⚠️ 機器人需要管理頻道權限。",
                ephemeral=True,
            )
            return

        with transaction() as db:
            db.execute(
                """
                INSERT INTO character_forum_settings(
                    guild_id,
                    category_id
                )
                VALUES (?,?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    category_id=excluded.category_id
                """,
                (
                    str(interaction.guild_id),
                    str(分類.id),
                ),
            )

        await interaction.response.send_message(
            "✅ 已設定角色專屬論壇分類："
            f"**{分類.name}**。\n"
            "系統將自動補建已通過二審的角色論壇。",
            ephemeral=True,
        )

    # --------------------------------------
    # 群主手動補建
    # --------------------------------------

    @app_commands.command(
        name="角色論壇補建",
        description="立即檢查並補建角色論壇",
    )
    @app_commands.guild_only()
    async def rebuild(
        self,
        interaction: discord.Interaction,
    ):
        if (
            interaction.guild.owner_id
            != interaction.user.id
        ):
            await interaction.response.send_message(
                "⛔ 只有群主可以執行。",
                ephemeral=True,
            )
            return

        if not get_category(
            interaction.guild_id
        ):
            await interaction.response.send_message(
                "⚠️ 請先使用 /角色論壇設定。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        count, failed = await self.run_scan(
            interaction.guild,
            limit=100,
        )

        report = (
            f"✅ 已檢查／補建 {count} 個角色論壇。"
        )

        if failed:
            report += (
                "\n⚠️ 未完成：\n"
                + "\n".join(failed[:12])
                + "\n目前條件尚未齊全"
            )

        await interaction.followup.send(
            report[:1900],
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CharacterForums(bot)
    )
