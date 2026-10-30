"""公司公關與傳聞紀錄，72 小時結算。此版不直接變更角色屬性或金流。"""

from __future__ import annotations

import time
import logging
import os

import discord
from discord.ext import commands, tasks

from core.database import get_connection, transaction
from modules.company_business import _entity, has_permission, initialize as initialize_business

LOG = logging.getLogger(__name__)

SETTLE_SECONDS = 72 * 3600
PLAYER_SOURCES = {"玩家", "造謠"}
ALL_SOURCES = {"系統", "條件", "玩家", "造謠"}
RUMOR_CATEGORIES = ("戀情", "商業", "工作", "人際", "私生活", "其他")


def initialize():
    initialize_business()
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_optout (
            guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,character_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_company_rumors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,business_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,source_type TEXT NOT NULL,
            creator_id TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,settle_at INTEGER NOT NULL,
            settled_at INTEGER,trace_progress INTEGER NOT NULL DEFAULT 0,
            revealed_by TEXT,
            UNIQUE(guild_id,id)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_company_rumors_due
            ON rp_company_rumors(status,settle_at)""")
        # 新表與既有案件並存；不刪除或改寫任何歷史傳聞。
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_category_consent (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            category TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,character_id,category)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_case_meta (
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            category TEXT NOT NULL DEFAULT '其他',
            rule_version INTEGER NOT NULL DEFAULT 1,
            visibility TEXT NOT NULL DEFAULT 'public',
            PRIMARY KEY(guild_id,rumor_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL, character_id INTEGER NOT NULL,
            actor_id TEXT NOT NULL, action_type TEXT NOT NULL,
            resource_type TEXT, resource_amount INTEGER NOT NULL DEFAULT 0,
            result_json TEXT, created_at INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL, holder_character_id INTEGER NOT NULL,
            evidence_type TEXT NOT NULL, credibility TEXT NOT NULL DEFAULT '低',
            authentic INTEGER NOT NULL DEFAULT 1, disclosed INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_investigations (
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id,rumor_id,character_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_police_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL, reporter_character_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'investigating',
            created_at INTEGER NOT NULL, finished_at INTEGER
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_case_links (
            guild_id TEXT NOT NULL, parent_id INTEGER NOT NULL,
            child_id INTEGER NOT NULL, reason TEXT,
            PRIMARY KEY(guild_id,parent_id,child_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_settlement_ledger (
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            settlement_type TEXT NOT NULL, recipient_id TEXT NOT NULL,
            reward_json TEXT NOT NULL, created_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,rumor_id,settlement_type,recipient_id)
        )""")


def rumor_allowed(db, guild_id, character_id, category):
    """所有來源都應呼叫的授權檢查；未設定時沿用預設允許。"""
    if category not in RUMOR_CATEGORIES:
        raise ValueError("未知傳聞分類。")
    gid, cid = str(guild_id), int(character_id)
    general = db.execute("SELECT enabled FROM rp_rumor_optout WHERE guild_id=? AND character_id=?", (gid, cid)).fetchone()
    if general and not general["enabled"]:
        return False
    specific = db.execute("""SELECT enabled FROM rp_rumor_category_consent
        WHERE guild_id=? AND character_id=? AND category=?""", (gid, cid, category)).fetchone()
    return not specific or bool(specific["enabled"])


def set_category_consent(guild_id, actor_id, character_id, category, enabled):
    initialize()
    gid, cid = str(guild_id), int(character_id)
    if category not in RUMOR_CATEGORIES:
        raise ValueError("未知傳聞分類。")
    with transaction() as db:
        owner = db.execute("SELECT owner_id FROM characters WHERE guild_id=? AND id=?", (gid, cid)).fetchone()
        if not owner or str(owner["owner_id"]) != str(actor_id):
            raise PermissionError("只有角色持有人可以修改傳聞分類授權。")
        db.execute("""INSERT INTO rp_rumor_category_consent
            (guild_id,character_id,category,enabled,updated_at) VALUES (?,?,?,?,?)
            ON CONFLICT(guild_id,character_id,category) DO UPDATE SET
            enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (gid, cid, category, int(bool(enabled)), int(time.time())))


def set_consent(guild_id, actor_id, character_id, enabled):
    initialize()
    gid, uid, cid = str(guild_id), str(actor_id), int(character_id)
    with transaction() as db:
        row = db.execute("SELECT owner_id FROM characters WHERE guild_id=? AND id=?", (gid, cid)).fetchone()
        if not row or str(row["owner_id"]) != uid:
            raise PermissionError("只有角色持有人可以設定自己的傳聞授權。")
        db.execute("""INSERT INTO rp_rumor_optout(guild_id,character_id,enabled,updated_at)
            VALUES (?,?,?,?) ON CONFLICT(guild_id,character_id)
            DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (gid, cid, int(bool(enabled)), int(time.time())))
    return f"已{'開啟' if enabled else '關閉'}角色 #{cid} 的傳聞參與授權。"


def create_rumor(guild_id, actor_id, business_id, character_id, source_type, title, description, category="其他"):
    initialize()
    gid, uid, bid, cid = str(guild_id), str(actor_id), str(business_id).strip(), int(character_id)
    source_type, title, description = str(source_type).strip(), str(title).strip(), str(description).strip()
    if source_type not in PLAYER_SOURCES:
        raise ValueError("玩家只能選擇「玩家」或「造謠」來源；系統／條件事件由程式觸發。")
    if category not in RUMOR_CATEGORIES:
        raise ValueError("未知傳聞分類。")
    if not (1 <= len(title) <= 80 and 1 <= len(description) <= 500):
        raise ValueError("標題需 1～80 字，內容需 1～500 字。")
    with transaction() as db:
        _entity(db, gid, bid)
        if not has_permission(db, gid, bid, uid, "public_relations"):
            raise PermissionError("你沒有該公司的公關管理權限。")
        row = db.execute("SELECT id,status FROM characters WHERE guild_id=? AND id=?", (gid, cid)).fetchone()
        if not row or row["status"] != "active":
            raise ValueError("只能對已啟用角色建立傳聞。")
        if not rumor_allowed(db, gid, cid, category):
            raise PermissionError("這個角色已拒絕該類型的傳聞。")
        now = int(time.time())
        cur = db.execute("""INSERT INTO rp_company_rumors
            (guild_id,business_id,character_id,source_type,creator_id,title,description,created_at,settle_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (gid, bid, cid, source_type, uid, title, description, now, now + SETTLE_SECONDS))
        db.execute("""INSERT INTO rp_rumor_case_meta
            (guild_id,rumor_id,category,rule_version) VALUES (?,?,?,1)""",
            (gid, cur.lastrowid, category))
    return f"✅ 傳聞案件 #{cur.lastrowid} 已建立，將於 72 小時後結案。"


def list_rumors(guild_id, actor_id):
    initialize()
    gid, uid = str(guild_id), str(actor_id)
    with get_connection() as db:
        rows = db.execute("""SELECT * FROM rp_company_rumors
            WHERE guild_id=? ORDER BY id DESC LIMIT 150""", (gid,)).fetchall()
        result = []
        for row in rows:
            if has_permission(db, gid, row["business_id"], uid, "public_relations"):
                result.append(dict(row))
                if len(result) >= 15:
                    break
    return result


def settle_due(now=None):
    """使用唯一正式結案入口；不再執行舊的評級直結案流程。

    此處不生成獎懲方案，也不替歷史案件補發。
    必須先由受信任的案件流程保存規則快照與正式獎懲方案。
    未準備完成的案件保持 active，供管理員檢查與後續重試。

    注意：只有在 RUMOR_FINALIZATION_ENABLED=1 時定時任務才會呼叫本函式。
    手動呼叫此函式也必須明確指定 enabled=True。
    """
    return run_locked_due(now=now, enabled=False)


def run_locked_due(now=None, *, enabled=False, limit=100):
    """受信任的排程入口；回傳已成功結案的案件數。"""
    if not enabled:
        LOG.info('傳聞正式結算未啟用；略過到期案件（不修改資料）')
        return 0

    initialize()
    timestamp = int(time.time()) if now is None else int(now)
    limit = max(1, min(int(limit), 500))
    from core.rumor_finalization import finalize_locked_case

    with get_connection() as db:
        due = db.execute(
            """SELECT guild_id,id FROM rp_company_rumors
               WHERE status='active' AND settle_at<=?
               ORDER BY settle_at ASC,id ASC LIMIT ?""",
            (timestamp, limit),
        ).fetchall()
        cases = [(str(row['guild_id']), int(row['id'])) for row in due]

    completed = 0
    for gid, rid in cases:
        try:
            result = finalize_locked_case(gid, rid, trusted=True, now=timestamp)
            if result.state == 'settled':
                completed += 1
        except (ValueError, LookupError, PermissionError) as exc:
            LOG.warning(
                '傳聞 #%s（伺服器 %s）缺少正式結算條件，保持待處理：%s',
                rid, gid, exc,
            )
        except Exception:
            LOG.exception(
                '傳聞 #%s（伺服器 %s）結算失敗，等待後續重試', rid, gid
            )
    return completed


class RumorModal(discord.ui.Modal, title="建立公司傳聞"):
    business = discord.ui.TextInput(label="公司編號")
    target = discord.ui.TextInput(label="對象角色編號")
    source = discord.ui.TextInput(label="來源：玩家／造謠", placeholder="玩家")
    headline = discord.ui.TextInput(label="傳聞標題（80字內）")
    detail = discord.ui.TextInput(label="傳聞內容", style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            msg = create_rumor(interaction.guild_id, interaction.user.id, self.business.value,
                               self.target.value, self.source.value, self.headline.value, self.detail.value)
        except (ValueError, PermissionError, TypeError) as exc:
            msg = f"❌ {exc}"
        await interaction.response.send_message(msg, ephemeral=True)


class ConsentModal(discord.ui.Modal, title="角色傳聞授權"):
    character = discord.ui.TextInput(label="角色編號")
    enabled = discord.ui.TextInput(label="輸入開啟或關閉")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if self.enabled.value not in {"開啟", "關閉"}:
                raise ValueError("請輸入開啟或關閉。")
            msg = set_consent(interaction.guild_id, interaction.user.id,
                              self.character.value, self.enabled.value == "開啟")
        except (ValueError, PermissionError, TypeError) as exc:
            msg = f"❌ {exc}"
        await interaction.response.send_message(msg, ephemeral=True)


class RumorView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="📰 建立傳聞", style=discord.ButtonStyle.primary)
    async def create(self, interaction, button):
        await interaction.response.send_modal(RumorModal())

    @discord.ui.button(label="📚 案件清單", style=discord.ButtonStyle.secondary)
    async def records(self, interaction, button):
        rows = list_rumors(interaction.guild_id, interaction.user.id)
        text = "\n".join(f"#{r['id']}｜角色#{r['character_id']}｜{r['title']}｜{r['status']}｜<t:{r['settle_at']}:F>" for r in rows)
        await interaction.response.send_message(text[:1900] if text else "目前沒有可查看的案件。", ephemeral=True)

    @discord.ui.button(label="🔐 授權開關", style=discord.ButtonStyle.secondary)
    async def consent(self, interaction, button):
        await interaction.response.send_modal(ConsentModal())


class RumorButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🗞️ 公司公關", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction):
        await interaction.response.send_message("公司公關與傳聞管理", view=RumorView(), ephemeral=True)


class RumorScheduler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_due.start()

    def cog_unload(self):
        self.check_due.cancel()

    @tasks.loop(hours=1)
    async def check_due(self):
        if os.getenv("RUMOR_FINALIZATION_ENABLED", "0") != "1":
            return
        run_locked_due(enabled=True)

    @check_due.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    initialize()
    from modules.company_portal import CompanyPanel
    if not getattr(CompanyPanel, "_rumor_hooked", False):
        previous = CompanyPanel.__init__

        def with_rumor(self, uid, gid, is_owner):
            previous(self, uid, gid, is_owner)
            self.add_item(RumorButton())

        CompanyPanel.__init__ = with_rumor
        CompanyPanel._rumor_hooked = True
    await bot.add_cog(RumorScheduler(bot))
