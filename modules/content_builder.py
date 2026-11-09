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
- 獎項
- 活動
- 世界觀
- 任務
"""

from __future__ import annotations

import logging
import sqlite3
import json

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
    "mission",
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

    if selected == "mission":
        from modules.task_admin import open_task_admin
        await open_task_admin(i)
        return True

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
# 世界觀設定：獨立伺服器配置
# ==========================================

WORLD_DEFAULT = {
    "template": "entertainment", "template_version": 1,
    "world_name": "現代娛樂圈", "background": "",
    "features": {"bank": True, "shop": True, "company": True, "faction": True, "rumor": True},
    "channels": {}, "terms": {"company": "公司", "group": "集團", "brand": "品牌", "faction": "陣營", "position": "職位", "currency": "金幣"},
    "stats": {}, "status_levels": {}, "company_rules": {}, "detail_rules": {},
    "categories": {}, "permissions": {}, "overrides": {},
}

def world_init():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS world_configs (
            guild_id TEXT PRIMARY KEY, config_json TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("""CREATE TABLE IF NOT EXISTS world_template_updates (
            guild_id TEXT PRIMARY KEY, pending_version INTEGER NOT NULL,
            notice TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

def world_get(gid):
    world_init()
    with get_connection() as db:
        row = db.execute("SELECT config_json FROM world_configs WHERE guild_id=?", (str(gid),)).fetchone()
    if not row:
        return json.loads(json.dumps(WORLD_DEFAULT, ensure_ascii=False))
    value = json.loads(row[0])
    return {**json.loads(json.dumps(WORLD_DEFAULT, ensure_ascii=False)), **value}

def world_save(gid, data):
    world_init()
    with transaction() as db:
        db.execute("""INSERT INTO world_configs(guild_id,config_json) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET config_json=excluded.config_json,
            updated_at=CURRENT_TIMESTAMP""", (str(gid), json.dumps(data, ensure_ascii=False)))

class WorldBackgroundModal(discord.ui.Modal):
    """世界背景專用表單：直接修改名稱與介紹，不需輸入內部鍵名。"""
    def __init__(self, guild_id):
        super().__init__(title="世界背景設定")
        data = world_get(guild_id)
        self.world_name = discord.ui.TextInput(label="世界觀名稱", default=str(data.get("world_name") or ""), max_length=100)
        self.background = discord.ui.TextInput(label="世界背景介紹", default=str(data.get("background") or "")[:2000], style=discord.TextStyle.paragraph, max_length=2000, required=False)
        self.add_item(self.world_name)
        self.add_item(self.background)

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await tell(interaction, "⛔ 僅限群主修改世界觀。")
        name = str(self.world_name).strip()
        if not name:
            return await tell(interaction, "世界觀名稱不可為空。")
        data = world_get(interaction.guild_id)
        data["world_name"] = name
        data["background"] = str(self.background).strip()
        for key in ("world_name", "background"):
            data.setdefault("overrides", {})[key] = True
        world_save(interaction.guild_id, data)
        await interaction.response.send_message("✅ 世界背景已儲存。\n" + world_category_summary(interaction.guild_id, "root"), ephemeral=True)


class WorldCurrencyModal(discord.ui.Modal):
    """用詞管理中的貨幣名稱設定。"""
    def __init__(self, guild_id):
        super().__init__(title="貨幣稱呼設定")
        terms = world_get(guild_id).get("terms", {})
        self.currency = discord.ui.TextInput(label="貨幣稱呼", default=str(terms.get("currency", "金幣")), max_length=30)
        self.add_item(self.currency)

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await tell(interaction, "⛔ 僅限群主修改世界觀。")
        value = str(self.currency).strip()
        if not value:
            return await tell(interaction, "貨幣稱呼不可為空。")
        data = world_get(interaction.guild_id)
        data.setdefault("terms", {})["currency"] = value
        data.setdefault("overrides", {})["terms.currency"] = True
        world_save(interaction.guild_id, data)
        await interaction.response.send_message("✅ 貨幣稱呼已儲存。其他模組仍須讀取世界觀設定才會套用新稱呼。", ephemeral=True)


class WorldEditModal(discord.ui.Modal):
    def __init__(self, category, field=None):
        super().__init__(title="世界觀設定"[:45])
        self.category = category
        self.field = field
        self.key_input = discord.ui.TextInput(label="設定鍵名", default=field or "", max_length=100)
        self.value_input = discord.ui.TextInput(label="設定內容（文字、數字、true/false 或 JSON）", style=discord.TextStyle.paragraph, max_length=2000)
        self.add_item(self.key_input)
        self.add_item(self.value_input)

    async def on_submit(self, i):
        if not owner(i):
            return await tell(i, "⛔ 僅限群主修改世界觀。")
        data = world_get(i.guild_id)
        key = str(self.key_input).strip()
        raw = str(self.value_input).strip()
        if not key:
            return await tell(i, "設定鍵名不可為空。")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        if self.category == "root":
            if key not in ("world_name", "background"):
                return await tell(i, "世界背景只能修改 world_name 或 background。")
            data[key] = value
        else:
            data.setdefault(self.category, {})[key] = value
            data.setdefault("overrides", {})[self.category + "." + key] = True
        world_save(i.guild_id, data)
        await i.response.edit_message(content="🌍 **世界觀設定**\n設定已保存。", view=WorldSettingsView(i))

WORLD_CATEGORIES = [
    ("🌐 世界背景", "root"), ("🔘 功能開關", "features"),
    ("🔗 頻道連結", "channels"), ("✏️ 用詞自訂", "terms"),
    ("📊 數值設定", "stats"), ("🎖️ 地位設定", "status_levels"),
    ("🏢 公司與組織", "company_rules"), ("⚙️ 細項規則", "detail_rules"),
    ("🗂️ 分類管理", "categories"), ("🔐 權限設定", "permissions"),
]
WORLD_LABELS = dict((value, label) for label, value in WORLD_CATEGORIES)


class WorldRuleModal(discord.ui.Modal):
    """結構化數值／地位設定；僅保存設定，不直接改動玩家資料。"""
    def __init__(self, category):
        super().__init__(title="數值設定" if category == "stats" else "地位設定")
        self.category = category
        self.rule_id = discord.ui.TextInput(label="數值代碼／地位等級", placeholder="例如 流量熱度 或 12", max_length=60)
        self.display_name = discord.ui.TextInput(label="顯示名稱", max_length=80)
        self.enabled = discord.ui.TextInput(label="啟用（是／否）", default="是", max_length=5)
        self.minimum = discord.ui.TextInput(label="最低值／熱度門檻", required=False, max_length=20)
        self.maximum = discord.ui.TextInput(label="最高值／商業價值門檻", required=False, max_length=20)
        for field in (self.rule_id, self.display_name, self.enabled, self.minimum, self.maximum):
            self.add_item(field)

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await tell(interaction, "⛔ 只有群主可以修改世界觀。")
        key = self.rule_id.value.strip()
        name = self.display_name.value.strip()
        flag = self.enabled.value.strip().lower()
        if flag not in ("是", "否", "true", "false"):
            return await tell(interaction, "請在啟用欄填寫「是」或「否」。")
        if not key or not name:
            return await tell(interaction, "代碼與名稱不可為空。")
        enabled = flag in ("是", "true")
        try:
            lower = int(self.minimum.value) if self.minimum.value.strip() else 0
            upper = int(self.maximum.value) if self.maximum.value.strip() else None
        except ValueError:
            return await tell(interaction, "數值與門檻必須填寫整數。")
        if lower < 0 or (upper is not None and upper < 0):
            return await tell(interaction, "數值不可為負數。")
        if self.category == "stats":
            if upper is not None and upper < lower:
                return await tell(interaction, "最高值不可小於最低值。")
            rule = {"name": name, "enabled": enabled, "min": lower, "max": upper}
        else:
            if not key.isdecimal() or not 1 <= int(key) <= 20:
                return await tell(interaction, "地位等級請填 1～20。")
            rule = {"name": name, "enabled": enabled, "heat_min": lower,
                    "commercial_min": upper if upper is not None else 0}
        data = world_get(interaction.guild_id)
        data.setdefault(self.category, {})[key] = rule
        data.setdefault("overrides", {})[self.category + "." + key] = True
        world_save(interaction.guild_id, data)
        await interaction.response.send_message(
            f"✅ 已保存「{name}」的世界觀設定。既有玩家數值不變；實際計算串接仍需啟用。",
            ephemeral=True,
        )


class WorldStatusWeightsModal(discord.ui.Modal):
    """以百分比設定地位五項權重；預設逐級晉升，不開放跳級。"""
    KEYS = ("influence", "heat", "commercial", "professional", "auxiliary")
    LABELS = ("影響指數 %", "流量熱度 %", "商業價值 %", "專業實力 %", "綜合輔助表現 %")

    def __init__(self, guild_id, group):
        super().__init__(title="地位評分權重｜" + ("LV1～18" if group == "lv1_18" else "LV19～20"))
        self.group = group
        defaults = (35, 20, 15, 15, 15) if group == "lv1_18" else (50, 15, 10, 15, 10)
        saved = world_get(guild_id).get("status_weights", {}).get(group, {})
        self.inputs = []
        for index, (key, label) in enumerate(zip(self.KEYS, self.LABELS)):
            raw = saved.get(key, defaults[index] / 100) if isinstance(saved, dict) else defaults[index] / 100
            field = discord.ui.TextInput(label=label, default=str(round(float(raw) * 100, 4)), max_length=12)
            self.add_item(field)
            self.inputs.append(field)

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await tell(interaction, "⛔ 只有群主可以修改地位權重。")
        import math
        try:
            numbers = [float(field.value.strip()) for field in self.inputs]
        except ValueError:
            return await tell(interaction, "請填入有效百分比數字。")
        if any(not math.isfinite(v) or v < 0 or v > 100 for v in numbers):
            return await tell(interaction, "每項權重必須在 0～100% 之間。")
        if abs(sum(numbers) - 100) > 0.001:
            return await tell(interaction, "五項權重合計必須等於 100%。")
        if numbers[0] <= 0:
            return await tell(interaction, "影響指數的評分權重必須大於 0%，且晉升門檻始終強制檢查。")
        data = world_get(interaction.guild_id)
        data.setdefault("status_weights", {})[self.group] = {
            key: value / 100 for key, value in zip(self.KEYS, numbers)
        }
        data.setdefault("overrides", {})["status_weights." + self.group] = True
        world_save(interaction.guild_id, data)
        await interaction.response.send_message(
            "✅ 地位權重已儲存。晉升仍預設逐級進行，每次最多升一級，不能跳級。",
            ephemeral=True,
        )


class WorldOrganizationModal(discord.ui.Modal):
    """安全編輯組織顯示用詞；不更動資料表、角色歸屬或公司編號。"""
    def __init__(self, guild_id):
        super().__init__(title="公司與組織稱呼設定")
        terms = world_get(guild_id).get("terms", {})
        self.company = discord.ui.TextInput(label="公司稱呼", default=str(terms.get("company", "公司")), max_length=30)
        self.group = discord.ui.TextInput(label="集團稱呼", default=str(terms.get("group", "集團")), max_length=30)
        self.brand = discord.ui.TextInput(label="品牌稱呼", default=str(terms.get("brand", "品牌")), max_length=30)
        self.faction = discord.ui.TextInput(label="陣營稱呼", default=str(terms.get("faction", "陣營")), max_length=30)
        self.position = discord.ui.TextInput(label="職位稱呼", default=str(terms.get("position", "職位")), max_length=30)
        for item in (self.company, self.group, self.brand, self.faction, self.position):
            self.add_item(item)

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await tell(interaction, "⛔ 僅限群主修改世界觀。")
        terms = {"company": str(self.company).strip(), "group": str(self.group).strip(),
                 "brand": str(self.brand).strip(), "faction": str(self.faction).strip(),
                 "position": str(self.position).strip()}
        if not all(terms.values()):
            return await tell(interaction, "⛔ 稱呼不可留空。")
        data = world_get(interaction.guild_id)
        data.setdefault("terms", {}).update(terms)
        overrides = data.setdefault("overrides", {})
        for key in terms:
            overrides["terms." + key] = True
        world_save(interaction.guild_id, data)
        await interaction.response.edit_message(
            content="✅ 組織稱呼已儲存。現有資料與公司編號不變。\n"
                    "提醒：其他功能須接入 world_terms.py 才會顯示新稱呼。",
            view=WorldCategoryView(interaction, "company_rules"),
        )


# 世界觀可視化管理：不要求群主輸入 JSON。
WORLD_FEATURE_NAMES = {key: label for key, label, *_ in __import__("modules.world_feature_editor", fromlist=["FEATURES"]).FEATURES}


def world_category_summary(gid, category):
    data = world_get(gid)
    title = WORLD_LABELS[category]
    if category == "root":
        return f"{title}\n名稱：{data.get('world_name') or '未設定'}\n背景：{(str(data.get('background') or '尚未填寫'))[:700]}"
    section = data.get(category, {})
    if category == "features":
        lines = [f"{'✅' if section.get(k, True) else '⛔'} {v}" for k, v in WORLD_FEATURE_NAMES.items()]
        return f"{title}\n" + "\n".join(lines) + "\n\n提醒：這些是世界觀配置值，其他模組仍須讀取設定才會實際停用功能。"
    if category == "terms":
        return title + "\n" + "\n".join(f"{k}：{v}" for k, v in section.items())
    if category == "channels":
        return title + "\n" + ("\n".join(f"{k}：<#{v}>" if str(v).isdigit() else f"{k}：{v}" for k,v in section.items()) if section else "尚未指定頻道")
    if category == "status_levels":
        lines = [f"LV{k}｜{v.get('name', '未命名')}｜{'啟用' if v.get('enabled', True) else '停用'}" for k,v in sorted(section.items(), key=lambda x:int(x[0]) if str(x[0]).isdigit() else 999) if isinstance(v, dict)]
        return title + "\n" + ("\n".join(lines[:20]) if lines else "尚未設定地位名稱")
    if category == "stats":
        lines = [f"{v.get('name', k)}：{'啟用' if v.get('enabled', True) else '停用'}｜{v.get('min', 0)}～{v.get('max', '無上限')}" for k,v in section.items() if isinstance(v,dict)]
        return title + "\n" + ("\n".join(lines[:18]) if lines else "尚未設定數值")
    if not isinstance(section, dict):
        return title + "\n目前設定格式不正確，請檢查資料。"
    lines = [f"• {key}：{str(value)[:80]}" for key, value in section.items()]
    return title + "\n" + ("\n".join(lines[:15]) if lines else "尚無自訂設定")


class WorldFeatureSelect(discord.ui.Select):
    def __init__(self, gid):
        current = world_get(gid).get("features", {})
        super().__init__(placeholder="選擇要切換的功能", min_values=1, max_values=1,
            options=[discord.SelectOption(label=name, value=key, description="目前：啟用" if current.get(key, True) else "目前：停用") for key,name in WORLD_FEATURE_NAMES.items()])

    async def callback(self, i):
        if not owner(i):
            return await tell(i, "⛔ 僅群主可修改功能開關。")
        key = self.values[0]
        data = world_get(i.guild_id)
        section = data.setdefault("features", {})
        section[key] = not bool(section.get(key, True))
        data.setdefault("overrides", {})["features." + key] = True
        world_save(i.guild_id, data)
        await i.response.edit_message(content=world_category_summary(i.guild_id,"features"),view=WorldFeatureView(i))


class WorldFeatureView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.uid,self.gid=i.user.id,i.guild_id
        self.add_item(WorldFeatureSelect(self.gid))

    async def interaction_check(self, i):
        if i.user.id != self.uid or i.guild_id != self.gid or not owner(i):
            await tell(i, "⛔ 無權操作此面板。")
            return False
        return True

    @discord.ui.button(label="◀️ 返回世界觀",style=discord.ButtonStyle.secondary,row=1)
    async def back(self, i, button):
        await i.response.edit_message(content=world_summary(i.guild_id),view=WorldSettingsView(i))


class WorldChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="選擇世界觀資料頻道", channel_types=[discord.ChannelType.text],min_values=1,max_values=1)

    async def callback(self,i):
        if not owner(i):
            return await tell(i,"⛔ 僅群主可修改頻道。")
        channel=self.values[0]
        data=world_get(i.guild_id)
        data.setdefault("channels",{})["world_content"]=str(channel.id)
        data.setdefault("overrides",{})["channels.world_content"]=True
        world_save(i.guild_id,data)
        await i.response.edit_message(content=world_category_summary(i.guild_id,"channels"),view=WorldChannelView(i))


class WorldChannelView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600)
        self.uid,self.gid=i.user.id,i.guild_id
        self.add_item(WorldChannelSelect())

    async def interaction_check(self,i):
        if i.user.id != self.uid or i.guild_id != self.gid or not owner(i):
            await tell(i,"⛔ 無權操作此面板。")
            return False
        return True

    @discord.ui.button(label="◀️ 返回世界觀",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,button):
        await i.response.edit_message(content=world_summary(i.guild_id),view=WorldSettingsView(i))


class WorldCategoryView(discord.ui.View):
    """先進入分類頁，再編輯，避免直接彈出難懂的 JSON 表單。"""
    def __init__(self, i, category):
        super().__init__(timeout=600)
        self.uid, self.gid = i.user.id, i.guild_id
        self.category = category
        if category != "status_levels":
            self.remove_item(self.low_weights)
            self.remove_item(self.high_weights)
        if category not in ("company_rules", "terms"):
            self.remove_item(self.organization_terms)
        if category != "terms":
            self.remove_item(self.currency_terms)

    async def interaction_check(self, i):
        if i.user.id != self.uid or i.guild_id != self.gid or not (owner(i) or __import__("modules.world_permission_editor", fromlist=["has_world_permission"]).has_world_permission(i, "stats_edit")):
            await tell(i, "⛔ 無權操作此設定面板。")
            return False
        return True

    @discord.ui.button(label="✏️ 新增／修改設定", style=discord.ButtonStyle.primary)
    async def edit(self, i, button):
        if self.category == "root":
            modal = WorldBackgroundModal(i.guild_id)
        elif self.category == "terms":
            modal = WorldOrganizationModal(i.guild_id)
        elif self.category in ("stats", "status_levels"):
            modal = WorldRuleModal(self.category)
        else:
            modal = WorldEditModal(self.category)
        await i.response.send_modal(modal)

    @discord.ui.button(label="⚖️ LV1～18 權重", style=discord.ButtonStyle.secondary, row=1)
    async def low_weights(self, i, button):
        if self.category != "status_levels":
            return await tell(i, "請先進入地位設定。")
        await i.response.send_modal(WorldStatusWeightsModal(i.guild_id, "lv1_18"))

    @discord.ui.button(label="⚖️ LV19～20 權重", style=discord.ButtonStyle.secondary, row=1)
    async def high_weights(self, i, button):
        if self.category != "status_levels":
            return await tell(i, "請先進入地位設定。")
        await i.response.send_modal(WorldStatusWeightsModal(i.guild_id, "lv19_20"))

    @discord.ui.button(label="🏢 組織稱呼", style=discord.ButtonStyle.secondary, row=1)
    async def organization_terms(self, i, button):
        await i.response.send_modal(WorldOrganizationModal(i.guild_id))

    @discord.ui.button(label="💰 貨幣稱呼", style=discord.ButtonStyle.secondary, row=2)
    async def currency_terms(self, i, button):
        await i.response.send_modal(WorldCurrencyModal(i.guild_id))

    @discord.ui.button(label="◀️ 返回世界觀設定", style=discord.ButtonStyle.secondary)
    async def back(self, i, button):
        await i.response.edit_message(
            content=world_summary(i.guild_id), view=WorldSettingsView(i)
        )


def world_summary(gid):
    data = world_get(gid)
    with get_connection() as db:
        pending = db.execute(
            "SELECT pending_version FROM world_template_updates WHERE guild_id=?",
            (str(gid),)
        ).fetchone()
    version = data.get("template_version", 1)
    suffix = f"\\n🔔 可更新至 v{pending[0]}（須群主確認）" if pending and pending[0] > version else ""
    return (
        f"🌍 **世界觀設定**\\n"
        f"名稱：{data.get('world_name', '未命名')}\\n"
        f"模板：{data.get('template', 'custom')} · v{version}"
        f"{suffix}\\n請從下拉選單選擇要管理的分類。"
    )


class WorldSettingsView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.uid, self.gid = i.user.id, i.guild_id
        options = [
            discord.SelectOption(label=label, value=category)
            for label, category in WORLD_CATEGORIES
        ]
        select = discord.ui.Select(
            placeholder="選擇世界觀設定類別", options=options, row=0
        )
        async def selected(interaction):
            category = select.values[0]
            if category == "stats" and not owner(interaction):
                from modules.world_permission_editor import has_world_permission
                if not has_world_permission(interaction, "stats_edit"):
                    await interaction.response.send_message("⛔ 你沒有數值管理權限。", ephemeral=True)
                    return
            elif category != "stats" and not owner(interaction):
                await interaction.response.send_message("⛔ 這項世界觀設定僅限群主。", ephemeral=True)
                return
            if category == "root":
                from modules.world_background_editor import WorldBackgroundView, background_summary
                view = WorldBackgroundView(interaction)
                content = background_summary(interaction.guild_id)
            elif category == "features":
                from modules.world_feature_editor import FeatureHomeView, feature_summary
                view = FeatureHomeView(interaction)
                content = feature_summary(interaction.guild_id)
            elif category == "channels":
                from modules.world_channel_editor import ChannelHomeView, channel_summary
                view = ChannelHomeView(interaction)
                content = channel_summary(interaction.guild_id)
            elif category == "terms":
                from modules.world_term_editor import TermHomeView, summary
                view = TermHomeView(interaction)
                content = summary(interaction.guild_id)
            elif category == "stats":
                from modules.world_stats_editor import StatHomeView, text
                view = StatHomeView(interaction)
                content = text(interaction.guild_id)
            elif category == "status_levels":
                from modules.world_status_editor import StatusHomeView, status_summary
                view = StatusHomeView(interaction)
                content = status_summary(interaction.guild_id)
            elif category == "company_rules":
                from modules.world_organization_editor import OrganizationHomeView, organization_summary
                view = OrganizationHomeView(interaction)
                content = organization_summary(interaction.guild_id)
            elif category == "detail_rules":
                from modules.world_detail_rules_editor import RuleHomeView, summary
                view = RuleHomeView(interaction)
                content = summary(interaction.guild_id)
            elif category == "categories":
                from modules.world_category_editor import CategoryHomeView, summary
                view = CategoryHomeView(interaction)
                content = summary(interaction.guild_id)
            elif category == "permissions":
                from modules.world_permission_editor import PermissionHomeView, summary
                view = PermissionHomeView(interaction)
                content = summary(interaction.guild_id)
            else:
                view = WorldCategoryView(interaction, category)
                content = world_category_summary(interaction.guild_id, category)
            await interaction.response.edit_message(content=content, view=view)
        select.callback = selected
        self.add_item(select)

    async def interaction_check(self, i):
        if i.user.id != self.uid or i.guild_id != self.gid or not owner(i):
            await tell(i, "⛔ 無權操作此設定面板。")
            return False
        return True

    @discord.ui.button(label="🎬 套用娛樂圈預設", style=discord.ButtonStyle.primary, row=1)
    async def preset(self, i, button):
        await i.response.edit_message(
            content="套用娛樂圈預設？既有自訂設定與玩家資料將保留。",
            view=WorldPresetConfirmView(i),
        )

    @discord.ui.button(label="🔔 模板更新", style=discord.ButtonStyle.secondary, row=1)
    async def updates(self, i, button):
        world_init()
        with get_connection() as db:
            pending = db.execute(
                "SELECT pending_version, notice FROM world_template_updates WHERE guild_id=?",
                (str(i.guild_id),)
            ).fetchone()
        current = world_get(i.guild_id).get("template_version", 1)
        if not pending or pending[0] <= current:
            await tell(i, "目前沒有待確認的世界觀模板更新。")
            return
        await i.response.edit_message(
            content=f"🔔 **娛樂圈模板更新預覽**\\n目前：v{current} → 新版：v{pending[0]}\\n"
                    f"{pending[1]}\\n\\n更新不會直接刪除玩家資料，已自訂項目保留。",
            view=WorldUpdateConfirmView(i, pending[0]),
        )

    @discord.ui.button(label="◀️ 返回內容建立", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, i, button):
        await i.response.edit_message(content="✍️ **內容建立中心**", view=ContentView(i))


class WorldUpdateConfirmView(discord.ui.View):
    def __init__(self, i, version):
        super().__init__(timeout=300)
        self.uid, self.gid, self.version = i.user.id, i.guild_id, version

    async def interaction_check(self, i):
        if i.user.id != self.uid or i.guild_id != self.gid or not owner(i):
            await tell(i, "⛔ 僅群主可確認更新。")
            return False
        return True

    @discord.ui.button(label="確認更新", style=discord.ButtonStyle.success)
    async def apply(self, i, button):
        with transaction() as db:
            pending = db.execute(
                "SELECT pending_version FROM world_template_updates WHERE guild_id=?",
                (str(i.guild_id),)
            ).fetchone()
            if not pending or pending[0] != self.version:
                await tell(i, "更新版本已變更，請重新開啟更新預覽。")
                return
        data = world_get(i.guild_id)
        # 不憑空套用未知規則；此版本只在模板更新內容已發布時確認版本。
        data["template_version"] = self.version
        world_save(i.guild_id, data)
        with transaction() as db:
            db.execute(
                "DELETE FROM world_template_updates WHERE guild_id=? AND pending_version=?",
                (str(i.guild_id), self.version)
            )
        await i.response.edit_message(
            content="✅ 已確認模板版本。既有自訂設定保留。",
            view=WorldSettingsView(i),
        )

    @discord.ui.button(label="暫不更新／返回", style=discord.ButtonStyle.secondary)
    async def back(self, i, button):
        await i.response.edit_message(
            content=world_summary(i.guild_id), view=WorldSettingsView(i)
        )


class WorldPresetConfirmView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=300)
        self.uid, self.gid = i.user.id, i.guild_id

    async def interaction_check(self, i):
        if i.user.id != self.uid or i.guild_id != self.gid or not owner(i):
            await tell(i, "⛔ 無權操作。")
            return False
        return True

    @discord.ui.button(label="確認套用", style=discord.ButtonStyle.success)
    async def apply(self, i, button):
        old = world_get(i.guild_id)
        new = json.loads(json.dumps(WORLD_DEFAULT, ensure_ascii=False))
        for category in ("features", "channels", "terms", "stats", "status_levels",
                         "company_rules", "detail_rules", "categories", "permissions"):
            new[category].update(old.get(category, {}))
        new["overrides"] = old.get("overrides", {})
        for field in ("world_name", "background"):
            if old.get(field) and old.get(field) != WORLD_DEFAULT.get(field):
                new[field] = old[field]
        world_save(i.guild_id, new)
        await i.response.edit_message(
            content="✅ 已套用娛樂圈設定模板（保留既有自訂）。",
            view=WorldSettingsView(i),
        )

    @discord.ui.button(label="取消並返回", style=discord.ButtonStyle.secondary)
    async def back(self, i, button):
        await i.response.edit_message(
            content=world_summary(i.guild_id), view=WorldSettingsView(i)
        )


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
            ] + [
                discord.SelectOption(label="🚶 路人角色", value="extra_characters", description="批次建立與管理路人角色"),
                discord.SelectOption(label="📋 通用官方內容管理", value="content_records", description="查看清單、編輯草稿、發布與下架"),
            ],
            row=0,
        )

    async def callback(self, i):
        from modules.task_permissions import authorized as task_authorized
        if not owner(i) and not task_authorized(i):
            return await tell(i, "⛔ 沒有內容建立權限。")

        selected = self.values[0]
        if not owner(i) and selected != "mission":
            return await tell(i, "⛔ 任務授權只適用於任務管理。")

        if selected == "extra_characters":
            from modules.extra_character_manager import ExtraCharacterView
            await i.response.edit_message(
                content="🚶 **路人角色管理中心**",
                view=ExtraCharacterView(i),
            )
            return

        if selected == "content_records":
            await i.response.edit_message(
                content="📋 **通用官方內容管理**\n請選擇清單、編輯草稿或發布／下架。\n注意：此處僅管理通用官方內容記錄，專屬系統請從上層選單進入。",
                view=ContentRecordsView(i),
            )
            return

        try:
            handled = await open_specialized_manager(
                i,
                selected,
            )

            if handled:
                return

            if selected == "world":
                await i.response.edit_message(content="🌍 **世界觀設定**\n每個伺服器獨立保存；模板後續更新須群主確認。", view=WorldSettingsView(i))
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

class ContentRecordsSelect(discord.ui.Select):
    """集中管理通用內容紀錄，不另設三個重複快捷按鈕。"""

    def __init__(self):
        super().__init__(
            placeholder="選擇通用官方內容操作",
            row=0,
            options=[
                discord.SelectOption(label="📋 查看內容清單", value="list"),
                discord.SelectOption(label="✏️ 編輯草稿", value="edit"),
                discord.SelectOption(label="📢 發布／下架", value="publish"),
                discord.SelectOption(label="↩️ 返回內容類型", value="back"),
            ],
        )

    async def callback(self, i):
        if not owner(i):
            return await tell(i, "⛔ 僅限群主使用。")
        action = self.values[0]
        if action == "back":
            return await i.response.edit_message(
                content="✍️ **官方內容建立中心**\n請從下拉選單選擇要建立／管理的類型。",
                view=ContentView(i),
            )
        if action == "list":
            try:
                message = list_items(i.guild_id)
            except sqlite3.Error:
                LOG.exception("讀取官方內容清單失敗。")
                message = "⚠️ 無法讀取官方內容清單。"
            return await tell(i, message)
        if action == "edit":
            return await i.response.send_modal(EditContent())
        if action == "publish":
            return await i.response.send_modal(PublishContent())


class ContentRecordsView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.user_id = i.user.id
        self.guild_id = i.guild_id
        self.add_item(ContentRecordsSelect())

    async def interaction_check(self, i):
        if i.user.id != self.user_id or i.guild_id != self.guild_id or not owner(i):
            await tell(i, "⛔ 這不是你的管理面板，或你的權限不足。")
            return False
        return True



    @discord.ui.button(label="⬅️ 返回內容建立", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(
            content="✍️ **內容建立中心**", view=ContentView(interaction))

class ContentView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.user_id = i.user.id
        self.guild_id = i.guild_id
        if owner(i):
            self.add_item(ContentTypeSelect())
        else:
            selector = ContentTypeSelect()
            selector.options = [x for x in selector.options if x.value == "mission"]
            self.add_item(selector)

    async def interaction_check(self, i):
        from modules.task_permissions import authorized as task_authorized
        if i.user.id != self.user_id or i.guild_id != self.guild_id or not (owner(i) or task_authorized(i)):
            await tell(i, "⛔ 這不是你的管理面板，或你的權限不足。")
            return False
        return True

    @discord.ui.button(label="↩️ 返回官方", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, i, button):
        from modules.official_center import OfficialHome
        await i.response.edit_message(
            content="🏛️ **官方管理中心**",
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
        from modules.task_permissions import authorized as task_authorized
        if not owner(i) and not task_authorized(i):
            return await tell(i, "⛔ 沒有內容建立權限。")

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
