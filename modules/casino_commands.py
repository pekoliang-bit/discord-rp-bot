"""Discord /娛樂城（目前安全開放展示、紀錄與經核准刮刮樂）。

開獎/扣款操作使用 ephemeral Discord 介面；彩票、商品池及賭桌
在相關功能未完成交易橋接之前不提供可下注按鈕。
"""
from __future__ import annotations

import asyncio
import secrets
import json
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection
from modules.casino_repository import init_casino_schema, list_games, list_character_rounds
from modules.casino_lottery import init_lottery_schema
from modules.casino_effects import init_effects_schema, leaderboard, queue_settled_round_effects
from modules.casino_scratch import scratch_ticket, validate_scratch
from modules.casino_service import CasinoError
from modules.casino_gacha import validate_gacha, draw_gacha, draw_ten
from modules.casino_lottery import buy_lottery, _settings as validate_lottery


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


class GachaPicker(discord.ui.Select):
    def __init__(self, games: list[dict], cid: int, uid: int):
        self.cid, self.uid = cid, uid
        super().__init__(placeholder='選擇抽抽樂獎池', options=[
            discord.SelectOption(label=g['name'][:100], value=g['code'],
                description=f"{g['ticket_price']:,} 金幣／抽") for g in games[:25]])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('這不是你的操作選單。', ephemeral=True)
            return
        from modules.casino_repository import get_game
        game = await asyncio.to_thread(get_game, interaction.guild_id, self.values[0])
        if not game:
            await interaction.response.send_message('獎池已不存在。', ephemeral=True)
            return
        try:
            spec = await asyncio.to_thread(validate_gacha, game)
        except (CasinoError, ValueError) as exc:
            await interaction.response.send_message(f'獎池尚未開放：{exc}', ephemeral=True)
            return
        await interaction.response.send_message(
            f"🎁 **{game['name']}**｜每抽 {game['ticket_price']:,} 金幣\n"
            '金幣／商品單抽可用；十連抽只支援純金幣獎池。',
            view=GachaActionView(game['code'],self.cid,self.uid,not spec['has_items']), ephemeral=True)


class GachaActionView(discord.ui.View):
    def __init__(self, code: str, cid: int, uid: int, allow_ten: bool):
        super().__init__(timeout=180)
        self.code,self.cid,self.uid=code,cid,uid
        self.ten.disabled=not allow_ten

    async def _run(self, interaction:discord.Interaction, is_ten:bool):
        if interaction.user.id!=self.uid:
            await interaction.response.send_message('這不是你的操作選單。',ephemeral=True)
            return
        # 同一互動 ID 重試必須取得原交易結果，不可重新扣款。
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            key=f'casino-ui:{interaction.id}'
            if is_ten:
                results=await asyncio.to_thread(draw_ten,guild_id=interaction.guild_id,
                    player_id=interaction.user.id,character_id=self.cid,
                    game_code=self.code,batch_key=key,payment_source='wallet')
                descriptions=[]
                for r in results:
                    out=json.loads(r.get('outcome_json') or '{}')
                    descriptions.append(f"{out.get('rarity','?')}｜{int(r.get('reward_coins') or 0):,} 金幣")
                message='🎁 **十連抽結果**\n'+'\n'.join(descriptions)
            else:
                result=await asyncio.to_thread(draw_gacha,guild_id=interaction.guild_id,
                    player_id=interaction.user.id,character_id=self.cid,
                    game_code=self.code,request_key=key,payment_source='wallet')
                out=json.loads(result.get('outcome_json') or '{}')
                prize=(f"官方商城商品 #{out['item_id']} ×{out.get('quantity',1)}" if out.get('type')=='official_shop_item'
                      else f"{int(result.get('reward_coins') or 0):,} 金幣")
                message=f"🎁 **單抽結果**｜{out.get('rarity','?')}\n獎品：{prize}\n紀錄：`{result['id']}`"
                try:await asyncio.to_thread(queue_settled_round_effects,interaction.guild_id,int(result['id']))
                except Exception:pass
            await interaction.followup.send(message,ephemeral=True)
        except (CasinoError,ValueError) as exc:
            await interaction.followup.send(f'抽獎未完成：{exc}',ephemeral=True)
        except Exception:
            await interaction.followup.send('抽獎流程發生異常，請先確認交易紀錄，避免重複購買。',ephemeral=True)

    @discord.ui.button(label='單抽',style=discord.ButtonStyle.primary)
    async def single(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._run(interaction,False)

    @discord.ui.button(label='十連抽',style=discord.ButtonStyle.secondary)
    async def ten(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._run(interaction,True)


class GachaView(discord.ui.View):
    def __init__(self,games:list[dict],cid:int,uid:int):
        super().__init__(timeout=180)
        self.add_item(GachaPicker(games,cid,uid))


class LotteryPicker(discord.ui.Select):
    def __init__(self, draws:list[dict],cid:int,uid:int):
        self.cid,self.uid,self.draws=cid,uid,{str(d['id']):d for d in draws[:25]}
        super().__init__(placeholder='選擇尚在販售的彩票期別（電腦選號）', options=[
            discord.SelectOption(label=f"{d['name']}｜{d['draw_code']}"[:100],value=str(d['id']),
                description=f"{d['ticket_price']:,} 金幣／注"[:100]) for d in draws[:25]])

    async def callback(self,interaction:discord.Interaction):
        if interaction.user.id!=self.uid:
            await interaction.response.send_message('這不是你的操作選單。',ephemeral=True)
            return
        d=self.draws[self.values[0]]
        await interaction.response.send_message(f"🎟️ {d['name']} 第 {d['draw_code']} 期\n購買一注電腦選號，票價 {d['ticket_price']:,} 金幣。",
            view=LotteryBuyView(d,self.cid,self.uid),ephemeral=True)


class LotteryBuyView(discord.ui.View):
    def __init__(self,draw:dict,cid:int,uid:int):
        super().__init__(timeout=180)
        self.draw,self.cid,self.uid=draw,cid,uid

    @discord.ui.button(label='確認購買一注',style=discord.ButtonStyle.primary)
    async def buy(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.user.id!=self.uid:
            await interaction.response.send_message('這不是你的操作選單。',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            d=self.draw
            info=await asyncio.to_thread(buy_lottery,guild_id=interaction.guild_id,
                player_id=interaction.user.id,character_id=self.cid,game_code=d['code'],
                draw_id=int(d['id']),request_key=f'casino-ui:{interaction.id}',numbers=None,payment_source='wallet')
            await interaction.followup.send(f"🎟️ 購票成功！\n票號：`{info['round_id']}`\n選號：**{', '.join(map(str,info['numbers']))}**\n期別：{d['draw_code']}",ephemeral=True)
        except (CasinoError,ValueError) as exc:
            await interaction.followup.send(f'購票未完成：{exc}',ephemeral=True)
        except Exception:
            await interaction.followup.send('購票過程發生異常，請先確認交易紀錄。',ephemeral=True)


class LotteryView(discord.ui.View):
    def __init__(self,draws:list[dict],cid:int,uid:int):
        super().__init__(timeout=180)
        self.add_item(LotteryPicker(draws,cid,uid))


def _open_lottery_draws(gid:int)->list[dict]:
    with get_connection() as conn:
        rows=conn.execute('''SELECT d.id,d.draw_code,d.cutoff_at,g.code,g.name,g.ticket_price,
            g.kind,g.funding_mode,g.settings_json FROM casino_lottery_draws d
            JOIN casino_games g ON g.id=d.game_id AND g.guild_id=d.guild_id
            WHERE d.guild_id=? AND g.enabled=1 AND d.state='selling' ORDER BY d.cutoff_at LIMIT 100''',(str(gid),)).fetchall()
    found=[]
    for row in rows:
        d=dict(row)
        try:
            validate_lottery(d)
            if datetime.fromisoformat(d['cutoff_at'].replace('Z','+00:00')).astimezone(timezone.utc)>datetime.now(timezone.utc):
                found.append(d)
        except (CasinoError,ValueError,TypeError,KeyError):continue
    return found[:25]


class CasinoGameMenu(discord.ui.View):
    def __init__(self, cid:int, uid:int):
        super().__init__(timeout=180)
        self.cid,self.uid=cid,uid

    async def _open(self,interaction:discord.Interaction,kind:str):
        if interaction.user.id!=self.uid:
            await interaction.response.send_message('這不是你的操作選單。',ephemeral=True)
            return
        if kind=='lottery':
            draws=await asyncio.to_thread(_open_lottery_draws,interaction.guild_id)
            await interaction.response.send_message('🎟️ **彩票**｜請選擇已核准的開售期別。' if draws else '目前沒有可購買的彩票期別。',
                view=LotteryView(draws,self.cid,self.uid) if draws else None,ephemeral=True)
            return
        games=await asyncio.to_thread(list_games,interaction.guild_id,kind=kind)
        valid=[]
        for g in games:
            try:
                await asyncio.to_thread(validate_scratch if kind=='scratch' else validate_gacha,g)
                valid.append(g)
            except (CasinoError,ValueError,KeyError):continue
        view=(ScratchView(valid,self.cid,self.uid) if kind=='scratch' else GachaView(valid,self.cid,self.uid)) if valid else None
        await interaction.response.send_message(f"{'刮刮樂' if kind=='scratch' else '抽抽樂'}：{len(valid)} 種已核准可選。" if valid else '目前沒有已核准開放的獎池。',view=view,ephemeral=True)

    @discord.ui.button(label='刮刮樂',style=discord.ButtonStyle.primary)
    async def scratch(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._open(interaction,'scratch')

    @discord.ui.button(label='彩票',style=discord.ButtonStyle.secondary)
    async def lottery(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._open(interaction,'lottery')

    @discord.ui.button(label='抽抽樂',style=discord.ButtonStyle.success)
    async def gacha(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._open(interaction,'gacha')


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
        recent=await asyncio.to_thread(list_character_rounds,gid,cid,5)
        await interaction.response.send_message(
            f'🎰 **娛樂城｜角色 #{cid}**\n最近交易：{len(recent)} 筆（最多五筆）\n'
            '目前可選刮刮樂、彩票及抽抽樂；未完成金流整合的賭桌暫停開放。',
            view=CasinoGameMenu(cid,self.owner_id),ephemeral=True)


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
