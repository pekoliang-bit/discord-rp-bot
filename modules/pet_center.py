"""Independent /寵物中心 with Discord dropdown menus and ownership checks."""
from __future__ import annotations
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands,tasks
from core import pets_v1 as pet
from core.world_interaction_rules import pet_rules
from core.database import get_connection
LOG=logging.getLogger(__name__)

TERMS=('購買或領養寵物前，我已閱讀照護責任：寵物健康與依賴會隨時間改變；'
       '生病受傷應及時就醫，走失需協尋；晶片編號永久唯一；'
       '保育類動物不可買賣或私養，救援與通報不等於取得所有權。')

def mine(gid,uid):
    with get_connection() as db:
        return [dict(r) for r in db.execute("SELECT id,name FROM characters WHERE guild_id=? AND owner_id=? AND status='active' ORDER BY id LIMIT 25",(str(gid),str(uid)))]

class Guard(discord.ui.View):
    def __init__(self,i,cid):super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id;self.cid=int(cid)
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid:
            await i.response.send_message('請使用自己的角色面板。',ephemeral=True);return False
        return True

class BuyPetModal(discord.ui.Modal,title='購買寵物'):
    name=discord.ui.TextInput(label='寵物姓名',max_length=50)
    species=discord.ui.TextInput(label='世界觀已上架的物種名稱',max_length=50,placeholder='例如 貓')
    rarity=discord.ui.TextInput(label='稀有度代碼（common/rare/epic/legendary）',default='common',max_length=25)
    def __init__(self,cid):super().__init__();self.cid=cid
    async def on_submit(self,i):
        try:
            data=await asyncio.to_thread(pet.purchase,i.guild_id,self.cid,i.user.id,self.name.value,self.species.value,rarity=self.rarity.value.strip())
            await i.response.send_message(f'✅ 已購買 **{data["name"]}**，晶片 `{data["chip"]}`，支付 {data["price"]} 金幣。',ephemeral=True)
        except (ValueError,PermissionError) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class Agree(discord.ui.View):
    def __init__(self,i,cid):super().__init__(timeout=300);self.gid=i.guild_id;self.uid=i.user.id;self.cid=cid
    @discord.ui.button(label='我已閱讀並同意，簽署注意事項',style=discord.ButtonStyle.success)
    async def sign(self,i,b):
        if i.user.id!=self.uid or i.guild_id!=self.gid:return await i.response.send_message('只能由本人簽署。',ephemeral=True)
        try:await asyncio.to_thread(pet.accept_terms,i.guild_id,self.cid,i.user.id)
        except (ValueError,PermissionError) as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 注意事項已簽署。現在可重新選擇「購買寵物」。',ephemeral=True)

class LostPosterModal(discord.ui.Modal,title='發布寵物協尋'):
    location=discord.ui.TextInput(label='最後出現位置',max_length=100)
    description=discord.ui.TextInput(label='外觀、習性、失蹤情況',style=discord.TextStyle.paragraph,max_length=1000)
    reward=discord.ui.TextInput(label='懸賞金幣（0 表示無懸賞）',default='0',max_length=12)
    def __init__(self,cid,pid):super().__init__();self.cid=cid;self.pid=pid
    async def on_submit(self,i):
        try:
            data=await asyncio.to_thread(pet.publish_poster,i.guild_id,self.pid,self.cid,i.user.id,self.description.value,self.location.value,int(self.reward.value))
            await i.response.send_message('✅ 寵物協尋已發布。'+f'懸賞 {data["reward"]} 金幣；額外曝光倍率 {data["reach_multiplier"]:.2f}。',ephemeral=True)
        except (ValueError,PermissionError) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class LostMarkModal(discord.ui.Modal,title='登記寵物走失'):
    location=discord.ui.TextInput(label='最後看見的位置',max_length=100)
    def __init__(self,cid,pid):super().__init__();self.cid=cid;self.pid=pid
    async def on_submit(self,i):
        try:await asyncio.to_thread(pet.mark_missing,i.guild_id,self.pid,self.cid,i.user.id,self.location.value)
        except (ValueError,PermissionError) as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 寵物已列為走失。返回寵物中心的「寵物協尋」發布尋找啟事。',ephemeral=True)

class CoownerModal(discord.ui.Modal,title='指定共同飼主'):
    target=discord.ui.TextInput(label='共同飼主的角色編號',max_length=18)
    def __init__(self,cid,pid):super().__init__();self.cid=cid;self.pid=pid
    async def on_submit(self,i):
        try:await asyncio.to_thread(pet.add_coowner,i.guild_id,self.pid,self.cid,i.user.id,int(self.target.value))
        except (ValueError,PermissionError,LookupError) as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 已新增共同飼主。',ephemeral=True)

class TransferModal(discord.ui.Modal,title='更改寵物主要飼主'):
    target=discord.ui.TextInput(label='新主要飼主角色編號（須先是共同飼主）',max_length=18)
    def __init__(self,cid,pid):super().__init__();self.cid=cid;self.pid=pid
    async def on_submit(self,i):
        try:
            chip=await asyncio.to_thread(pet.transfer_pet,i.guild_id,self.pid,self.cid,i.user.id,int(self.target.value))
            await i.response.send_message(f'✅ 新主要飼主已更新；原晶片 `{chip}` 永久保留。',ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class AdoptStrayModal(discord.ui.Modal,title='領養已檢查的流浪動物'):
    name=discord.ui.TextInput(label='寵物新名字',max_length=50)
    species=discord.ui.TextInput(label='醫院確認的物種',max_length=50)
    def __init__(self,cid,eid):super().__init__();self.cid=cid;self.eid=eid
    async def on_submit(self,i):
        try:
            data=await asyncio.to_thread(pet.adopt_stray,i.guild_id,self.eid,self.cid,i.user.id,self.name.value,self.species.value)
            await i.response.send_message(f'✅ 領養成功，晶片 `{data["chip"]}` 已登記。',ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class AdoptStrayView(Guard):
    def __init__(self,i,cid,eid):super().__init__(i,cid);self.eid=eid
    @discord.ui.button(label='領養經醫院確認的無主動物',style=discord.ButtonStyle.success)
    async def adopt(self,i,b):await i.response.send_modal(AdoptStrayModal(self.cid,self.eid))

class PetActionsView(Guard):
    def __init__(self,i,cid,pid):
        super().__init__(i,cid);self.pid=int(pid)
        opts=[('play','陪玩'),('walk','公園散步'),('feed','餵食'),('wrong_food','錯誤食物'),
          ('飼料','購買飼料'),('零食','購買零食'),('玩具','購買玩具'),('健康檢查','健康檢查'),('看醫生','看醫生')]
        sel=discord.ui.Select(placeholder='選擇寵物照顧／購買／醫療',options=[discord.SelectOption(label=label,value=value) for value,label in opts],row=0)
        async def cb(interaction):
            action=sel.values[0]
            try:
                if action in ('play','walk','feed','wrong_food'):
                    result=await asyncio.to_thread(pet.care,interaction.guild_id,self.pid,self.cid,interaction.user.id,action)
                else:result=await asyncio.to_thread(pet.buy_service,interaction.guild_id,self.pid,self.cid,interaction.user.id,action)
                await interaction.response.send_message('✅ 操作完成：'+str(result),ephemeral=True)
            except (ValueError,PermissionError,LookupError) as exc:await interaction.response.send_message(f'⚠️ {exc}',ephemeral=True)
        sel.callback=cb;self.add_item(sel)
    @discord.ui.button(label='轉讓給共同飼主',style=discord.ButtonStyle.secondary,row=1)
    async def transfer(self,i,b):await i.response.send_modal(TransferModal(self.cid,self.pid))
    @discord.ui.button(label='登記走失',style=discord.ButtonStyle.danger,row=1)
    async def missing(self,i,b):await i.response.send_modal(LostMarkModal(self.cid,self.pid))
    @discord.ui.button(label='指定共同飼主',style=discord.ButtonStyle.secondary,row=1)
    async def coowner(self,i,b):await i.response.send_modal(CoownerModal(self.cid,self.pid))
    @discord.ui.button(label='◀️ 返回寵物中心',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):await i.response.edit_message(content='🐾 寵物中心',view=PetHomeView(i,self.cid))

class PetSelectView(Guard):
    def __init__(self,i,cid):
        super().__init__(i,cid)
        rows=pet.list_pets(i.guild_id,cid)
        if rows:
            sel=discord.ui.Select(placeholder='選擇寵物',options=[discord.SelectOption(label=f'#{r["id"]} {r["name"]}｜{r["species"]}（{r["state"]}）'[:100],value=str(r['id'])) for r in rows[:25]])
            async def cb(interaction):
                p=next(x for x in rows if x['id']==int(sel.values[0]))
                await interaction.response.edit_message(content=f'🐾 {p["name"]}\n健康 {p["health"]}｜依賴 {p["bond"]}\n晶片：`{p["chip"]}`｜狀態 {p["state"]}',view=PetActionsView(interaction,self.cid,p['id']))
            sel.callback=cb;self.add_item(sel)
        self.empty=not bool(rows)
    @discord.ui.button(label='◀️ 返回',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content='🐾 寵物中心',view=PetHomeView(i,self.cid))

class LostView(Guard):
    def __init__(self,i,cid):
        super().__init__(i,cid)
        rows=[x for x in pet.list_pets(i.guild_id,cid) if x['state']=='missing']
        if rows:
            sel=discord.ui.Select(placeholder='選擇走失寵物，發布協尋',options=[discord.SelectOption(label=f'#{x["id"]} {x["name"]}',value=str(x['id'])) for x in rows[:25]])
            async def cb(interaction):await interaction.response.send_modal(LostPosterModal(self.cid,int(sel.values[0])))
            sel.callback=cb;self.add_item(sel)
    @discord.ui.button(label='查看全服公開協尋',style=discord.ButtonStyle.primary,row=1)
    async def posters(self,i,b):
        rows=await asyncio.to_thread(pet.public_posters,i.guild_id)
        await i.response.send_message('\n'.join(f'#{r["pet_id"]} {r["name"]}｜{r["location"]}｜懸賞{r["reward"]}' for r in rows)[:1800] or '目前沒有公開協尋',ephemeral=True)
    @discord.ui.button(label='◀️ 返回',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):await i.response.edit_message(content='🐾 寵物中心',view=PetHomeView(i,self.cid))

class EncounterView(Guard):
    def __init__(self,i,cid,encounter):
        super().__init__(i,cid);self.encounter=encounter
        opts=[('leave','離開'),('observe','觀察'),('shelter','嘗試收留'),('hospital','帶去醫院掃晶片'),('report','通報救援')]
        sel=discord.ui.Select(placeholder='選擇遇見動物後的處理',options=[discord.SelectOption(label=v,value=k) for k,v in opts])
        async def cb(interaction):
            try:result=await asyncio.to_thread(pet.resolve_encounter,interaction.guild_id,self.encounter['id'],self.cid,interaction.user.id,sel.values[0])
            except (ValueError,PermissionError) as exc:return await interaction.response.send_message(f'⚠️ {exc}',ephemeral=True)
            if result['notify_owner']:
                try:
                    from modules.notifications import queue_notification
                    with get_connection() as db:
                        owner=db.execute('SELECT main_character_id FROM rp_pets_v1 WHERE guild_id=? AND id=?',
                            (str(interaction.guild_id),int(result['pet_id']))).fetchone()
                    if owner:
                        await asyncio.to_thread(queue_notification,interaction.guild_id,'private','role',
                           f'🐾 你的寵物 #{result["pet_id"]} 疑似被其他角色找到，已送醫掃描晶片。請到 /寵物中心 查看協尋資訊。',
                           character_id=int(owner['main_character_id']),dedupe_key=f'pet:chip:{interaction.guild_id}:{self.encounter["id"]}')
                except Exception:LOG.exception('寵物晶片通知尚待重試')
            view=AdoptStrayView(interaction,self.cid,self.encounter['id']) if result['kind']=='stray' and result['choice']=='hospital' else None
            await interaction.response.edit_message(content='🐾 '+result['message'],view=view)
        sel.callback=cb;self.add_item(sel)

class ConfirmReturnModal(discord.ui.Modal,title='管理員｜確認晶片並尋回'):
    pet_id=discord.ui.TextInput(label='走失寵物編號',max_length=12)
    rescuer=discord.ui.TextInput(label='協助者角色編號（空白=退回原飼主）',required=False,max_length=12)
    async def on_submit(self,i):
        if not i.guild or not (i.user.id==i.guild.owner_id or i.user.guild_permissions.administrator):
            return await i.response.send_message('只有管理員可核實晶片與確認尋回。',ephemeral=True)
        try:
            rc=int(self.rescuer.value) if str(self.rescuer.value or '').strip() else None
            result=await asyncio.to_thread(pet.complete_rescue,i.guild_id,int(self.pet_id.value),authorized=True,rescuer_character=rc)
            await i.response.send_message(f'✅ 寵物 #{result["pet_id"]} 已尋回；懸賞／退款 {result["reward"]} 金幣已核發到角色 #{result["receiver"]}。',ephemeral=True)
        except (ValueError,LookupError,PermissionError) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class PetStaffView(discord.ui.View):
    @discord.ui.button(label='🔎 核對晶片／確認尋回',style=discord.ButtonStyle.primary)
    async def verify(self,i,b):
        if not i.guild or not (i.user.id==i.guild.owner_id or i.user.guild_permissions.administrator):
            return await i.response.send_message('沒有權限。',ephemeral=True)
        await i.response.send_modal(ConfirmReturnModal())

class PetHomeView(Guard):
    def __init__(self,i,cid):
        super().__init__(i,cid)
        opts=[('buy','購買寵物'),('terms','首次購買注意事項與晶片'),('care','我的寵物／餵食／用品／醫療'),('encounter','隨機遇見動物')]
        if any(x['state']=='missing' for x in pet.list_pets(i.guild_id,cid)):
            opts.append(('lost','寵物協尋（已有寵物失蹤）'))
        if i.guild and (i.user.id==i.guild.owner_id or i.user.guild_permissions.administrator):
            opts.append(('verify','管理員：核對晶片／確認尋回'))
        sel=discord.ui.Select(placeholder='選擇寵物中心服務',options=[discord.SelectOption(label=v,value=k) for k,v in opts])
        async def cb(interaction):
            mode=sel.values[0]
            if mode=='verify':return await interaction.response.send_modal(ConfirmReturnModal())
            if mode=='buy':return await interaction.response.send_modal(BuyPetModal(self.cid))
            if mode=='terms':return await interaction.response.send_message('📜 '+TERMS,view=Agree(interaction,self.cid),ephemeral=True)
            if mode=='care':
                v=PetSelectView(interaction,self.cid)
                return await interaction.response.edit_message(content='選擇一隻寵物來照顧' if not v.empty else '你目前沒有寵物。',view=v)
            if mode=='lost':return await interaction.response.edit_message(content='🔎 寵物協尋｜選擇走失寵物或查看全服啟事',view=LostView(interaction,self.cid))
            if mode=='encounter':
                event=await asyncio.to_thread(pet.begin_encounter,interaction.guild_id,self.cid,interaction.user.id)
                return await interaction.response.edit_message(content='🐾 你遇到'+event['description']+'，你要怎麼做？',view=EncounterView(interaction,self.cid,event))
        sel.callback=cb;self.add_item(sel)

class CharacterSelect(discord.ui.View):
    def __init__(self,i,rows):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
        sel=discord.ui.Select(placeholder='選擇使用寵物中心的角色',options=[discord.SelectOption(label=f'#{r["id"]} {r["name"]}'[:100],value=str(r['id'])) for r in rows])
        async def cb(interaction):
            if interaction.user.id!=self.uid:return await interaction.response.send_message('這不是你的角色選單。',ephemeral=True)
            await interaction.response.edit_message(content='🐾 寵物中心',view=PetHomeView(interaction,int(sel.values[0])))
        sel.callback=cb;self.add_item(sel)

class PetCenter(commands.Cog):
    def __init__(self,bot):self.bot=bot;self.worker.start()
    def cog_unload(self):self.worker.cancel()
    @app_commands.command(name='寵物中心',description='寵物商店、醫療、協尋、晶片與救援')
    async def pet_center(self,i:discord.Interaction):
        if not i.guild:return await i.response.send_message('僅限伺服器使用。',ephemeral=True)
        rows=await asyncio.to_thread(mine,i.guild_id,i.user.id)
        if not rows:
            if i.user.id==i.guild.owner_id or i.user.guild_permissions.administrator:
                return await i.response.send_message('🐾 管理員寵物協尋管理',view=PetStaffView(),ephemeral=True)
            return await i.response.send_message('請先啟用自己的角色。',ephemeral=True)
        await i.response.send_message('🐾 選擇角色後進入寵物中心',view=CharacterSelect(i,rows),ephemeral=True)
    @tasks.loop(hours=1)
    async def worker(self):
        try:
            with get_connection() as db:
                has=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_pets_v1'").fetchone()
                gids=[r[0] for r in db.execute('SELECT DISTINCT guild_id FROM rp_pets_v1')] if has else []
            for gid in gids:await asyncio.to_thread(pet.daily,gid)
        except Exception:LOG.exception('寵物定時結算失敗')
    @worker.before_loop
    async def prepare(self):await self.bot.wait_until_ready()

async def setup(bot):await bot.add_cog(PetCenter(bot))
