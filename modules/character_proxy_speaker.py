"""Tupperbox 風格角色發言：於有 Webhook 權限的文字頻道輸入 [#角色ID] 訊息。

未啟用 Message Content Intent 時，仍支援「@機器人 [#角色ID] 訊息」。
永遠驗證角色與操作玩家在同一伺服器、狀態為 active；不使用代理帳號冒充別人。
"""
from __future__ import annotations
import logging
import re
import discord
from discord.ext import commands
from core.database import get_connection, transaction
from core.character_proxy_identity import initialize, eligible, display

LOG=logging.getLogger(__name__)
PATTERN=re.compile(r'^\[#(\d+)\]\s+([\s\S]{1,1800})$')
WEBHOOK_NAME='Discord RP 角色代理'


class CharacterProxySpeaker(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    async def cog_load(self):
        # 補上舊角色的代理身分；僅新增缺少的紀錄，不改角色設定或錢包。
        with transaction() as db:
            initialize(db)
            rows=db.execute("SELECT guild_id,id FROM characters WHERE status='active'").fetchall()
            from core.character_proxy_identity import activate
            for row in rows:
                activate(db,row['guild_id'],row['id'])

    @commands.Cog.listener()
    async def on_message(self,message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id:
            return
        text=message.content or ''
        # 無 Message Content Intent 時，Discord 仍會提供明確提及本機器人的訊息內容。
        bot_user=self.bot.user
        if bot_user is not None:
            text=re.sub(r'^<@!?' + re.escape(str(bot_user.id)) + r'>\s*', '', text)
        match=PATTERN.fullmatch(text)
        if not match:
            return
        cid=int(match.group(1))
        if cid<=0:
            return
        content=match.group(2).strip()
        if not content:
            return
        channel=message.channel
        if not isinstance(channel,(discord.TextChannel,discord.Thread)):
            return
        # Discord permissions are checked on the destination (thread parent for webhooks).
        guild=message.guild
        me=guild.me
        target=channel.parent if isinstance(channel,discord.Thread) else channel
        if me is None or target is None or not target.permissions_for(me).manage_webhooks:
            return
        if not channel.permissions_for(message.author).send_messages:
            return
        with get_connection() as db:
            row=eligible(db,guild.id,cid,message.author.id)
            if not row:
                return
            existing=db.execute('SELECT 1 FROM rp_character_proxy_messages WHERE guild_id=? AND message_id=?',
                                (str(guild.id),str(message.id))).fetchone()
            if existing:
                return
            name,avatar=display(row)
        with transaction() as db:
            initialize(db)
            claim=db.execute('INSERT OR IGNORE INTO rp_character_proxy_messages(guild_id,message_id,character_id) VALUES(?,?,?)',
                             (str(guild.id),str(message.id),cid))
            if claim.rowcount == 0:
                return
        try:
            hooks=await target.webhooks()
            webhook=next((h for h in hooks if h.name==WEBHOOK_NAME and h.user and h.user.id==me.id),None)
            if webhook is None:
                webhook=await target.create_webhook(name=WEBHOOK_NAME,reason='角色代理發言')
            kwargs={'content':content,'username':name,'allowed_mentions':discord.AllowedMentions.none(),
                    'wait':True}
            if avatar.startswith(('https://','http://')):
                kwargs['avatar_url']=avatar
            if isinstance(channel,discord.Thread):
                kwargs['thread']=channel
            await webhook.send(**kwargs)
            try:
                await message.delete()
            except discord.HTTPException:
                LOG.warning('角色代理發言成功但無法刪除原訊息 %s',message.id)
        except discord.HTTPException:
            with transaction() as db:
                db.execute('DELETE FROM rp_character_proxy_messages WHERE guild_id=? AND message_id=?',
                           (str(guild.id),str(message.id)))
            LOG.exception('角色代理發言失敗 guild=%s character=%s',guild.id,cid)
            try:
                await message.reply('⚠️ 角色代理發言失敗，請確認機器人有管理 Webhook 權限。',
                                    mention_author=False,delete_after=15)
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(CharacterProxySpeaker(bot))
