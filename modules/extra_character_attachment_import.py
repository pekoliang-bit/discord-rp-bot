"""監聽群主指定的四類照片附件頻道，不新增斜線指令。"""
from __future__ import annotations
import discord
from discord.ext import commands
from modules import extra_character_store as store
class ExtraPhotoAttachmentCog(commands.Cog):
    def __init__(self,bot):self.bot=bot
    @commands.Cog.listener()
    async def on_message(self,message):
        if not message.guild or message.author.bot or not message.attachments:return
        if message.author.id!=message.guild.owner_id:return
        cfg=store.get_photo_channel(message.guild.id,message.channel.id)
        if not cfg:return
        urls=[a.url for a in message.attachments if (a.content_type and a.content_type.lower().startswith('image/')) or a.filename.lower().endswith(('.png','.jpg','.jpeg','.webp'))]
        if not urls:return
        total=store.add_photos(message.guild.id,urls,gender=cfg['gender'],age_group=cfg['age_group'],channel_id=message.channel.id,message_id=message.id)
        if total:
            left=store.photo_inventory(message.guild.id).get((cfg['gender'],cfg['age_group']),0)
            await message.reply(f'✅ 收錄 {total} 張照片到本分類，目前剩餘 {left} 張。'+(' ⚠️ 庫存不足五張，請補充。' if left<=5 else ''),mention_author=False)
async def setup(bot):await bot.add_cog(ExtraPhotoAttachmentCog(bot))
