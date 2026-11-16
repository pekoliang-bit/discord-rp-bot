"""角色週年自動發禮及私人通知；保留獨立抽獎功能。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks

from core import birthday_core_v3 as birthday
from core.birthday_config_v3 import settings
from core.database import get_connection
from core.prelaunch_gate import is_open

LOG=logging.getLogger(__name__)
LABELS={'coins':'金幣','action_points':'行動值','free_points':'自由點數','coupon':'單筆5,000金幣折價券'}


def _status(guild_id,character_id):
    gid,cid=str(guild_id),int(character_id)
    with get_connection() as db:
        birthday.setup(db)
        info=birthday.status(db,gid,cid)
        if not info:return None
        cycle=info['cycle']
        claimed=db.execute('SELECT 1 FROM rp_birthday_claims_v3 WHERE guild_id=? AND character_id=? AND cycle=?',
                           (gid,cid,cycle)).fetchone() is not None
        ticket=db.execute('SELECT total FROM rp_birthday_tickets_v3 WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()
        coupons=db.execute('SELECT COUNT(*) FROM rp_birthday_coupons_v3 WHERE guild_id=? AND character_id=? AND consumed_at IS NULL',(gid,cid)).fetchone()[0]
        credit=db.execute('''SELECT SUM(remaining_sessions) FROM rp_birthday_location_bundles_v3
            WHERE guild_id=? AND character_id=?''',(gid,cid)).fetchone()[0] if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_birthday_location_bundles_v3'").fetchone() else 0
        return dict(info,claimed=claimed,tickets=int(ticket['total']) if ticket else 0,
                    coupons=int(coupons),credits=int(credit or 0))


class BirthdayPanel(discord.ui.View):
    def __init__(self,interaction,character_id):
        super().__init__(timeout=600)
        self.guild_id=str(interaction.guild_id)
        self.user_id=int(interaction.user.id)
        self.character_id=int(character_id)

    async def interaction_check(self,i):
        if str(i.guild_id)!=self.guild_id or i.user.id!=self.user_id:
            await i.response.send_message('這不是你的生日福利面板。',ephemeral=True)
            return False
        return True

    def text(self):
        data=_status(self.guild_id,self.character_id)
        if not data:
            return '🎂 **系統生日福利**\n目前還沒有正式的角色啟用週年基準。舊角色請聯絡管理員核對啟用時間。'
        cfg=settings(self.guild_id)
        next_time=data['next_at'].strftime('%Y-%m-%d %H:%M UTC')
        lines=[f'🎂 **角色 #{self.character_id}｜系統生日福利**',
               '角色正式啟用後，每30個現實日算一個遊戲年；與角色資料上的生日分開。',
               f'系統週年：第 {data["cycle"]} 年｜下次週年：{next_time}',
               f'本週年禮包：{"已領取" if data["claimed"] else "尚未領取"}',
               f'生日抽獎券：{data["tickets"]}張｜未使用5,000金幣券：{data["coupons"]}張',
               f'場所預付課程／餐點剩餘：{data["credits"]}次']
        if data['cycle']>=1:
            lines.extend([
               f'固定禮包：{cfg["coins"]:,}金幣、{cfg["free_actions"]}行動值、{cfg["free_points"]}自由點數及{cfg["birthday_tickets"]}張生日抽獎券',
               f'雙商城9折：{"有效" if data["birthday_discount"] else "本週年已結束"}（72小時）',
               f'行動值每點350金幣：{"有效" if data["action_discount"] else "本週年已結束"}（24小時）',
               '地點優惠在週年前72小時內，可於對應地點的 /行動 使用；每間每週年一次。'])
        else:
            lines.append('首次系統週年滿30現實日才有禮包。')
        return '\n'.join(lines)[:1900]

    @discord.ui.button(label='🎁 領取本週年禮包',style=discord.ButtonStyle.success,row=0)
    async def claim(self,i,button):
        try:
            result=await asyncio.to_thread(birthday.claim,self.guild_id,self.character_id,self.user_id)
        except (ValueError,PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        if result['status']=='already':
            return await i.response.send_message('本週年已領取過，無法重複領取。',ephemeral=True)
        await i.response.edit_message(content=self.text()+'\n✅ 本週年固定禮物已入帳。',view=self)

    @discord.ui.button(label='🎰 使用生日抽獎券',style=discord.ButtonStyle.primary,row=0)
    async def draw(self,i,button):
        try:
            result=await asyncio.to_thread(birthday.draw,self.guild_id,self.character_id,self.user_id,f'discord:birthday:{i.id}')
        except (ValueError,PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        label=LABELS.get(result['kind'],result['kind'])
        result_text=f'🎉 抽中：{label}'+(f' ×{result["amount"]}' if result['kind']!='coupon' else '')
        await i.response.edit_message(content=self.text()+f'\n{result_text}',view=self)

    @discord.ui.button(label='🏷️ 下筆購物啟用5,000折價券',style=discord.ButtonStyle.secondary,row=1)
    async def coupon(self,i,button):
        try:
            await asyncio.to_thread(birthday.arm_coupon,self.guild_id,self.character_id,self.user_id)
        except (ValueError,PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.edit_message(content=self.text()+'\n✅ 已啟用單筆5,000金幣券；實際結帳時與其他折扣比較，只採最優惠者。',view=self)

    @discord.ui.button(label='🔄 重新整理',style=discord.ButtonStyle.secondary,row=1)
    async def refresh(self,i,button):
        await i.response.edit_message(content=self.text(),view=self)


async def show_birthday_panel(interaction,character_id):
    """僅供 /查看 的本人角色按鈕使用；不取代原有角色面板。"""
    with get_connection() as db:
        character=db.execute("SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?",
                             (str(interaction.guild_id),int(character_id))).fetchone()
    if not character or str(character['owner_id'])!=str(interaction.user.id) or character['status']!='active':
        return await interaction.response.send_message('只能查看自己已啟用角色的生日福利。',ephemeral=True)
    view=BirthdayPanel(interaction,character_id)
    await interaction.response.send_message(view.text(),view=view,ephemeral=True)


class BirthdayAnniversaryNotices(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self.worker.start()

    def cog_unload(self):
        self.worker.cancel()

    @tasks.loop(hours=4)
    async def worker(self):
        try:
            with get_connection() as db:
                birthday.setup(db)
                rows=[dict(r) for r in db.execute('''SELECT a.guild_id,a.character_id,c.owner_id
                    FROM rp_character_anniversaries_v3 a JOIN characters c
                    ON c.guild_id=a.guild_id AND c.id=a.character_id
                    WHERE c.status='active' ''')]
            from modules.notifications import queue_notification
            for r in rows:
                gid,cid=r['guild_id'],r['character_id']
                from core.beta_sandbox_v1 import is_beta as is_test_character
                with get_connection() as _db:
                    if is_test_character(_db,gid,cid):
                        continue
                if not is_open(gid):continue
                with get_connection() as db:
                    birthday.setup(db)
                    info=birthday.status(db,gid,cid)
                if not info or info['cycle']<1:continue
                try:
                    # Claim is unique per (guild, character, anniversary).  It is
                    # safe on worker retries, and does not back-pay every prior year.
                    result = await asyncio.to_thread(
                        birthday.claim, gid, cid, r['owner_id']
                    )
                    # Always retry the idempotent notice after a successful
                    # (or previously completed) claim.  Queue failure must not
                    # permanently lose the notice after funds have been granted.
                    cfg=settings(gid)
                    await asyncio.to_thread(
                        queue_notification, gid, 'private', 'role',
                        f'🎂 生日禮包已自動發放：{cfg["coins"]:,}金幣、'
                        f'{cfg["free_actions"]}行動值、{cfg["free_points"]}自由點數、'
                        f'{cfg["birthday_tickets"]}張生日抽獎券。'
                        '雙商城9折72小時與行動值每點350金幣24小時，'
                        '均自週年到期時刻起算；各地點優惠72小時。',
                        character_id=cid, priority='high',
                        dedupe_key=f'birthday:v3:{gid}:{cid}:{info["cycle"]}'
                    )
                except Exception:
                    LOG.exception('角色生日週年通知失敗：%s/%s',gid,cid)
        except Exception:
            LOG.exception('角色週年通知排程失敗')

    @worker.before_loop
    async def before_worker(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    if not bot.get_cog('BirthdayAnniversaryNotices'):
        await bot.add_cog(BirthdayAnniversaryNotices(bot))
