"""/官方 → 世界觀: editable per-guild pet and network emoji rules."""
from __future__ import annotations
import json
import discord
from core.world_interaction_rules import (PET_DEFAULT,REACTION_DEFAULT,pet_rules,reaction_rules,
    validate_pet,validate_reactions)


def parent():
    from modules import content_builder
    return content_builder

def is_manager(i):
    return bool(i.guild and (i.user.id==i.guild.owner_id or
        getattr(getattr(i.user,'guild_permissions',None),'administrator',False)))

def save(gid,key,values):
    p=parent();data=p.world_get(gid);data[key]=values
    data.setdefault('overrides',{})[key]=True;p.world_save(gid,data)

def reaction_summary(gid):
    r=reaction_rules(gid)
    labels={'fans_up':'粉絲增加','fans_down':'粉絲減少','heat_up':'流量增加',
        'heat_down':'流量減少','haters_up':'黑粉增加','haters_down':'黑粉減少',
        'favor_up':'路人緣增加','favor_down':'路人緣減少'}
    return ('😀 **世界觀｜網路表符**\n每次有效反應隨機增減 '
        f'{r["random_min"]}～{r["random_max"]}；未分類表符固定流量+1。\n'
        f'撤回後取消原效果，撤回人另有 {r["withdraw_penalty_chance"]}% 機率受 '
        f'{r["withdraw_penalty_min"]}～{r["withdraw_penalty_max"]} 點懲罰。\n\n'+
        '\n'.join(f'**{label}**：{r[key] or "（空）"}' for key,label in labels.items()))[:1900]

def pet_summary(gid):
    r=pet_rules(gid)
    return ('🐾 **世界觀｜寵物設定**\n'
        f'健康上限 {r["health_max"]}，依賴上限 {r["bond_max"]}\n'
        f'每日健康 -{r["health_daily_loss"]}、依賴 -{r["bond_daily_loss"]}；'
        f'每12小時未玩樂額外 -{r["bond_inactive_12h_loss"]}\n'
        f'每日主人屬性獎勵機率 {r["daily_bonus_chance"]}%；'
        f'獎勵幅度 {r["daily_bonus_min"]}～{r["daily_bonus_max"]}\n'
        f'寵物等級：{"啟用" if r["pet_levels_enabled"] else "關閉（本服預設）"}\n'
        '可透過下方選單調整健康、依賴、疾病、走失、醫療和物種價格；每伺服器獨立。')

class Guard(discord.ui.View):
    def __init__(self,i):super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not is_manager(i):
            await i.response.send_message('只有本群管理員可修改。',ephemeral=True);return False
        return True

class ReactionEditModal(discord.ui.Modal,title='網路表符｜批量設定'):
    def __init__(self,gid,field):
        super().__init__();self.field=field
        labels={'fans_up':'粉絲增加','fans_down':'粉絲減少','heat_up':'流量增加','heat_down':'流量減少',
         'haters_up':'黑粉增加','haters_down':'黑粉減少','favor_up':'路人緣增加','favor_down':'路人緣減少'}
        self.input=discord.ui.TextInput(label=labels[field]+'（整排表符）',style=discord.TextStyle.paragraph,
            default=reaction_rules(gid)[field],required=False,max_length=1000)
        self.add_item(self.input)
    async def on_submit(self,i):
        if not is_manager(i):return await i.response.send_message('沒有管理權限',ephemeral=True)
        data=reaction_rules(i.guild_id);data[self.field]=str(self.input.value or '').strip()
        try:save(i.guild_id,'reactions',validate_reactions(data))
        except ValueError as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 已保存表符分類。',ephemeral=True)

class ReactionRangeModal(discord.ui.Modal,title='社群表符｜隨機與撤回規則'):
    lo=discord.ui.TextInput(label='普通表符隨機最小值',default='1',max_length=6)
    hi=discord.ui.TextInput(label='普通表符隨機最大值',default='10',max_length=6)
    chance=discord.ui.TextInput(label='撤回操作人額外懲罰機率 %',default='20',max_length=3)
    penalty=discord.ui.TextInput(label='懲罰最小與最大值（如1,3）',default='1,3',max_length=20)
    async def on_submit(self,i):
        if not is_manager(i):return await i.response.send_message('沒有管理權限',ephemeral=True)
        try:
            a,b=[int(x.strip()) for x in str(self.penalty).replace('，',',').split(',')]
            data=reaction_rules(i.guild_id);data.update(random_min=int(self.lo.value),random_max=int(self.hi.value),
                withdraw_penalty_chance=int(self.chance.value),withdraw_penalty_min=a,withdraw_penalty_max=b)
            save(i.guild_id,'reactions',validate_reactions(data))
        except (ValueError,TypeError) as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 數值範圍更新成功。',ephemeral=True)

class ReactionWorldView(Guard):
    def __init__(self,i):
        super().__init__(i)
        labels={'fans_up':'粉絲增加','fans_down':'粉絲減少','heat_up':'流量增加','heat_down':'流量減少',
         'haters_up':'黑粉增加','haters_down':'黑粉減少','favor_up':'路人緣增加','favor_down':'路人緣減少'}
        sel=discord.ui.Select(placeholder='選擇要批量設定的表符分類',options=[discord.SelectOption(label=x,value=k) for k,x in labels.items()],row=0)
        async def cb(interaction):await interaction.response.send_modal(ReactionEditModal(interaction.guild_id,sel.values[0]))
        sel.callback=cb;self.add_item(sel)
    @discord.ui.button(label='🎲 隨機範圍／撤回風險',style=discord.ButtonStyle.primary,row=1)
    async def chance(self,i,b):await i.response.send_modal(ReactionRangeModal())
    @discord.ui.button(label='🔄 查看最新設定',style=discord.ButtonStyle.secondary,row=1)
    async def refresh(self,i,b):await i.response.edit_message(content=reaction_summary(i.guild_id),view=ReactionWorldView(i))
    @discord.ui.button(label='◀️ 返回世界觀',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):await i.response.edit_message(content=parent().world_summary(i.guild_id),view=parent().WorldSettingsView(i))

PET_FIELDS={
 'health_max':'健康上限','bond_max':'依賴上限','health_daily_loss':'每日健康扣除',
 'bond_daily_loss':'每日依賴扣除','bond_inactive_12h_loss':'12小時未玩樂扣除',
 'sick_health_loss_min':'生病傷害最小','sick_health_loss_max':'生病傷害最大',
 'injured_health_loss_min':'受傷傷害最小','injured_health_loss_max':'受傷傷害最大',
 'untreated_24h_health_loss':'未治療每24小時扣除','sick_daily_bond_loss':'生病依賴每日扣除',
 'wrong_food_health_loss':'錯誤飼料扣除','play_bond_gain':'單次玩樂依賴增加',
 'daily_bonus_chance':'每日主人獎勵機率%','daily_bonus_min':'主人加成最少點數',
 'daily_bonus_max':'主人加成最多點數','walk_escape_chance':'散步逃跑機率%',
 'critical_hours':'健康歸零救治倒數(小時)','pet_level_max':'寵物最高等級',
 'missing_risk_low':'依賴50至99逃跑機率%','missing_risk_mid':'依賴1至49逃跑機率%',
 'missing_risk_high':'依賴0逃跑機率%',
 'disease_daily_chance':'每日生病機率%','injury_daily_chance':'每日受傷機率%',
 'encounter_after_action_chance':'行動完成後遇見動物機率%',
}
class PetNumberModal(discord.ui.Modal,title='世界觀｜修改寵物規則'):
    def __init__(self,gid,key):
        super().__init__();self.key=key
        self.number=discord.ui.TextInput(label=PET_FIELDS[key],default=str(pet_rules(gid)[key]),max_length=8)
        self.add_item(self.number)
    async def on_submit(self,i):
        if not is_manager(i):return await i.response.send_message('沒有管理權限',ephemeral=True)
        try:
            data=pet_rules(i.guild_id);data[self.key]=int(self.number.value)
            save(i.guild_id,'pets',validate_pet(data))
        except (ValueError,TypeError) as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 寵物規則更新成功。',ephemeral=True)
class PetSpeciesModal(discord.ui.Modal,title='寵物品種與價格'):
    name=discord.ui.TextInput(label='物種／品種名稱',max_length=50)
    price=discord.ui.TextInput(label='價格（金幣非負整數）',max_length=12)
    async def on_submit(self,i):
        if not is_manager(i):return await i.response.send_message('沒有管理權限',ephemeral=True)
        try:
            n=self.name.value.strip();p=int(self.price.value)
            if not n or not 0<=p<=10**10:raise ValueError('名稱或價格不合法')
            data=pet_rules(i.guild_id);data['species'][n]=p;save(i.guild_id,'pets',validate_pet(data))
        except ValueError as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 已更新物種價格。',ephemeral=True)

class PetPriceModal(discord.ui.Modal,title='世界觀｜設定寵物價格'):
    name=discord.ui.TextInput(label='商品或稀有度代碼',max_length=50)
    price=discord.ui.TextInput(label='價格（金幣）或稀有度倍率（百分比）',max_length=12)
    def __init__(self,group):super().__init__();self.group=group
    async def on_submit(self,i):
        if not is_manager(i):return await i.response.send_message('沒有管理權限',ephemeral=True)
        try:
            n=self.name.value.strip();p=int(self.price.value)
            if not n or not 0<=p<=10**10:raise ValueError('名稱或價格不合法')
            from core import pet_market_v2 as market
            matching=next((r for r in market.catalog(i.guild_id,active_only=False)
                           if r['code']==n),None)
            if matching:
                market.manage(i.guild_id,matching['kind'],matching['code'],matching['label'],p,
                              species=matching['species'],stock=matching['stock'],
                              active=bool(matching['active']),basic=bool(matching['basic']),
                              description=matching['description'])
            else:
                data=pet_rules(i.guild_id);data[self.group][n]=p;save(i.guild_id,'pets',validate_pet(data))
        except ValueError as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 世界觀寵物定價已更新。',ephemeral=True)

class PetPricesView(Guard):
    def __init__(self,i):
        super().__init__(i)
        opts=[discord.SelectOption(label='物種與品種價格',value='species'),
            discord.SelectOption(label='飼料／用品／醫療價格',value='supplies'),
            discord.SelectOption(label='稀有度價格倍率（100=原價）',value='rarity_multipliers')]
        sel=discord.ui.Select(placeholder='選擇要修改的價格類別',options=opts)
        async def cb(interaction):await interaction.response.send_modal(PetPriceModal(sel.values[0]))
        sel.callback=cb;self.add_item(sel)

class ProtectedSpeciesModal(discord.ui.Modal,title='不可買賣的保育物種'):
    names=discord.ui.TextInput(label='保育類名稱（每行一種）',style=discord.TextStyle.paragraph,max_length=2000)
    def __init__(self,gid):
        super().__init__()
        self.names.default='\n'.join(pet_rules(gid)['protected_species'])
    async def on_submit(self,i):
        if not is_manager(i):return await i.response.send_message('沒有管理權限',ephemeral=True)
        items=list(dict.fromkeys(x.strip() for x in self.names.value.splitlines() if x.strip()))
        if len(items)>100:return await i.response.send_message('最多 100 種保育動物',ephemeral=True)
        data=pet_rules(i.guild_id);data['protected_species']=items
        save(i.guild_id,'pets',validate_pet(data))
        await i.response.send_message('✅ 已更新不可買賣物種清單。',ephemeral=True)

class PetWorldView(Guard):
    def __init__(self,i):
        super().__init__(i)
        keys=list(PET_FIELDS)
        sel=discord.ui.Select(placeholder='選擇寵物健康／依賴／每日數值',options=[discord.SelectOption(label=v,value=k) for k,v in PET_FIELDS.items()],row=0)
        async def cb(interaction):await interaction.response.send_modal(PetNumberModal(interaction.guild_id,sel.values[0]))
        sel.callback=cb;self.add_item(sel)
    @discord.ui.button(label='🛍️ 物種／用品／稀有度定價',style=discord.ButtonStyle.primary,row=1)
    async def species(self,i,b):await i.response.send_message('選擇要調整的定價類別',view=PetPricesView(i),ephemeral=True)
    @discord.ui.button(label='✨ 啟用／停用等級',style=discord.ButtonStyle.secondary,row=1)
    async def level(self,i,b):
        data=pet_rules(i.guild_id);data['pet_levels_enabled']=not data['pet_levels_enabled'];save(i.guild_id,'pets',validate_pet(data))
        await i.response.edit_message(content=pet_summary(i.guild_id),view=PetWorldView(i))
    @discord.ui.button(label='🛡️ 保育類清單',style=discord.ButtonStyle.secondary,row=2)
    async def protected(self,i,b):await i.response.send_modal(ProtectedSpeciesModal(i.guild_id))
    @discord.ui.button(label='🔄 重新整理',style=discord.ButtonStyle.secondary,row=2)
    async def refresh(self,i,b):await i.response.edit_message(content=pet_summary(i.guild_id),view=PetWorldView(i))
    @discord.ui.button(label='◀️ 返回世界觀',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):await i.response.edit_message(content=parent().world_summary(i.guild_id),view=parent().WorldSettingsView(i))
