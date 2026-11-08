"""現有 /網路 直播中心子頁；不註冊 Slash 指令。"""
from __future__ import annotations
import asyncio
from datetime import datetime,timezone
import discord
from core import live_rankings_v11 as rank

class LiveRankView(discord.ui.View):
    def __init__(self,owner_id,character_id):
        super().__init__(timeout=300)
        self.owner_id=int(owner_id)
        self.character_id=int(character_id)
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('只能使用自己的面板。',ephemeral=True)
            return False
        return True
    @discord.ui.button(label='刷新排行',emoji='🔄',row=0)
    async def refresh(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        data=await asyncio.to_thread(rank.period_rows,interaction.guild.id)
        info=await asyncio.to_thread(rank.room,interaction.guild.id,self.character_id)
        await interaction.followup.send(format_ranking(data,info),view=LiveRankView(self.owner_id,self.character_id),ephemeral=True)
    @discord.ui.button(label='購買官方曝光',emoji='📣',row=0)
    async def promote(self,interaction,button):
        await interaction.response.send_modal(PromoteModal(self.character_id))

class PromoteModal(discord.ui.Modal,title='購買官方直播曝光'):
    coins=discord.ui.TextInput(label='支付金幣（每100金幣＋10熱度）',placeholder='例如1000',max_length=8)
    def __init__(self,character_id):
        super().__init__();self.character_id=int(character_id)
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            from core import live_stream
            current=await asyncio.to_thread(live_stream.current,interaction.guild.id,self.character_id)
            if not current: raise ValueError('只有開播中的直播主能購買曝光')
            result=await asyncio.to_thread(rank.promote,interaction.guild.id,interaction.user.id,self.character_id,current['id'],int(str(self.coins.value)),interaction.id)
            await interaction.followup.send(f"✅ 已取得 {result['heat']} 熱度。" if not result['already_done'] else '這筆曝光購買已完成，不會重複扣款。',ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('直播曝光購買失敗')
            await interaction.followup.send('⚠️ 無法完成曝光購買，請查看日誌。',ephemeral=True)

def format_ranking(data,info):
    finish=datetime.fromtimestamp(data['period_end'],timezone.utc).strftime('%m/%d %H:%M UTC')
    rows=data['rows']
    items='\n'.join(f"{i}. 角色 #{r['character_id']}｜{r['heat']:,} 熱度" for i,r in enumerate(rows[:15],1)) or '本期尚無熱度紀錄'
    return (f"🏆 **直播間熱度排行**｜每小時展示更新，120小時結算\n"
            f"本期結束：{finish}\n{items}\n\n"
            f"📊 我的直播間：LV{info['level']}｜永久熱度 {info['total_heat']:,}｜每次有效開播初始熱度 +{info['opening_bonus']}\n"
            f"🔔 直播間關注者：{info['followers']}\n"
            '⚠️ 未核准名次獎勵只封存紀錄，不會自動增加角色數值。')

class RoomFollowView(discord.ui.View):
    def __init__(self,owner_id,viewer_character_id,host_character_id):
        super().__init__(timeout=300);self.owner_id=int(owner_id)
        self.viewer_character_id=int(viewer_character_id);self.host_character_id=int(host_character_id)
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('請使用自己的 /網路 面板。',ephemeral=True)
            return False
        return True
    @discord.ui.button(label='關注直播間／開播提醒',emoji='🔔')
    async def follow(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        try:
            success=await asyncio.to_thread(rank.follow,interaction.guild.id,interaction.user.id,self.viewer_character_id,self.host_character_id)
            await interaction.followup.send('✅ 已關注直播間。' if success else '你已經關注這間直播間。',ephemeral=True)
        except (ValueError,LookupError,PermissionError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)

class BrandVisitModal(discord.ui.Modal,title='品牌代表進入直播間'):
    brand=discord.ui.TextInput(label='已核准品牌 ID',max_length=40)
    def __init__(self,session_id):
        super().__init__();self.sid=int(session_id)
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result=await asyncio.to_thread(rank.brand_visit,interaction.guild.id,interaction.user.id,
                str(self.brand.value).strip(),self.sid)
            await interaction.followup.send(f'✅ 品牌代表進場，直播熱度 +{result}。' if result else '本小時品牌代表進場熱度已計算。',ephemeral=True)
        except (ValueError,LookupError,PermissionError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('品牌代表進房失敗')
            await interaction.followup.send('⚠️ 品牌代表進房失敗，請查看日誌。',ephemeral=True)
