
"""Discord RP｜官方內容建立中心。

由 /官方 → 管理功能中心 → 內容建立 進入。

功能：
- 建立九種類型的官方內容
- 儲存草稿
- 查看最近 20 筆內容
- 編輯草稿
- 發布到指定 Discord 頻道或討論串
- 長內容自動分段發布
- 下架內容紀錄
- 群主最高權限
- 操作介面身分隔離
- 返回官方管理功能中心

注意：
- 抽獎、獎項、任務等目前屬於內容建立及公告。
- 自動抽獎、獎勵發放及任務結算需要另外整合。
- 下架不會自動刪除已發布訊息。
- 本模組不註冊新的斜線指令。
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)


LOG = logging.getLogger(__name__)

TIMEOUT = 600
MAX_BODY_LENGTH = 3500
MAX_PART_LENGTH = 1800


CONTENT_TYPES = {
    "story": "📖 劇情",
    "product": "🛍️ 商品企劃",
    "lottery": "🎰 抽獎獎單",
    "prize": "🏆 獎項",
    "event": "🎪 活動",
    "npc": "👑 官方 NPC",
    "achievement": "🎖️ 成就稱號",
    "world": "🌍 世界觀",
    "mission": "📜 任務",
}

# 與之前版本相容。
TYPES = CONTENT_TYPES


# ==========================================
# 權限
# ==========================================

def owner(interaction):
    return bool(
        interaction.guild is not None
        and interaction.user.id
        == interaction.guild.owner_id
    )


def require_owner(interaction):
    if not owner(interaction):
        raise PermissionError(
            "只有伺服器群主可以管理官方內容。"
        )


# ==========================================
# 資料庫
# ==========================================

def init_db():
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            official_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (
                        status IN (
                            'draft',
                            'published',
                            'archived'
                        )
                    ),
                creator_id TEXT NOT NULL,
                editor_id TEXT NOT NULL,
                message_id TEXT,
                channel_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_official_contents
            ON official_contents(
                guild_id,
                status,
                id
            )
            """
        )


# ==========================================
# 建立草稿
# ==========================================

def create(guild_id, user_id, kind, title, body):
    init_db()

    if kind not in CONTENT_TYPES:
        raise ValueError(
            "未知的內容類型。"
        )

    title = str(title).strip()
    body = str(body).strip()

    if not 1 <= len(title) <= 100:
        raise ValueError(
            "標題必須介於 1～100 字。"
        )

    if not 1 <= len(body) <= MAX_BODY_LENGTH:
        raise ValueError(
            "內容必須介於 1～3500 字。"
        )

    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO official_contents (
                guild_id,
                kind,
                title,
                body,
                creator_id,
                editor_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                kind,
                title,
                body,
                str(user_id),
                str(user_id),
            ),
        )

        return cursor.lastrowid


# ==========================================
# 查詢內容
# ==========================================

def read(guild_id, content_id):
    init_db()

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM official_contents
            WHERE guild_id=?
              AND id=?
            """,
            (
                str(guild_id),
                int(content_id),
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "找不到這筆官方內容。"
        )

    return dict(row)


def list_items(guild_id):
    init_db()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                kind,
                title,
                status
            FROM official_contents
            WHERE guild_id=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (
                str(guild_id),
            ),
        ).fetchall()

    if not rows:
        return "目前沒有官方內容紀錄。"

    names = {
        "draft": "草稿",
        "published": "已發布",
        "archived": "已下架",
    }

    lines = [
        "📋 **官方內容管理**",
        "",
    ]

    for row in rows:
        title = discord.utils.escape_mentions(
            str(row["title"])
        ).replace("`", "ˋ")

        lines.append(
            f"#{row['id']}｜"
            f"{CONTENT_TYPES.get(row['kind'], row['kind'])}｜"
            f"{title[:30]}｜"
            f"{names.get(row['status'], row['status'])}"
        )

    return "\n".join(lines)[:1900]


# ==========================================
# 修改草稿
# ==========================================

def update(
    guild_id,
    user_id,
    content_id,
    title,
    body,
):
    current = read(
        guild_id,
        content_id,
    )

    if current["status"] != "draft":
        raise ValueError(
            "只有草稿可以編輯。"
        )

    title = str(title).strip()
    body = str(body).strip()

    if not 1 <= len(title) <= 100:
        raise ValueError(
            "標題長度必須是 1～100 字。"
        )

    if not 1 <= len(body) <= MAX_BODY_LENGTH:
        raise ValueError(
            "內容長度必須是 1～3500 字。"
        )

    with transaction() as conn:
        result = conn.execute(
            """
            UPDATE official_contents
            SET title=?,
                body=?,
                editor_id=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
              AND status='draft'
            """,
            (
                title,
                body,
                str(user_id),
                str(guild_id),
                int(content_id),
            ),
        )

        if result.rowcount != 1:
            raise ValueError(
                "草稿狀態已變更，請重新操作。"
            )


# ==========================================
# 內容狀態
# ==========================================

def set_status(
    guild_id,
    user_id,
    content_id,
    status,
    channel_id=None,
    message_id=None,
):
    if status not in (
        "draft",
        "published",
        "archived",
    ):
        raise ValueError(
            "內容狀態不合法。"
        )

    with transaction() as conn:
        result = conn.execute(
            """
            UPDATE official_contents
            SET status=?,
                editor_id=?,
                channel_id=?,
                message_id=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
            """,
            (
                status,
                str(user_id),
                (
                    str(channel_id)
                    if channel_id
                    else None
                ),
                (
                    str(message_id)
                    if message_id
                    else None
                ),
                str(guild_id),
                int(content_id),
            ),
        )

        if result.rowcount != 1:
            raise ValueError(
                "找不到這筆內容。"
            )


# ==========================================
# 長文字分段
# ==========================================

def split_text(text, limit=MAX_PART_LENGTH):
    """依照字數將內容分成多則 Discord 訊息。"""

    text = str(text)

    if not text:
        return []

    parts = []

    while text:
        if len(text) <= limit:
            parts.append(text)
            break

        split_at = text.rfind(
            "\n",
            0,
            limit + 1,
        )

        if split_at <= 0:
            split_at = limit

        chunk = text[:split_at].strip()

        if not chunk:
            chunk = text[:limit]
            split_at = limit

        parts.append(chunk)

        text = text[split_at:].lstrip("\n")

    return parts


# ==========================================
# 建立內容表單
# ==========================================

class NewContent(discord.ui.Modal):
    def __init__(self, kind):
        if kind not in CONTENT_TYPES:
            raise ValueError(
                "內容類型不存在。"
            )

        super().__init__(
            title="新增官方內容草稿",
            timeout=300,
        )

        self.kind = kind

        self.heading = discord.ui.TextInput(
            label="標題",
            min_length=1,
            max_length=100,
            required=True,
        )

        self.details = discord.ui.TextInput(
            label="內容／規則",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=MAX_BODY_LENGTH,
            required=True,
        )

        self.add_item(self.heading)
        self.add_item(self.details)

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)

            content_id = create(
                interaction.guild_id,
                interaction.user.id,
                self.kind,
                str(self.heading.value),
                str(self.details.value),
            )

            await interaction.response.send_message(
                "✅ **官方內容草稿建立成功**\n\n"
                f"編號：#{content_id}\n"
                f"類型：{CONTENT_TYPES[self.kind]}\n\n"
                "可從內容管理中心編輯或發布。",
                ephemeral=True,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 編輯草稿表單
# ==========================================

class EditContent(discord.ui.Modal):
    def __init__(self):
        super().__init__(
            title="編輯官方內容草稿",
            timeout=300,
        )

        self.cid = discord.ui.TextInput(
            label="內容編號",
            max_length=12,
            required=True,
        )

        self.heading = discord.ui.TextInput(
            label="新標題",
            max_length=100,
            required=True,
        )

        self.details = discord.ui.TextInput(
            label="新內容（完整替換）",
            style=discord.TextStyle.paragraph,
            max_length=MAX_BODY_LENGTH,
            required=True,
        )

        self.add_item(self.cid)
        self.add_item(self.heading)
        self.add_item(self.details)

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)

            update(
                interaction.guild_id,
                interaction.user.id,
                int(self.cid.value),
                str(self.heading.value),
                str(self.details.value),
            )

            message = "✅ 官方內容草稿已更新。"

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            message = f"⚠️ {exc}"

        await interaction.response.send_message(
            message[:1900],
            ephemeral=True,
        )


# ==========================================
# 發布與下架表單
# ==========================================

class PublishContent(discord.ui.Modal):
    def __init__(self):
        super().__init__(
            title="發布／下架官方內容",
            timeout=300,
        )

        self.cid = discord.ui.TextInput(
            label="內容編號",
            max_length=12,
            required=True,
        )

        self.action = discord.ui.TextInput(
            label="操作：發布 或 下架",
            max_length=2,
            required=True,
        )

        self.channel = discord.ui.TextInput(
            label="發布頻道 ID（發布時必填）",
            max_length=22,
            required=False,
        )

        self.add_item(self.cid)
        self.add_item(self.action)
        self.add_item(self.channel)

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)

        except PermissionError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            content_id = int(
                self.cid.value
            )

            record = read(
                interaction.guild_id,
                content_id,
            )

            action = str(
                self.action.value
            ).strip()

            if action == "下架":
                if record["status"] == "archived":
                    raise ValueError(
                        "這筆內容已經下架。"
                    )

                set_status(
                    interaction.guild_id,
                    interaction.user.id,
                    content_id,
                    "archived",
                    record["channel_id"],
                    record["message_id"],
                )

                message = (
                    "✅ 已將內容標記為下架。\n"
                    "原本發布到 Discord 的訊息"
                    "不會自動刪除。"
                )

            elif action == "發布":
                if record["status"] != "draft":
                    raise ValueError(
                        "只有草稿可以發布。"
                    )

                channel_id = int(
                    str(self.channel.value).strip()
                )

                channel = await interaction.guild.fetch_channel(
                    channel_id
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
                        "請選擇本伺服器的文字頻道或討論串。"
                    )

                prefix = (
                    f"**{CONTENT_TYPES[record['kind']]}｜"
                    f"{discord.utils.escape_mentions(record['title'])}**"
                )

                body = discord.utils.escape_mentions(
                    record["body"]
                )

                parts = split_text(body)

                if not parts:
                    raise ValueError(
                        "沒有可發布的內容。"
                    )

                sent_messages = []

                try:
                    for index, part in enumerate(parts):
                        if index == 0:
                            text = (
                                prefix
                                + "\n\n"
                                + part
                            )
                        else:
                            text = (
                                f"**續篇 {index + 1}/{len(parts)}**\n\n"
                                + part
                            )

                        sent = await channel.send(
                            text,
                            allowed_mentions=(
                                discord.AllowedMentions.none()
                            ),
                        )

                        sent_messages.append(sent)

                except discord.HTTPException:
                    # 避免中途發送失敗時，
                    # 留下沒有完成的連續公告。
                    for sent in sent_messages:
                        try:
                            await sent.delete()
                        except discord.HTTPException:
                            LOG.warning(
                                "清理部分發布訊息失敗：%s",
                                sent.id,
                            )
                    raise

                set_status(
                    interaction.guild_id,
                    interaction.user.id,
                    content_id,
                    "published",
                    channel.id,
                    sent_messages[0].id,
                )

                message = (
                    "✅ **官方內容發布成功**\n\n"
                    f"內容編號：#{content_id}\n"
                    f"發布頻道：{channel.mention}\n"
                    f"訊息數量：{len(sent_messages)}"
                )

            else:
                raise ValueError(
                    "操作只能填寫「發布」或「下架」。"
                )

        except (
            ValueError,
            sqlite3.Error,
            discord.HTTPException,
        ) as exc:
            LOG.warning(
                "官方內容發布或下架失敗：%s",
                exc,
            )

            message = f"⚠️ {exc}"

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )


# ==========================================
# 內容類型選單
# ==========================================

class ContentTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=name,
                value=key,
            )
            for key, name in CONTENT_TYPES.items()
        ]

        super().__init__(
            placeholder="選擇要建立的內容類型",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        try:
            require_owner(interaction)

            await interaction.response.send_modal(
                NewContent(
                    self.values[0]
                )
            )

        except (
            PermissionError,
            ValueError,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 內容管理主面板
# ==========================================

class ContentView(discord.ui.View):
    def __init__(self, interaction):
        super().__init__(
            timeout=TIMEOUT
        )

        self.user_id = interaction.user.id
        self.guild_id = interaction.guild_id

        self.add_item(
            ContentTypeSelect()
        )

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.user_id
            or interaction.guild_id != self.guild_id
            or not owner(interaction)
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的內容管理面板，"
                "或你沒有管理權限。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="📋 內容清單",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def listing(self, interaction, button):
        try:
            content = list_items(
                interaction.guild_id
            )

            await interaction.response.send_message(
                content[:1900],
                ephemeral=True,
            )

        except sqlite3.Error as exc:
            await interaction.response.send_message(
                f"⚠️ 資料庫錯誤：{exc}",
                ephemeral=True,
            )

    @discord.ui.button(
        label="✏️ 編輯草稿",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def editing(self, interaction, button):
        await interaction.response.send_modal(
            EditContent()
        )

    @discord.ui.button(
        label="📢 發布／下架",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def publishing(self, interaction, button):
        await interaction.response.send_modal(
            PublishContent()
        )

    @discord.ui.button(
        label="⬅️ 返回管理中心",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def back(self, interaction, button):
        from modules.official_center import (
            AdminToolsView,
            management_text,
        )

        view = AdminToolsView(
            interaction
        )

        await interaction.response.edit_message(
            content=management_text(view),
            view=view,
        )


# ==========================================
# 官方管理入口
# ==========================================

class ContentButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="✍️ 內容建立",
            style=discord.ButtonStyle.primary,
            row=3,
        )

    async def callback(self, interaction):
        try:
            require_owner(interaction)

            await interaction.response.edit_message(
                content=(
                    "✍️ **官方內容建立中心**\n\n"
                    "請選擇內容類型建立草稿，"
                    "或管理現有內容。\n\n"
                    "目前支援劇情、商品企劃、"
                    "抽獎獎單、獎項、活動、"
                    "官方 NPC、成就稱號、"
                    "世界觀與任務的文字內容管理。"
                ),
                view=ContentView(interaction),
            )

        except PermissionError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 模組初始化
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    LOG.info(
        "官方內容建立中心已載入。"
    )
