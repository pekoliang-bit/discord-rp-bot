"""世界觀管理第七部分：組織類型規則、說明及狀態。"""
from __future__ import annotations
import discord

TYPES={
 'company':('🏢 一般公司','管理一般公司成立、職位與營運規則。'),
 'brand':('🏷️ 品牌','管理品牌升級、門檻與代言背景規則。'),
 'group':('🏙️ 集團','管理集團成立與旗下公司歸屬規則。'),
 'agency':('🎬 經紀公司','管理藝人簽約、經紀與解約規則。'),
 'mcn':('📱 MCN','管理網紅合作與 MCN 組織規則。'),
}

def core():
 from modules import content_builder
 return content_builder

def rules(gid):
 d=core().world_get(gid).get('company_rules',{})
 return d if isinstance(d,dict) else {}

def organization_summary(gid):
 r=rules(gid)
 lines=['🏢 **世界觀｜公司與組織**','選擇要管理的組織類型；每一項都有獨立說明與修改介面。','']
 for key,(label,_) in TYPES.items():
  x=r.get(key,{})
  if not isinstance(x,dict):x={}
  lines.append(f"{label}｜{'啟用' if x.get('enabled',True) else '停用'}")
 lines+=['','⚠️ 此處管理世界觀組織規則的文字設定與開關；實際成立資格、品牌升級及財務運算須由公司模組讀取才能生效。']
 return '\n'.join(lines)

def organization_detail(gid,key):
 label,meaning=TYPES[key]
 x=rules(gid).get(key,{})
 if not isinstance(x,dict):x={}
 return (f'{label}\n**用途：**{meaning}\n**目前狀態：**'+('啟用' if x.get('enabled',True) else '停用')+
  f"\n**申請規則說明：**{x.get('application','尚未設定')}\n**營運／升級規則說明：**{x.get('operation','尚未設定')}\n"
  f"**合約與分潤說明：**{x.get('contract','尚未設定')}\n**備註：**{x.get('notes','尚未設定')}\n\n"
  '請將完整制度填入對應欄位；本頁不會直接改寫現有公司或合約。')[:1900]

class Guard(discord.ui.View):
 def __init__(self,i):
  super().__init__(timeout=600)
  self.uid=i.user.id;self.gid=i.guild_id
 async def interaction_check(self,i):
  if i.guild_id!=self.gid or i.user.id!=self.uid or not core().owner(i):
   await i.response.send_message('⛔ 僅開啟面板的群主可以修改。',ephemeral=True)
   return False
  return True

class OrgModal(discord.ui.Modal):
 def __init__(self,gid,key):
  self.key=key
  data=rules(gid).get(key,{})
  if not isinstance(data,dict):data={}
  super().__init__(title='修改'+TYPES[key][0][:35])
  fields=[('application','成立／申請資格','例如：成立條件、申請限制'),('operation','營運與升級規則','例如：升級限制、營業額與熱度'),('contract','合約與分潤規則','例如：合約期限與分潤方式'),('notes','補充說明','例如：特殊例外或附註')]
  self.fields={}
  for code,label,example in fields:
   widget=discord.ui.TextInput(label=label,style=discord.TextStyle.paragraph,required=False,default=str(data.get(code,''))[:900],placeholder=example,max_length=900)
   self.fields[code]=widget;self.add_item(widget)
 async def on_submit(self,i):
  if not core().owner(i):return await i.response.send_message('⛔ 僅群主可修改。',ephemeral=True)
  data=core().world_get(i.guild_id)
  section=data.setdefault('company_rules',{})
  x=section.get(self.key,{})
  if not isinstance(x,dict):x={}
  section[self.key]={**x,**{k:f.value.strip() for k,f in self.fields.items()}}
  data.setdefault('overrides',{})['company_rules.'+self.key]=True
  core().world_save(i.guild_id,data)
  await i.response.send_message('✅ 已保存。\n'+organization_detail(i.guild_id,self.key),ephemeral=True)

class OrgSelect(discord.ui.Select):
 def __init__(self):
  super().__init__(placeholder='選擇組織類型',options=[discord.SelectOption(label=x[0],value=k,description=x[1][:95]) for k,x in TYPES.items()])
 async def callback(self,i):
  key=self.values[0]
  await i.response.edit_message(content=organization_detail(i.guild_id,key),view=OrganizationDetailView(i,key))

class OrganizationHomeView(Guard):
 def __init__(self,i):
  super().__init__(i);self.add_item(OrgSelect())
 @discord.ui.button(label='組織名稱用詞設定',style=discord.ButtonStyle.secondary,row=1)
 async def terms(self,i,b):
  from modules.world_term_editor import TermHomeView, summary
  await i.response.edit_message(content=summary(i.guild_id),view=TermHomeView(i))
 @discord.ui.button(label='返回世界觀',style=discord.ButtonStyle.secondary,row=1)
 async def back(self,i,b):await i.response.edit_message(content=core().world_summary(i.guild_id),view=core().WorldSettingsView(i))

class OrganizationDetailView(Guard):
 def __init__(self,i,key):
  super().__init__(i);self.key=key
 @discord.ui.button(label='編輯各項規則說明',style=discord.ButtonStyle.primary)
 async def edit(self,i,b):await i.response.send_modal(OrgModal(i.guild_id,self.key))
 @discord.ui.button(label='啟用／停用',style=discord.ButtonStyle.secondary)
 async def toggle(self,i,b):
  data=core().world_get(i.guild_id)
  section=data.setdefault('company_rules',{})
  x=section.get(self.key,{})
  if not isinstance(x,dict):x={}
  x['enabled']=not x.get('enabled',True)
  section[self.key]=x
  data.setdefault('overrides',{})['company_rules.'+self.key]=True
  core().world_save(i.guild_id,data)
  await i.response.edit_message(content=organization_detail(i.guild_id,self.key),view=OrganizationDetailView(i,self.key))
 @discord.ui.button(label='返回組織清單',style=discord.ButtonStyle.secondary,row=1)
 async def back(self,i,b):await i.response.edit_message(content=organization_summary(i.guild_id),view=OrganizationHomeView(i))
