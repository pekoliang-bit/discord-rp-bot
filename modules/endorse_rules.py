"""代言競品風險提示與歷史頭銜查詢（不擅自阻擋未知門檻）。"""

from __future__ import annotations

from core.database import get_connection
from modules.endorse import initialize
import discord


def competition_report(guild_id, character_id, new_business_id):
    """依公司主要類別提示在約競品；不自動扣除數值或解除合約。"""
    initialize()
    gid, cid, bid = str(guild_id), int(character_id), str(new_business_id)
    with get_connection() as db:
        target = db.execute("""SELECT business_id,name,kind,category,industry
            FROM rp_business_entities WHERE guild_id=? AND business_id=? AND status='active'""", (gid, bid)).fetchone()
        if target is None:
            raise ValueError("找不到有效公司或品牌。")
        rows = db.execute("""SELECT c.id,c.business_id,b.name,b.category,b.industry,
            c.tier,c.market,c.series
            FROM rp_endorsement_contracts c
            JOIN rp_business_entities b ON b.guild_id=c.guild_id AND b.business_id=c.business_id
            WHERE c.guild_id=? AND c.character_id=? AND c.status='active' AND c.business_id!=?""", (gid, cid, bid)).fetchall()
    conflicts = []
    for row in rows:
        same_category = bool(target["category"] and target["category"] == row["category"])
        same_industry = bool(target["industry"] and target["industry"] == row["industry"])
        if same_category or same_industry:
            conflicts.append({"contract_id": row["id"], "business_id": row["business_id"],
                "business_name": row["name"], "tier": row["tier"], "market": row["market"],
                "reason": "同品類" if same_category else "同產業"})
    return {"target": dict(target), "conflicts": conflicts,
        "warning": "同類別不一定是競品，需由公司或官方確認；高咖位競品風險減少60%須待門檻與扣分規則確定。"}


def title_history(guild_id, character_id):
    """保留所有已成立代言的歷史紀錄，T10 不因合約終止而消失。"""
    initialize()
    gid, cid = str(guild_id), int(character_id)
    with get_connection() as db:
        rows = db.execute("""SELECT c.id,c.business_id,b.name AS business_name,c.tier,
            c.contract_type,c.market,c.series,c.fee,c.status,c.decided_at
            FROM rp_endorsement_contracts c
            LEFT JOIN rp_business_entities b ON b.guild_id=c.guild_id AND b.business_id=c.business_id
            WHERE c.guild_id=? AND c.character_id=? AND c.status IN ('active','ended')
            ORDER BY c.tier DESC,c.decided_at DESC,c.id DESC""", (gid, cid)).fetchall()
    return [dict(row) for row in rows]


class HistoryModal(discord.ui.Modal, title="代言頭銜歷史"):
    character_id = discord.ui.TextInput(label="角色編號")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            gid, cid, uid = str(interaction.guild_id), int(self.character_id.value), str(interaction.user.id)
            with get_connection() as db:
                row = db.execute("SELECT owner_id FROM characters WHERE guild_id=? AND id=?", (gid, cid)).fetchone()
                if not row:
                    raise ValueError("找不到角色。")
                if str(row["owner_id"]) != uid and interaction.guild.owner_id != interaction.user.id:
                    raise PermissionError("只能查看自己的角色（群主除外）。")
            rows = title_history(gid, cid)
            lines = [f"#{r['id']}｜{r['business_name'] or r['business_id']}｜T{r['tier']}｜{r['status']}｜{r['market']}" for r in rows[:20]]
            result = "\n".join(lines)[:1950] if lines else "尚無生效過的代言紀錄。"
        except (ValueError, PermissionError, TypeError) as exc:
            result = f"❌ {exc}"
        await interaction.response.send_message(result, ephemeral=True)


class HistoryButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🏅 歷史代言", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction):
        await interaction.response.send_modal(HistoryModal())


async def setup(bot):
    initialize()
    from modules import endorse
    from modules.company_portal import CompanyPanel
    if not getattr(endorse, "_competition_hooked", False):
        original_offer = endorse.offer

        def offer_with_warning(*args, **kwargs):
            guild_id, business_id, _, character_id = args[:4]
            report = competition_report(guild_id, character_id, business_id)
            result = original_offer(*args, **kwargs)
            if report["conflicts"]:
                names = "、".join(item["business_name"] for item in report["conflicts"][:4])
                result += f"\n⚠️ 競品提醒：該藝人已有相同品類或產業代言（{names}）。請人工確認是否構成競品。"
            return result

        endorse.offer = offer_with_warning
        endorse._competition_hooked = True
    if getattr(CompanyPanel, "_endorse_history_hooked", False):
        return
    original_init = CompanyPanel.__init__

    def with_history(self, uid, gid, is_owner):
        original_init(self, uid, gid, is_owner)
        self.add_item(HistoryButton())

    CompanyPanel.__init__ = with_history
    CompanyPanel._endorse_history_hooked = True
