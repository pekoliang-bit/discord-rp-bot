"""代言合約查詢與雙方協議終止；直接接在 /公司 面板。"""

from __future__ import annotations

import time

import discord

from core.database import get_connection, transaction
from modules.company_business import _entity, has_permission
from modules.endorse import initialize as initialize_endorse


def initialize():
    initialize_endorse()
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_termination (
            guild_id TEXT NOT NULL,
            contract_id INTEGER NOT NULL,
            requested_by TEXT NOT NULL,
            requester_side TEXT NOT NULL CHECK(requester_side IN ('artist','company')),
            reason TEXT NOT NULL DEFAULT '',
            requested_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,contract_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            contract_id INTEGER NOT NULL,
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            recorded_at INTEGER NOT NULL
        )""")


def _contract(db, gid, contract_id):
    row = db.execute("""SELECT c.*,ch.owner_id AS artist_owner, b.owner_id AS company_owner
        FROM rp_endorsement_contracts c
        JOIN characters ch ON ch.guild_id=c.guild_id AND ch.id=c.character_id
        JOIN rp_business_entities b ON b.guild_id=c.guild_id AND b.business_id=c.business_id
        WHERE c.guild_id=? AND c.id=?""", (gid, contract_id)).fetchone()
    if not row:
        raise ValueError("找不到這筆代言合約。")
    return row


def _side(db, row, uid):
    if str(row["artist_owner"]) == uid:
        return "artist"
    if has_permission(db, row["guild_id"], row["business_id"], uid, "endorsement"):
        return "company"
    raise PermissionError("你不是這份合約的藝人持有人或公司代言管理者。")


def contracts(guild_id, actor_id):
    initialize()
    gid, uid = str(guild_id), str(actor_id)
    with get_connection() as db:
        rows = db.execute("""SELECT c.id,c.business_id,c.character_id,c.tier,c.fee,
            c.contract_type,c.market,c.series,c.status,ch.owner_id AS artist_owner,
            b.owner_id AS company_owner
            FROM rp_endorsement_contracts c
            JOIN characters ch ON ch.guild_id=c.guild_id AND ch.id=c.character_id
            JOIN rp_business_entities b ON b.guild_id=c.guild_id AND b.business_id=c.business_id
            WHERE c.guild_id=? AND c.status IN ('pending','active') ORDER BY c.id DESC LIMIT 100""", (gid,)).fetchall()
        result = []
        for row in rows:
            if row["artist_owner"] != uid and not has_permission(db, gid, row["business_id"], uid, "endorsement"):
                continue
            request = db.execute("""SELECT requester_side FROM rp_endorsement_termination
                WHERE guild_id=? AND contract_id=?""", (gid, row["id"])).fetchone()
            result.append({**dict(row), "termination_side": request["requester_side"] if request else None})
            if len(result) >= 15:
                break
    return result


def request_termination(guild_id, actor_id, contract_id, reason):
    initialize()
    gid, uid, contract_id = str(guild_id), str(actor_id), int(contract_id)
    reason = str(reason).strip()
    if not reason or len(reason) > 200:
        raise ValueError("請填寫 1～200 字的終止原因。")
    with transaction() as db:
        row = _contract(db, gid, contract_id)
        if row["status"] != "active":
            raise ValueError("只有生效中的合約可以提出協議終止。")
        side = _side(db, row, uid)
        if db.execute("SELECT 1 FROM rp_endorsement_termination WHERE guild_id=? AND contract_id=?", (gid, contract_id)).fetchone():
            raise ValueError("已有待另一方回覆的終止申請。")
        db.execute("""INSERT INTO rp_endorsement_termination
            (guild_id,contract_id,requested_by,requester_side,reason,requested_at)
            VALUES (?,?,?,?,?,?)""", (gid, contract_id, uid, side, reason, int(time.time())))
        db.execute("""INSERT INTO rp_endorsement_history
            (guild_id,contract_id,actor_id,action,reason,recorded_at)
            VALUES (?,?,?,?,?,?)""", (gid, contract_id, uid, "termination_requested", reason, int(time.time())))
    return f"✅ 已提出合約 #{contract_id} 的終止申請，須由另一方同意才會生效。"


def answer_termination(guild_id, actor_id, contract_id, approve):
    initialize()
    gid, uid, contract_id = str(guild_id), str(actor_id), int(contract_id)
    with transaction() as db:
        row = _contract(db, gid, contract_id)
        if row["status"] != "active":
            raise ValueError("這份合約已不在有效狀態。")
        side = _side(db, row, uid)
        request = db.execute("""SELECT * FROM rp_endorsement_termination
            WHERE guild_id=? AND contract_id=?""", (gid, contract_id)).fetchone()
        if not request:
            raise ValueError("這份合約目前沒有待回覆的終止申請。")
        if side == request["requester_side"]:
            raise PermissionError("提出申請的一方不能代替另一方批准。")
        if approve:
            db.execute("""UPDATE rp_endorsement_contracts SET status='ended'
                WHERE guild_id=? AND id=? AND status='active'""", (gid, contract_id))
        db.execute("DELETE FROM rp_endorsement_termination WHERE guild_id=? AND contract_id=?", (gid, contract_id))
        db.execute("""INSERT INTO rp_endorsement_history
            (guild_id,contract_id,actor_id,action,reason,recorded_at)
            VALUES (?,?,?,?,?,?)""", (gid, contract_id, uid, "terminated" if approve else "termination_declined", request["reason"], int(time.time())))
    return f"{'✅ 已由雙方協議終止' if approve else '已拒絕終止'}合約 #{contract_id}。既有付款與帳本不會變動。"


class ContractActionModal(discord.ui.Modal, title="代言合約管理"):
    contract_id = discord.ui.TextInput(label="合約編號")
    operation = discord.ui.TextInput(label="操作：提出終止／同意終止／拒絕終止")
    reason = discord.ui.TextInput(label="終止理由（提出終止時必填）", required=False, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            operation = self.operation.value.strip()
            if operation == "提出終止":
                result = request_termination(interaction.guild_id, interaction.user.id, int(self.contract_id.value), self.reason.value)
            elif operation in ("同意終止", "拒絕終止"):
                result = answer_termination(interaction.guild_id, interaction.user.id, int(self.contract_id.value), operation == "同意終止")
            else:
                raise ValueError("請輸入提出終止、同意終止或拒絕終止。")
        except (ValueError, PermissionError, TypeError) as exc:
            result = f"❌ {exc}"
        await interaction.response.send_message(result, ephemeral=True)


class ContractButton(discord.ui.Button):
    def __init__(self, mode):
        self.mode = mode
        super().__init__(label="📑 我的代言" if mode == "list" else "📝 合約管理", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction):
        if self.mode == "manage":
            return await interaction.response.send_modal(ContractActionModal())
        rows = contracts(interaction.guild_id, interaction.user.id)
        lines = []
        for item in rows:
            pending = "｜⚠️ 待另一方回覆終止" if item["termination_side"] else ""
            lines.append(f"#{item['id']}｜{item['business_id']}｜角色#{item['character_id']}｜T{item['tier']}｜{item['contract_type']}｜{item['status']}｜{item['fee']:,} 金幣{pending}")
        await interaction.response.send_message("\n".join(lines)[:1950] if lines else "目前沒有可查看的代言合約。", ephemeral=True)


async def setup(bot):
    initialize()
    from modules.company_portal import CompanyPanel
    if getattr(CompanyPanel, "_endorse_life_hooked", False):
        return
    original = CompanyPanel.__init__

    def with_lifecycle(self, uid, gid, is_owner):
        original(self, uid, gid, is_owner)
        self.add_item(ContractButton("list"))
        self.add_item(ContractButton("manage"))

    CompanyPanel.__init__ = with_lifecycle
    CompanyPanel._endorse_life_hooked = True
