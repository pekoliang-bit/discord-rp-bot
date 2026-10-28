"""Discord RP｜官方內容建立中心 V2。

由 /官方 進入。

功能：
- 官方內容草稿建立
- 草稿編輯
- 內容列表
- 發布 Discord 公告
- 下架內容
- 下架後重新編輯
- 專屬管理模組導向
- 群主最高管理權限
- 防止其他玩家操作管理面板

專屬管理：
- 劇情
- 抽獎獎單
- 官方 NPC
- 成就稱號
- 標籤庫

一般草稿：
- 商品企劃
- 獎項
- 活動
- 世界觀
- 任務
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import (
    transaction,
    get_connection,
)


LOG = logging.getLogger(__name__)


# ==========================================
# 內容類型
# ==========================================

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
    "tags": "🏷️ 標籤庫",
}


# 這些類型必須進入真正的管理系統，
# 不允許建立只有文字、沒有遊戲效果的假資料。
SPECIALIZED_TYPES = frozenset({
    "story",
    "lottery",
    "npc",
    "achievement",
    "event",
    "tags",
    "prize",
})


# ==========================================
# 資料庫
# ==========================================

def init_db():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            official_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN (
                        'draft',
                        'published',
                        'archived'
                    )),

                creator_id TEXT NOT NULL,
                editor_id TEXT NOT NULL,

                message_id TEXT,
                channel_id TEXT,

                created_at TEXT
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at TEXT
                    DEFAULT CURRENT_TIMESTAMP
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
# 群主權限
# ==========================================

def owner(i: discord.Interaction) -> bool:
    return bool(
        i.guild is not None
        and i.user.id == i.guild.owner_id
    )


async def tell(i, message):
    message = str(message)[:1900]

    if i.response.is_done():
        await i.followup.send(
            message,
            ephemeral=True,
        )
    else:
        await i.response.send_message(
            message,
            ephemeral=True,
        )


def clean_text(value, *, limit, label):
    value = str(value).strip()

    if not 1 <= len(value) <= limit:
        raise ValueError(
            f"{label}需為1～{limit}字。"
        )

    return value


# ==========================================
# 建立草稿
# ==========================================

def create(gid, uid, kind, title, body):
    init_db()

    if kind not in TYPES:
        raise ValueError(
            "未知內容類型。"
        )

    if kind in SPECIALIZED_TYPES:
        raise ValueError(
            "此類型必須透過專屬管理介面建立。"
        )

    title = clean_text(
        title,
        limit=100,
        label="標題",
    )

    body = clean_text(
        body,
        limit=3500,
        label="內容",
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

        return cur.lastrowid


# ==========================================
# 內容清單
# ==========================================

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
        return "📭 目前沒有官方內容草稿。"

    status_names = {
        "draft": "📝 草稿",
        "published": "✅ 已發布",
        "archived": "📦 已下架",
    }

    lines = [
        "📋 **最近20筆官方內容**",
        "",
    ]

    for row in rows:
        title = discord.utils.escape_mentions(
            str(row["title"])[:35]
        )

        title = discord.utils.escape_markdown(
            title
        )

        status = status_names.get(
            row["status"],
            row["status"],
        )

        lines.append(
            f"#{row['id']}｜"
            f"{TYPES.get(row['kind'], row['kind'])}"
            f"｜{title}｜{status}"
        )

    return "\n".join(lines)[:1900]


# ==========================================
# 讀取內容
# ==========================================

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


# ==========================================
# 編輯草稿
# ==========================================

def update(gid, uid, cid, title, body):
    """草稿可以修改。

    已下架內容允許重新編輯，
    編輯後狀態恢復為草稿。

    已發布內容必須先下架。
    """

    init_db()

    title = clean_text(
        title,
        limit=100,
        label="標題",
    )

    body = clean_text(
        body,
        limit=3500,
        label="內容",
    )

    with transaction() as db:
        row = db.execute(
            """
            SELECT status
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

        if row["status"] == "published":
            raise ValueError(
                "內容仍在發布中，請先下架再編輯。"
            )

        cursor = db.execute(
            """
            UPDATE official_contents
            SET
                title=?,
                body=?,
                status='draft',
                editor_id=?,
                channel_id=NULL,
                message_id=NULL,
                updated_at=CURRENT_TIMESTAMP

            WHERE guild_id=?
              AND id=?
              AND status IN ('draft', 'archived')
            """,
            (
                title,
                body,
                str(uid),
                str(gid),
                int(cid),
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "內容狀態已變更，請重新操作。"
            )


# ==========================================
# 更新內容狀態
# ==========================================

def set_status(
    gid,
    uid,
    cid,
    status,
    channel_id=None,
    message_id=None,
):
    """更新官方內容狀態。

    發布：草稿 -> 已發布
    下架：已發布 -> 已下架

    不會自動刪除 Discord 公告。
    """

    init_db()

    if status not in (
        "draft",
        "published",
        "archived",
    ):
        raise ValueError(
            "未知內容狀態。"
        )

    with transaction() as db:
        row = db.execute(
            """
            SELECT status
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

        previous = row["status"]

        if status == "published":
            if previous != "draft":
                raise ValueError(
                    "只有草稿可以發布。"
                )

            if not channel_id or not message_id:
                raise ValueError(
                    "發布時必須記錄頻道與訊息 ID。"
                )

        elif status == "archived":
            if previous != "published":
                raise ValueError(
                    "只有已發布內容可以下架。"
                )

        elif status == "draft":
            if previous != "archived":
                raise ValueError(
                    "只有已下架內容可以恢復草稿。"
                )

        cursor = db.execute(
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
              AND status=?
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
                previous,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "內容狀態已改變，操作未完成。"
            )


# ==========================================
# 建立草稿表單
# ==========================================

class NewContent(
    discord.ui.Modal,
    title="新增官方內容草稿",
):

    heading = discord.ui.TextInput(
        label="標題",
        max_length=100,
        required=True,
    )

    details = discord.ui.TextInput(
        label="內容／規則",
        style=discord.TextStyle.paragraph,
        max_length=3500,
        required=True,
    )

    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    async def on_submit(self, i):
        if not owner(i):
            return await tell(
                i,
                "⛔ 只有群主可以建立官方內容。"
            )

        try:
            cid = create(
                i.guild_id,
                i.user.id,
                self.kind,
                self.heading.value,
                self.details.value,
            )

            msg = (
                f"✅ 已建立"
                f"{TYPES[self.kind]}草稿 #{cid}。\n"
                "可使用「發布／下架」正式發布。"
            )

        except (ValueError, sqlite3.Error) as exc:
            msg = f"⚠️ {exc}"

        await tell(i, msg)


# ==========================================
# 編輯草稿表單
# ==========================================

class EditContent(
    discord.ui.Modal,
    title="編輯官方內容",
):

    cid = discord.ui.TextInput(
        label="內容編號",
        max_length=12,
        required=True,
    )

    heading = discord.ui.TextInput(
        label="新標題",
        max_length=100,
        required=True,
    )

    details = discord.ui.TextInput(
        label="新內容（完整替換）",
        style=discord.TextStyle.paragraph,
        max_length=3500,
        required=True,
    )

    async def on_submit(self, i):
        if not owner(i):
            return await tell(
                i,
                "⛔ 只有群主可以編輯官方內容。"
            )

        try:
            cid = int(
                str(self.cid.value).strip()
            )

            update(
                i.guild_id,
                i.user.id,
                cid,
                self.heading.value,
                self.details.value,
            )

            msg = (
                f"✅ 內容 #{cid} 修改成功。\n"
                "目前狀態：草稿。"
            )

        except (ValueError, sqlite3.Error) as exc:
            msg = f"⚠️ {exc}"

        await tell(i, msg)


# ==========================================
# 發布訊息內容
# ==========================================

def build_publication_text(record):
    kind = TYPES.get(
        record["kind"],
        record["kind"],
    )

    title = discord.utils.escape_mentions(
        record["title"]
    )

    body = discord.utils.escape_mentions(
        record["body"]
    )

    content = (
        f"**{kind}｜{title}**\n\n{body}"
    )

    if len(content) > 2000:
        raise ValueError(
            "內容超過 Discord 單則訊息"
            "2000 字限制，請縮短後再發布。"
        )

    return content


# ==========================================
# 發布與下架表單
# ==========================================

class PublishContent(
    discord.ui.Modal,
    title="發布／下架官方內容",
):

    cid = discord.ui.TextInput(
        label="內容編號",
        max_length=12,
        required=True,
    )

    action = discord.ui.TextInput(
        label="操作：發布 或 下架",
        max_length=2,
        required=True,
    )

    channel = discord.ui.TextInput(
        label="發布頻道 ID（下架時可留空）",
        required=False,
        max_length=22,
    )

    async def on_submit(self, i):
        if not owner(i):
            return await tell(
                i,
                "⛔ 只有群主可以發布或下架。"
            )

        await i.response.defer(
            ephemeral=True
        )

        try:
            cid = int(
                str(self.cid.value).strip()
            )

            action = str(
                self.action.value
            ).strip()

            record = read(
                i.guild_id,
                cid,
            )

            # ------------------------------
            # 下架
            # ------------------------------

            if action == "下架":
                set_status(
                    i.guild_id,
                    i.user.id,
                    cid,
                    "archived",
                    record["channel_id"],
                    record["message_id"],
                )

                msg = (
                    f"✅ 內容 #{cid} 已下架。\n"
                    "資料已標記為下架，"
                    "原 Discord 公告不會自動刪除。\n"
                    "如需修改，可直接使用"
                    "「編輯草稿」。"
                )

            # ------------------------------
            # 發布
            # ------------------------------

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
                        "請填寫有效的頻道 ID。"
                    )

                target = await i.guild.fetch_channel(
                    int(channel_text)
                )

                if (
                    not isinstance(
                        target,
                        (
                            discord.TextChannel,
                            discord.Thread,
                        ),
                    )
                    or target.guild.id != i.guild_id
                ):
                    raise ValueError(
                        "發布位置必須是本伺服器的"
                        "文字頻道或討論串。"
                    )

                if (
                    isinstance(target, discord.Thread)
                    and target.archived
                ):
                    raise ValueError(
                        "目標討論串已封存，"
                        "請先解除封存。"
                    )

                content = build_publication_text(
                    record
                )

                sent = await target.send(
                    content,
                    allowed_mentions=(
                        discord.AllowedMentions.none()
                    ),
                )

                try:
                    set_status(
                        i.guild_id,
                        i.user.id,
                        cid,
                        "published",
                        target.id,
                        sent.id,
                    )

                except Exception:
                    LOG.exception(
                        "Discord 訊息已送出，"
                        "但資料庫發布狀態更新失敗："
                        "guild=%s content=%s "
                        "message=%s",
                        i.guild_id,
                        cid,
                        sent.id,
                    )

                    # 避免資料庫失敗後
                    # 留下未登記的重複公告。
                    try:
                        await sent.delete()

                    except discord.HTTPException:
                        LOG.exception(
                            "未能清理未登記的公告訊息。"
                        )

                    raise

                msg = (
                    f"✅ 內容 #{cid} 已發布！\n"
                    f"發布頻道：{target.mention}\n"
                    f"訊息 ID：{sent.id}"
                )

            else:
                raise ValueError(
                    "操作只能輸入「發布」或「下架」。"
                )

        except (
            ValueError,
            sqlite3.Error,
            discord.HTTPException,
            discord.Forbidden,
            discord.NotFound,
        ) as exc:
            msg = f"⚠️ {exc}"

        await i.followup.send(
            msg[:1900],
            ephemeral=True,
        )


# ==========================================
# 專屬管理介面導向
# ==========================================

async def open_specialized_manager(
    i,
    selected,
):
    """開啟已存在的專屬管理模組。

    各管理模組保留自己的設定、
    資料表和操作邏輯。

    回傳 True 表示已處理。
    """

    # ======================================
    # 獎項／榮譽專屬管理（沿用既有 zz_awards）
    # ======================================

    if selected == "prize":
        from modules.zz_awards import (
            init_tables as init_award_tables,
            listing as award_listing,
            AwardsAdminView,
        )

        init_award_tables()
        content, page, _ = award_listing(i.guild_id)
        await i.response.edit_message(
            content=content,
            view=AwardsAdminView(i, page),
        )
        return True

    if selected == "event":
        from modules.event_project_manager import EventAdminView, is_event_admin
        if not is_event_admin(i):
            await tell(i, "僅群主或正式活動管理員可使用活動管理中心。")
            return True
        await i.response.edit_message(
            content="🎪 **活動企劃管理**\n玩家透過 `/活動企劃` 提交，正式管理員使用 `/活動企劃審核`；臨時主辦只能管理自己通過的活動。",
            view=EventAdminView(i),
        )
        return True

    if selected == "tags":
        from modules.tag_admin_ui import (
            TagAdminView,
        )

        await i.response.edit_message(
            content=(
                "🏷️ **官方標籤庫管理**\n"
                "建立、修改、啟用、"
                "停用及管理角色標籤。"
            ),
            view=TagAdminView(i),
        )

        return True

    if selected == "lottery":
        from modules.lottery_admin_ui import (
            LotteryAdminView,
        )

        await i.response.edit_message(
            content=(
                "🎰 **抽獎獎單管理**\n"
                "管理正式抽獎獎單與獎項。"
            ),
            view=LotteryAdminView(i),
        )

        return True

    if selected == "npc":
        from modules.official_npc_manager import (
            OfficialNpcView,
        )

        await i.response.edit_message(
            content=(
                "👑 **官方 NPC 管理**\n"
                "新 NPC 請先透過"
                "`/角色單登記` 完成登記。"
            ),
            view=OfficialNpcView(i),
        )

        return True

    if selected == "story":
        from modules.story_builder import (
            StoryBuilderView,
        )

        await i.response.edit_message(
            content=(
                "📖 **角色劇情管理**\n"
                "建立及編輯正式劇情內容。"
            ),
            view=StoryBuilderView(i),
        )

        return True

    # ======================================
    # 成就稱號專屬管理
    # ======================================

    if selected == "achievement":
        from modules.achievement_admin_ui import (
            AchievementAdminView,
        )

        await i.response.edit_message(
            content=(
                "🎖️ **成就稱號管理中心**\n\n"
                "管理固定稱號、"
                "活動專屬稱號、"
                "取得條件及獎勵設定。\n"
                "稱號資料直接連接正式成就系統，"
                "不建立普通文字草稿。"
            ),
            view=AchievementAdminView(i),
        )

        return True

    return False


# ==========================================
# 內容類型選單
# ==========================================

class ContentTypeSelect(
    discord.ui.Select
):

    def __init__(self):
        super().__init__(
            placeholder="選擇要建立／管理的內容類型",
            options=[
                discord.SelectOption(
                    label=name,
                    value=key,
                )
                for key, name in TYPES.items()
            ],
            row=0,
        )

    async def callback(self, i):
        if not owner(i):
            return await tell(
                i,
                "⛔ 僅限群主使用。"
            )

        selected = self.values[0]

        try:
            handled = await open_specialized_manager(
                i,
                selected,
            )

            if handled:
                return

            # 其餘類型走官方內容草稿系統。
            if selected not in TYPES:
                return await tell(
                    i,
                    "未知的內容類型。"
                )

            await i.response.send_modal(
                NewContent(selected)
            )

        except (
            ImportError,
            AttributeError,
        ):
            LOG.exception(
                "內容建立專屬管理模組無法載入：%s",
                selected,
            )

            await tell(
                i,
                "⚠️ 此管理功能尚未正確載入，"
                "請確認對應模組已安裝。"
            )

        except Exception:
            LOG.exception(
                "開啟內容管理介面失敗：%s",
                selected,
            )

            await tell(
                i,
                "⚠️ 開啟管理介面失敗，"
                "請檢查機器人紀錄。"
            )


# ==========================================
# 內容建立中心主介面
# ==========================================

class ContentView(
    discord.ui.View
):

    def __init__(self, i):
        super().__init__(
            timeout=600
        )

        self.user_id = i.user.id
        self.guild_id = i.guild_id

        self.add_item(
            ContentTypeSelect()
        )

        # 保留原本劇情快捷按鈕。
        from modules.story_builder import (
            StoryBuilderButton,
        )

        self.add_item(
            StoryBuilderButton()
        )

        # 保留原本抽獎快捷按鈕。
        from modules.lottery_admin_ui import (
            LotteryAdminButton,
        )

        self.add_item(
            LotteryAdminButton()
        )

    async def interaction_check(self, i):
        if (
            i.user.id != self.user_id
            or i.guild_id != self.guild_id
            or not owner(i)
        ):
            await tell(
                i,
                "⛔ 這不是你的管理面板，"
                "或你的權限不足。",
            )

            return False

        return True

    # --------------------------------------
    # 官方內容清單
    # --------------------------------------

    @discord.ui.button(
        label="📋 內容清單",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def listing(
        self,
        i,
        button,
    ):
        try:
            message = list_items(
                i.guild_id
            )

        except sqlite3.Error:
            LOG.exception(
                "讀取官方內容清單失敗。"
            )

            message = (
                "⚠️ 無法讀取官方內容清單。"
            )

        await tell(
            i,
            message,
        )

    # --------------------------------------
    # 編輯草稿
    # --------------------------------------

    @discord.ui.button(
        label="✏️ 編輯草稿",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def editing(
        self,
        i,
        button,
    ):
        await i.response.send_modal(
            EditContent()
        )

    # --------------------------------------
    # 發布與下架
    # --------------------------------------

    @discord.ui.button(
        label="📢 發布／下架",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def publishing(
        self,
        i,
        button,
    ):
        await i.response.send_modal(
            PublishContent()
        )

    # --------------------------------------
    # 返回官方
    # --------------------------------------

    @discord.ui.button(
        label="↩️ 返回官方",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def back(
        self,
        i,
        button,
    ):
        from modules.official_center import (
            OfficialHome,
        )

        await i.response.edit_message(
            content=(
                "🏛️ **官方管理中心**"
            ),
            view=OfficialHome(i),
        )


# ==========================================
# 官方中心快捷入口
# ==========================================

class ContentButton(
    discord.ui.Button
):

    def __init__(self):
        super().__init__(
            label="✍️ 內容建立",
            style=discord.ButtonStyle.primary,
            row=3,
        )

    async def callback(self, i):
        if not owner(i):
            return await tell(
                i,
                "⛔ 只有群主可以建立官方內容。",
            )

        try:
            view = ContentView(i)

        except Exception:
            LOG.exception(
                "內容建立中心初始化失敗。"
            )

            return await tell(
                i,
                "⚠️ 內容建立中心無法載入，"
                "請確認劇情與抽獎管理模組存在。"
            )

        await i.response.edit_message(
            content=(
                "✍️ **官方內容建立中心**\n\n"
                "請從下拉選單選擇管理項目。\n"
                "劇情、抽獎、NPC、稱號、"
                "標籤使用專屬管理介面。\n\n"
                "其他官方內容可先建立草稿，"
                "確認後再發布。"
            ),
            view=view,
        )


# ==========================================
# Discord 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    LOG.info(
        "官方內容建立中心 V2 已載入。"
    )
