
"""Discord RP｜官方內容建立中心。

功能：
- 官方內容草稿建立、編輯、發布與下架
- 劇情管理入口
- 抽獎管理入口
- 官方 NPC 管理入口
- 商品企劃、獎項、活動、成就、世界觀、任務草稿
- 返回官方管理中心

正式的角色、劇情與抽獎資料由各自模組管理。
本模組的 official_contents 只保存一般官方內容草稿。
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import transaction, get_connection


LOG = logging.getLogger(__name__)


TYPES = {
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


# ==========================================
# 資料庫初始化
# ==========================================

def init_db():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS official_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK(
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
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_official_contents
            ON official_contents(
                guild_id,
                status,
                id
            )
        """)


# ==========================================
# 權限與工具
# ==========================================

def owner(i: discord.Interaction) -> bool:
    return bool(
        i.guild
        and i.user.id == i.guild.owner_id
    )


def safe_text(value) -> str:
    return discord.utils.escape_mentions(str(value))


async def tell(
    i: discord.Interaction,
    message: str,
):
    if i.response.is_done():
        await i.followup.send(
            message[:1900],
            ephemeral=True,
        )
    else:
        await i.response.send_message(
            message[:1900],
            ephemeral=True,
        )


# ==========================================
# 草稿資料操作
# ==========================================

def create(
    gid,
    uid,
    kind,
    title,
    body,
):
    init_db()

    if kind not in TYPES:
        raise ValueError("未知內容類型。")

    title = title.strip()
    body = body.strip()

    if not 1 <= len(title) <= 100:
        raise ValueError(
            "標題必須為 1～100 字。"
        )

    if not 1 <= len(body) <= 3500:
        raise ValueError(
            "內容必須為 1～3500 字。"
        )

    with transaction() as db:
        cur = db.execute(
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
                str(gid),
                kind,
                title,
                body,
                str(uid),
                str(uid),
            ),
        )

        return int(cur.lastrowid)


def list_items(gid):
    init_db()

    with get_connection() as db:
        rows = db.execute(
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
            (str(gid),),
        ).fetchall()

    if not rows:
        return "目前沒有官方內容草稿。"

    status_names = {
        "draft": "草稿",
        "published": "已發布",
        "archived": "已下架",
    }

    return "\n".join(
        (
            f"#{r['id']}｜"
            f"{TYPES.get(r['kind'], r['kind'])}｜"
            f"{safe_text(r['title'][:35])}｜"
            f"{status_names.get(r['status'], r['status'])}"
        )
        for r in rows
    )


def read(gid, cid):
    init_db()

    with get_connection() as db:
        row = db.execute(
            """
            SELECT *
            FROM official_contents
            WHERE guild_id=?
              AND id=?
            """,
            (
                str(gid),
                int(cid),
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "找不到這筆官方內容。"
        )

    return dict(row)


def update(
    gid,
    uid,
    cid,
    title,
    body,
):
    row = read(gid, cid)

    if row["status"] != "draft":
        raise ValueError(
            "只有草稿可以編輯；"
            "已發布內容須先下架。"
        )

    title = title.strip()
    body = body.strip()

    if not 1 <= len(title) <= 100:
        raise ValueError(
            "標題必須為 1～100 字。"
        )

    if not 1 <= len(body) <= 3500:
        raise ValueError(
            "內容必須為 1～3500 字。"
        )

    with transaction() as db:
        cur = db.execute(
            """
            UPDATE official_contents
            SET
                title=?,
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
                str(uid),
                str(gid),
                int(cid),
            ),
        )

        if cur.rowcount != 1:
            raise ValueError(
                "草稿狀態已變更，請重新操作。"
            )


def set_status(
    gid,
    uid,
    cid,
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
            "內容狀態錯誤。"
        )

    with transaction() as db:
        cur = db.execute(
            """
            UPDATE official_contents
            SET
                status=?,
                editor_id=?,
                channel_id=?,
                message_id=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
            """,
            (
                status,
                str(uid),
                (
                    str(channel_id)
                    if channel_id is not None
                    else None
                ),
                (
                    str(message_id)
                    if message_id is not None
                    else None
                ),
                str(gid),
                int(cid),
            ),
        )

        if cur.rowcount != 1:
            raise ValueError(
                "找不到這筆官方內容。"
            )


# ==========================================
# 建立新內容草稿
# ==========================================

class NewContent(
    discord.ui.Modal,
    title="新增官方內容草稿",
):

    heading = discord.ui.TextInput(
        label="標題",
        max_length=100,
    )

    details = discord.ui.TextInput(
        label="內容／規則",
        style=discord.TextStyle.paragraph,
        max_length=3500,
    )

    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    async def on_submit(
        self,
        i: discord.Interaction,
    ):
        if not owner(i):
            return await tell(
                i,
                "⛔ 僅限群主建立官方內容。",
            )

        try:
            cid = create(
                i.guild_id,
                i.user.id,
                self.kind,
                str(self.heading.value),
                str(self.details.value),
            )

            message = (
                f"✅ 已建立"
                f"{TYPES[self.kind]}草稿 #{cid}。\n"
                "可從內容管理功能進行編輯及發布。"
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            LOG.exception("建立官方內容草稿失敗")
            message = f"⚠️ {exc}"

        await tell(i, message)


# ==========================================
# 編輯內容草稿
# ==========================================

class EditContent(
    discord.ui.Modal,
    title="編輯內容草稿",
):

    cid = discord.ui.TextInput(
        label="內容編號",
        max_length=12,
    )

    heading = discord.ui.TextInput(
        label="新標題",
        max_length=100,
    )

    details = discord.ui.TextInput(
        label="新內容（完整替換）",
        style=discord.TextStyle.paragraph,
        max_length=3500,
    )

    async def on_submit(
        self,
        i: discord.Interaction,
    ):
        if not owner(i):
            return await tell(
                i,
                "⛔ 僅限群主修改。",
            )

        try:
            update(
                i.guild_id,
                i.user.id,
                int(str(self.cid.value)),
                str(self.heading.value),
                str(self.details.value),
            )

            message = "✅ 草稿修改成功。"

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            message = f"⚠️ {exc}"

        await tell(i, message)


# ==========================================
# 發布與下架
# ==========================================

class PublishContent(
    discord.ui.Modal,
    title="發布／下架官方內容",
):

    cid = discord.ui.TextInput(
        label="內容編號",
        max_length=12,
    )

    action = discord.ui.TextInput(
        label="操作：發布 或 下架",
        max_length=2,
    )

    channel = discord.ui.TextInput(
        label="發布到頻道 ID（發布時必填）",
        required=False,
        max_length=22,
    )

    async def on_submit(
        self,
        i: discord.Interaction,
    ):
        if not owner(i):
            return await tell(
                i,
                "⛔ 僅限群主發布或下架。",
            )

        await i.response.defer(
            ephemeral=True,
        )

        try:
            cid = int(str(self.cid.value).strip())
            record = read(i.guild_id, cid)

            action = str(
                self.action.value
            ).strip()

            if action == "下架":

                if record["status"] != "published":
                    raise ValueError(
                        "只有已發布的內容可以下架。"
                    )

                # 不自動刪除原始 Discord 訊息。
                set_status(
                    i.guild_id,
                    i.user.id,
                    cid,
                    "archived",
                    record["channel_id"],
                    record["message_id"],
                )

                message = (
                    "✅ 已將內容標記為下架。\n"
                    "原 Discord 訊息仍然保留，"
                    "如需刪除請自行處理。"
                )

            elif action == "發布":

                if record["status"] != "draft":
                    raise ValueError(
                        "只有草稿可以發布。"
                    )

                channel_text = str(
                    self.channel.value or ""
                ).strip()

                if not channel_text.isdigit():
                    raise ValueError(
                        "請輸入正確的發布頻道 ID。"
                    )

                channel = await i.guild.fetch_channel(
                    int(channel_text)
                )

                if (
                    not isinstance(
                        channel,
                        (
                            discord.TextChannel,
                            discord.Thread,
                        ),
                    )
                    or channel.guild.id != i.guild_id
                ):
                    raise ValueError(
                        "必須指定本伺服器的"
                        "文字頻道或討論串。"
                    )

                title = safe_text(
                    record["title"]
                )

                body = safe_text(
                    record["body"]
                )

                content = (
                    f"**{TYPES[record['kind']]}｜"
                    f"{title}**\n\n{body}"
                )

                if len(content) > 2000:
                    raise ValueError(
                        "發布內容超過 Discord "
                        "單則訊息 2000 字限制，"
                        "請縮短內容。"
                    )

                sent = await channel.send(
                    content,
                    allowed_mentions=(
                        discord.AllowedMentions.none()
                    ),
                )

                set_status(
                    i.guild_id,
                    i.user.id,
                    cid,
                    "published",
                    channel.id,
                    sent.id,
                )

                message = (
                    f"✅ 已發布內容 #{cid} "
                    f"至 {channel.mention}。"
                )

            else:
                raise ValueError(
                    "操作只能輸入「發布」或「下架」。"
                )

        except (
            ValueError,
            discord.HTTPException,
            sqlite3.Error,
        ) as exc:
            LOG.exception("官方內容發布／下架失敗")
            message = f"⚠️ {exc}"

        await i.followup.send(
            message[:1900],
            ephemeral=True,
        )


# ==========================================
# 內容類型下拉選單
# ==========================================

class ContentTypeSelect(
    discord.ui.Select,
):

    def __init__(self):
        options = [
            discord.SelectOption(
                label=name,
                value=key,
            )
            for key, name in TYPES.items()
        ]

        super().__init__(
            placeholder="選擇要建立的內容類型",
            options=options,
            row=0,
        )

    async def callback(
        self,
        i: discord.Interaction,
    ):
        if not owner(i):
            return await tell(
                i,
                "⛔ 僅限群主管理官方內容。",
            )

        selected = self.values[0]

        # ==================================
        # 抽獎管理
        # ==================================

        if selected == "lottery":
            from modules.lottery_admin_ui import (
                LotteryAdminView,
            )

            await i.response.edit_message(
                content=(
                    "🎟️ **抽獎獎單管理**\n"
                    "建立獎單及多行獎品、"
                    "選擇可抽獎身分組、"
                    "查看中獎與領獎紀錄。"
                ),
                view=LotteryAdminView(i),
            )
            return

        # ==================================
        # 劇情編輯器
        # ==================================

        if selected == "story":
            from modules.story_builder import (
                StoryBuilderView,
            )

            await i.response.edit_message(
                content=(
                    "📖 **角色劇情編輯器**\n"
                    "管理章節、回目、"
                    "分支與劇情獎勵。"
                ),
                view=StoryBuilderView(i),
            )
            return

        # ==================================
        # 官方 NPC 管理中心
        # ==================================

        if selected == "npc":
            from modules.official_npc_manager import (
                OfficialNpcView,
            )

            await i.response.edit_message(
                content=(
                    "👑 **官方 NPC 管理中心**\n\n"
                    "可查看所有已登記的官方 NPC，"
                    "修改名稱及管理啟用狀態。\n\n"
                    "新增 NPC 請先透過"
                    " `/角色單登記` "
                    "提交原始角色單訊息連結。\n"
                    "群主可以代為登記，"
                    "但不會因此改變角色擁有者。"
                ),
                view=OfficialNpcView(i),
            )
            return

        # ==================================
        # 其他官方內容
        # ==================================

        # 目前尚未有專屬管理模組的類型，
        # 繼續使用官方內容草稿功能。
        await i.response.send_modal(
            NewContent(selected)
        )


# ==========================================
# 官方內容建立主面板
# ==========================================

class ContentView(
    discord.ui.View,
):

    def __init__(
        self,
        i: discord.Interaction,
    ):
        super().__init__(
            timeout=600
        )

        self.user_id = i.user.id
        self.guild_id = i.guild_id

        self.add_item(
            ContentTypeSelect()
        )

        # 劇情快捷按鈕
        from modules.story_builder import (
            StoryBuilderButton,
        )

        self.add_item(
            StoryBuilderButton()
        )

        # 抽獎快捷按鈕
        from modules.lottery_admin_ui import (
            LotteryAdminButton,
        )

        self.add_item(
            LotteryAdminButton()
        )

    async def interaction_check(
        self,
        i: discord.Interaction,
    ):
        if (
            i.user.id != self.user_id
            or i.guild_id != self.guild_id
            or not owner(i)
        ):
            await tell(
                i,
                (
                    "⛔ 這不是你的管理面板，"
                    "或你的權限不足。"
                ),
            )
            return False

        return True

    @discord.ui.button(
        label="📋 內容清單",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def listing(self, i, button):
        await tell(
            i,
            list_items(i.guild_id),
        )

    @discord.ui.button(
        label="✏️ 編輯草稿",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def editing(self, i, button):
        await i.response.send_modal(
            EditContent()
        )

    @discord.ui.button(
        label="📢 發布／下架",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def publishing(self, i, button):
        await i.response.send_modal(
            PublishContent()
        )

    @discord.ui.button(
        label="↩️ 返回官方",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def back(self, i, button):
        from modules.official_center import (
            OfficialHome,
        )

        await i.response.edit_message(
            content="🏛️ **官方管理中心**",
            view=OfficialHome(i),
        )


# ==========================================
# 官方中心入口按鈕
# ==========================================

class ContentButton(
    discord.ui.Button,
):

    def __init__(self):
        super().__init__(
            label="✍️ 內容建立",
            style=discord.ButtonStyle.primary,
            row=3,
        )

    async def callback(
        self,
        i: discord.Interaction,
    ):
        if not owner(i):
            return await tell(
                i,
                "⛔ 只有群主可以管理官方內容。",
            )

        await i.response.edit_message(
            content=(
                "✍️ **內容建立中心**\n\n"
                "請選擇要管理的內容類型。\n"
                "劇情、抽獎與官方 NPC "
                "使用專屬管理介面。\n"
                "其他內容可先建立草稿。"
            ),
            view=ContentView(i),
        )


# ==========================================
# Discord 模組載入入口
# ==========================================

async def setup(
    bot: commands.Bot,
):
    init_db()

    LOG.info(
        "官方內容建立中心已載入。"
    )
