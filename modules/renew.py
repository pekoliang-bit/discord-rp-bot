"""到期代言的人工續約：公司再次邀請，藝人重新同意才付款。"""

from __future__ import annotations

import re

import discord

from core.database import get_connection, transaction
from modules.company_business import has_permission
from modules.endorse import initialize as initialize_endorse
from modules.endorse_term import expire_due


def initialize():
    initialize_endorse()
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_renewals (
            guild_id TEXT NOT NULL,
            old_contract_id INTEGER NOT NULL,
            new_contract_id INTEGER NOT NULL,
            requested_by TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,old_contract_id,new_contract_id),
            UNIQUE(guild_id,new_contract_id)
        )""")


def renew(guild_id, actor_id, old_contract_id, tier, fee):
    """只續約已到期合約，不延長舊約、不自動轉帳。"""
    initialize()
    from modules import endorse

    gid, uid = str(guild_id), str(actor_id)
    old_contract_id, tier, fee = int(old_contract_id), int(tier), int(fee)
    # 每次檢查先更新到期狀態，不必等每小時排程。
    expire_due()
    with get_connection() as db:
        old = db.execute("""SELECT * FROM rp_endorsement_contracts
            WHERE guild_id=? AND id=?""", (gid, old_contract_id)).fetchone()
        if not old:
            raise ValueError("找不到原代言合約。")
        if old["status"] != "expired":
            raise ValueError("只有已到期的合約可以使用續約；尚未到期請等待期滿。")
        if not has_permission(db, gid, old["business_id"], uid, "endorsement"):
            raise PermissionError("你沒有原品牌的代言管理權限。")
        prior = db.execute("""SELECT 1 FROM rp_endorsement_renewals l
            JOIN rp_endorsement_contracts n ON n.guild_id=l.guild_id AND n.id=l.new_contract_id
            WHERE l.guild_id=? AND l.old_contract_id=?
              AND n.status IN ('pending','active')""", (gid, old_contract_id)).fetchone()
        if prior:
            raise ValueError("原合約已有待回覆或生效中的續約，不能重複邀請。")
        fields = dict(old)

    # 透過既有邀約入口檢查地位、最低價格、品牌權限、財務額度及競品。
    message = endorse.offer(
        gid, fields["business_id"], uid, fields["character_id"],
        tier, fee, fields["contract_type"], fields["market"],
        fields["series"], f"續約原合約 #{old_contract_id}",
    )
    match = re.search(r"邀約\s*#(\d+)", message)
    if not match:
        raise ValueError("邀約已送出，但未能確認新合約編號；請聯絡管理員核對。")
    new_contract_id = int(match.group(1))
    with transaction() as db:
        # 續約連結僅為紀錄，真正付款仍由原邀約的藝人同意流程完成。
        db.execute("""INSERT OR IGNORE INTO rp_endorsement_renewals
            (guild_id,old_contract_id,new_contract_id,requested_by)
            VALUES (?,?,?,?)""", (gid, old_contract_id, new_contract_id, uid))
    return message + f"\n🔄 本次為舊合約 #{old_contract_id} 的續約，新的有效期限從藝人接受時起算。"


class RenewalModal(discord.ui.Modal, title="到期代言續約"):
    contract = discord.ui.TextInput(label="已到期的原合約編號")
    tier = discord.ui.TextInput(label="新代言級別 T1～T10", placeholder="例如 4")
    fee = discord.ui.TextInput(label="新一期代言費（金幣）", placeholder="例如 120000")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            msg = renew(interaction.guild_id, interaction.user.id,
                        self.contract.value, self.tier.value, self.fee.value)
        except (ValueError, PermissionError, TypeError) as exc:
            msg = f"❌ {exc}"
        await interaction.response.send_message(msg[:1950], ephemeral=True)


class RenewalButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🔄 代言續約", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction):
        await interaction.response.send_modal(RenewalModal())


async def setup(bot):
    initialize()
    from modules.company_portal import CompanyPanel
    if getattr(CompanyPanel, "_renew_hooked", False):
        return
    previous = CompanyPanel.__init__

    def with_renewal(self, uid, gid, is_owner):
        previous(self, uid, gid, is_owner)
        self.add_item(RenewalButton())

    CompanyPanel.__init__ = with_renewal
    CompanyPanel._renew_hooked = True
