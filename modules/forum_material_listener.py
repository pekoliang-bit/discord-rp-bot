"""18個指定論壇帖子的留言收件；僅群主和 Administrator 可匯入。"""
from __future__ import annotations
import asyncio
import logging
import discord
from discord.ext import commands
from core import forum_material_ingest_v1 as inbox

LOG=logging.getLogger(__name__)
class ForumMaterialListener(commands.Cog):
    def __init__(self,bot):self.bot=bot

    @commands.Cog.listener()
    async def on_message(self,message:discord.Message):
        if not message.guild or message.author.bot:return
        if str(message.guild.id)!=inbox.GUILD_ID:return
        kind=inbox.THREADS.get(str(message.channel.id))
        if kind is None:return
        member=message.author
        if member.id!=message.guild.owner_id and not getattr(member.guild_permissions,'administrator',False):return
        urls=[a.url for a in message.attachments
              if (a.content_type or '').lower().startswith('image/') or
              a.filename.lower().endswith(('.png','.jpeg','.jpg','.webp'))]
        try:
            payload=await asyncio.to_thread(inbox.parse,message.content,kind,urls)
            state,detail=await asyncio.to_thread(inbox.commit_material,message.guild.id,message.id,
                                  message.channel.id,member.id,kind,payload)
            if state=='duplicate':return
            await message.reply(('✅ ' if state in ('imported','draft') else '📥 ')+detail,
                                mention_author=False,allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            LOG.warning('素材收件失敗 guild=%s thread=%s message=%s: %s',message.guild.id,
                        message.channel.id,message.id,exc,exc_info=True)
            await message.reply('⚠️ 未入庫：'+str(exc)[:1500],mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none())

async def setup(bot):
    await asyncio.to_thread(inbox.setup)
    await bot.add_cog(ForumMaterialListener(bot))
