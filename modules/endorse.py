"""/公司 代言邀約：獨立模組，不修改 /官方 與現有玩家資料。"""

from __future__ import annotations

import time

import discord

from core.database import account_balance, get_connection, get_or_create_account, transaction
from core.economy import _payment
from modules.character_status import ENDORSEMENT_MAX_TIER, get_status, minimum_endorsement_fee
from modules.company_business import _entity, has_permission
from modules.company_treasury import initialize_treasury


def initialize():
    initialize_treasury()
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, business_id TEXT NOT NULL,
            character_id INTEGER NOT NULL, offered_by TEXT NOT NULL,
            tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 10),
            fee INTEGER NOT NULL CHECK(fee >= 0),
            contract_type TEXT NOT NULL, market TEXT NOT NULL,
            series TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            decided_by TEXT, created_at INTEGER NOT NULL,
            decided_at INTEGER, payment_key TEXT NOT NULL UNIQUE,
            UNIQUE(guild_id, id)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_endorsement_pending
            ON rp_endorsement_contracts(guild_id, character_id, status)""")


def _character(db, gid, cid):
    row = db.execute("SELECT id, owner_id, name, status FROM characters WHERE guild_id=? AND id=?", (gid, cid)).fetchone()
    if not row or row["status"] != "active":
        raise ValueError("只能邀請本伺服器已啟用的角色。")
    return row


def _can_pay(db, gid, bid, uid, fee):
    entity = _entity(db, gid, bid)
    if str(entity["owner_id"]) == uid:
        return
    if not has_permission(db, gid, bid, uid, "finance"):
        raise PermissionError("代言支出需要公司持有人或財務授權。")
    row = db.execute("""SELECT max_per_transaction FROM rp_company_finance_limits
        WHERE guild_id=? AND business_id=? AND manager_user_id=?""", (gid, bid, uid)).fetchone()
    if not row or int(row["max_per_transaction"]) < fee:
        raise PermissionError("這筆代言費超出你的單筆支出上限。")


def offer(guild_id, business_id, actor_id, character_id, tier, fee, contract_type, market, series="", note=""):
    initialize()
    gid, bid, uid, cid = str(guild_id), str(business_id).strip(), str(actor_id), int(character_id)
    tier, fee = int(tier), int(fee)
    contract_type, market, series, note = [str(x).strip() for x in (contract_type, market, series, note)]
    if contract_type not in {"單次", "短期", "長期"}:
        raise ValueError("合約類型只能填「單次／短期／長期」。")
    if not market or len(market) > 60 or len(series) > 60 or len(note) > 250:
        raise ValueError("市場必填，市場及系列最多60字、備註最多250字。")
    if tier < 1 or tier > 10 or fee < 0:
        raise ValueError("代言級別 T1～T10，費用不得為負數。")
    if fee < minimum_endorsement_fee(tier):
        # T10 品牌起源可作免費榮譽頭銜，但需明確以市場欄標示「榮譽」。
        if not (tier == 10 and fee == 0 and market == "榮譽"):
            raise ValueError(f"T{tier} 最低代言費為 {minimum_endorsement_fee(tier):,} 金幣。")
    level = max(1, min(20, int(get_status(gid, cid)["level"])))
    with transaction() as db:
        entity = _entity(db, gid, bid)
        if not has_permission(db, gid, bid, uid, "endorsement"):
            raise PermissionError("你沒有代言管理權限。")
        if fee:
            _can_pay(db, gid, bid, uid, fee)
        character = _character(db, gid, cid)
        if tier > ENDORSEMENT_MAX_TIER[max(1, min(level, 20)) - 1] and tier != 10:
            raise ValueError(f"角色目前 LV{level}，最多可接受 T{ENDORSEMENT_MAX_TIER[level - 1]}。")
        if tier == 10 and market != "榮譽":
            raise ValueError("T10 必須標示市場為「榮譽」，商業合約仍需支付最低費用。")
        # 同品牌在同市場／系列的有效合約必須由管理人員先行處理。
        if tier != 10:
            exists = db.execute("""SELECT id FROM rp_endorsement_contracts
                WHERE guild_id=? AND business_id=? AND status IN ('pending','active')
                  AND ((character_id=?) OR (market=? AND series=? AND tier=?)) LIMIT 1""",
                (gid, bid, cid, market, series, tier)).fetchone()
            if exists:
                raise ValueError("此品牌已有相同角色或相同市場／系列／級別的待審或有效合約。")
        cur = db.execute("""INSERT INTO rp_endorsement_contracts
            (guild_id,business_id,character_id,offered_by,tier,fee,contract_type,market,series,note,created_at,payment_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (gid, bid, cid, uid, tier, fee, contract_type, market, series, note, int(time.time()), f"endorse:{gid}:{bid}:{cid}:{time.time_ns()}"))
        return f"✅ 已送出代言邀約 #{cur.lastrowid}\n{entity['name']} → {character['name']}\nT{tier}｜{contract_type}｜{market}｜{fee:,} 金幣\n角色持有人可在 /公司 → 回覆代言邀請 處處理。"


def pending(guild_id, actor_id):
    initialize()
    gid, uid = str(guild_id), str(actor_id)
    with get_connection() as db:
        rows = db.execute("""SELECT c.id,c.business_id,c.character_id,c.tier,c.fee,c.contract_type,c.market,c.series
            FROM rp_endorsement_contracts c JOIN characters ch ON ch.guild_id=c.guild_id AND ch.id=c.character_id
            WHERE c.guild_id=? AND ch.owner_id=? AND c.status='pending'
            ORDER BY c.id DESC LIMIT 15""", (gid, uid)).fetchall()
    return list(map(dict, rows))


def decide(guild_id, actor_id, contract_id, accept):
    initialize()
    gid, uid, contract_id = str(guild_id), str(actor_id), int(contract_id)
    with transaction() as db:
        row = db.execute("SELECT * FROM rp_endorsement_contracts WHERE guild_id=? AND id=?", (gid, contract_id)).fetchone()
        if not row or row["status"] != "pending":
            raise ValueError("找不到待回覆的邀約（可能已處理）。")
        character = _character(db, gid, int(row["character_id"]))
        if str(character["owner_id"]) != uid:
            raise PermissionError("只有該角色的持有人可以回覆代言邀請。")
        if accept:
            _entity(db, gid, row["business_id"])
            if row["fee"]:
                # 收款當下再次核對公司財務授權與餘額；確保不能越權付款。
                _can_pay(db, gid, row["business_id"], row["offered_by"], int(row["fee"]))
                sender = get_or_create_account(db, gid, "organization", row["business_id"], "treasury")
                receiver = get_or_create_account(db, gid, "character", str(row["character_id"]), "wallet")
                if account_balance(db, gid, sender) < row["fee"]:
                    raise ValueError("公司金庫餘額不足，合約尚未生效。")
                result = _payment(db, gid, row["payment_key"], sender, receiver, int(row["fee"]), f"代言合約 #{contract_id} T{row['tier']}")
                if result.duplicated:
                    raise ValueError("此合約款項已付款，請聯絡管理員核對。")
            db.execute("""UPDATE rp_endorsement_contracts SET status='active',decided_by=?,decided_at=?
                WHERE guild_id=? AND id=? AND status='pending'""", (uid, int(time.time()), gid, contract_id))
            return f"✅ 已接受代言合約 #{contract_id}，T{row['tier']}，收到 {row['fee']:,} 金幣。"
        db.execute("""UPDATE rp_endorsement_contracts SET status='declined',decided_by=?,decided_at=?
            WHERE guild_id=? AND id=? AND status='pending'""", (uid, int(time.time()), gid, contract_id))
        return f"已婉拒代言邀約 #{contract_id}。"


class OfferModal(discord.ui.Modal, title="發出代言邀約"):
    business = discord.ui.TextInput(label="公司／品牌編號")
    character = discord.ui.TextInput(label="藝人角色編號")
    tier = discord.ui.TextInput(label="代言級別 T1～T10", placeholder="例如 4")
    fee = discord.ui.TextInput(label="代言費（金幣）", placeholder="例如 120000")
    details = discord.ui.TextInput(label="類型｜市場｜系列（系列可空）", placeholder="短期｜台灣｜春季系列")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            data = [x.strip() for x in str(self.details).split("｜")]
            if len(data) not in (2, 3):
                raise ValueError("最後一欄請填：類型｜市場｜系列（可省略系列）。")
            msg = offer(interaction.guild_id, self.business.value,
                        interaction.user.id, int(self.character.value), int(self.tier.value),
                        int(self.fee.value), data[0], data[1], data[2] if len(data) == 3 else "")
        except (ValueError, PermissionError, TypeError) as exc:
            msg = f"❌ {exc}"
        await interaction.response.send_message(msg, ephemeral=True)


class DecideModal(discord.ui.Modal, title="回覆代言邀約"):
    contract = discord.ui.TextInput(label="邀約編號", placeholder="先由 /公司 → 查看待回覆邀約 取得")
    action = discord.ui.TextInput(label="接受或拒絕", placeholder="接受")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            action = str(self.action).strip()
            if action not in {"接受", "拒絕"}:
                raise ValueError("只能輸入「接受」或「拒絕」。")
            msg = decide(interaction.guild_id, interaction.user.id, int(self.contract.value), action == "接受")
        except (ValueError, PermissionError, TypeError) as exc:
            msg = f"❌ {exc}"
        await interaction.response.send_message(msg, ephemeral=True)


class EndorseButton(discord.ui.Button):
    def __init__(self, mode):
        self.mode = mode
        super().__init__(label={"offer": "🤝 代言邀約", "pending": "📨 待回覆邀約", "decide": "✅ 回覆代言"}[mode],
                         style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction):
        if self.mode == "offer":
            await interaction.response.send_modal(OfferModal())
        elif self.mode == "decide":
            await interaction.response.send_modal(DecideModal())
        else:
            rows = pending(interaction.guild_id, interaction.user.id)
            msg = "\n".join(f"#{r['id']}｜{r['business_id']}｜角色#{r['character_id']}｜T{r['tier']}｜{r['contract_type']}｜{r['market']}｜{r['fee']:,} 金幣" for r in rows)
            await interaction.response.send_message(msg or "目前沒有待回覆的代言邀約。", ephemeral=True)


async def setup(bot):
    initialize()
    from modules.company_portal import CompanyPanel
    if getattr(CompanyPanel, "_endorse_hooked", False):
        return
    original = CompanyPanel.__init__

    def with_endorse(self, uid, gid, is_owner):
        original(self, uid, gid, is_owner)
        for mode in ("offer", "pending", "decide"):
            self.add_item(EndorseButton(mode))

    CompanyPanel.__init__ = with_endorse
    CompanyPanel._endorse_hooked = True
