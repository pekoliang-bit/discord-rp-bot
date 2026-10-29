"""陣營系統第一階段：查看、角色加入、內部排行榜。"""
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from modules import faction_core

class FactionJoinView(discord.ui.View):
    def __init__(self, guild_id, owner_id, character_id):
        super().__init__(timeout=180)
        self.guild_id=guild_id
        self.owner_id=owner_id
        self.character_id=character_id
        options=[
            discord.SelectOption(label=name[:100],value=str(fid))
            for fid,name,_ in faction_core.list_factions(guild_id)
        ][:25]
        picker=discord.ui.Select(placeholder="選擇角色所屬陣營",options=options)
        picker.callback=self.choose
        self.picker=picker
        self.add_item(picker)

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message("這不是你的角色選擇介面。",ephemeral=True)
            return False
        return True

    async def choose(self,interaction):
        try:
            faction_core.join(self.guild_id,self.character_id,int(self.picker.values[0]),self.owner_id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)
            return
        await interaction.response.send_message("已加入陣營！每個角色的歸屬獨立計算。",ephemeral=True)
        self.stop()

class FactionPanel(discord.ui.View):
    def __init__(self,guild_id,owner_id):
        super().__init__(timeout=180)
        self.guild_id=guild_id
        self.owner_id=owner_id
        options=[discord.SelectOption(label=name[:100],value=str(fid))
                 for fid,name,_ in faction_core.list_factions(guild_id)][:25]
        select=discord.ui.Select(placeholder="查看陣營經費與內部排行",options=options)
        select.callback=self.inspect
        self.select=select
        self.add_item(select)

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message("請自行使用 /陣營 開啟面板。",ephemeral=True)
            return False
        return True

    async def inspect(self,interaction):
        try:
            info=faction_core.snapshot(self.guild_id,int(self.select.values[0]))
            rank="\\n".join(
                f"{i}. {name}（角色 #{cid}）— {score} 分"
                for i,(cid,name,score) in enumerate(info["rank"],1)
            ) or "本期尚無有效貢獻"
            await interaction.response.send_message(
                f"**{info['name']}**\\n經費池：{info['pool']:,} 金幣\\n"
                f"第 {info['period']} 期內部排行\\n{rank}",
                ephemeral=True)
        except ValueError as exc:
            await interaction.response.send_message(str(exc),ephemeral=True)

class FactionCog(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    @app_commands.command(name="陣營",description="查看五個陣營、經費池與排行榜")
    @app_commands.guild_only()
    async def faction(self,interaction:discord.Interaction):
        factions=faction_core.list_factions(interaction.guild_id)
        text="\\n".join(f"• **{name}**｜經費 {pool:,} 金幣" for _,name,pool in factions)
        await interaction.response.send_message(
            "**陣營中心**\\n"+text+
            "\\n\\n每個角色獨立選擇陣營。內部積分只計有效入池資金，每100金幣=1分；違約金不計分。"
            "\\n目前提供查看功能；選擇角色加入請使用 `/加入陣營`。",
            view=FactionPanel(interaction.guild_id,interaction.user.id),ephemeral=True)

    @app_commands.command(name="加入陣營",description="替自己已啟用的角色選擇初始陣營")
    @app_commands.guild_only()
    @app_commands.describe(角色編號="角色的資料庫編號")
    async def join_faction(self,interaction:discord.Interaction,角色編號:int):
        # 在選單前先驗證角色，提交時由核心再次驗證。
        from core.database import get_connection
        with get_connection() as db:
            row=db.execute("""SELECT owner_id,status,character_type FROM characters
                WHERE guild_id=? AND id=?""",(str(interaction.guild_id),角色編號)).fetchone()
        if not row or str(row["owner_id"])!=str(interaction.user.id) or row["status"]!="active" or row["character_type"]!="player":
            await interaction.response.send_message("請填寫自己已啟用的原創角色編號。",ephemeral=True)
            return
        await interaction.response.send_message(
            f"角色 #{角色編號}：請選擇初始陣營。選定後不能直接重新選擇。",
            view=FactionJoinView(interaction.guild_id,interaction.user.id,角色編號),ephemeral=True)

async def setup(bot):
    faction_core.initialize()
    await bot.add_cog(FactionCog(bot))
