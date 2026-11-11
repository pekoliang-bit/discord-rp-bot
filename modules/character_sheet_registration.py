"""Discord RP｜角色單訊息連結登記與"""Discord RP｜角色單訊息連結登記與標籤庫整合。

支援：
- /角色單登記
- Discord 訊息應用程式選單：登記角色單
- /玩家npc修改
- 官方標籤庫初始標籤選擇

角色歸屬依原始訊息作者或群主指定的實際擁有者，
registered_by 僅記錄執行登記的使用者。

依賴：
- core.database
- modules.tag_library
- modules.tag_selection_ui
"""

from __future__ import annotations

import logging
import json
import re
import sqlite3
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction


LOG = logging.getLogger(__name__)

LINK_RE = re.compile(
    r"/channels/(\d+)/(\d+)/(\d+)/?"
)

BIRTHDAY_FIELD_RE = re.compile(
    r"(?:^|\n)\s*(?:生日|出生月日)\s*[：:]\s*(\d{1,2}[/\-]\d{1,2})\b"
)


def explicit_sheet_birthday(message_text: str) -> str | None:
    """只接受角色單明確的生日欄位，不猜測人物背景。"""
    from modules.character_numbering import normalize_birthday
    match = BIRTHDAY_FIELD_RE.search(message_text or "")
    value = normalize_birthday(match.group(1)) if match else None
    return f"{value[:2]}/{value[2:]}" if value else None


TAG_FIELD_RE = re.compile(
    r"(?:^|\n)\s*(?:標籤|角色標籤)"
    r"\s*[：:]\s*([^\n]+)"
)

VALID_TYPES = {
    "player": "原創角色",
    "player_npc": "玩家 NPC",
    "official_npc": "官方 NPC",
}


# ==========================================
# 資料庫初始化
# ==========================================

def initialize():
    with transaction() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            character_sheet_links (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                sheet_url TEXT NOT NULL,
                registered_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (
                    guild_id,
                    character_id
                ),

                UNIQUE (
                    guild_id,
                    message_id
                ),

                FOREIGN KEY (
                    guild_id,
                    character_id
                )
                REFERENCES characters(
                    guild_id,
                    id
                )
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_character_sheet_links_channel
            ON character_sheet_links(
                guild_id,
                channel_id
            )
        """)


# ==========================================
# Discord 訊息連結
# ==========================================

def parse_link(
    guild_id: int,
    raw: str,
):
    url = urlsplit(raw.strip())

    if (
        url.scheme != "https"
        or url.hostname not in {
            "discord.com",
            "www.discord.com",
            "discordapp.com",
        }
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "請提供有效的 Discord 訊息連結。"
        )

    match = LINK_RE.fullmatch(
        url.path
    )

    if (
        not match
        or int(match.group(1)) != int(guild_id)
    ):
        raise ValueError(
            "角色單連結必須是本伺服器的訊息。"
        )

    return (
        int(match.group(2)),
        int(match.group(3)),
    )


async def fetch_sheet(
    interaction: discord.Interaction,
    url: str,
):
    if interaction.guild is None:
        raise ValueError(
            "只能在伺服器內登記角色單。"
        )

    channel_id, message_id = parse_link(
        interaction.guild_id,
        url,
    )

    try:
        channel = (
            interaction.guild.get_channel_or_thread(
                channel_id
            )
        )

        if channel is None:
            channel = (
                await interaction.guild.fetch_channel(
                    channel_id
                )
            )

        if (
            not isinstance(
                channel,
                (
                    discord.TextChannel,
                    discord.Thread,
                ),
            )
            or channel.guild.id
            != interaction.guild_id
        ):
            raise ValueError(
                "角色單必須來自本伺服器的"
                "文字頻道或討論串。"
            )

        message = await channel.fetch_message(
            message_id
        )

    except (
        discord.Forbidden,
        discord.NotFound,
    ) as exc:
        raise ValueError(
            "無法讀取原始角色單訊息。"
            "請檢查訊息連結與機器人權限。"
        ) from exc

    return channel, message


async def validate_sheet(
    interaction: discord.Interaction,
    url: str,
):
    """保留舊模組呼叫時的相容性。"""

    channel, message = await fetch_sheet(
        interaction,
        url,
    )

    if (
        message.author.id
        not in (
            interaction.user.id,
            interaction.guild.owner_id,
        )
        and interaction.user.id
        != interaction.guild.owner_id
    ):
        raise PermissionError(
            "只能登記自己的角色單，"
            "群主可以代為登記。"
        )

    return channel, message


# ==========================================
# 標籤解析
# ==========================================

def parse_sheet_tags(
    text: str,
):
    """僅讀取原始訊息中明確標示的標籤欄位。

    範例：
    標籤：溫文爾雅、才華橫溢

    不從角色故事或背景描述擅自推測標籤。
    """

    match = TAG_FIELD_RE.search(
        text or ""
    )

    if match is None:
        return []

    raw = match.group(1).strip()

    values = re.split(
        r"[、,，/／;；]+",
        raw,
    )

    result = []

    for value in values:
        name = value.strip()

        if name and name not in result:
            result.append(name)

    return result


def check_sheet_tags(
    guild_id,
    sheet_text,
    kind,
):
    """回傳標籤檢查結果與提示。"""

    from modules.tag_library import (
        tags,
        initial_limit,
    )

    declared = parse_sheet_tags(
        sheet_text
    )

    valid_names = {
        str(row["name"])
        for row in tags(guild_id)
    }

    limit = initial_limit(kind)

    known = [
        name for name in declared
        if name in valid_names
    ]

    unknown = [
        name for name in declared
        if name not in valid_names
    ]

    notices = []

    if unknown:
        notices.append(
            "未收錄標籤："
            + "、".join(unknown)
        )

    if len(declared) > limit:
        notices.append(
            f"初始標籤最多只能選 {limit} 個。"
        )

    if not declared:
        notices.append(
            "角色單沒有填寫標籤，"
            "請從官方標籤庫選擇。"
        )

    if not notices:
        notices.append(
            "請確認官方標籤庫中的初始標籤。"
        )

    return {
        "declared": declared,
        "known": known,
        "unknown": unknown,
        "limit": limit,
        "notice": "\n".join(notices),
    }


async def open_tag_selection(
    interaction: discord.Interaction,
    character_id: int,
    kind: str,
    owner_id: int,
    sheet_text: str,
):
    """登記完成後開啟標籤選單。"""

    from modules.tag_selection_ui import (
        show_tag_picker,
    )

    result = check_sheet_tags(
        interaction.guild_id,
        sheet_text,
        kind,
    )

    is_owner = (
        interaction.guild is not None
        and interaction.user.id
        == interaction.guild.owner_id
    )

    await show_tag_picker(
        interaction,
        character_id,
        kind,
        owner_id,
        force=is_owner,
        notice=result["notice"],
    )


# ==========================================
# 核心登記
# ==========================================

def register_sheet(
    guild_id: int,
    user_id: int,
    owner_id: int,
    kind: str,
    name: str,
    channel_id: int,
    message_id: int,
    url: str,
    character_id: int | None = None,
    *,
    character_owner_id: int | None = None,
    birthday: str | None = None,
):
    """
    user_id:
        執行登記的人。

    owner_id:
        Discord 伺服器群主。

    character_owner_id:
        角色真正擁有者。

    三者不能混為一談。
    """

    if kind not in VALID_TYPES:
        raise ValueError(
            "未知的角色種類。"
        )

    if kind == "player_npc" and character_id is None:
        raise ValueError(
            "玩家 NPC 不能直接透過角色單連結啟用。"
            "請由群主從 /登記 匯入待認領模板，"
            "玩家再使用 /創角 → NPC 申請，完成兩階段審核。"
        )

    name = name.strip()

    if not 1 <= len(name) <= 100:
        raise ValueError(
            "角色名稱必須為 1～100 字。"
        )

    actor = str(user_id)

    real_owner = str(
        character_owner_id
        if character_owner_id is not None
        else user_id
    )

    server_owner = str(owner_id)

    is_admin = (
        actor == server_owner
    )

    if (
        kind == "official_npc"
        and not is_admin
    ):
        raise PermissionError(
            "只有群主可以登記官方 NPC。"
        )

    if (
        not is_admin
        and real_owner != actor
    ):
        raise PermissionError(
            "玩家只能替自己登記角色。"
        )

    if (
        kind == "player"
        and character_id is None
    ):
        raise ValueError(
            "原創角色請填寫"
            "已通過二審的角色編號。"
        )

    if (
        kind != "player"
        and character_id is not None
    ):
        raise ValueError(
            "新 NPC 不必填寫既有角色編號。"
        )

    with transaction() as db:

        db.execute(
            """
            INSERT OR IGNORE
            INTO guilds(guild_id)
            VALUES (?)
            """,
            (str(guild_id),),
        )

        linked = db.execute(
            """
            SELECT character_id
            FROM character_sheet_links
            WHERE guild_id=?
              AND message_id=?
            """,
            (
                str(guild_id),
                str(message_id),
            ),
        ).fetchone()

        if linked is not None:
            if (
                character_id is None
                or int(linked["character_id"])
                != int(character_id)
            ):
                raise ValueError(
                    "這則角色單訊息已經登記其他角色，"
                    "不能重複登記。"
                )

        if kind == "player":

            row = db.execute(
                """
                SELECT
                    id,
                    owner_id,
                    character_type,
                    status
                FROM characters
                WHERE guild_id=?
                  AND id=?
                """,
                (
                    str(guild_id),
                    int(character_id),
                ),
            ).fetchone()

            if (
                row is None
                or row["character_type"] != "player"
                or row["status"]
                not in (
                    "approved",
                    "active",
                )
            ):
                raise ValueError(
                    "該原創角色不存在，"
                    "或尚未完成二階審核。"
                )

            if str(row["owner_id"]) != real_owner:
                raise PermissionError(
                    "指定玩家不是這個角色原本的擁有者，"
                    "無法更動歸屬。"
                )

        else:

            if (
                kind == "player_npc"
                and not is_admin
            ):
                other = db.execute(
                    """
                    SELECT id
                    FROM characters
                    WHERE guild_id=?
                      AND owner_id=?
                      AND character_type='player_npc'
                      AND status!='deleted'
                    LIMIT 1
                    """,
                    (
                        str(guild_id),
                        real_owner,
                    ),
                ).fetchone()

                if other:
                    raise ValueError(
                        "每名玩家只能建立一隻玩家 NPC；"
                        "修改請使用 `/玩家npc修改`。"
                    )

            cursor = db.execute(
                """
                INSERT INTO characters (
                    guild_id,
                    owner_id,
                    name,
                    character_type,
                    status
                )
                VALUES (?, ?, ?, ?, 'active')
                """,
                (
                    str(guild_id),
                    real_owner,
                    name,
                    kind,
                ),
            )

            character_id = cursor.lastrowid
            if birthday:
                db.execute(
                    "UPDATE characters SET settings_json=? WHERE guild_id=? AND id=?",
                    (json.dumps({'birthday': birthday}, ensure_ascii=False),
                     str(guild_id), int(character_id)),
                )

            # NPC 登記可立即啟用，但公開號碼只有正式開群後且生日已填才核發。
            from modules.character_numbering import issue_in_transaction
            issue_in_transaction(db, guild_id, character_id)

        db.execute(
            """
            INSERT INTO character_sheet_links (
                guild_id,
                character_id,
                message_id,
                channel_id,
                sheet_url,
                registered_by
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(guild_id, character_id)
            DO UPDATE SET
                message_id=excluded.message_id,
                channel_id=excluded.channel_id,
                sheet_url=excluded.sheet_url,
                registered_by=excluded.registered_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                int(character_id),
                str(message_id),
                str(channel_id),
                url,
                actor,
            ),
        )

        return int(character_id)


# ==========================================
# 統一登記入口
# ==========================================

async def do_register(
    interaction: discord.Interaction,
    kind: str,
    name: str,
    url: str,
    character_id: int | None = None,
    owner_override: discord.Member | None = None,
    source_message: discord.Message | None = None,
):
    if interaction.guild is None:
        raise ValueError(
            "請在伺服器內操作。"
        )

    is_admin = (
        interaction.user.id
        == interaction.guild.owner_id
    )

    if (
        owner_override is not None
        and not is_admin
    ):
        raise PermissionError(
            "只有群主可以指定"
            "代發角色單的實際擁有者。"
        )

    if source_message is None:
        channel, message = await fetch_sheet(
            interaction,
            url,
        )

    else:
        message = source_message
        channel = message.channel

        if (
            message.guild is None
            or message.guild.id
            != interaction.guild_id
        ):
            raise ValueError(
                "必須選擇本伺服器的角色單訊息。"
            )

        url = message.jump_url

    if message.author.bot:
        raise ValueError(
            "不能使用機器人訊息作為角色單。"
        )

    if (
        not is_admin
        and message.author.id
        != interaction.user.id
    ):
        raise PermissionError(
            "只能提交自己發布的角色單；"
            "群主可以代提交。"
        )

    true_owner = (
        owner_override.id
        if owner_override is not None
        else message.author.id
    )

    if (
        not is_admin
        and true_owner
        != interaction.user.id
    ):
        raise PermissionError(
            "角色擁有者必須是玩家本人。"
        )

    if (
        kind == "official_npc"
        and not is_admin
    ):
        raise PermissionError(
            "只有群主可以建立官方 NPC。"
        )

    new_id = register_sheet(
        interaction.guild_id,
        interaction.user.id,
        interaction.guild.owner_id,
        kind,
        name,
        channel.id,
        message.id,
        url,
        character_id,
        character_owner_id=true_owner,
        birthday=explicit_sheet_birthday(message.content or ""),
    )

    # 官方 NPC 的群主登記已是正式授權；玩家 NPC 只能申請後啟用。
    if kind == "official_npc":
        try:
            from core.history_v23 import attempt_lock
            attempt_lock(interaction.guild_id,new_id)
        except Exception:
            LOG.exception("官方 NPC 歷史履歷等待補建：%s",new_id)
    return (
        new_id,
        true_owner,
        message.jump_url,
        message.content or "",
    )


# ==========================================
# 訊息右鍵選單 Modal
# ==========================================

class RegisterMessageModal(
    discord.ui.Modal,
    title="登記這則角色單訊息",
):

    role_type = discord.ui.TextInput(
        label="類型：原創／玩家NPC／官方NPC",
        placeholder="玩家NPC",
        max_length=10,
    )

    character_name = discord.ui.TextInput(
        label="角色名稱",
        max_length=100,
    )

    character_id_field = discord.ui.TextInput(
        label="已核准原創角色編號（NPC留空）",
        required=False,
        max_length=15,
    )

    owner_field = discord.ui.TextInput(
        label="代發時的玩家 Discord ID（僅群主）",
        required=False,
        max_length=25,
    )

    def __init__(
        self,
        message: discord.Message,
    ):
        super().__init__()
        self.message = message

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            types = {
                "原創": "player",
                "原創角色": "player",
                "玩家npc": "player_npc",
                "玩家NPC": "player_npc",
                "官方npc": "official_npc",
                "官方NPC": "official_npc",
            }

            kind = types.get(
                str(self.role_type.value).strip()
            )

            if kind is None:
                raise ValueError(
                    "類型請填：原創、玩家NPC"
                    " 或 官方NPC。"
                )

            cid_text = str(
                self.character_id_field.value or ""
            ).strip()

            owner_text = str(
                self.owner_field.value or ""
            ).strip()

            character_id = (
                int(cid_text)
                if cid_text
                else None
            )

            selected_owner = None

            if owner_text:
                if (
                    interaction.guild is None
                    or interaction.user.id
                    != interaction.guild.owner_id
                ):
                    raise PermissionError(
                        "只有群主能指定玩家 ID。"
                    )

                selected_owner = (
                    await interaction.guild.fetch_member(
                        int(owner_text)
                    )
                )

            (
                new_id,
                true_owner,
                link,
                sheet_text,
            ) = await do_register(
                interaction,
                kind,
                str(self.character_name.value),
                self.message.jump_url,
                character_id,
                selected_owner,
                self.message,
            )

            await interaction.followup.send(
                (
                    f"✅ {VALID_TYPES[kind]}登記成功！\n"
                    f"角色編號：`{new_id}`\n"
                    f"角色擁有者：<@{true_owner}>\n"
                    f"角色單：{link}\n"
                    "已核准資料不需再次審核。\n"
                    "接下來請確認角色初始標籤。"
                ),
                ephemeral=True,
            )

            await open_tag_selection(
                interaction,
                new_id,
                kind,
                true_owner,
                sheet_text,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
            discord.HTTPException,
        ) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 玩家 NPC 修改
# ==========================================

class ModifyNPCModal(
    discord.ui.Modal,
    title="修改玩家 NPC 角色單",
):

    new_link = discord.ui.TextInput(
        label="新角色單訊息連結",
        max_length=250,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            channel, message = await fetch_sheet(
                interaction,
                str(self.new_link.value),
            )

            if (
                message.author.id
                != interaction.user.id
            ):
                raise PermissionError(
                    "更新連結必須是自己發布的角色單。"
                )

            with transaction() as db:

                row = db.execute(
                    """
                    SELECT id
                    FROM characters
                    WHERE guild_id=?
                      AND owner_id=?
                      AND character_type='player_npc'
                      AND status!='deleted'
                    ORDER BY id
                    LIMIT 1
                    """,
                    (
                        str(interaction.guild_id),
                        str(interaction.user.id),
                    ),
                ).fetchone()

                if row is None:
                    raise ValueError(
                        "你沒有可修改的玩家 NPC。"
                    )

                conflict = db.execute(
                    """
                    SELECT character_id
                    FROM character_sheet_links
                    WHERE guild_id=?
                      AND message_id=?
                    """,
                    (
                        str(interaction.guild_id),
                        str(message.id),
                    ),
                ).fetchone()

                if (
                    conflict
                    and int(conflict["character_id"])
                    != int(row["id"])
                ):
                    raise ValueError(
                        "此角色單已綁定其他角色。"
                    )

                db.execute(
                    """
                    INSERT INTO character_sheet_links (
                        guild_id,
                        character_id,
                        message_id,
                        channel_id,
                        sheet_url,
                        registered_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?)

                    ON CONFLICT(guild_id, character_id)
                    DO UPDATE SET
                        message_id=excluded.message_id,
                        channel_id=excluded.channel_id,
                        sheet_url=excluded.sheet_url,
                        registered_by=excluded.registered_by,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        str(interaction.guild_id),
                        int(row["id"]),
                        str(message.id),
                        str(channel.id),
                        message.jump_url,
                        str(interaction.user.id),
                    ),
                )

            await interaction.followup.send(
                (
                    f"✅ 玩家 NPC #{row['id']} "
                    "的角色單連結已更新。"
                ),
                ephemeral=True,
            )

            await open_tag_selection(
                interaction,
                int(row["id"]),
                "player_npc",
                interaction.user.id,
                message.content or "",
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
            discord.HTTPException,
        ) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# Discord Cog
# ==========================================

class CharacterSheetRegistration(
    commands.Cog
):

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

        self.message_command = (
            app_commands.ContextMenu(
                name="登記角色單",
                callback=self.register_from_message,
            )
        )

    async def cog_load(self):
        initialize()

        self.bot.tree.add_command(
            self.message_command
        )

    async def cog_unload(self):
        self.bot.tree.remove_command(
            self.message_command.name,
            type=discord.AppCommandType.message,
        )

    async def register_from_message(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "只能在伺服器內使用。",
                ephemeral=True,
            )

        if (
            interaction.user.id
            != interaction.guild.owner_id
            and message.author.id
            != interaction.user.id
        ):
            return await interaction.response.send_message(
                (
                    "⛔ 只能登記自己的角色單；"
                    "群主可以代登記。"
                ),
                ephemeral=True,
            )

        await interaction.response.send_modal(
            RegisterMessageModal(message)
        )

    @app_commands.command(
        name="角色單登記",
        description=(
            "用原始角色單訊息連結"
            "登記角色或 NPC"
        ),
    )
    @app_commands.guild_only()
    @app_commands.describe(
        類型="角色種類",
        角色名稱="角色名稱",
        角色單連結=(
            "複製原始角色單訊息的連結"
        ),
        角色編號=(
            "原創角色需填已核准編號"
        ),
        指定擁有者=(
            "僅群主代發角色單時指定玩家"
        ),
    )
    @app_commands.choices(
        類型=[
            app_commands.Choice(
                name="原創角色（已審核）",
                value="player",
            ),
            app_commands.Choice(
                name="玩家 NPC",
                value="player_npc",
            ),
            app_commands.Choice(
                name="官方 NPC",
                value="official_npc",
            ),
        ]
    )
    async def register(
        self,
        interaction: discord.Interaction,
        類型: app_commands.Choice[str],
        角色名稱: str,
        角色單連結: str,
        角色編號: int | None = None,
        指定擁有者: discord.Member | None = None,
    ):
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            (
                new_id,
                true_owner,
                link,
                sheet_text,
            ) = await do_register(
                interaction,
                類型.value,
                角色名稱,
                角色單連結,
                角色編號,
                指定擁有者,
            )

            await interaction.followup.send(
                (
                    f"✅ {VALID_TYPES[類型.value]}"
                    "登記成功！\n"
                    f"角色編號：`{new_id}`\n"
                    f"角色擁有者：<@{true_owner}>\n"
                    f"角色單：{link}\n"
                    "已審核資料不需再次審核。\n"
                    "接下來請確認角色初始標籤。"
                ),
                ephemeral=True,
            )

            await open_tag_selection(
                interaction,
                new_id,
                類型.value,
                true_owner,
                sheet_text,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
            discord.HTTPException,
        ) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @app_commands.command(
        name="玩家npc修改",
        description=(
            "修改自己的玩家 NPC "
            "角色單訊息連結"
        ),
    )
    @app_commands.guild_only()
    async def modify(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.send_modal(
            ModifyNPCModal()
        )


# ==========================================
# 模組載入
# ==========================================

async def setup(
    bot: commands.Bot,
):
    await bot.add_cog(
        CharacterSheetRegistration(bot)
    )

    LOG.info(
        "角色單連結登記及標籤整合模組已載入。"
    )
