"""Discord RP 成就／榮譽／稱號管理：附加到既有 /官方 與 /查看。

僅伺服器擁有者可以增修、停用、頒發與撤銷。
獎勵可混用：金錢、屬性、商城物品、稱號。
不改動既有 core.database 結構。
"""
from __future__ import annotations

import json
import logging
import sqlite3

import discord
from discord.ext import commands, tasks

from core.database import get_connection, transaction, get_or_create_account

LOG = logging.getLogger(__name__)
KINDS = {"achievement": "成就", "honor": "榮譽", "title": "稱號"}
STATS = {"流量熱度", "商業價值", "路人緣", "公關", "影響指數", "容貌", "才智", "歌唱", "舞蹈", "演繹", "主持", "綜藝", "個人才藝"}


def owner(interaction):
    return (interaction.guild is not None and
            interaction.guild.owner_id == interaction.user.id)


def require_owner(interaction):
    if not owner(interaction):
        raise ValueError("只有伺服器群主可以操作成就與榮譽管理。")


def init_tables():
    with transaction() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_award_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('achievement','honor','title')),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            rewards_json TEXT NOT NULL DEFAULT '{}',
            condition_json TEXT NOT NULL DEFAULT '{}',
            hidden INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_award_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            definition_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            granted_by TEXT NOT NULL,
            rewards_snapshot TEXT NOT NULL,
            granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            revoked INTEGER NOT NULL DEFAULT 0,
            revoked_by TEXT,
            revoked_at TEXT,
            UNIQUE(guild_id,definition_id,character_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_award_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            definition_id INTEGER,
            character_id INTEGER,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_award_showcase (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            definition_id INTEGER NOT NULL,
            PRIMARY KEY(guild_id, character_id)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_award_auto ON rp_award_definitions(guild_id,active)")


def audit(conn, guild_id, actor_id, action, definition_id=None, character_id=None, detail=""):
    conn.execute("""INSERT INTO rp_award_audit
        (guild_id,actor_id,action,definition_id,character_id,detail)
        VALUES (?,?,?,?,?,?)""",
        (str(guild_id), str(actor_id), action, definition_id, character_id, detail[:1000]))


def positive_int(value, label, *, zero=True, maximum=10**12):
    try:
        number = int(str(value).strip())
    except (ValueError, TypeError):
        raise ValueError(f"{label}必須是整數。")
    if number < (0 if zero else 1) or number > maximum:
        raise ValueError(f"{label}超出允許範圍。")
    return number


def parse_rewards(raw):
    """例如：金錢=500;屬性=歌唱:2,舞蹈:1;物品=12:1;稱號=人氣新人"""
    data = {"money": 0, "attributes": {}, "items": {}, "title": ""}
    for segment in (x.strip() for x in raw.replace("；", ";").split(";") if x.strip()):
        if "=" not in segment:
            raise ValueError("獎勵格式錯誤，請使用「種類=內容」，以分號分隔。")
        key, value = (x.strip() for x in segment.split("=", 1))
        if key in ("金錢", "money"):
            data["money"] = positive_int(value, "金錢")
        elif key in ("屬性", "attributes"):
            for token in value.replace("，", ",").split(","):
                name, sep, amount = token.strip().rpartition(":")
                if not sep or name not in STATS:
                    raise ValueError("屬性請填「歌唱:2,舞蹈:1」等十三項正式屬性。")
                data["attributes"][name] = data["attributes"].get(name, 0) + positive_int(amount, "屬性點數", zero=False, maximum=100000)
        elif key in ("物品", "items"):
            for token in value.replace("，", ",").split(","):
                pid, sep, quantity = token.strip().partition(":")
                if not sep:
                    raise ValueError("物品請填「商品編號:數量」，如「12:1」。")
                item_id = positive_int(pid, "商品編號", zero=False)
                data["items"][str(item_id)] = data["items"].get(str(item_id), 0) + positive_int(quantity, "物品數量", zero=False, maximum=10000)
        elif key in ("稱號", "title"):
            if not value or len(value) > 80:
                raise ValueError("稱號必須是 1～80 個字。")
            data["title"] = value
        else:
            raise ValueError(f"未知的獎勵種類：{key}")
    return data


def reward_text(data):
    parts = []
    if data.get("money"):
        parts.append(f"💰 {data['money']:,} 金幣")
    if data.get("attributes"):
        parts.append("📈 " + "、".join(f"{k}+{v}" for k, v in data["attributes"].items()))
    if data.get("items"):
        parts.append("🎁 " + "、".join(f"商品#{k}×{v}" for k, v in data["items"].items()))
    if data.get("title"):
        parts.append(f"🏷️ {data['title']}")
    return "｜".join(parts) or "無額外獎勵"


def parse_condition(raw):
    """留空手動；money:1000、fans:500、awareness:100、attribute:歌唱:80。"""
    raw = raw.strip()
    if not raw:
        return {}
    key, sep, remainder = raw.partition(":")
    if not sep:
        raise ValueError("自動條件格式不正確。")
    key = key.strip().lower()
    if key in ("money", "fans", "awareness"):
        return {"type": key, "minimum": positive_int(remainder, "門檻", zero=False)}
    if key == "attribute":
        name, sep2, threshold = remainder.rpartition(":")
        if not sep2 or name.strip() not in STATS:
            raise ValueError("屬性條件範例：attribute:歌唱:80")
        return {"type": "attribute", "name": name.strip(), "minimum": positive_int(threshold, "門檻", zero=False)}
    raise ValueError("自動條件僅支援 money、fans、awareness、attribute。")


def condition_text(data):
    if not data:
        return "群主手動頒發"
    kind = data["type"]
    labels = {"money": "貨幣總額", "fans": "死忠粉絲", "awareness": "大眾認知度", "attribute": data.get("name", "屬性")}
    return f"{labels.get(kind,kind)} ≥ {data['minimum']:,}"


def definition(guild_id, award_id, conn=None):
    if conn is None:
        with get_connection() as db:
            row = db.execute("SELECT * FROM rp_award_definitions WHERE guild_id=? AND id=?", (str(guild_id), int(award_id))).fetchone()
    else:
        row = conn.execute("SELECT * FROM rp_award_definitions WHERE guild_id=? AND id=?", (str(guild_id), int(award_id))).fetchone()
    if row is None:
        raise ValueError("找不到此成就／榮譽編號。")
    return dict(row)


def save_definition(guild_id, actor_id, kind, name, description, rewards_raw, condition_raw, old_id=None):
    kind = kind.strip().lower()
    if kind not in KINDS:
        raise ValueError("分類只能填 achievement、honor 或 title。")
    name, description = name.strip(), description.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("名稱需要 1～100 字。")
    rewards = parse_rewards(rewards_raw)
    condition = parse_condition(condition_raw)
    if kind == "title" and not rewards["title"]:
        rewards["title"] = name
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)", (str(guild_id),))
        if old_id is None:
            cursor = conn.execute("""INSERT INTO rp_award_definitions
                (guild_id,kind,name,description,rewards_json,condition_json,created_by)
                VALUES (?,?,?,?,?,?,?)""",
                (str(guild_id), kind, name, description, json.dumps(rewards, ensure_ascii=False), json.dumps(condition, ensure_ascii=False), str(actor_id)))
            award_id = cursor.lastrowid
            action = "create"
        else:
            old = definition(guild_id, old_id, conn)
            award_id = old["id"]
            conn.execute("""UPDATE rp_award_definitions SET kind=?,name=?,description=?,
                rewards_json=?,condition_json=?,updated_at=CURRENT_TIMESTAMP
                WHERE guild_id=? AND id=?""",
                (kind, name, description, json.dumps(rewards, ensure_ascii=False), json.dumps(condition, ensure_ascii=False), str(guild_id), award_id))
            action = "edit"
        audit(conn, guild_id, actor_id, action, award_id, detail=name)
    return award_id


def set_flag(guild_id, actor_id, award_id, flag):
    with transaction() as conn:
        row = definition(guild_id, award_id, conn)
        column = "hidden" if flag == "hidden" else "active"
        new_value = 0 if row[column] else 1
        conn.execute(f"UPDATE rp_award_definitions SET {column}=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?", (new_value, str(guild_id), int(award_id)))
        audit(conn, guild_id, actor_id, f"{column}:{new_value}", award_id)
    return f"#{award_id} 的{('隱藏' if flag == 'hidden' else '啟用')}狀態已設為 {bool(new_value)}。"


def check_condition(conn, guild_id, character_id, condition):
    if not condition:
        return False
    kind = condition["type"]
    if kind == "money":
        row = conn.execute("""SELECT COALESCE(SUM(e.amount),0) AS n FROM accounts a
            LEFT JOIN ledger_entries e ON e.guild_id=a.guild_id AND e.account_id=a.id
            WHERE a.guild_id=? AND a.owner_type='character' AND a.owner_id=?
            AND a.account_type IN ('wallet','bank')""", (str(guild_id), str(character_id))).fetchone()
        value = int(row["n"])
    elif kind == "attribute":
        row = conn.execute("SELECT value FROM attributes WHERE guild_id=? AND character_id=? AND name=?", (str(guild_id), int(character_id), condition["name"])).fetchone()
        value = int(row["value"]) if row else 0
    elif kind in ("fans", "awareness"):
        column = "loyal_fans" if kind == "fans" else "public_awareness"
        try:
            row = conn.execute(f"SELECT {column} AS n FROM character_system_tracking WHERE guild_id=? AND character_id=?", (str(guild_id), int(character_id))).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return False
            raise
        value = int(row["n"]) if row else 0
    else:
        return False
    return value >= condition["minimum"]


def grant(guild_id, actor_id, award_id, character_id, *, automatic=False):
    guild_id = str(guild_id)
    with transaction() as conn:
        item = definition(guild_id, award_id, conn)
        if not item["active"]:
            raise ValueError("此成就目前已停用。")
        character = conn.execute("SELECT id,status FROM characters WHERE guild_id=? AND id=?", (guild_id, int(character_id))).fetchone()
        if not character or character["status"] not in ("active", "retired"):
            raise ValueError("角色不存在，或尚未達可頒發狀態。")
        previous = conn.execute("SELECT id FROM rp_award_grants WHERE guild_id=? AND definition_id=? AND character_id=?", (guild_id, int(award_id), int(character_id))).fetchone()
        if previous:
            return False
        if automatic and not check_condition(conn, guild_id, character_id, json.loads(item["condition_json"])):
            return False
        rewards = json.loads(item["rewards_json"])
        # 先驗證商城商品與背包容量，再正式落帳；全部在同一交易內。
        product_rows = []
        if rewards.get("items"):
            from modules.shop import adjust_inventory
            for product_id, quantity in rewards["items"].items():
                row = conn.execute("SELECT id,stock FROM shop_products WHERE guild_id=? AND id=?", (guild_id, int(product_id))).fetchone()
                if row is None or (row["stock"] is not None and row["stock"] < int(quantity)):
                    raise ValueError(f"商品 #{product_id} 不存在或庫存不足。")
                product_rows.append((int(product_id), int(quantity), row["stock"]))
        amount = int(rewards.get("money") or 0)
        if amount:
            from_account = get_or_create_account(conn, guild_id, "system", "achievement_rewards", "wallet")
            to_account = get_or_create_account(conn, guild_id, "character", str(character_id), "wallet")
            cursor = conn.execute("INSERT INTO ledger_transactions(guild_id,unique_key,description) VALUES (?,?,?)", (guild_id, f"rp_award:{award_id}:{character_id}", f"成就榮譽獎勵：{item['name']}"))
            transaction_id = cursor.lastrowid
            conn.execute("INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount) VALUES (?,?,?,?)", (guild_id, transaction_id, from_account, -amount))
            conn.execute("INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount) VALUES (?,?,?,?)", (guild_id, transaction_id, to_account, amount))
        for stat, points in rewards.get("attributes", {}).items():
            if stat not in STATS or int(points) <= 0:
                raise ValueError("獎勵屬性設定不合法。")
            conn.execute("""INSERT INTO attributes(guild_id,character_id,name,value,version)
                VALUES (?,?,?,?,0) ON CONFLICT(guild_id,character_id,name)
                DO UPDATE SET value=value+excluded.value,version=version+1""", (guild_id, int(character_id), stat, int(points)))
        for product_id, quantity, stock in product_rows:
            adjust_inventory(conn, guild_id, int(character_id), product_id, quantity)
            if stock is not None:
                cursor = conn.execute("UPDATE shop_products SET stock=stock-? WHERE guild_id=? AND id=? AND stock>=?", (quantity, guild_id, product_id, quantity))
                if cursor.rowcount != 1:
                    raise ValueError("發獎過程中商品庫存不足。")
        conn.execute("""INSERT INTO rp_award_grants
            (guild_id,definition_id,character_id,source,granted_by,rewards_snapshot)
            VALUES (?,?,?,?,?,?)""", (guild_id, int(award_id), int(character_id), "automatic" if automatic else "manual", str(actor_id), item["rewards_json"]))
        audit(conn, guild_id, actor_id, "grant_auto" if automatic else "grant_manual", int(award_id), int(character_id), item["name"])
    return True


def revoke(guild_id, actor_id, award_id, character_id):
    with transaction() as conn:
        cursor = conn.execute("""UPDATE rp_award_grants SET revoked=1,revoked_by=?,revoked_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND definition_id=? AND character_id=? AND revoked=0""", (str(actor_id), str(guild_id), int(award_id), int(character_id)))
        if cursor.rowcount != 1:
            raise ValueError("找不到可撤銷的頒發紀錄。")
        conn.execute("DELETE FROM rp_award_showcase WHERE guild_id=? AND character_id=? AND definition_id=?", (str(guild_id), int(character_id), int(award_id)))
        audit(conn, guild_id, actor_id, "revoke_no_clawback", int(award_id), int(character_id))


def listing(guild_id, page=0):
    page = max(0, page)
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM rp_award_definitions WHERE guild_id=?", (str(guild_id),)).fetchone()["n"]
        last = max(0, (total - 1) // 12)
        page = min(page, last)
        rows = conn.execute("SELECT * FROM rp_award_definitions WHERE guild_id=? ORDER BY id DESC LIMIT 12 OFFSET ?", (str(guild_id), page * 12)).fetchall()
    lines = [f"🏆 **成就與榮譽設定**｜第 {page+1}/{last+1} 頁｜共 {total} 項"]
    for r in rows:
        reward = json.loads(r["rewards_json"])
        cond = json.loads(r["condition_json"])
        lines.append(f"\n**#{r['id']}｜{KINDS[r['kind']]}：{r['name']}**")
        lines.append(f"{'✅ 啟用' if r['active'] else '⏸️ 停用'}｜{'🙈 隱藏' if r['hidden'] else '👁️ 公開'}｜{condition_text(cond)}")
        lines.append(f"獎勵：{reward_text(reward)}")
    if not rows:
        lines.append("目前尚無設定。")
    return "\n".join(lines)[:1900], page, last


def character_awards(guild_id, character_id):
    with get_connection() as conn:
        rows = conn.execute("""SELECT d.id,d.kind,d.name,d.description,d.rewards_json,g.granted_at
            FROM rp_award_grants g JOIN rp_award_definitions d
            ON d.guild_id=g.guild_id AND d.id=g.definition_id
            WHERE g.guild_id=? AND g.character_id=? AND g.revoked=0
            ORDER BY g.granted_at DESC,g.id DESC""", (str(guild_id), int(character_id))).fetchall()
        showcased = conn.execute("SELECT definition_id FROM rp_award_showcase WHERE guild_id=? AND character_id=?", (str(guild_id), int(character_id))).fetchone()
    return [dict(row) for row in rows], (showcased["definition_id"] if showcased else None)


def award_embed(guild_id, character_id):
    with get_connection() as conn:
        char = conn.execute("SELECT name FROM characters WHERE guild_id=? AND id=? AND status IN ('active','retired')", (str(guild_id), int(character_id))).fetchone()
    if char is None:
        raise ValueError("角色目前無法公開查看。")
    rows, showcase = character_awards(guild_id, character_id)
    embed = discord.Embed(title=f"🏆 {char['name']} 的成就與榮譽", color=discord.Color.gold())
    if not rows:
        embed.description = "尚未獲得任何成就、榮譽或稱號。"
        return embed
    lines = []
    for row in rows[:24]:
        data = json.loads(row["rewards_json"])
        title = data.get("title") or (row["name"] if row["kind"] == "title" else "")
        marker = "🌟 " if row["id"] == showcase else ""
        lines.append(f"{marker}**{KINDS[row['kind']]}｜{row['name']}**" + (f"（稱號：{title}）" if title else ""))
    embed.description = "\n".join(lines)[:3900]
    embed.set_footer(text=f"共有 {len(rows)} 項；🌟 代表目前展示的稱號。")
    return embed


async def error_reply(interaction, exc):
    message = f"⚠️ {str(exc)[:1700]}"
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class BoundView(discord.ui.View):
    def __init__(self, interaction):
        super().__init__(timeout=900)
        self.user_id = interaction.user.id
        self.guild_id = interaction.guild_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id or interaction.guild_id != self.guild_id or not owner(interaction):
            await error_reply(interaction, ValueError("僅原操作群主可使用此面板。"))
            return False
        return True


class DefinitionModal(discord.ui.Modal):
    def __init__(self, previous=None):
        super().__init__(title="新增成就／榮譽／稱號" if previous is None else "修改成就／榮譽")
        self.previous = previous
        p = previous or {}
        rewards = json.loads(p["rewards_json"]) if previous else {}
        parts = []
        if rewards.get("money"):
            parts.append(f"金錢={rewards['money']}")
        if rewards.get("attributes"):
            parts.append("屬性=" + ",".join(f"{k}:{v}" for k,v in rewards["attributes"].items()))
        if rewards.get("items"):
            parts.append("物品=" + ",".join(f"{k}:{v}" for k,v in rewards["items"].items()))
        if rewards.get("title"):
            parts.append("稱號=" + rewards["title"])
        cond = json.loads(p["condition_json"]) if previous else {}
        cond_string = ""
        if cond:
            cond_string = (f"attribute:{cond['name']}:{cond['minimum']}" if cond["type"] == "attribute" else f"{cond['type']}:{cond['minimum']}")
        self.kind = discord.ui.TextInput(label="分類：achievement / honor / title", default=p.get("kind", "achievement"), max_length=20)
        self.name = discord.ui.TextInput(label="名稱", default=p.get("name", ""), max_length=100)
        self.description = discord.ui.TextInput(label="說明（可留空）", required=False, style=discord.TextStyle.paragraph, default=p.get("description", ""), max_length=500)
        self.rewards = discord.ui.TextInput(label="獎勵（可混合，以分號分隔）", required=False, style=discord.TextStyle.paragraph, default=";".join(parts), placeholder="金錢=500;屬性=歌唱:2;物品=12:1;稱號=人氣新人", max_length=900)
        self.condition = discord.ui.TextInput(label="自動條件（留空代表手動）", required=False, default=cond_string, placeholder="fans:500 或 attribute:演繹:80", max_length=100)
        for field in (self.kind,self.name,self.description,self.rewards,self.condition):
            self.add_item(field)

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)
            number = save_definition(interaction.guild_id, interaction.user.id, str(self.kind.value), str(self.name.value), str(self.description.value), str(self.rewards.value), str(self.condition.value), self.previous["id"] if self.previous else None)
            await interaction.response.send_message(f"✅ 已儲存 #{number}：{self.name.value}", ephemeral=True)
        except (ValueError, sqlite3.Error) as exc:
            LOG.exception("儲存榮譽設定失敗") if isinstance(exc, sqlite3.Error) else None
            await error_reply(interaction, exc)


class IdModal(discord.ui.Modal):
    def __init__(self, action):
        self.action = action
        labels = {"edit":"修改設定", "active":"啟用／停用", "hidden":"公開／隱藏", "grant":"手動頒發", "revoke":"撤銷榮譽"}
        super().__init__(title=labels[action])
        self.award = discord.ui.TextInput(label="成就／榮譽編號", placeholder="例如 12", max_length=12)
        self.add_item(self.award)
        self.character = None
        if action in ("grant", "revoke"):
            self.character = discord.ui.TextInput(label="角色編號", placeholder="例如 35", max_length=12)
            self.add_item(self.character)

    async def on_submit(self, interaction):
        try:
            require_owner(interaction)
            aid = positive_int(self.award.value, "成就編號", zero=False)
            if self.action == "edit":
                await interaction.response.send_modal(DefinitionModal(definition(interaction.guild_id, aid)))
                return
            if self.action in ("active", "hidden"):
                message = set_flag(interaction.guild_id, interaction.user.id, aid, self.action)
            else:
                cid = positive_int(self.character.value, "角色編號", zero=False)
                if self.action == "grant":
                    added = grant(interaction.guild_id, interaction.user.id, aid, cid)
                    message = "✅ 頒發成功，獎勵已入帳。" if added else "此角色已獲頒過該項目，不會重複發獎。"
                else:
                    revoke(interaction.guild_id, interaction.user.id, aid, cid)
                    message = "✅ 已撤銷展示資格；已領取的金錢、屬性及物品不會追回。"
            await interaction.response.send_message(message, ephemeral=True)
        except (ValueError, sqlite3.Error) as exc:
            LOG.exception("榮譽操作失敗") if isinstance(exc, sqlite3.Error) else None
            await error_reply(interaction, exc)


class AwardsAdminView(BoundView):
    def __init__(self, interaction, page=0):
        super().__init__(interaction)
        self.page = page
        _, _, last = listing(interaction.guild_id, page)
        self.previous.disabled = page <= 0
        self.next.disabled = page >= last

    @discord.ui.button(label="➕ 新增", style=discord.ButtonStyle.success, row=0)
    async def create(self, interaction, button):
        await interaction.response.send_modal(DefinitionModal())

    @discord.ui.button(label="✏️ 修改", style=discord.ButtonStyle.primary, row=0)
    async def edit(self, interaction, button):
        await interaction.response.send_modal(IdModal("edit"))

    @discord.ui.button(label="🎖️ 頒發", style=discord.ButtonStyle.success, row=0)
    async def grant_button(self, interaction, button):
        await interaction.response.send_modal(IdModal("grant"))

    @discord.ui.button(label="🚫 撤銷", style=discord.ButtonStyle.danger, row=0)
    async def revoke_button(self, interaction, button):
        await interaction.response.send_modal(IdModal("revoke"))

    @discord.ui.button(label="⏸️ 啟停", style=discord.ButtonStyle.secondary, row=1)
    async def active(self, interaction, button):
        await interaction.response.send_modal(IdModal("active"))

    @discord.ui.button(label="🙈 公開／隱藏", style=discord.ButtonStyle.secondary, row=1)
    async def hidden(self, interaction, button):
        await interaction.response.send_modal(IdModal("hidden"))

    @discord.ui.button(label="⬅️ 上一頁", style=discord.ButtonStyle.secondary, row=2)
    async def previous(self, interaction, button):
        text, page, _ = listing(interaction.guild_id, self.page - 1)
        await interaction.response.edit_message(content=text, view=AwardsAdminView(interaction, page))

    @discord.ui.button(label="➡️ 下一頁", style=discord.ButtonStyle.secondary, row=2)
    async def next(self, interaction, button):
        text, page, _ = listing(interaction.guild_id, self.page + 1)
        await interaction.response.edit_message(content=text, view=AwardsAdminView(interaction, page))

    @discord.ui.button(label="🔄 刷新", style=discord.ButtonStyle.primary, row=2)
    async def refresh(self, interaction, button):
        text, page, _ = listing(interaction.guild_id, self.page)
        await interaction.response.edit_message(content=text, view=AwardsAdminView(interaction, page))

    @discord.ui.button(label="📜 操作紀錄", style=discord.ButtonStyle.secondary, row=3)
    async def log(self, interaction, button):
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM rp_award_audit WHERE guild_id=? ORDER BY id DESC LIMIT 15", (str(interaction.guild_id),)).fetchall()
        lines = [f"#{r['id']}｜{r['created_at']}｜{r['action']}｜獎項#{r['definition_id']}｜角色#{r['character_id']}｜操作者{r['actor_id']}" for r in rows]
        await interaction.response.send_message("📜 **最近操作紀錄**\n" + ("\n".join(lines)[:1750] or "尚無紀錄"), ephemeral=True)


class AwardsPublicButton(discord.ui.Button):
    def __init__(self, character_id):
        super().__init__(label="🏆 成就榮譽", style=discord.ButtonStyle.secondary, row=2)
        self.character_id = character_id

    async def callback(self, interaction):
        try:
            from core.permissions import Actor
            from modules.character_view import get_character
            actor = Actor.from_interaction(interaction)
            get_character(actor, self.character_id)
            embed = award_embed(interaction.guild_id, self.character_id)
            await interaction.response.send_message(embed=embed, ephemeral=True, view=ShowcaseView(interaction, self.character_id))
        except Exception as exc:
            LOG.exception("成就公開面板讀取失敗")
            await error_reply(interaction, exc)


class ShowcaseView(discord.ui.View):
    def __init__(self, interaction, character_id):
        super().__init__(timeout=900)
        self.user_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.character_id = int(character_id)

    @discord.ui.button(label="🌟 選擇展示稱號", style=discord.ButtonStyle.primary)
    async def equip(self, interaction, button):
        if interaction.user.id != self.user_id or interaction.guild_id != self.guild_id:
            await error_reply(interaction, ValueError("這不是你的角色面板。"))
            return
        try:
            from modules.character_view import get_character
            from core.permissions import Actor
            actor = Actor.from_interaction(interaction)
            character = get_character(actor, self.character_id)
            if str(character["owner_id"]) != str(interaction.user.id):
                raise ValueError("只有角色本人可以設定展示稱號。")
            await interaction.response.send_modal(ShowcaseModal(self.character_id))
        except (ValueError, sqlite3.Error) as exc:
            await error_reply(interaction, exc)


class ShowcaseModal(discord.ui.Modal, title="選擇展示稱號"):
    award_id = discord.ui.TextInput(label="已獲得的稱號／獎項編號（填 0 取消展示）", max_length=12)

    def __init__(self, character_id):
        super().__init__()
        self.character_id = character_id

    async def on_submit(self, interaction):
        try:
            from modules.character_view import get_character
            from core.permissions import Actor
            row = get_character(Actor.from_interaction(interaction), self.character_id)
            if str(row["owner_id"]) != str(interaction.user.id):
                raise ValueError("僅角色本人可以設定稱號。")
            aid = positive_int(self.award_id.value, "獎項編號")
            with transaction() as conn:
                if aid:
                    entry = conn.execute("""SELECT d.rewards_json FROM rp_award_grants g
                        JOIN rp_award_definitions d ON d.guild_id=g.guild_id AND d.id=g.definition_id
                        WHERE g.guild_id=? AND g.character_id=? AND g.definition_id=? AND g.revoked=0""", (str(interaction.guild_id), self.character_id, aid)).fetchone()
                    if not entry or not json.loads(entry["rewards_json"]).get("title"):
                        raise ValueError("這個獎項沒有可用稱號，或你尚未獲得。")
                    conn.execute("""INSERT INTO rp_award_showcase(guild_id,character_id,definition_id)
                        VALUES (?,?,?) ON CONFLICT(guild_id,character_id)
                        DO UPDATE SET definition_id=excluded.definition_id""", (str(interaction.guild_id), self.character_id, aid))
                else:
                    conn.execute("DELETE FROM rp_award_showcase WHERE guild_id=? AND character_id=?", (str(interaction.guild_id), self.character_id))
            await interaction.response.send_message("✅ 展示稱號已更新。", ephemeral=True)
        except (ValueError, sqlite3.Error) as exc:
            await error_reply(interaction, exc)


def patch_interfaces():
    from modules.official_center import OfficialHome
    from modules.character_view import CharacterDetailView

    if not getattr(OfficialHome, "_rp_awards_patched", False):
        original = OfficialHome.__init__

        def init_official(self, interaction, *args, **kwargs):
            original(self, interaction, *args, **kwargs)
            if owner(interaction):
                button = discord.ui.Button(label="🏆 獎項管理", style=discord.ButtonStyle.success, row=3)

                async def open_awards(clicked):
                    try:
                        require_owner(clicked)
                        content, page, _ = listing(clicked.guild_id)
                        await clicked.response.send_message(content, view=AwardsAdminView(clicked, page), ephemeral=True)
                    except (ValueError, sqlite3.Error) as exc:
                        await error_reply(clicked, exc)

                button.callback = open_awards
                self.add_item(button)

        OfficialHome.__init__ = init_official
        OfficialHome._rp_awards_patched = True

    if not getattr(CharacterDetailView, "_rp_awards_patched", False):
        original_detail = CharacterDetailView.__init__

        def init_detail(self, actor, character_id, *args, **kwargs):
            original_detail(self, actor, character_id, *args, **kwargs)
            # 成就榮譽由 character_view 的七鍵主選單處理。

        CharacterDetailView.__init__ = init_detail
        CharacterDetailView._rp_awards_patched = True


class AwardsAddon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        init_tables()
        patch_interfaces()
        self.automatic_scan.start()

    def cog_unload(self):
        self.automatic_scan.cancel()

    @tasks.loop(minutes=5)
    async def automatic_scan(self):
        # 限定每輪最多處理 1000 筆候選，防止大型伺服器長時間阻塞。
        with get_connection() as conn:
            rows = conn.execute("""SELECT d.guild_id,d.id AS award_id,c.id AS character_id
                FROM rp_award_definitions d JOIN characters c ON c.guild_id=d.guild_id
                LEFT JOIN rp_award_grants g ON g.guild_id=d.guild_id
                    AND g.definition_id=d.id AND g.character_id=c.id
                WHERE d.active=1 AND d.condition_json!='{}'
                    AND c.status IN ('active','retired') AND g.id IS NULL
                ORDER BY d.id,c.id LIMIT 1000""").fetchall()
        for row in rows:
            try:
                grant(row["guild_id"], "system", row["award_id"], row["character_id"], automatic=True)
            except Exception:
                LOG.exception("自動頒發失敗：%s", dict(row))

    @automatic_scan.before_loop
    async def before_scan(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AwardsAddon(bot))
