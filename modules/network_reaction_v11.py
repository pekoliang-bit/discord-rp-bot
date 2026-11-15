"""Discord reaction events and explicit withdrawal button for forum posts."""
from __future__ import annotations
import asyncio
import logging
import discord
from discord.ext import commands
from core import network_reaction_rewards_v11 as rewards
LOG=logging.getLogger(__name__)

class WithdrawModal(discord.ui.Modal,title='撤回貼文表符'):
    emoji=discord.ui.TextInput(label='要撤回的表符',placeholder='例如 ❤️',max_length=100)
    reactor=discord.ui.TextInput(label='原本按表符的 Discord 使用者 ID（可留空）',required=False,max_length=25)
    def __init__(self,thread_id):super().__init__();self.thread_id=thread_id
    async def on_submit(self,i):
        if not i.guild:return await i.response.send_message('僅限伺服器使用',ephemeral=True)
        uid=str(self.reactor.value or '').strip() or str(i.user.id)
        if not uid.isdecimal():return await i.response.send_message('使用者 ID 必須為數字',ephemeral=True)
        is_moderator=bool(i.user.id==i.guild.owner_id or i.user.guild_permissions.administrator or i.user.guild_permissions.manage_messages)
        if uid!=str(i.user.id) and not is_moderator:
            return await i.response.send_message('只有管理員能撤回其他玩家的表符。',ephemeral=True)
        try:
            result=await asyncio.to_thread(rewards.withdraw,i.guild_id,self.thread_id,uid,self.emoji.value,
                  actor_id=i.user.id,penalty=True,authorized=is_moderator)
            if not result:return await i.response.send_message('這個表符沒有可撤回的有效紀錄。',ephemeral=True)
            note=''
            try:
                msg=await i.channel.fetch_message(self.thread_id)
                member=i.guild.get_member(int(uid)) or await i.guild.fetch_member(int(uid))
                await msg.remove_reaction(str(self.emoji.value),member)
            except (discord.HTTPException,AttributeError):
                note='\n⚠️ 數值已撤回，但 Discord 表符未能同步移除，請管理員確認頻道權限。'
            await i.response.send_message('✅ 已撤回原表符數值。'+('額外反應：'+str(result['penalty']) if result['penalty'] else '此次未觸發額外扣點。')+note,ephemeral=True)
        except (ValueError,PermissionError) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class WithdrawReactionView(discord.ui.View):
    def __init__(self):super().__init__(timeout=None)
    @discord.ui.button(label='撤回我的表符',emoji='↩️',style=discord.ButtonStyle.secondary,custom_id='v11:network:withdraw')
    async def withdraw(self,i,b):
        if not isinstance(i.channel,discord.Thread):return await i.response.send_message('只能在網路論壇貼文裡使用。',ephemeral=True)
        await i.response.send_modal(WithdrawModal(i.channel.id))

class NetworkReactions(commands.Cog):
    def __init__(self,bot):self.bot=bot
    @commands.Cog.listener()
    async def on_raw_reaction_add(self,p):
        if not p.guild_id or (self.bot.user and p.user_id==self.bot.user.id):return
        try: await asyncio.to_thread(rewards.add,p.guild_id,p.message_id,p.user_id,str(p.emoji))
        except Exception:LOG.exception('network reaction settlement failed')
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self,p):
        if not p.guild_id:return
        try:
            # Cannot identify who removed a reaction through this event.
            # Only reverse the recorded benefit; never punish an innocent reactor.
            await asyncio.to_thread(rewards.withdraw,p.guild_id,p.message_id,p.user_id,str(p.emoji),actor_id=p.user_id,penalty=False)
        except Exception:LOG.exception('network reaction rollback failed')

async def setup(bot):
    bot.add_view(WithdrawReactionView())
    await bot.add_cog(NetworkReactions(bot))
