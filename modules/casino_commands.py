"""Discord /娛樂城（目前安全開放展示、紀錄與經核准刮刮樂）。

開獎/扣款操作使用 ephemeral Discord 介面；彩票、商品池及賭桌
在相關功能未完成交易橋接之前不提供可下注按鈕。
"""
from __future__ import annotations

import asyncio
import secrets
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection
from modules.casino_repository import init_casino_schema, list_games, list_character_rounds
from modules.casino_lottery import init_lottery_schema
from modules.casino_effects import init_effects_schema, leaderboard, queue_settled_round_effects
from modules.casino_scratch import scratch_ticket, validate_scratch
from modules.casino_service import CasinoError


def _characters(gid: int, uid: int) -> list[dict]:
    with get_connection() as conn:
        rows=conn.execute('''SELECT id,name,age FROM characters WHERE guild_id=? AND owner_id=?
           AND status IN ('active','approved') AND character_type='player' AND age>=20
           ORDER BY id LIMIT 25''', (str(gid),str(uid))).fetchall()
        return [dict(r) for r in rows]


class ScratchPicker(discord.ui.Select):
    def __init__(self, games: list[dict], character_id: int, owner_id: int):
        self.character_id,self.owner_id=character_id,owner_id
        options=[discord.SelectOption(label=g['name'][:100], value=g['code'][:100],
                    description=f"{g['ticket_price']:,} 金幣／張"[:100]) for g in games[:25]]
        super().__init__(placeholder='選擇要購買的刮刮樂',options=options,min_values=1,max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('這不是你的操作選單。',ephemeral=True);return
        await interaction.response.defer(ephemeral=True,thinking=True)
        game_code=self.values[0]
        try:
            from modules.casino_repository import get_game
            game=await asyncio.to_thread(get_game,interaction.guild_id,game_code)
            if game is None: raise CasinoError('找不到票種')
            await asyncio.to_thread(validate_scratch,game)
            key=f'ui:{interaction.id}:{secrets.token_hex(8)}'
            result=await asyncio.to_thread(scratch_ticket,guild_id=interaction.guild_id,
                      player_id=interaction.user.id,character_id=self.character_id,
                      game_code=game_code,request_key=key,payment_source='wallet')
            import json
            info=json.loads(result.get('outcome_json') or '{}')
            name=info.get('prize_name','銘謝惠顧')
            coins=int(result.get('reward_coins') or 0)
            await interaction.followup.send(
                f'🎟️ **{game["name"]}**\n結果：**{name}**\n獎金：**{coins:,} 金幣**\n票券編號：`{result["id"]}`',ephemeral=True)
            try:
                await asyncio.to_thread(queue_settled_round_effects,interaction.guild_id,int(result['id']))
            except Exception:
                # 成功付款與結算不應因派送佇列故障而誤報失敗。
                pass
        except (CasinoError,ValueError) as exc:
            await interaction.followup.send(f'無法購票：{exc}',ephemeral=True)
        except Exception:
            await interaction.followup.send('系統暫時無法完成購票，請查看交易紀錄後再試。',ephemeral=True)


class ScratchView(discord.ui.View):
    def __init__(self, games: list[dict], character_id: int, owner_id: int):
        super().__init__(timeout=180)
        self.add_item(ScratchPicker(games,character_id,owner_id))


class CharacterSelect(discord.ui.Select):
    def __init__(self, characters: list[dict], owner_id: int):
        self.owner_id=owner_id
        super().__init__(placeholder='選擇娛樂城角色（年滿20歲）',options=[
            discord.SelectOption(label=str(c['name'])[:100],value=str(c['id']),
                description=f"{c['age']} 歲"[:100]) for c in characters[:25]])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('這不是你的操作選單。',ephemeral=True);return
        cid=int(self.values[0]); gid=interaction.guild_id
        games=await asyncio.to_thread(list_games,gid,kind='scratch')
        available=[]
        from modules.casino_repository import get_game
        for g in games:
            try:
                await asyncio.to_thread(validate_scratch,g)
            except (CasinoError,ValueError,KeyError):
                continue
            available.append(g)
        view=ScratchView(available,cid,self.owner_id) if available else None
        recent=await asyncio.to_thread(list_character_rounds,gid,cid,5)
        message=f'🎰 **娛樂城｜角色 #{cid}**\n刮刮樂：{len(available)} 種可購買。\n'
        message+='其他玩法目前尚未完成安全交易接線，暫停下注。\n'
        message+=f'最近交易筆數（最多顯示五筆）：{len(recent)}'
        await interaction.response.send_message(message,view=view,ephemeral=True)


class CharacterView(discord.ui.View):
    def __init__(self, characters: list[dict], owner_id: int):
        super().__init__(timeout=180)
        self.add_item(CharacterSelect(characters,owner_id))


class CasinoCommands(commands.Cog):
    def __init__(self,bot: commands.Bot): self.bot=bot

    @app_commands.command(name='娛樂城',description='開啟角色娛樂城：刮刮樂、彩票、抽抽樂與賭場')
    @app_commands.guild_only()
    async def casino(self,interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message('請在伺服器內使用。',ephemeral=True);return
        try:
            chars=await asyncio.to_thread(_characters,interaction.guild_id,interaction.user.id)
            if not chars:
                await interaction.response.send_message('沒有年滿20歲的已啟用原創角色，無法使用娛樂城。',ephemeral=True)
                return
            await interaction.response.send_message('🎰 **娛樂城**｜請先選擇角色。',
                      view=CharacterView(chars,interaction.user.id),ephemeral=True)
        except Exception:
            await interaction.response.send_message('娛樂城暫時無法讀取角色資料。',ephemeral=True)

    @app_commands.command(name='娛樂城排行',description='查看娛樂城金幣獲利排行榜')
    @app_commands.guild_only()
    @app_commands.choices(board=[
        app_commands.Choice(name='淨獲利',value='profit'),
        app_commands.Choice(name='最高單次獎金',value='largest_win'),
        app_commands.Choice(name='累積獎金',value='total_winnings')])
    async def ranking(self,interaction: discord.Interaction,board:app_commands.Choice[str]):
        ranks=await asyncio.to_thread(leaderboard,interaction.guild_id,board.value,10)
        body='\n'.join(f'{i}. 角色 #{r["character_id"]}：{r["score"]:,} 金幣'
                       for i,r in enumerate(ranks,1)) or '目前沒有已結算的娛樂城紀錄。'
        await interaction.response.send_message(f'🏆 **娛樂城排行｜{board.name}**\n{body}',ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await asyncio.to_thread(init_casino_schema)
    await asyncio.to_thread(init_lottery_schema)
    await asyncio.to_thread(init_effects_schema)
    await bot.add_cog(CasinoCommands(bot))
