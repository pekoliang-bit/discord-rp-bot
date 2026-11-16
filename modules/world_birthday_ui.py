"""/官方 → 世界觀 → 生日設定：群主及 Administrator 可指定個別地點福利。

採明確地點 key，避免把整個世界的每一家場所不分種類地套同一種優惠。
已核准的生日固定禮物、期限與折扣預設值不由此頁更動。
"""
from __future__ import annotations

import discord

from core.birthday_config_v3 import settings

ALLOWED = ('餐廳','咖啡廳','一般課程','高級課程','停用')


def _is_admin(i):
    return bool(i.guild and (i.user.id==i.guild.owner_id or i.user.guild_permissions.administrator))


def summary(gid):
    values=settings(gid)
    lines=['🎂 **生日世界觀設定**','30現實日＝1遊戲年，從角色正式啟用週年起算。',
           f'固定禮物：{values["coins"]:,}金幣、{values["free_actions"]}行動值、{values["free_points"]}自由點數、{values["birthday_tickets"]}張生日券',
           f'雙商城折扣：{values["store_percent_off"]}%（{values["store_hours"]}小時）',
           f'生日行動值價：每點{values["action_price"]}金幣（{values["action_hours"]}小時）',
           f'地點福利有效：{values["location_hours"]}小時，每間每週年一次。',
           '可針對具體地點設定「餐廳／咖啡廳／一般課程／高級課程／停用」。']
    items=values.get('venue_rules',{})
    lines.extend(f'• `{key}`：{val}' for key,val in sorted(items.items())[:25])
    from core.official_birthday_gifts_v1 import load as load_gifts
    gifts=load_gifts()
    ready=sum(bool(x.get('enabled') and (x.get('gift') or {}).get('action_key')) for x in gifts.values())
    lines.append(f'🎁 官方生日資料：{len(gifts)}個地點；已具備核准行動綁定且啟用：{ready}個。')
    lines.append('官方福利讀取 assets/birthday_locations 的獨立 JSON；未綁定 action_key 的場所不會扣款或發獎。玩家店鋪不使用官方預設。')
    return '\n'.join(lines)[:1900]


class VenueBirthdayModal(discord.ui.Modal,title='設定個別地點生日福利'):
    location=discord.ui.TextInput(label='地點 key（須與 /行動 所用地點相同）',max_length=100)
    offer=discord.ui.TextInput(label='類型：餐廳／咖啡廳／一般課程／高級課程／停用',max_length=10)

    async def on_submit(self,i):
        if not _is_admin(i):
            return await i.response.send_message('沒有世界觀管理權限。',ephemeral=True)
        key=str(self.location.value).strip()
        typ=str(self.offer.value).strip()
        if not key or typ not in ALLOWED:
            return await i.response.send_message('地點鍵名不可空白；福利類型必須從五種核准選項中選擇。',ephemeral=True)
        from modules.content_builder import world_get,world_save
        world=world_get(i.guild_id)
        birthday=dict(world.get('birthday') or {})
        rules=dict(birthday.get('venue_rules') or {})
        rules[key]=typ
        birthday['venue_rules']=rules
        world['birthday']=birthday
        world_save(i.guild_id,world)
        await i.response.edit_message(content=summary(i.guild_id),view=BirthdayWorldView(i))


class BirthdayWorldView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600)
        self.uid=i.user.id
        self.gid=i.guild_id

    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not _is_admin(i):
            await i.response.send_message('只有管理員本人可調整此伺服器設定。',ephemeral=True)
            return False
        return True

    @discord.ui.button(label='🏬 設定指定地點生日福利',style=discord.ButtonStyle.primary)
    async def edit(self,i,button):
        await i.response.send_modal(VenueBirthdayModal())

    @discord.ui.button(label='🔄 重新整理',style=discord.ButtonStyle.secondary)
    async def refresh(self,i,button):
        await i.response.edit_message(content=summary(i.guild_id),view=BirthdayWorldView(i))

    @discord.ui.button(label='⬅️ 返回世界觀',style=discord.ButtonStyle.secondary)
    async def back(self,i,button):
        from modules.content_builder import WorldSettingsView,world_summary
        await i.response.edit_message(content=world_summary(i.guild_id),view=WorldSettingsView(i))
