"""Pet center V2: browse -> cart -> consent -> atomic checkout/chip.

Feeding, play, walks and renaming are accessible from /查看 -> 寵物相關.
Random animal encounters are triggered after /行動, never manually from this menu.
"""
from __future__ import annotations
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands,tasks
from core import pets_v1 as pet
from core import pet_market_v2 as market
from core.world_interaction_rules import pet_rules
from core.database import get_connection
from core.prelaunch_gate import is_open

LOG=logging.getLogger(__name__)
PAGE_SIZE=20


def mine(gid,uid):
    with get_connection() as db:
        return [dict(r) for r in db.execute("SELECT id,name FROM characters WHERE guild_id=? AND owner_id=? AND status='active' ORDER BY id LIMIT 25",(str(gid),str(uid)))]


def staff(i):
    return bool(i.guild and (i.user.id==i.guild.owner_id or i.user.guild_permissions.administrator))


def terms(gid):
    r=pet_rules(gid)
    return (f'📜 **寵物飼主須知／晶片登記**\n'
            f'健康上限 {r["health_max"]}；每天 -{r["health_daily_loss"]}。'
            f'餵錯食物 -{r["wrong_food_health_loss"]}；'
            f'生病 -{r["sick_health_loss_min"]}～{r["sick_health_loss_max"]}；'
            f'受傷 -{r["injured_health_loss_min"]}～{r["injured_health_loss_max"]}。'
            f'未治療每 24 小時額外 -{r["untreated_24h_health_loss"]}；'
            f'健康歸零後可能死亡，請盡速救治。\n'
            f'依賴上限 {r["bond_max"]}；每天 -{r["bond_daily_loss"]}，'
            f'每 12 小時未玩樂再 -{r["bond_inactive_12h_loss"]}；'
            f'患病或受傷期間每天額外 -{r["sick_daily_bond_loss"]}。'
            f'陪玩 +{r["play_bond_gain"]}；合適零食、玩具及用品 +{r["gift_bond_min"]}～{r["gift_bond_max"]}。\n'
            f'依賴低於 100 有可能走失，散步也有小概率逃跑。'
            f'寵物每天約 {r["daily_bonus_chance"]}% 機率為主要飼主增加一般正向數值，不增加影響指數。\n'
            '寵物不限飼養數量，允許共同飼主；購買前必須備齊基礎飼料及用品。'
            '結帳時一次扣款、植入永久唯一晶片並登記飼主。'
            '保育類動物不可販售或私養；晶片資料可供系統自動核對。'
            '我承諾妥善照顧，走失時進行協尋。')


def cart_text(data):
    lines=['🧺 **寵物與商品選購清單**']
    for x in data['items']:
        lines.append(f'• {x["species"]+"／" if x["kind"]=="breed" else ""}{x["label"]} ×{x["quantity"]}：{x["price"]*x["quantity"]:,} 金幣')
    lines.append(f'**合計：{data["total"]:,} 金幣**')
    if not data['pet_selected']:lines.append('ℹ️ 尚未選擇寵物；可僅購買用品補貨，不需另打一枚晶片。')
    if data['missing_basics']:lines.append('⚠️ 必備用品尚未選齊：'+', '.join(data['missing_basics']))
    lines.append('選購不會立即扣款；所有付款及晶片施打在最後的「統一結帳」一次完成。')
    return '\n'.join(lines)


class Guard(discord.ui.View):
    def __init__(self,i,cid):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id;self.cid=int(cid)
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid:
            await i.response.send_message('請使用自己的角色介面。',ephemeral=True);return False
        return True


class PageView(Guard):
    """Common paging for species/breeds/items; no more than 25 select entries."""
    def __init__(self,i,cid,kind,species='',page=0):
        super().__init__(i,cid);self.kind=kind;self.species=species;self.page=max(0,page)
        if kind=='species':
            values=[(s,s) for s in market.species_list(i.guild_id)]
        else:
            rows=market.catalog(i.guild_id,'breed' if kind=='breed' else 'item',species if kind=='breed' else None)
            values=[(r['code'],f'{r["label"]}｜{r["price"]:,} 金幣'+('｜缺貨' if r['stock']==0 else '')) for r in rows]
        self.values=values
        maximum=max(0,(len(values)-1)//PAGE_SIZE);self.page=min(self.page,maximum)
        page_items=values[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        if page_items:
            opts=[discord.SelectOption(label=label[:100],value=code) for code,label in page_items]
            sel=discord.ui.Select(placeholder={'species':'第一步：選擇物種','breed':'第二步：選擇品種（顯示售價）','item':'選擇飼料、用品及玩具'}[kind],options=opts,row=0)
            async def callback(x):
                code=sel.values[0]
                if kind=='species':
                    await x.response.edit_message(content='🐾 已選物種：'+code+'。接著選擇品種。',view=PageView(x,self.cid,'breed',code))
                else:
                    try:cart=await asyncio.to_thread(market.select,x.guild_id,self.cid,x.user.id,'breed' if kind=='breed' else 'item',code)
                    except (ValueError,LookupError,PermissionError) as exc:
                        return await x.response.send_message(f'⚠️ {exc}',ephemeral=True)
                    await x.response.edit_message(content=f'✅ 已選擇 {next((r["label"] for r in market.catalog(x.guild_id,"breed") if r["code"]==code),code)}。\n接下來依序選購用品。',view=WizardView(x,self.cid,code)) if kind=='breed' else await x.response.edit_message(content=cart_text(cart),view=CartView(x,self.cid))
            sel.callback=callback;self.add_item(sel)
    @discord.ui.button(label='◀ 上頁',style=discord.ButtonStyle.secondary,row=1)
    async def prev(self,i,b):
        await i.response.edit_message(view=PageView(i,self.cid,self.kind,self.species,max(0,self.page-1)))
    @discord.ui.button(label='下頁 ▶',style=discord.ButtonStyle.secondary,row=1)
    async def nxt(self,i,b):
        await i.response.edit_message(view=PageView(i,self.cid,self.kind,self.species,self.page+1))
    @discord.ui.button(label='返回選購清單',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        await i.response.edit_message(content=cart_text(market.preview(i.guild_id,self.cid)),view=CartView(i,self.cid))



class WizardView(Guard):
    """選完品種後逐頁選用品；不同品質會替換同一頁的舊選項。"""
    def __init__(self,i,cid,breed_code,step=0):
        super().__init__(i,cid)
        self.breed_code=breed_code
        breed=next((r for r in market.catalog(i.guild_id,'breed') if r['code']==breed_code),None)
        if not breed: raise LookupError('品種已下架')
        self.species=breed['species'];self.pages=market.wizard_pages(i.guild_id,breed_code)
        self.step=max(0,min(int(step),len(self.pages)))
        if self.step<len(self.pages):
            group,label,required=self.pages[self.step]
            choices=market.wizard_items(i.guild_id,self.species,group,breed_code)
            if choices:
                selected=discord.ui.Select(placeholder=f'第 {self.step+1}/{len(self.pages)} 頁｜{label}'+('（必選）' if required else '（選購）'),
                    options=[discord.SelectOption(label=f'{x["label"]}｜{x["price"]:,} 金幣'[:100],value=x['code']) for x in choices[:25]],row=0)
                async def chosen(x):
                    try:
                        await asyncio.to_thread(market.select,x.guild_id,self.cid,x.user.id,'item',selected.values[0])
                    except (ValueError,LookupError,PermissionError) as exc:
                        return await x.response.send_message(f'⚠️ {exc}',ephemeral=True)
                    await x.response.edit_message(content=self.page_text(x.guild_id, self.step+1),view=WizardView(x,self.cid,self.breed_code,self.step+1))
                selected.callback=chosen;self.add_item(selected)

    def page_text(self,gid,index=None):
        index=self.step if index is None else index
        state=market.preview(gid,self.cid)
        if index>=len(self.pages):
            return cart_text(state)+'\n✅ 已完成用品逐頁挑選，請檢查所有必備頁面。'
        group,label,required=self.pages[index]
        return f'🐾 **逐頁選購 {index+1}/{len(self.pages)}**｜{label}'+('（必選）' if required else '（可略過）')+f'\n目前總額：{state["total"]:,} 金幣。選擇用品不會扣款。'

    @discord.ui.button(label='◀ 上一頁',style=discord.ButtonStyle.secondary,row=1)
    async def prev(self,i,b):
        step=max(0,self.step-1)
        await i.response.edit_message(content=self.page_text(i.guild_id,step),view=WizardView(i,self.cid,self.breed_code,step))

    @discord.ui.button(label='下一頁／略過 ▶',style=discord.ButtonStyle.primary,row=1)
    async def next(self,i,b):
        state=market.preview(i.guild_id,self.cid)
        if self.step<len(self.pages):
            group,label,required=self.pages[self.step]
            if required and group in state['missing_basics']:
                return await i.response.send_message(f'⚠️ {label} 是必選項目，請先選擇適用品質。',ephemeral=True)
        step=min(self.step+1,len(self.pages))
        await i.response.edit_message(content=self.page_text(i.guild_id,step),view=WizardView(i,self.cid,self.breed_code,step))

    @discord.ui.button(label='檢查購物車／結帳',style=discord.ButtonStyle.success,row=2)
    async def cart(self,i,b):
        state=market.preview(i.guild_id,self.cid)
        await i.response.edit_message(content=cart_text(state),view=CartView(i,self.cid))


class CheckoutModal(discord.ui.Modal,title='統一結帳・晶片施打與飼主登記'):
    name=discord.ui.TextInput(label='寵物名字',max_length=50)
    def __init__(self,cid):super().__init__();self.cid=cid
    async def on_submit(self,i):
        try:
            result=await asyncio.to_thread(market.checkout,i.guild_id,self.cid,i.user.id,self.name.value,f'discord:pet:checkout:{i.id}')
        except (ValueError,PermissionError,LookupError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message(f'✅ **領養／購買完成**\n寵物：{result["name"]}（{result["species"]}・{result["breed"]}）'
            f'\n寵物編號 #{result["pet_id"]}\n已一次支付 {result["total"]:,} 金幣（含必備商品）'
            f'\n💉 晶片已植入且自動登記：`{result["chip"]}`\n'
            '之後請使用 `/查看 → 寵物相關` 餵食、陪玩或修改名字。',ephemeral=True)


class Agree(discord.ui.View):
    def __init__(self,i,cid):super().__init__(timeout=300);self.gid=i.guild_id;self.uid=i.user.id;self.cid=cid
    @discord.ui.button(label='我已閱讀並同意，簽署飼主須知',style=discord.ButtonStyle.success)
    async def sign(self,i,b):
        if i.user.id!=self.uid or i.guild_id!=self.gid:
            return await i.response.send_message('只能由角色持有者簽署。',ephemeral=True)
        try:await asyncio.to_thread(pet.accept_terms,i.guild_id,self.cid,i.user.id)
        except (ValueError,PermissionError) as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 飼主須知已簽署，請返回寵物中心選購並結帳。',ephemeral=True)


class CartView(Guard):
    def __init__(self,i,cid):
        super().__init__(i,cid)
        present=market.preview(i.guild_id,cid)['items']
        if present:
            sel=discord.ui.Select(placeholder='從清單移除商品／重新選擇品種',
                options=[discord.SelectOption(label=('寵物：' if x['kind']=='breed' else '用品：')+x['label'][:88],
                  value=x['kind']+':'+x['code']) for x in present[:25]],row=3)
            async def discard(x):
                kind,code=sel.values[0].split(':',1)
                try:state=await asyncio.to_thread(market.remove,x.guild_id,self.cid,x.user.id,kind,code)
                except (ValueError,PermissionError,LookupError) as exc:
                    return await x.response.send_message(f'⚠️ {exc}',ephemeral=True)
                await x.response.edit_message(content=cart_text(state),view=CartView(x,self.cid))
            sel.callback=discard;self.add_item(sel)
    @discord.ui.button(label='挑選寵物（物種→品種）',style=discord.ButtonStyle.primary,row=0)
    async def breed(self,i,b):
        await i.response.edit_message(content='🐾 第一步：請選擇寵物物種',view=PageView(i,self.cid,'species'))
    @discord.ui.button(label='挑選飼料／用品／玩具',style=discord.ButtonStyle.primary,row=0)
    async def item(self,i,b):
        await i.response.edit_message(content='🛒 請選擇商品，必備用品需全部加入清單。',view=PageView(i,self.cid,'item'))
    @discord.ui.button(label='查看飼主須知／簽署',style=discord.ButtonStyle.secondary,row=1)
    async def sign(self,i,b):await i.response.send_message(terms(i.guild_id),view=Agree(i,self.cid),ephemeral=True)
    @discord.ui.button(label='確認清單／付款與晶片登記',style=discord.ButtonStyle.success,row=1)
    async def checkout(self,i,b):
        state=market.preview(i.guild_id,self.cid)
        if not state['pet_selected']:
            if not state['items']:
                return await i.response.send_message('請先選擇寵物或用品。',ephemeral=True)
            try:res=await asyncio.to_thread(market.checkout_supplies,i.guild_id,self.cid,i.user.id,f'discord:pet:supplies:{i.id}')
            except (ValueError,LookupError,PermissionError) as exc:
                return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
            return await i.response.send_message(f'✅ 用品已一次付款 {res["total"]:,} 金幣並存入角色寵物用品庫存。',ephemeral=True)
        if not state['ready']:
            return await i.response.send_message(cart_text(state)+'\n請先補齊必備用品才可付款。',ephemeral=True)
        await i.response.send_modal(CheckoutModal(self.cid))
    @discord.ui.button(label='返回寵物中心',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):await i.response.edit_message(content='🐾 寵物中心',view=PetHomeView(i,self.cid))


class RenameModal(discord.ui.Modal,title='修改寵物名稱'):
    name=discord.ui.TextInput(label='新的寵物名字',max_length=50)
    def __init__(self,cid,pid):super().__init__();self.cid=cid;self.pid=pid
    async def on_submit(self,i):
        try:name=await asyncio.to_thread(market.rename,i.guild_id,self.pid,self.cid,i.user.id,self.name.value)
        except (ValueError,LookupError,PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message(f'✅ 寵物已更名為 **{name}**。',ephemeral=True)


class PetActionsView(Guard):
    def __init__(self,i,cid,pid):
        super().__init__(i,cid);self.pid=int(pid)
        opts=[('play','陪玩（依賴增加）'),('walk','公園散步'),('feed','餵食'),('wrong_food','餵錯食物（健康下降）')]
        sel=discord.ui.Select(placeholder='在 /查看 管理寵物日常照顧',options=[discord.SelectOption(label=v,value=k) for k,v in opts],row=0)
        async def callback(x):
            try:res=await asyncio.to_thread(pet.care,x.guild_id,self.pid,self.cid,x.user.id,sel.values[0])
            except (ValueError,LookupError,PermissionError) as exc:return await x.response.send_message(f'⚠️ {exc}',ephemeral=True)
            await x.response.send_message(f'✅ 健康 {res["health"]}｜依賴 {res["bond"]}｜狀態 {res["state"]}',ephemeral=True)
        sel.callback=callback;self.add_item(sel)
    @discord.ui.button(label='修改寵物名字',style=discord.ButtonStyle.primary,row=1)
    async def rename(self,i,b):await i.response.send_modal(RenameModal(self.cid,self.pid))
    @discord.ui.button(label='返回寵物清單',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        await i.response.edit_message(content='🐾 選擇寵物',view=PetSelectView(i,self.cid))


class PetSelectView(Guard):
    def __init__(self,i,cid,page=0):
        super().__init__(i,cid)
        rows=pet.list_pets(i.guild_id,cid)
        self.empty=not bool(rows)
        self.page=min(max(int(page),0),max(0,(len(rows)-1)//PAGE_SIZE))
        page_rows=rows[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        if page_rows:
            sel=discord.ui.Select(placeholder='選擇自己的寵物',options=[discord.SelectOption(label=f'#{r["id"]} {r["name"]}｜{r["species"]}（{r["state"]}）'[:100],value=str(r['id'])) for r in page_rows],row=0)
            async def callback(x):
                row=next(r for r in page_rows if r['id']==int(sel.values[0]))
                await x.response.edit_message(content=f'🐾 {row["name"]}\n健康 {row["health"]}｜依賴 {row["bond"]}\n晶片：`{row["chip"]}`',view=PetActionsView(x,self.cid,row['id']))
            sel.callback=callback;self.add_item(sel)
        self.max_page=max(0,(len(rows)-1)//PAGE_SIZE)
    @discord.ui.button(label='上一頁',style=discord.ButtonStyle.secondary,row=1)
    async def prev(self,i,b):
        await i.response.edit_message(content='🐾 選擇寵物',view=PetSelectView(i,self.cid,max(0,self.page-1)))
    @discord.ui.button(label='下一頁',style=discord.ButtonStyle.secondary,row=1)
    async def nxt(self,i,b):
        await i.response.edit_message(content='🐾 選擇寵物',view=PetSelectView(i,self.cid,min(self.max_page,self.page+1)))


class MissingModal(discord.ui.Modal,title='寵物協尋啟事'):
    pet_id=discord.ui.TextInput(label='走失寵物編號',max_length=12)
    location=discord.ui.TextInput(label='最後出現位置',max_length=100)
    description=discord.ui.TextInput(label='特徵及協尋線索',style=discord.TextStyle.paragraph,max_length=1000)
    reward=discord.ui.TextInput(label='懸賞金幣',default='0',max_length=12)
    def __init__(self,cid):super().__init__();self.cid=cid
    async def on_submit(self,i):
        try:
            pid=int(self.pet_id.value)
            existing=next((x for x in pet.list_pets(i.guild_id,self.cid) if x['id']==pid),None)
            if not existing:raise PermissionError('你不是這隻寵物的飼主')
            if existing['state']!='missing':
                await asyncio.to_thread(pet.mark_missing,i.guild_id,pid,self.cid,i.user.id,self.location.value)
            result=await asyncio.to_thread(pet.publish_poster,i.guild_id,pid,self.cid,i.user.id,self.description.value,self.location.value,int(self.reward.value))
        except (ValueError,LookupError,PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        try:
            from core.channel_bindings_v1 import get_binding
            channel_id=get_binding(i.guild_id,'pet_missing')
            channel=i.guild.get_channel(int(channel_id)) if channel_id else None
            if isinstance(channel,discord.TextChannel):
                await channel.send(f'🐾 **寵物協尋公告**\n寵物 #{pid}\n最後地點：{discord.utils.escape_mentions(self.location.value)}\n'
                    f'{discord.utils.escape_mentions(self.description.value)}\n懸賞：{result["reward"]:,} 金幣',allowed_mentions=discord.AllowedMentions.none())
        except Exception:LOG.exception('寵物協尋公告失敗，但已託管懸賞')
        await i.response.send_message('✅ 協尋已建立，懸賞已託管；晶片比對由系統處理。',ephemeral=True)


class EncounterResultView(Guard):
    """Shown only after an approved action triggered a random encounter."""
    def __init__(self,i,cid,encounter):
        super().__init__(i,cid);self.encounter=encounter
        opts=[('leave','離開'),('observe','觀察'),('shelter','嘗試收留'),
              ('hospital','帶去醫院掃晶片'),('report','通知動物救援')]
        sel=discord.ui.Select(placeholder='遇見動物後你想怎麼做？',options=[discord.SelectOption(label=v,value=k) for k,v in opts])
        async def cb(x):
            try:
                result=await asyncio.to_thread(pet.resolve_encounter,x.guild_id,self.encounter['id'],self.cid,x.user.id,sel.values[0])
                note=''
                if result.get('pet_id') and result['choice']=='hospital':
                    # The chip already belongs to the original pet; identity is
                    # system-verified. No manual staff confirmation required.
                    recovered=await asyncio.to_thread(pet.complete_rescue,x.guild_id,result['pet_id'],authorized=True,rescuer_character=self.cid)
                    note=f'\n💉 晶片自動比對完成，已通知原飼主，尋回懸賞 {recovered["reward"]:,} 金幣已結算。'
                    try:
                        from modules.notifications import queue_notification
                        with get_connection() as db:
                            owner=db.execute('SELECT main_character_id FROM rp_pets_v1 WHERE guild_id=? AND id=?',(str(x.guild_id),int(result['pet_id']))).fetchone()
                        if owner:
                            await asyncio.to_thread(queue_notification,x.guild_id,'private','role',
                                f'🐾 晶片自動核對完成，你的寵物 #{result["pet_id"]} 已尋回。',
                                character_id=int(owner['main_character_id']),
                                dedupe_key=f'pet:chip:auto:{x.guild_id}:{self.encounter["id"]}')
                    except Exception:LOG.exception('已尋回寵物，但通知發送失敗')
            except (ValueError,PermissionError,LookupError) as exc:
                return await x.response.send_message(f'⚠️ {exc}',ephemeral=True)
            await x.response.edit_message(content='🐾 '+result['message']+note,view=None)
        sel.callback=cb;self.add_item(sel)


class ServiceView(Guard):
    def __init__(self,i,cid,kind):
        super().__init__(i,cid)
        rows=pet.list_pets(i.guild_id,cid)
        if rows:
            sel=discord.ui.Select(placeholder='選擇需要服務的寵物',options=[discord.SelectOption(label=f'{r["name"]} #{r["id"]}'[:100],value=str(r['id'])) for r in rows[:25]])
            async def callback(x):
                try:res=await asyncio.to_thread(pet.buy_service,x.guild_id,int(sel.values[0]),self.cid,x.user.id,'健康檢查' if kind=='check' else '看醫生')
                except (ValueError,PermissionError,LookupError) as exc:return await x.response.send_message(f'⚠️ {exc}',ephemeral=True)
                await x.response.send_message(f'✅ 已完成服務：{res}',ephemeral=True)
            sel.callback=callback;self.add_item(sel)
    @discord.ui.button(label='返回寵物中心',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content='🐾 寵物中心',view=PetHomeView(i,self.cid))


class PetHomeView(Guard):
    def __init__(self,i,cid):
        super().__init__(i,cid)
        options=[('cart','🐾 寵物與商品挑選'),('checkout','💉 付款結帳與晶片施打'),
                 ('check','🩺 健康檢查'),('treat','🏥 傷病治療'),('training','🎓 寵物訓練（規則待核准）'),('terms','📜 飼主須知')]
        if any(p['state']=='missing' for p in pet.list_pets(i.guild_id,cid)):
            options.append(('missing','🔎 寵物協尋'))
        sel=discord.ui.Select(placeholder='選擇寵物中心服務',options=[discord.SelectOption(label=v,value=k) for k,v in options])
        async def callback(x):
            mode=sel.values[0]
            if mode=='cart':return await x.response.edit_message(content=cart_text(market.preview(x.guild_id,self.cid)),view=CartView(x,self.cid))
            if mode=='checkout':return await x.response.edit_message(content=cart_text(market.preview(x.guild_id,self.cid)),view=CartView(x,self.cid))
            if mode=='training':return await x.response.send_message('🎓 寵物訓練入口已保留；費用、成功率、效果尚未核准，因此暫不扣款或修改數值。',ephemeral=True)
            if mode=='terms':return await x.response.send_message(terms(x.guild_id),view=Agree(x,self.cid),ephemeral=True)
            if mode in ('check','treat'):return await x.response.edit_message(content='選擇寵物進行醫療服務',view=ServiceView(x,self.cid,mode))
            if mode=='missing':return await x.response.send_modal(MissingModal(self.cid))
        sel.callback=callback;self.add_item(sel)
        if staff(i):
            button=discord.ui.Button(label='🔧 寵物及商品上架／下架',style=discord.ButtonStyle.secondary,row=1)
            async def admin_click(x):
                if not staff(x):return await x.response.send_message('僅限管理員。',ephemeral=True)
                await x.response.send_message('🔧 寵物及商品上架／下架',view=AdminView(),ephemeral=True)
            button.callback=admin_click
            self.add_item(button)


class AdminEdit(discord.ui.Modal,title='管理員｜寵物／商品上架下架'):
    kind=discord.ui.TextInput(label='類型：breed（品種）／item（商品）',default='breed',max_length=8)
    code=discord.ui.TextInput(label='商品唯一代碼',max_length=80)
    label=discord.ui.TextInput(label='品種名稱／商品名稱',max_length=100)
    species=discord.ui.TextInput(label='物種（品種必填；用品可留空）',required=False,max_length=50)
    price=discord.ui.TextInput(label='售價（金幣，-1 代表下架）',max_length=15)
    async def on_submit(self,i):
        if not staff(i):return await i.response.send_message('沒有管理權限。',ephemeral=True)
        try:
            price=int(self.price.value)
            if price==-1:
                old=next((r for r in market.catalog(i.guild_id,active_only=False) if r['kind']==self.kind.value and r['code']==self.code.value),None)
                if not old:raise LookupError('找不到要下架的商品')
                data=market.manage(i.guild_id,old['kind'],old['code'],old['label'],old['price'],species=old['species'],stock=old['stock'],active=False,basic=bool(old['basic']))
            else:
                old=next((r for r in market.catalog(i.guild_id,active_only=False) if r['kind']==self.kind.value and r['code']==self.code.value),None)
                data=market.manage(i.guild_id,self.kind.value,self.code.value,self.label.value,price,species=self.species.value,stock=old['stock'] if old else -1,active=True,basic=bool(old['basic']) if old else False)
        except (ValueError,LookupError,PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message(f'✅ 商品設定已更新：`{data["code"]}`；上架：{data["active"]}',ephemeral=True)


class AdminStockModal(discord.ui.Modal,title='管理員｜庫存與必備用品設定'):
    kind=discord.ui.TextInput(label='類型：breed 或 item',default='item',max_length=8)
    code=discord.ui.TextInput(label='商品唯一代碼',max_length=80)
    stock=discord.ui.TextInput(label='庫存（-1 代表不限數量）',default='-1',max_length=12)
    basic=discord.ui.TextInput(label='是否為必備用品（是／否）',default='否',max_length=3)
    async def on_submit(self,i):
        if not staff(i):return await i.response.send_message('沒有管理權限。',ephemeral=True)
        try:
            row=next((r for r in market.catalog(i.guild_id,active_only=False)
                 if r['kind']==self.kind.value.strip() and r['code']==self.code.value.strip()),None)
            if not row:raise LookupError('商品代碼不存在')
            basic=self.basic.value.strip() in ('是','yes','true','1')
            result=market.manage(i.guild_id,row['kind'],row['code'],row['label'],row['price'],
               species=row['species'],stock=int(self.stock.value),active=bool(row['active']),basic=basic,
               description=row['description'])
        except (LookupError,ValueError,PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message(f'✅ `{result["code"]}` 庫存與必備選項已更新。',ephemeral=True)


class AdminView(discord.ui.View):
    def __init__(self):super().__init__(timeout=300)
    @discord.ui.button(label='新增／修改／上下架品種或用品',style=discord.ButtonStyle.primary)
    async def edit(self,i,b):
        if not staff(i):return await i.response.send_message('沒有管理權限。',ephemeral=True)
        await i.response.send_modal(AdminEdit())
    @discord.ui.button(label='設定庫存／必備用品',style=discord.ButtonStyle.secondary)
    async def stock(self,i,b):
        if not staff(i):return await i.response.send_message('沒有管理權限。',ephemeral=True)
        await i.response.send_modal(AdminStockModal())
    @discord.ui.button(label='檢視目前上架清單',style=discord.ButtonStyle.secondary)
    async def listings(self,i,b):
        if not staff(i):return await i.response.send_message('沒有管理權限。',ephemeral=True)
        rows=market.catalog(i.guild_id,active_only=False)
        lines=[f'{r["kind"]}｜{r["species"]} {r["label"]}｜{r["price"]:,}｜{"上架" if r["active"] else "下架"}｜`{r["code"]}`' for r in rows]
        await i.response.send_message('\n'.join(lines)[:1900] or '目前沒有商品',ephemeral=True)


class CharacterSelect(discord.ui.View):
    def __init__(self,i,rows):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
        sel=discord.ui.Select(placeholder='選擇使用寵物中心的角色',options=[discord.SelectOption(label=f'#{r["id"]} {r["name"]}'[:100],value=str(r['id'])) for r in rows])
        async def cb(x):
            if x.user.id!=self.uid or x.guild_id!=self.gid:
                return await x.response.send_message('這不是你的角色選單。',ephemeral=True)
            await x.response.edit_message(content='🐾 寵物中心',view=PetHomeView(x,int(sel.values[0])))
        sel.callback=cb;self.add_item(sel)


class PetCenter(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.worker.start()
    def cog_unload(self):self.worker.cancel()
    @app_commands.command(name='寵物中心',description='寵物選購、用品、付款與晶片、健康檢查、傷病治療、協尋')
    async def pet_center(self,i:discord.Interaction):
        if not i.guild:return await i.response.send_message('僅限伺服器使用。',ephemeral=True)
        rows=await asyncio.to_thread(mine,i.guild_id,i.user.id)
        if not rows:
            if staff(i):return await i.response.send_message('🔧 管理員｜寵物及商品上架／下架',view=AdminView(),ephemeral=True)
            return await i.response.send_message('請先啟用自己的角色。',ephemeral=True)
        await i.response.send_message('🐾 選擇角色後進入寵物中心',view=CharacterSelect(i,rows),ephemeral=True)
    @tasks.loop(hours=1)
    async def worker(self):
        try:
            with get_connection() as db:
                has=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_pets_v1'").fetchone()
                gids=[r[0] for r in db.execute('SELECT DISTINCT guild_id FROM rp_pets_v1')] if has else []
            for gid in gids:
                if is_open(gid):await asyncio.to_thread(pet.daily,gid)
        except Exception:LOG.exception('寵物定時結算失敗')
    @worker.before_loop
    async def ready(self):await self.bot.wait_until_ready()

async def setup(bot):await bot.add_cog(PetCenter(bot))
