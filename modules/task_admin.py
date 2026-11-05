
"""Official task management via existing /官方 → 內容建立 → 任務 route.
No standalone slash commands. Permissions are task-scoped grants maintained by guild owner.
"""
from __future__ import annotations
import json
import discord
from core.database import get_connection
from modules.task_repository import initialize,publish_task,get_definition,admin_update,admin_set_status
from modules.task_permissions import authorized,grant
from modules.task_validation import parse_object,validate_rules,validate_rewards
from modules.task_audit import log,recent
from modules.task_review import pending,decide

KINDS={'daily':'日常','screen':'影視','variety':'綜藝','endorsement':'代言','limited':'限時','special':'特殊'}

async def reply(i,message):
    if i.response.is_done():await i.followup.send(str(message)[:1900],ephemeral=True)
    else:await i.response.send_message(str(message)[:1900],ephemeral=True)

class CreateTask(discord.ui.Modal,title='建立任務草稿'):
    kind=discord.ui.TextInput(label='分類代碼',placeholder='daily / screen / variety ...',max_length=20)
    code=discord.ui.TextInput(label='唯一識別碼',max_length=60)
    title_input=discord.ui.TextInput(label='名稱',max_length=100)
    description=discord.ui.TextInput(label='內容',style=discord.TextStyle.paragraph,max_length=800)
    subtype=discord.ui.TextInput(label='作品類型（如電影、訪談）',required=False,max_length=60)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'無權建立任務')
        k=str(self.kind).strip()
        if k not in KINDS:return await reply(i,'分類需為 '+', '.join(KINDS))
        try:
            publish_task(i.guild_id,str(self.code).strip(),k,str(self.title_input).strip(),
               description=str(self.description),subtype=str(self.subtype).strip(),status='draft')
            log(i.guild_id,i.user.id,'create',None,{'code':str(self.code)})
        except (ValueError,Exception) as e:return await reply(i,'無法建立：'+str(e)[:170])
        await reply(i,'✅ 草稿已建立。請使用「編輯」設定條件、獎勵與回報方式，再發布。')

class EditTask(discord.ui.Modal,title='編輯任務欄位'):
    task_id=discord.ui.TextInput(label='任務 ID',placeholder='例如 12',max_length=12)
    field=discord.ui.TextInput(label='欄位',placeholder='title / description / level / requirements_json / rewards_json / report_json / subtype',max_length=40)
    value=discord.ui.TextInput(label='新值；JSON 欄位輸入完整 JSON',style=discord.TextStyle.paragraph,max_length=1800)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'無權修改任務')
        try:
            rid=int(str(self.task_id)); field=str(self.field).strip(); val=str(self.value).strip()
            allowed={'title','description','level','requirements_json','rewards_json','report_json','subtype','starts_at','ends_at'}
            if field not in allowed:raise ValueError('不支援欄位：'+field)
            row=get_definition(i.guild_id,rid)
            if not row:raise ValueError('任務不存在')
            if field=='level':val=int(val)
            elif field.endswith('_json'):
                val=parse_object(val)
                if field=='requirements_json':validate_rules(val)
                if field=='rewards_json':validate_rewards(val)
            elif field in ('starts_at','ends_at') and not val:val=None
            result=admin_update(i.guild_id,rid,row['version'],{field:val})
            log(i.guild_id,i.user.id,'edit',rid,{'field':field,'version':result['version']})
        except (ValueError,TypeError) as e:return await reply(i,'編輯失敗：'+str(e))
        await reply(i,f'✅ #{rid} 已修改；新版本 {result["version"]}。已接通告沿用舊條件快照。')

class StatusTask(discord.ui.Modal,title='任務發布與停用'):
    task_id=discord.ui.TextInput(label='任務 ID',max_length=12)
    state=discord.ui.TextInput(label='新狀態',placeholder='draft / published / paused / archived',max_length=20)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'無權管理狀態')
        try:
            rid=int(str(self.task_id));state=str(self.state).strip()
            row=get_definition(i.guild_id,rid)
            if not row:raise ValueError('找不到任務')
            updated=admin_set_status(i.guild_id,rid,state,row['version'])
            log(i.guild_id,i.user.id,'status',rid,{'from':row['status'],'to':state})
        except (ValueError,TypeError) as e:return await reply(i,'無法變更：'+str(e))
        await reply(i,f'✅ #{rid} → {updated["status"]}。發布不代表獎勵已可自動發放。')

class ProfileModal(discord.ui.Modal,title='作品五級條件模板'):
    subtype=discord.ui.TextInput(label='作品類型',placeholder='電影 / 短劇 / 訪談',max_length=60)
    level=discord.ui.TextInput(label='等級 (1-5)',max_length=1)
    rules=discord.ui.TextInput(label='最低條件 JSON',style=discord.TextStyle.paragraph,placeholder='{"min_cafe_level":2,"min_attributes":{"演技":35}}',max_length=1800)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'無權設定條件')
        from modules.task_conditions import set_profile
        try:
            parsed=validate_rules(parse_object(str(self.rules)))
            set_profile(i.guild_id,str(self.subtype).strip(),int(str(self.level)),parsed)
            log(i.guild_id,i.user.id,'profile',None,{'subtype':str(self.subtype),'level':str(self.level)})
        except (ValueError,TypeError) as e:return await reply(i,'模板無法保存：'+str(e))
        await reply(i,'✅ 條件模板已保存。玩家端只顯示最低要求。')

class GrantModal(discord.ui.Modal,title='群主授權任務管理員'):
    user_id=discord.ui.TextInput(label='Discord 使用者 ID',max_length=22)
    action=discord.ui.TextInput(label='授權或撤銷',placeholder='grant / revoke',max_length=10)
    async def on_submit(self,i):
        if not i.guild or i.user.id!=i.guild.owner_id:return await reply(i,'只有群主可授權')
        try:
            act=str(self.action).strip().lower()
            if act not in ('grant','revoke'):raise ValueError('請輸入 grant 或 revoke')
            target=str(self.user_id).strip()
            if not target.isdigit():raise ValueError('使用者 ID 必須為數字')
            grant(i.guild_id,i.guild.owner_id,i.user.id,target,act=='grant')
            log(i.guild_id,i.user.id,act,None,{'user_id':target})
        except ValueError as e:return await reply(i,str(e))
        await reply(i,'✅ 權限已更新')

class ReviewModal(discord.ui.Modal,title='審核任務回報'):
    character_id=discord.ui.TextInput(label='角色 ID',max_length=12)
    task_id=discord.ui.TextInput(label='任務 ID',max_length=12)
    cycle=discord.ui.TextInput(label='週期鍵',default='once',max_length=50)
    decision=discord.ui.TextInput(label='approve / reject',max_length=10)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'無權審核')
        try:
            action=str(self.decision).strip().lower()
            if action not in ('approve','reject'):raise ValueError('請選 approve 或 reject')
            result=decide(i.guild_id,i.user.id,i.guild.owner_id,int(str(self.character_id)),int(str(self.task_id)),str(self.cycle),action=='approve')
        except (ValueError,TypeError,PermissionError) as e:return await reply(i,'審核失敗：'+str(e))
        if action=='reject':
            await reply(i,'✅ 回報已退回，玩家可依規則重新回報。')
        elif result.get('claimed'):
            await reply(i,'✅ 回報通過；金幣及數值獎勵已結算（若有配置）。')
        else:
            await reply(i,'✅ 回報通過，但有獎勵尚待發放：'+str(result.get('error') or result.get('items') or '請執行補發檢查'))

class TaskAdminView(discord.ui.View):
    def __init__(self,user_id,guild_id):
        super().__init__(timeout=300);self.user_id=user_id;self.guild_id=guild_id
    async def interaction_check(self,i):
        if i.guild_id!=self.guild_id or i.user.id!=self.user_id or not authorized(i):
            await reply(i,'沒有這個任務管理面板的權限');return False
        return True
    @discord.ui.button(label='建立草稿',style=discord.ButtonStyle.primary,row=0)
    async def create(self,i,b):await i.response.send_modal(CreateTask())
    @discord.ui.button(label='編輯任務',row=0)
    async def edit(self,i,b):await i.response.send_modal(EditTask())
    @discord.ui.button(label='發布／停用',row=0)
    async def status(self,i,b):await i.response.send_modal(StatusTask())
    @discord.ui.button(label='五級條件',row=0)
    async def profile(self,i,b):await i.response.send_modal(ProfileModal())
    @discord.ui.button(label='授權管理員',row=1)
    async def permissions(self,i,b):await i.response.send_modal(GrantModal())
    @discord.ui.button(label='審核回報',row=1)
    async def review(self,i,b):await i.response.send_modal(ReviewModal())
    @discord.ui.button(label='待審清單',row=1)
    async def pending_list(self,i,b):
        rows=pending(i.guild_id)
        await reply(i,'\n'.join(
            f"角色#{r['character_id']} 任務#{r['task_id']} 週期:{r['cycle_key']} {r['title']}\n"
            f"證據：{('https://discord.com/channels/'+str(i.guild_id)+'/'+str(r['evidence_channel_id'])+'/'+str(r['evidence_message_id'])) if r['evidence_message_id'] else '未綁定留言'}"
            for r in rows) or '目前沒有待審回報')
    @discord.ui.button(label='任務清單',row=2)
    async def listing(self,i,b):
        initialize()
        with get_connection() as db:
            rows=db.execute('SELECT id,title,kind,level,status,version FROM rp_task_definitions WHERE guild_id=? ORDER BY id DESC LIMIT 25',(str(i.guild_id),)).fetchall()
        await reply(i,'\n'.join(f"#{r['id']} [{r['kind']} L{r['level']} {r['status']} v{r['version']}] {r['title']}" for r in rows) or '尚無任務')
    @discord.ui.button(label='操作紀錄',row=2)
    async def audit(self,i,b):
        rows=recent(i.guild_id,10)
        await reply(i,'\n'.join(f"#{r['id']} {r['action']} task:{r['task_id']} by:{r['actor_id']}" for r in rows) or '目前沒有紀錄')

async def open_task_admin(i):
    if not authorized(i):return await reply(i,'僅群主與正式授權的任務管理員可以使用')
    initialize()
    view=VarietyTaskAdminView(i.user.id,i.guild_id)
    msg='📋 **官方任務管理**\n建立、編輯、設定五級門檻與發布／停用。\n未發布任務不會出現在玩家清單；任務金幣與數值可結算；官方商城正式商品可發入背包；其他不支援商品留待補發。'
    if i.response.is_done():await i.followup.send(msg,view=view,ephemeral=True)
    else:await i.response.send_message(msg,view=view,ephemeral=True)

# 綜藝專屬管理入口以原本 /官方 → 任務管理 View 掛載，不新增 slash 指令。
class VarietyCastDecision(discord.ui.Modal,title='綜藝常駐錄取審核'):
    application_id=discord.ui.TextInput(label='綜藝申請編號',max_length=12)
    decision=discord.ui.TextInput(label='approve 或 reject',max_length=10)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'沒有審核權限')
        from modules.task_variety_cast import decide
        try:
            d=str(self.decision).strip().lower()
            if d not in ('approve','reject'):raise ValueError('請輸入 approve 或 reject')
            decide(i.guild_id,int(str(self.application_id)),i.user.id,i.guild.owner_id,d=='approve')
        except (ValueError,PermissionError) as e:return await reply(i,str(e))
        await reply(i,'✅ 已'+('錄取常駐嘉賓，後續可接取該節目已公告期數。' if d=='approve' else '拒絕申請。'))

class VarietyNewEpisode(discord.ui.Modal,title='發布綜藝下一期'):
    show_key=discord.ui.TextInput(label='節目代碼（night/cook/escape...）',max_length=40)
    episode=discord.ui.TextInput(label='期數（整數）',max_length=5)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'沒有發布權限')
        from modules.task_variety_seed import publish_episode
        try:n=publish_episode(i.guild_id,str(self.show_key).strip(),int(str(self.episode)))
        except ValueError as e:return await reply(i,str(e))
        await reply(i,f'✅ 期數已檢查；新增 {n} 個通告職位（已存在的保持不變）。')

class VarietyBroadcast(discord.ui.Modal,title='綜藝正式播出與曝光結算'):
    show_key=discord.ui.TextInput(label='節目代碼',max_length=40)
    episode=discord.ui.TextInput(label='期數',max_length=5)
    performance=discord.ui.TextInput(label='表現倍數 0.5 / 1 / 1.5 / 2',default='1',max_length=5)
    async def on_submit(self,i):
        if not authorized(i):return await reply(i,'沒有播出權限')
        from modules.task_variety_cast import broadcast
        try:
            count=broadcast(i.guild_id,str(self.show_key).strip(),int(str(self.episode)),i.user.id,i.guild.owner_id,float(str(self.performance)))
        except (ValueError,PermissionError) as e:return await reply(i,str(e))
        except Exception:
            import logging
            logging.getLogger(__name__).exception('綜藝播出獎勵失敗')
            return await reply(i,'播出處理中斷；可重試，獎勵具有冪等保護。')
        await reply(i,f'✅ 綜藝已播出，已處理 {count} 名完成當期錄製的演出者（重試不會重複發獎）。')

async def _variety_pending(i):
    from modules.task_variety_cast import pending
    rows=pending(i.guild_id)
    await reply(i,'\n'.join(f"#{r['id']} 《{r['title']}》角色#{r['character_id']}：{r['seat']}" for r in rows) or '目前沒有待審的綜藝常駐申請')

# 子類實現 UI 新按鈕，保持原本 TaskAdminView 與其他官方管理不受影響。
class VarietyTaskAdminView(TaskAdminView):
    @discord.ui.button(label='綜藝待審',row=2)
    async def variety_pending(self,i,b):await _variety_pending(i)
    @discord.ui.button(label='審核綜藝',row=2)
    async def variety_review(self,i,b):await i.response.send_modal(VarietyCastDecision())
    @discord.ui.button(label='綜藝下一期',row=3)
    async def variety_episode(self,i,b):await i.response.send_modal(VarietyNewEpisode())
    @discord.ui.button(label='綜藝播出',row=3)
    async def variety_air(self,i,b):await i.response.send_modal(VarietyBroadcast())
