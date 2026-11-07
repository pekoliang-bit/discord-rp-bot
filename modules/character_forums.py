"""Discord RP｜角色二審完成自動建立角色論壇。

指令：
/角色論壇設定
/角色論壇補建

功能：
- 群主分別指定女性與男性角色論壇分類
- 偵測 approved / active 角色
- 自動建立角色專屬 ForumChannel
- 建立通知、詢問、劇情討論、機器人使用四個貼文
- 記錄 ID，防止重複建立
- 機器人重啟後自動補建
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)

# 本伺服器的既定分類預設值；其他伺服器由各自群主綁定。
DEFAULT_GUILD_ID = "1544051203493601380"
DEFAULT_FEMALE_CATEGORY_ID = "1567786100460625972"
DEFAULT_MALE_CATEGORY_ID = "1567786323773759588"


def normalize_gender(value):
    """僅接受明確的男女值；不猜測未知性別。"""
    value = str(value or "").strip().casefold()
    if value in {"女", "女性", "female", "f", "woman", "girl"}:
        return "female"
    if value in {"男", "男性", "male", "m", "man", "boy"}:
        return "male"
    return None


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



def _forum_access_overwrites(guild, owner, bot_member):
    """Explicit per-forum overwrites; forum posts inherit forum visibility."""
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                           send_messages_in_threads=True, read_message_history=True),
        bot_member: discord.PermissionOverwrite(view_channel=True, manage_channels=True,
                                                manage_threads=True, send_messages=True,
                                                send_messages_in_threads=True, read_message_history=True),
    }


def _private_forum_is_secure(forum, owner_id):
    """Conservatively reject public or third-party explicit permission overwrites."""
    if not isinstance(forum, discord.ForumChannel):
        return False
    default = forum.overwrites_for(forum.guild.default_role)
    if default.view_channel is not False:
        return False
    owner = forum.guild.get_member(int(owner_id))
    if owner is None or forum.overwrites_for(owner).view_channel is not True:
        return False
    for target, permissions in forum.overwrites.items():
        if target.id in (forum.guild.default_role.id, owner.id, forum.guild.me.id):
            continue
        if permissions.view_channel is True:
            return False
    return True


# ==========================================
# 資料庫
# ==========================================

def initialize():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            character_forum_settings (
                guild_id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL DEFAULT '',
                female_category_id TEXT,
                male_category_id TEXT
            )
        """)

        setting_cols = {
            row["name"] for row in db.execute(
                "PRAGMA table_info(character_forum_settings)"
            )
        }
        for column in ("female_category_id", "male_category_id"):
            if column not in setting_cols:
                db.execute(
                    f"ALTER TABLE character_forum_settings ADD COLUMN {column} TEXT"
                )

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


def get_categories(guild_id):
    """回傳女性／男性分類。舊版單分類設定不會被錯用為另一性別。"""
    with get_connection() as db:
        row = db.execute(
            """SELECT female_category_id, male_category_id
               FROM character_forum_settings WHERE guild_id=?""",
            (str(guild_id),),
        ).fetchone()
    female = row["female_category_id"] if row else None
    male = row["male_category_id"] if row else None
    if str(guild_id) == DEFAULT_GUILD_ID:
        female = female or DEFAULT_FEMALE_CATEGORY_ID
        male = male or DEFAULT_MALE_CATEGORY_ID
    return {
        "female": int(female) if female else None,
        "male": int(male) if male else None,
    }


def get_category(guild_id, gender=None):
    """保留相容入口；必須指定性別才會回傳分類。"""
    return get_categories(guild_id).get(normalize_gender(gender))


def character_gender(character):
    try:
        data = json.loads(character.get("settings_json") or "{}")
        if not isinstance(data, dict):
            return None
        return normalize_gender(data.get("gender"))
    except (ValueError, TypeError):
        return None


def get_characters(guild_id):
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT
                id,
                name,
                owner_id,
                character_type,
                settings_json
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

        gender = character_gender(character)
        if gender is None:
            raise ValueError("角色性別尚未明確設定為男或女，無法建立論壇。")
        category_id = get_category(guild.id, gender)
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError("尚未設定有效的對應性別角色論壇分類。")

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

            owner_id = int(character["owner_id"])
            owner = guild.get_member(owner_id)
            if owner is None:
                try:
                    owner = await guild.fetch_member(owner_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    raise PermissionError("無法確認角色擁有者，停止建立私人論壇") from exc
            bot_member = guild.me
            if bot_member is None:
                raise PermissionError("無法確認機器人伺服器成員身分")
            overwrites = _forum_access_overwrites(guild, owner, bot_member)

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
                        for channel in guild.channels
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
                    if len(category.channels) >= 50:
                        raise ValueError(
                            f"{category.name} 分類已達 50 個頻道上限，請群主調整分類。"
                        )
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
                        overwrites=overwrites,
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

            # Repair legacy forums before creating or delivering private posts.
            # Restrict both @everyone and any previously granted third-party roles.
            if not _private_forum_is_secure(forum, owner_id):
                await forum.edit(
                    overwrites=overwrites,
                    reason=f"角色 #{character_id} 私人論壇權限修復",
                )
            if not _private_forum_is_secure(forum, owner_id):
                raise PermissionError("私人論壇權限未能確認，禁止發布私人內容")

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
                        # 已封存的論壇帖子可能不在 forum.threads 快取裡。
                        try:
                            async for archived in forum.archived_threads(limit=None):
                                if archived.name == title:
                                    thread = archived
                                    break
                        except discord.HTTPException:
                            LOG.warning("無法檢查論壇 #%s 的封存帖子", forum.id)

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

            # 通知角色持有人：只在身分啟用、論壇權限通過後發送一次。
            info=get_link(guild.id,character_id)
            notice_id=info.get('notice_id')
            if notice_id:
                from core.character_proxy_identity import claim_notice,unclaim_notice
                with transaction() as db:
                    new_notice=claim_notice(db,guild.id,character_id)
                if new_notice:
                    try:
                        notice_thread=guild.get_thread(int(notice_id))
                        if notice_thread is None:
                            notice_thread=await guild.fetch_channel(int(notice_id))
                        await notice_thread.send(
                            f"<@{character['owner_id']}> 🎭 **角色專屬代理發言已建立**\n"
                            f"角色：{character['name']}\n角色編號：#{character_id}（與角色資料庫編號一致）\n"
                            "於有 Webhook 權限的頻道輸入 `@小助手 [#角色編號] 訊息` 即可代理發言。\n"
                            "若群主已開啟訊息內容授權與 RP_PROXY_MESSAGE_CONTENT_ENABLED，"
                            "也能直接輸入 `[#123] 大家好！`。\n"
                            "僅持有人可使用，角色停用後不能再發言。",
                            allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
                    except (discord.HTTPException,ValueError):
                        with transaction() as db:
                            unclaim_notice(db,guild.id,character_id)
                        LOG.exception('角色 #%s 代理通知發送失敗，留待下次補送',character_id)

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
                            and str(record["thread_id"]) == str(data["bot_id"])
                            and _private_forum_is_secure(forum, character["owner_id"])):
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
                if any(get_categories(guild.id).values()):
                    await self.run_scan(guild)

    @scan.before_loop
    async def before_scan(self):
        await self.bot.wait_until_ready()

    # --------------------------------------
    # 群主設定分類
    # --------------------------------------

    @app_commands.command(
        name="角色論壇設定",
        description="分別指定女性與男性角色專屬論壇分類",
    )
    @app_commands.guild_only()
    async def set_category(
        self,
        interaction: discord.Interaction,
        女性分類: discord.CategoryChannel,
        男性分類: discord.CategoryChannel,
    ):
        if interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "⛔ 只有群主可以設定。", ephemeral=True,
            )
            return
        me = interaction.guild.me
        if me is None or not me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "⚠️ 機器人需要管理頻道權限。", ephemeral=True,
            )
            return
        if 女性分類.id == 男性分類.id:
            await interaction.response.send_message(
                "⚠️ 男女論壇分類不可相同。", ephemeral=True,
            )
            return
        with transaction() as db:
            db.execute(
                """INSERT INTO character_forum_settings(
                       guild_id, category_id, female_category_id, male_category_id
                   ) VALUES (?, '', ?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                       female_category_id=excluded.female_category_id,
                       male_category_id=excluded.male_category_id""",
                (str(interaction.guild_id), str(女性分類.id), str(男性分類.id)),
            )
        await interaction.response.send_message(
            f"✅ 女性論壇：**{女性分類.name}**\n"
            f"✅ 男性論壇：**{男性分類.name}**\n"
            "後續新建論壇會依角色性別分流；既有論壇不會自動搬移。",
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

        if not all(get_categories(interaction.guild_id).values()):
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
