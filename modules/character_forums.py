"""Discord RP｜正式及測試角色私人論壇自動建立。

操作入口：/官方 → 功能管理 → 🧪 測試系統（不註冊額外 Slash 指令）

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
from discord.ext import commands, tasks

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)

# 本伺服器的既定分類預設值；其他伺服器由各自群主綁定。
DEFAULT_GUILD_ID = "1544051203493601380"
DEFAULT_FEMALE_CATEGORY_ID = "1567786100460625972"
DEFAULT_MALE_CATEGORY_ID = "1567786323773759588"
# 用戶指定的測試角色論壇入口。可以是分類、論壇頻道或論壇帖子。
DEFAULT_BETA_CATEGORY_ID = 1572210490707288114
# 舊素材帖子不再作為私人論壇建置入口。
DEFAULT_BETA_FORUM_ANCHOR_ID = 1572178575904219157


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
    """Private forum: only character owner and bot get explicit access.

    Discord Administrator can bypass channel overwrites. We do not grant other
    normal roles access to a player's private forum.
    """
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            send_messages_in_threads=True, create_public_threads=True,
            read_message_history=True,
        ),
        bot_member: discord.PermissionOverwrite(
            view_channel=True, manage_channels=True, manage_threads=True,
            create_public_threads=True, send_messages=True,
            send_messages_in_threads=True, read_message_history=True,
            embed_links=True, attach_files=True,
        ),
    }


def _private_forum_is_secure(forum, owner_id, *, owner=None, bot_member=None):
    """Verify explicit private channel overwrites without requiring member cache.

    ``guild.get_member`` can return None for an otherwise valid member. Passing
    the already-fetched owner is essential to prevent false failures. Reject
    access for everyone and third-party roles/users (except guild owner or
    Administrator, whose override Discord cannot suppress).
    """
    if not isinstance(forum, discord.ForumChannel):
        return False
    guild = forum.guild
    if forum.overwrites_for(guild.default_role).view_channel is not False:
        return False
    owner_id = int(owner_id)
    owner = owner if owner is not None else guild.get_member(owner_id)
    if owner is not None:
        if int(owner.id) != owner_id:
            return False
        if forum.overwrites_for(owner).view_channel is not True:
            return False
    else:
        # Cache miss: inspect explicit member overwrites by ID instead.
        if not any(int(target.id) == owner_id and isinstance(target, discord.Member)
                   and perm.view_channel is True
                   for target, perm in forum.overwrites.items()):
            return False
    bot_member = bot_member if bot_member is not None else guild.me
    bot_id = int(bot_member.id) if bot_member is not None else None
    if bot_id is None or forum.overwrites_for(bot_member).view_channel is not True:
        return False
    allowed = {int(guild.default_role.id), owner_id, bot_id}
    for target, perms in forum.overwrites.items():
        if int(target.id) not in allowed and perms.view_channel is True:
            return False
    return True


async def _refresh_forum(guild, forum):
    """Fetch after an overwrite edit; avoid checking stale discord.py cache."""
    current = await guild.fetch_channel(int(forum.id))
    if not isinstance(current, discord.ForumChannel):
        raise ValueError("角色論壇 ID 指向的頻道不是 ForumChannel")
    return current


def _missing_forum_permissions(forum, bot_member, owner):
    """Return actionable permissions missing for creating the fixed four posts."""
    bp = forum.permissions_for(bot_member)
    op = forum.permissions_for(owner)
    needed = (
        (bp.view_channel, "機器人檢視頻道"),
        (bp.create_public_threads, "機器人建立公開討論串"),
        (bp.send_messages_in_threads, "機器人在討論串發送訊息"),
        (bp.manage_threads, "機器人管理討論串"),
        (op.view_channel, "持有人檢視頻道"),
        (op.send_messages_in_threads, "持有人在討論串發言"),
    )
    return [label for ok, label in needed if not ok]


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
                male_category_id TEXT,
                beta_category_id TEXT
            )
        """)

        setting_cols = {
            row["name"] for row in db.execute(
                "PRAGMA table_info(character_forum_settings)"
            )
        }
        for column in ("female_category_id", "male_category_id", "beta_category_id"):
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


def get_beta_category(guild_id):
    """測試角色一律使用此獨立分類，不分性別。"""
    with get_connection() as db:
        row = db.execute(
            "SELECT beta_category_id FROM character_forum_settings WHERE guild_id=?",
            (str(guild_id),)
        ).fetchone()
    if str(guild_id) == DEFAULT_GUILD_ID:
        return DEFAULT_BETA_CATEGORY_ID
    return int(row["beta_category_id"]) if row and row["beta_category_id"] else None


async def resolve_beta_category(guild):
    """測試角固定使用獨立 Discord 分類；不使用素材帖子或男女分類。

    在指定伺服器，新核准分類優先於先前可能儲存的論壇帖子 ID。
    """
    category_id = get_beta_category(guild.id)
    if not category_id:
        raise ValueError("尚未設定測試角色專用分類，請執行 /測試角色論壇設定。")
    category = guild.get_channel(int(category_id))
    if category is None:
        try:
            category = await guild.fetch_channel(int(category_id))
        except discord.Forbidden as exc:
            raise PermissionError(f"機器人沒有讀取測試角色分類 {category_id} 的權限") from exc
        except discord.NotFound as exc:
            raise ValueError(f"找不到測試角色分類 {category_id}，請核對分類 ID") from exc
    if not isinstance(category, discord.CategoryChannel):
        raise ValueError(f"{category_id} 不是 Discord 分類（Category），無法在其中建立私人論壇")
    return category


def is_beta_forum_character(guild_id, character_id):
    """從正式測試角色綁定表辨識，絕不猜測姓名、性別或職業。"""
    with get_connection() as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_beta_characters'"
        ).fetchone()
        if not exists:
            return False
        return db.execute(
            "SELECT 1 FROM rp_beta_characters WHERE guild_id=? AND character_id=?",
            (str(guild_id), int(character_id))
        ).fetchone() is not None


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
                settings_json,
                status
            FROM characters
            WHERE guild_id=?
              AND status IN ('active','approved')
              AND character_type != 'official_npc'
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

        beta_character = is_beta_forum_character(guild.id, character_id)
        if beta_character:
            # 測試角不分男女；指定入口是論壇／帖子時從父分類建立私人論壇。
            category = await resolve_beta_category(guild)
        else:
            gender = character_gender(character)
            if gender is None:
                raise ValueError("正式角色性別尚未明確設定為男或女，無法建立論壇。")
            category_id = get_category(guild.id, gender)
            category = guild.get_channel(category_id) if category_id else None
            if not isinstance(category, discord.CategoryChannel):
                raise ValueError("指定的正式角色論壇分類不存在，請管理員重新設定。")

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

            # 已建立的角色論壇若位於舊分類，移到目前核准的正式／測試分類。
            # 只移動相同角色 ID 專屬論壇，不根據名稱猜測所有權。
            if forum.category_id != category.id:
                await forum.edit(
                    category=category,
                    overwrites=overwrites,
                    reason=f"角色 #{character_id} 專屬論壇分類更新",
                )
            # Repair legacy forums and verify freshly fetched permissions.
            # A member may not be in Discord's guild member cache; use the
            # explicitly fetched owner instead of guild.get_member(owner_id).
            if not _private_forum_is_secure(forum, owner_id, owner=owner, bot_member=bot_member):
                await forum.edit(
                    overwrites=overwrites,
                    reason=f"角色 #{character_id} 私人論壇權限修復",
                )
            forum = await _refresh_forum(guild, forum)
            if not _private_forum_is_secure(forum, owner_id, owner=owner, bot_member=bot_member):
                raise PermissionError(
                    "私人論壇覆寫權限驗證失敗；請檢查機器人是否具有管理頻道權限、"
                    "@everyone 是否被禁止檢視，以及是否存在額外開放的身分組。"
                )
            missing = _missing_forum_permissions(forum, bot_member, owner)
            if missing:
                raise PermissionError("角色論壇缺少權限：" + "、".join(missing))

            data = get_link(
                guild.id,
                character_id,
            )

            # --------------------------------
            # 建立四個固定貼文
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
                            thread = await guild.fetch_channel(int(data[field]))
                        except (discord.NotFound, discord.Forbidden):
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

                        try:
                            result = await forum.create_thread(
                                name=title,
                                content=content,
                                allowed_mentions=discord.AllowedMentions(
                                    users=True, roles=False, everyone=False,
                                ),
                                reason=f"角色 #{character_id} 固定帖子：{title}",
                            )
                        except discord.Forbidden as exc:
                            raise PermissionError(
                                f"無法建立「{title}」帖子：機器人缺少建立討論串、"
                                "在討論串發訊息或檢視頻道權限。"
                            ) from exc
                        except discord.HTTPException as exc:
                            raise RuntimeError(
                                f"建立「{title}」帖子失敗，Discord API：{exc}"
                            ) from exc
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
            if notice_id and character.get("status") == "active":
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
                    # IDs in the database alone do not prove the posts exist.
                    # Require all four actual in-memory threads to skip; when a
                    # thread is archived/missing, ensure_forum fetches it or
                    # reconstructs that one post idempotently.
                    thread_ids = [int(data[field]) for field in
                                  ("notice_id", "question_id", "story_id", "bot_id")]
                    cached_threads = [guild.get_thread(thread_id) for thread_id in thread_ids]
                    threads_present = all(
                        isinstance(thread, discord.Thread) and thread.parent_id == forum.id
                        for thread in cached_threads
                    )
                    if (record is not None
                            and str(record["forum_id"]) == str(forum.id)
                            and str(record["thread_id"]) == str(data["bot_id"])
                            and threads_present
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
                RuntimeError,
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
                if (any(get_categories(guild.id).values()) or get_beta_category(guild.id)
                        or str(guild.id) == DEFAULT_GUILD_ID):
                    await self.run_scan(guild)

    @scan.before_loop
    async def before_scan(self):
        await self.bot.wait_until_ready()

    # --------------------------------------
    # 群主設定分類
    # --------------------------------------

    async def set_beta_category(
        self,
        interaction: discord.Interaction,
        測試角色分類: discord.CategoryChannel,
    ):
        if (interaction.user.id != interaction.guild.owner_id and
                not interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message(
                "⛔ 僅群主或 Administrator 管理員可設定。", ephemeral=True)
        if not interaction.guild.me or not interaction.guild.me.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                "⚠️ 機器人缺少管理頻道權限。", ephemeral=True)
        with transaction() as db:
            db.execute("""INSERT INTO character_forum_settings(guild_id, beta_category_id)
                VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET
                beta_category_id=excluded.beta_category_id""",
                (str(interaction.guild_id), str(測試角色分類.id)))
        await interaction.response.defer(ephemeral=True)
        processed = 0
        errors = []
        for char in get_characters(interaction.guild_id):
            if not is_beta_forum_character(interaction.guild_id, char["id"]):
                continue
            try:
                await self.ensure_forum(interaction.guild, char)
                processed += 1
            except (discord.HTTPException, ValueError, PermissionError, sqlite3.Error, RuntimeError) as exc:
                LOG.exception("測試角色 #%s 論壇補建失敗", char["id"])
                errors.append(f"#{char['id']}：{type(exc).__name__}：{exc}")
        await interaction.followup.send(
            f"✅ 測試角色專用分類：{測試角色分類.mention}\n"
            f"補建成功 {processed} 個；失敗 {len(errors)} 個。\n" +
            ("\n".join(errors[:8]) if errors else "新抽角將立即在這個分類建立私人論壇。"),
            ephemeral=True)

    async def check_categories(self, interaction: discord.Interaction):
        if (interaction.guild.owner_id != interaction.user.id and
                not interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message(
                "⛔ 僅群主或 Administrator 管理員可檢查。", ephemeral=True)
        guild = interaction.guild
        cats = get_categories(guild.id)
        targets = (
            ("測試角色（不分男女）", get_beta_category(guild.id)),
            ("正式女性", cats["female"]),
            ("正式男性", cats["male"]),
        )
        me = guild.me
        lines = ["📂 **角色私人論壇分類檢查**"]
        for label, category_id in targets:
            category = guild.get_channel(category_id) if category_id else None
            if not isinstance(category, discord.CategoryChannel):
                lines.append(f"❌ {label}：{category_id or '未指定'}，目前未能在伺服器快取中確認是分類")
                continue
            if me is None:
                lines.append(f"⚠️ {label}：{category.mention}，無法確認機器人身分")
                continue
            perms = category.permissions_for(me)
            missing = [name for key, name in (
                (perms.view_channel, "檢視頻道"),
                (perms.manage_channels, "管理頻道"),
                (perms.send_messages, "發送訊息"),
                (perms.create_public_threads, "建立公開討論串"),
                (perms.manage_threads, "管理討論串"),
            ) if not key]
            capacity = len(category.channels)
            lines.append(
                f"{'✅' if not missing and capacity < 50 else '⚠️'} {label}：{category.mention} "
                f"（{category.id}；已用 {capacity}/50）" +
                (f"；缺少：{'、'.join(missing)}" if missing else "；權限檢查通過")
            )
        lines.append("以上為即時分類／權限檢查；建立論壇成功仍須實際執行補建驗證。")
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    async def set_category(
        self,
        interaction: discord.Interaction,
        女性分類: discord.CategoryChannel,
        男性分類: discord.CategoryChannel,
    ):
        if (interaction.guild.owner_id != interaction.user.id and
                not interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "⛔ 需要群主或 Administrator 權限。", ephemeral=True,
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

    async def rebuild(
        self,
        interaction: discord.Interaction,
    ):
        if (interaction.guild.owner_id != interaction.user.id and
                not interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "⛔ 僅群主或 Administrator 管理員可執行。", ephemeral=True)
            return

        if (not any(get_categories(interaction.guild_id).values())
                and not get_beta_category(interaction.guild_id)
                and str(interaction.guild_id) != DEFAULT_GUILD_ID):
            await interaction.response.send_message(
                "⚠️ 請至 /官方 → 功能管理 → 🧪 測試系統 設定論壇分類。",
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


async def ensure_activated_forum(bot, guild_id, character_id):
    """After database commit, make/recover an active character's private forum.

    Failure is retryable via 90-second scan and private notification dispatcher;
    never reverse a completed character activation or publish in a public channel.
    """
    cog = bot.get_cog("CharacterForums") if bot else None
    guild = bot.get_guild(int(guild_id)) if bot else None
    if cog is None or guild is None:
        return {"status": "deferred", "reason": "forum_cog_or_guild_unavailable"}
    with get_connection() as db:
        row = db.execute(
            """SELECT id,name,owner_id,character_type,settings_json,status
                 FROM characters WHERE guild_id=? AND id=?""",
            (str(guild_id), int(character_id)),
        ).fetchone()
    if not row or row["status"] not in ("active", "approved"):
        return {"status": "deferred", "reason": "character_not_approved_or_active"}
    try:
        forum = await cog.ensure_forum(guild, dict(row))
    except (discord.HTTPException, ValueError, PermissionError, sqlite3.Error, RuntimeError) as exc:
        LOG.exception("角色 #%s 即時論壇補建失敗，交由排程重試", character_id)
        return {"status": "deferred", "reason": f"{type(exc).__name__}: {exc}"[:300]}
    link = get_link(guild_id, character_id)
    if not all(link.get(name) for name in ("forum_id", "notice_id", "question_id", "story_id", "bot_id")):
        return {"status": "deferred", "reason": "incomplete_thread_links"}
    # ensure_forum completed verified checks using a fetched Member;
    # do not re-check guild member cache here and introduce a false failure.
    return {"status": "ready", "forum_id": str(forum.id), "notice_id": str(link["notice_id"])}


def forum_url(guild_id, forum_id):
    """已驗證建立／已綁定的私人論壇網址，不從角色名猜測。"""
    return f"https://discord.com/channels/{int(guild_id)}/{int(forum_id)}"


async def notify_approved_character(bot, guild_id, character_id):
    """二審完成後建立私人論壇並將網址交給持有人；Discord DM 失敗不撤銷審核。"""
    result = await ensure_activated_forum(bot, guild_id, character_id)
    if result.get("status") != "ready":
        return {"status": "deferred", "reason": result.get("reason", "forum_pending")}
    with get_connection() as db:
        row = db.execute(
            "SELECT owner_id,name,status FROM characters WHERE guild_id=? AND id=?",
            (str(guild_id), int(character_id))
        ).fetchone()
    if not row or row["status"] not in ("active", "approved"):
        return {"status": "deferred", "reason": "character_no_longer_approved"}
    link = forum_url(guild_id, result["forum_id"])
    guild = bot.get_guild(int(guild_id))
    member = guild.get_member(int(row["owner_id"])) if guild else None
    if member is None and guild:
        try:
            member = await guild.fetch_member(int(row["owner_id"]))
        except discord.HTTPException:
            member = None
    if member is None:
        return {"status": "ready", "forum_id": result["forum_id"], "url": link, "dm": "member_missing"}
    try:
        await member.send(
            f"✅ 你的角色 **{discord.utils.escape_markdown(str(row['name']))}** 已通過兩階段審核。\n"
            f"🔒 角色私人論壇：{link}\n"
            "論壇內已有通知、詢問、劇情討論、機器人使用四個帖子。\n"
            "請依原本流程確認正式啟用；通過審核並不代表已領取新人禮包。",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        dm = "sent"
    except discord.HTTPException as exc:
        LOG.warning("角色 #%s 審核通知無法送入 DM: %s", character_id, exc)
        dm = "blocked"
    return {"status": "ready", "forum_id": result["forum_id"], "url": link, "dm": dm}


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CharacterForums(bot)
    )
