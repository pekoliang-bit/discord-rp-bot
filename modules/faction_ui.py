"""陣營系統：唯一入口 /陣營；所有操作在選單中完成。"""
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection
from modules import faction_core

def owned_characters(guild_id, user_id):
    with get_connection() as db:
        return db.execute(
            """SELECT c.id,c.name,m.faction_id
               FROM characters c LEFT JOIN faction_members m
                 ON m.guild_id=c.guild_id AND m.character_id=c.id
               WHERE c.guild_id=? AND c.owner_id=?
                 AND c.status='active' AND c.character_type='player'
               ORDER BY c.id LIMIT 25""",
            (str(guild_id),str(user_id))
        ).fetchall()

class PrivateView(discord.ui.View):
    def __init__(self,guild_id,user_id):
        super().__init__(timeout=180)
        self.guild_id=guild_id
        self.user_id=user_id

    async def interaction_check(self,interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("請自行使用 /陣營 開啟面板。",ephemeral=True)
            return False
        return True

class FactionDetail(PrivateView):
    def __init__(self,guild_id,user_id):
        super().__init__(guild_id,user_id)
        choices=[discord.SelectOption(label=name[:100],value=str(fid))
                 for fid,name,_ in faction_core.list_factions(guild_id)]
        selector=discord.ui.Select(placeholder="選擇要查看的陣營",options=choices)
        selector.callback=self.show
        self.add_item(selector)
        self.selector=selector

    async def show(self,interaction):
        info=faction_core.snapshot(self.guild_id,int(self.selector.values[0]))
        rank="\\n".join(
            f"{i}. {discord.utils.escape_markdown(name)}（#{cid}）— {points} 分"
            for i,(cid,name,points) in enumerate(info["rank"],1)
        ) or "尚無有效入池貢獻"
        await interaction.response.edit_message(
            content=f"**{discord.utils.escape_markdown(info['name'])}**\\n"
                    f"經費池：{info['pool']:,} 金幣\\n"
                    f"第 {info['period']} 期內部排行榜\\n{rank}",
            view=self)

    @discord.ui.button(label="返回陣營首頁",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,button):
        await interaction.response.edit_message(
            content=home_text(self.guild_id),view=FactionHome(self.guild_id,self.user_id))

class JoinFaction(PrivateView):
    def __init__(self,guild_id,user_id,character_id):
        super().__init__(guild_id,user_id)
        self.character_id=character_id
        choices=[discord.SelectOption(label=name[:100],value=str(fid))
                 for fid,name,_ in faction_core.list_factions(guild_id)]
        selector=discord.ui.Select(placeholder="替角色選擇初始陣營",options=choices)
        selector.callback=self.join
        self.add_item(selector)
        self.selector=selector

    async def join(self,interaction):
        try:
            faction_core.join(self.guild_id,self.character_id,int(self.selector.values[0]),self.user_id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"角色 #{self.character_id} 已加入陣營！\\n"
                    +home_text(self.guild_id),
            view=FactionHome(self.guild_id,self.user_id))

    @discord.ui.button(label="返回角色選擇",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,button):
        await interaction.response.edit_message(
            content="請選擇要加入陣營的角色：",
            view=CharacterSelection(self.guild_id,self.user_id))

class CharacterSelection(PrivateView):
    def __init__(self,guild_id,user_id):
        super().__init__(guild_id,user_id)
        rows=owned_characters(guild_id,user_id)
        if rows:
            options=[
                discord.SelectOption(
                    label=str(r["name"])[:90],
                    description=f"角色 #{r['id']}｜"+("已有陣營" if r["faction_id"] is not None else "尚未加入"),
                    value=str(r["id"]))
                for r in rows]
            select=discord.ui.Select(placeholder="選擇自己的角色",options=options)
            select.callback=self.choose
            self.add_item(select)
            self.selector=select

    async def choose(self,interaction):
        cid=int(self.selector.values[0])
        rows=owned_characters(self.guild_id,self.user_id)
        row=next((r for r in rows if int(r["id"])==cid),None)
        if row is None:
            await interaction.response.send_message("角色已無法操作，請重新開啟 /陣營。",ephemeral=True)
            return
        if row["faction_id"] is not None:
            await interaction.response.send_message(
                "此角色已有陣營；轉換或退出需經正式違約流程，目前尚未開放。",ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"替 **{discord.utils.escape_markdown(str(row['name']))}**（#{cid}）選擇陣營：",
            view=JoinFaction(self.guild_id,self.user_id,cid))

    @discord.ui.button(label="返回陣營首頁",style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction,button):
        await interaction.response.edit_message(
            content=home_text(self.guild_id),view=FactionHome(self.guild_id,self.user_id))

def home_text(guild_id):
    factions=faction_core.list_factions(guild_id)
    text="\\n".join(
        f"• **{discord.utils.escape_markdown(name)}**｜經費池 {pool:,} 金幣"
        for _,name,pool in factions)
    return ("**⚔️ 陣營中心**\\n"+text+
            "\\n\\n每個角色獨立選擇陣營。"
            "有效個人入池金額每 100 金幣 = 1 積分；違約金入池但不計分。")

class FactionHome(PrivateView):
    @discord.ui.button(label="我的角色／加入陣營",style=discord.ButtonStyle.primary,row=0)
    async def join(self,interaction,button):
        rows=owned_characters(self.guild_id,self.user_id)
        if not rows:
            await interaction.response.send_message("目前沒有已啟用的原創角色。",ephemeral=True)
            return
        await interaction.response.edit_message(
            content="選擇要加入陣營的角色（已有陣營的角色不可重複加入）：",
            view=CharacterSelection(self.guild_id,self.user_id))

    @discord.ui.button(label="陣營資訊／內部排行",style=discord.ButtonStyle.secondary,row=0)
    async def info(self,interaction,button):
        await interaction.response.edit_message(
            content="請選擇要查看的陣營：",view=FactionDetail(self.guild_id,self.user_id))

    @discord.ui.button(label="商店與經費說明",style=discord.ButtonStyle.secondary,row=1)
    async def shop(self,interaction,button):
        await interaction.response.send_message(
            "**陣營商店與經費**\\n"
            "一般商店：成員可購買個人加成與道具。\\n"
            "專屬商店：當期第一、第二名使用陣營經費，購買全員正向加成。\\n"
            "暗害與防護：以經費支付。\\n"
            "目前上述交易功能尚未啟用，避免未接銀行扣款就產生資產變動。",
            ephemeral=True)

class FactionCog(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    @app_commands.command(name="陣營",description="陣營首頁：角色加入、陣營資訊、排行與商店")
    @app_commands.guild_only()
    async def faction(self,interaction:discord.Interaction):
        await interaction.response.send_message(
            home_text(interaction.guild_id),
            view=FactionHome(interaction.guild_id,interaction.user.id),
            ephemeral=True)

async def setup(bot):
    faction_core.initialize()
    await bot.add_cog(FactionCog(bot))
