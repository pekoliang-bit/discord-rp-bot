
"""V2.3 NPC 人設匯入、認領雙審與雙素材庫 UI；不修改角色初始屬性、金錢或額外加成。"""
from __future__ import annotations
import json
import re
import sqlite3
import discord
from core.database import transaction, get_connection
from core.history_v23 import init as history_init,put as asset_put,disable as asset_disable,stats as asset_stats,attempt_lock,put_batch

CUSTOM = re.compile(r'\(\s*(?:玩家|其他|才藝)?(?:自定義|自訂|自行填寫|待填)(?:[^)]*)\)|（\s*(?:玩家|其他|才藝)?(?:自定義|自訂|自行填寫|待填)(?:[^）]*)）',re.I)
FIELD = re.compile(r'^\s*(?:[-*]\s*)?([^：:\n]{1,38})\s*[：:]\s*(.*?)\s*$')
PROTECTED=('持有金錢','健康值','流量熱度','商業價值','路人緣','公關','影響指數','容貌','才智','唱跳','演繹','主持','歌唱','綜藝','個人才藝','死忠粉絲數','活躍黑粉數','大眾認知度','CP 好感度')

def init(db):
    history_init(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_npc_templates_v23 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, source_message TEXT NOT NULL,
        channel_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
        raw_text TEXT NOT NULL, fields_json TEXT NOT NULL, custom_json TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'available', claimed_by TEXT, revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(guild_id,source_message),
        CHECK(kind IN ('player_npc','official_npc')),
        CHECK(state IN ('available','reserved','claimed','disabled'))
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_npc_claims_v23 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, template_id INTEGER NOT NULL,
        applicant_id TEXT NOT NULL, answers_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'review_1',
        first_reviewer TEXT, second_reviewer TEXT, character_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id,template_id,id), CHECK(status IN ('review_1','review_2','approved','rejected'))
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_npc_claims_status_v23 ON rp_npc_claims_v23(guild_id,status,id)''')

def parse_text(text):
    fields={};custom={}
    for line in text.splitlines():
        m=FIELD.match(line)
        if not m:continue
        key,val=m.group(1).strip().lstrip('# ').strip(),m.group(2).strip()
        if key=='持有金錢':continue  # 預設 NPC 的金額一律移除，正式啟用後由另行核准的經濟流程決定。
        if key in fields:continue
        fields[key]=val
        if key in PROTECTED:
            if key=='個人才藝' and CUSTOM.search(val):
                custom['個人才藝名稱']=''
            continue
        if not val or CUSTOM.search(val):
            custom[key]=val
    name=(fields.get('本名／藝名') or fields.get('藝名') or fields.get('本名') or fields.get('角色姓名') or '').strip()
    if '/' in name:name=name.split('/')[-1].strip()
    if '／' in name:name=name.split('／')[-1].strip()
    if not name or len(name)>100:raise ValueError('人設單必須包含可辨識的「本名／藝名」或「角色姓名」。')
    # 玩家 NPC 也必須具備生日，否則正式開群後無法核發公開編號。
    # 舊模板沒有這個欄位時，僅新增「待填」而不擅自編造出生日期。
    if '生日' not in fields and '生日（月／日）' not in fields:
        fields['生日（月／日）']='（自定義）'
        custom['生日（月／日）']='（自定義）'
    return name,fields,custom

def profile_settings_from_sheet(fields):
    """只映射已填妥的敘事資料；數值、金幣不在本功能修改。"""
    def choose(*keys):
        for key in keys:
            val=str(fields.get(key,'')).strip()
            if val and not CUSTOM.search(val) and val not in ('待填','無','（待填）'):
                return val
        return ''
    result={}
    for attr,source in {
        'nationality':('國籍',), 'education':('學歷',),
        'languages':('擅長語言','語言'), 'biography':('生平簡述',),
        'birthday':('生日（月／日）','生日'),
        'main_track':('主攻賽道',), 'portrait_url':('角色照片','角色形象圖片','角色形象'),
        'public_persona':('外貌氣質','性格'), 'private_personality':('私下性格',),
    }.items():
        value=choose(*source)
        if value:result[attr]=value
    full=choose('本名／藝名','本名/藝名')
    if full:
        parts=re.split(r'[/／]',full,maxsplit=1)
        if len(parts)==2:
            if parts[0].strip():result['real_name']=parts[0].strip()
            if parts[1].strip():result['stage_name']=parts[1].strip()
        else:result['stage_name']=full
    else:
        stage=choose('藝名')
        if stage:result['stage_name']=stage
        name=choose('本名')
        if name:result['real_name']=name
    gender=choose('性別／性向','性別/性向','性別')
    if gender:result['gender']=re.split(r'[/／]',gender)[0].strip()
    height=choose('身高／體重','身高/體重','身高')
    if height:result['height_cm']=re.split(r'[/／]',height)[0].replace('cm','').strip()
    weight=choose('身高／體重','身高/體重','體重')
    if weight:
        parts=re.split(r'[/／]',weight)
        if len(parts)==2:result['weight_kg']=parts[1].replace('kg','').strip()
        elif '體重' in fields:result['weight_kg']=weight.replace('kg','').strip()
    years=choose('出道資歷','資歷年數')
    if years:
        m=re.search(r'\d+',years)
        if m:result['experience_years']=int(m.group(0))
    # 生日是唯一的公开号碼日期來源，沿用 /角色填寫 的驗證規則。
    if 'birthday' in result:
        from modules.character_numbering import normalize_birthday
        valid=normalize_birthday(result['birthday'])
        if not valid:raise ValueError('NPC 生日必須是有效的月／日，例如 04/18。')
        result['birthday']=f'{valid[:2]}/{valid[2:]}'
    result['npc_sheet_fields']={k:v for k,v in fields.items() if k!='持有金錢'}
    return result

def import_template(gid,message,channel,body,kind):
    name,fields,custom=parse_text(body)
    if kind not in ('player_npc','official_npc'):raise ValueError('NPC 類型不正確。')
    with transaction() as db:
        init(db)
        prev=db.execute('SELECT id FROM rp_npc_templates_v23 WHERE guild_id=? AND source_message=?',(str(gid),str(message))).fetchone()
        if prev:raise ValueError(f'原訊息已匯入模板 #{prev["id"]}，不可重複匯入。')
        row=db.execute('''INSERT INTO rp_npc_templates_v23
           (guild_id,source_message,channel_id,name,kind,raw_text,fields_json,custom_json,state)
           VALUES (?,?,?,?,?,?,?,?,'available')''',(str(gid),str(message),str(channel),name,kind,body,
           json.dumps(fields,ensure_ascii=False),json.dumps(custom,ensure_ascii=False))).lastrowid
        return row,name,len(custom)

def available(gid,page=0):
    with get_connection() as db:
        init(db)
        return [dict(r) for r in db.execute("SELECT id,name,custom_json FROM rp_npc_templates_v23 WHERE guild_id=? AND kind='player_npc' AND state='available' ORDER BY id LIMIT 25 OFFSET ?",(str(gid),max(0,int(page))*25))]

def claim(gid,uid,tid,answers):
    with transaction() as db:
        init(db)
        existing=db.execute('''SELECT 1 FROM rp_npc_claims_v23 WHERE guild_id=? AND applicant_id=? AND status IN ('review_1','review_2','approved')''',(str(gid),str(uid))).fetchone()
        owned=db.execute("SELECT 1 FROM characters WHERE guild_id=? AND owner_id=? AND character_type='player_npc' AND status!='deleted'",(str(gid),str(uid))).fetchone()
        if existing or owned:raise ValueError('每位玩家只能認領一名玩家 NPC，且不可同時提交多份申請。')
        template=db.execute("SELECT * FROM rp_npc_templates_v23 WHERE guild_id=? AND id=? AND kind='player_npc' AND state='available'",(str(gid),int(tid))).fetchone()
        if template is None:raise ValueError('該 NPC 已被申請或不存在，請刷新名單。')
        fields=json.loads(template['custom_json'])
        missing=[f for f in fields if not answers.get(f,'').strip()]
        if missing:raise ValueError('缺少自定義欄位：'+ '、'.join(missing[:10]))
        if set(answers)-set(fields):raise ValueError('不可變更 NPC 固定欄位。')
        birthday=answers.get('生日（月／日）') or answers.get('生日')
        if birthday:
            from modules.character_numbering import normalize_birthday
            if not normalize_birthday(birthday):raise ValueError('生日格式錯誤，請使用月／日，例如 04/18。')
        changed=db.execute("UPDATE rp_npc_templates_v23 SET state='reserved',claimed_by=? WHERE id=? AND state='available'",(str(uid),int(tid)))
        if changed.rowcount!=1:raise ValueError('角色已被其他申請佔用。')
        return db.execute('INSERT INTO rp_npc_claims_v23 (guild_id,template_id,applicant_id,answers_json) VALUES (?,?,?,?)',
            (str(gid),int(tid),str(uid),json.dumps(answers,ensure_ascii=False))).lastrowid

def pending(gid):
    with get_connection() as db:
        init(db)
        return [dict(r) for r in db.execute('''SELECT c.id,c.status,c.applicant_id,t.name,t.id AS template_id
            FROM rp_npc_claims_v23 c JOIN rp_npc_templates_v23 t ON t.id=c.template_id
            WHERE c.guild_id=? AND c.status IN ('review_1','review_2') ORDER BY c.id LIMIT 25''',(str(gid),))]

def decide(gid,cid,reviewer,guild_owner,approve):
    with transaction() as db:
        init(db)
        row=db.execute('''SELECT c.*,t.name,t.fields_json,t.kind,t.id AS template_key,t.custom_json,t.source_message,t.channel_id
        FROM rp_npc_claims_v23 c JOIN rp_npc_templates_v23 t ON t.id=c.template_id
        WHERE c.guild_id=? AND c.id=?''',(str(gid),int(cid))).fetchone()
        if not row or row['status'] not in ('review_1','review_2'):raise ValueError('案件已處理或不存在。')
        if str(reviewer)==str(row['applicant_id']) and str(reviewer)!=str(guild_owner):raise PermissionError('非群主不可審核自己的申請。')
        if not approve:
            db.execute("UPDATE rp_npc_claims_v23 SET status='rejected' WHERE id=?",(int(cid),))
            db.execute("UPDATE rp_npc_templates_v23 SET state='available',claimed_by=NULL WHERE id=? AND state='reserved'",(row['template_key'],))
            return 'rejected',None
        if row['status']=='review_1':
            db.execute("UPDATE rp_npc_claims_v23 SET status='review_2',first_reviewer=? WHERE id=?",(str(reviewer),int(cid)))
            return 'review_2',None
        # 第二審核准：建立 approved 狀態，啟用另由專門按鈕完成，避免發獎前錯序。
        original=json.loads(row['fields_json']);answers=json.loads(row['answers_json'])
        combined={k:(v if k not in answers else (CUSTOM.sub(answers[k],v) if v else answers[k])) for k,v in original.items()}
        if '個人才藝名稱' in answers:
            combined['個人才藝名稱']=answers['個人才藝名稱']
        age=combined.get('年齡','')
        try: age=int(age)
        except (ValueError,TypeError):age=None
        occupation=combined.get('職業','')
        settings=profile_settings_from_sheet(combined)
        if not settings.get('birthday'):
            raise ValueError('NPC 尚未填寫生日，不能完成第二階段審核。')
        settings.update({'npc_template_id':row['template_key'],
                  'npc_original_message':row['source_message'],'npc_claim_id':int(cid)})
        db.execute('INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)',(str(gid),))
        created=db.execute('''INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,character_type,settings_json)
               VALUES (?,?,?,?,?,'approved','player_npc',?)''',(str(gid),str(row['applicant_id']),row['name'],age,occupation,json.dumps(settings,ensure_ascii=False))).lastrowid
        db.execute("UPDATE rp_npc_claims_v23 SET status='approved',second_reviewer=?,character_id=? WHERE id=?",(str(reviewer),created,int(cid)))
        db.execute("UPDATE rp_npc_templates_v23 SET state='claimed' WHERE id=?",(row['template_key'],))
        return 'approved',created

def activate_claim(gid,uid,claim_id):
    from core.character_activation_v23 import activate_in_transaction,history_notification
    with transaction() as db:
        init(db)
        row=db.execute("SELECT * FROM rp_npc_claims_v23 WHERE guild_id=? AND id=? AND applicant_id=? AND status='approved'",
                       (str(gid),int(claim_id),str(uid))).fetchone()
        if not row or not row['character_id']:
            raise ValueError('找不到已完成二審且屬於你的 NPC 申請。')
        cid=int(row['character_id'])
        # 完整共用原創角色啟用：status、授權、代理身分、正式編號，一筆交易提交。
        # 缺少另一討論串核准的 NPC 數值時直接拒絕，不能以 0 或隨機數值取代。
        activate_in_transaction(db,gid,cid,uid,allowed_types=('player_npc',))
    state=history_notification(gid,cid)
    return cid,state

class ImportNPCModal(discord.ui.Modal,title='匯入 NPC 人設訊息'):
    link=discord.ui.TextInput(label='Discord 角色單訊息連結',max_length=300)
    kind=discord.ui.TextInput(label='類型 player_npc 或 official_npc',default='player_npc',max_length=20)
    async def on_submit(self,i):
        if not i.guild or i.user.id != i.guild.owner_id:
            return await i.response.send_message('僅群主能匯入 NPC 模板。',ephemeral=True)
        try:
            from modules.character_sheet_registration import fetch_sheet
            channel,message=await fetch_sheet(i,str(self.link.value))
            if message.author.id!=i.guild.owner_id and message.author.id!=i.user.id:
                raise ValueError('人設單訊息須由群主發表。')
            kind=str(self.kind.value).strip()
            template_id,name,count=import_template(i.guild_id,message.id,channel.id,message.content,kind)
            if kind=='official_npc':
                # 群主提交並確認後視為正式官方授權；採既有角色單登記核心。
                from modules.character_sheet_registration import initialize as links_init,register_sheet
                links_init()
                new_id=register_sheet(i.guild_id,i.user.id,i.guild.owner_id,'official_npc',name,channel.id,message.id,str(self.link.value),character_owner_id=i.guild.owner_id)
                with transaction() as db:
                    db.execute("UPDATE rp_npc_templates_v23 SET state='claimed',claimed_by=? WHERE id=?",(str(i.guild.owner_id),template_id))
                try:
                    attempt_lock(i.guild_id,new_id)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('官方 NPC 歷史補建待處理')
                return await i.response.send_message(f'✅ 官方 NPC「{name}」已由群主授權登記，角色 #{new_id}。\n歷史履歷只有在已核准素材齊全時才建立；不會提前發放數值。',ephemeral=True)
            return await i.response.send_message(f'✅ 已匯入待認領 NPC 模板 #{template_id}「{name}」，偵測 {count} 個玩家自定義欄位。\n尚未啟用，也沒有抽取履歷或發放金錢。',ephemeral=True)
        except (ValueError,PermissionError,sqlite3.Error,discord.HTTPException) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class NPCAnswersModal(discord.ui.Modal,title='NPC 認領｜填寫自定義欄位'):
    def __init__(self,tid,fields):
        super().__init__();self.tid=tid;self.fields=fields
        preview='\n'.join(f'{k}：' for k in fields)
        self.answers=discord.ui.TextInput(label='每行「欄位：內容」，必須填完全部空格',style=discord.TextStyle.paragraph,default=preview[:3900],max_length=4000)
        self.add_item(self.answers)
    async def on_submit(self,i):
        try:
            ans={}
            for line in str(self.answers.value).splitlines():
                m=FIELD.match(line)
                if m:ans[m.group(1).strip()]=m.group(2).strip()
            claim_id=claim(i.guild_id,i.user.id,self.tid,ans)
            await i.response.send_message(f'✅ NPC 申請 #{claim_id} 已送交二階段審核。審核通過前不會建立正式啟用角色或發獎。',ephemeral=True)
        except (ValueError,sqlite3.Error) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class NPCSelect(discord.ui.Select):
    def __init__(self,rows):
        super().__init__(placeholder='選擇尚未被認領的 NPC',options=[discord.SelectOption(label=r['name'][:90],value=str(r['id'])) for r in rows],row=0)
        self.mapping={str(r['id']):json.loads(r['custom_json']) for r in rows}
    async def callback(self,i):
        await i.response.send_modal(NPCAnswersModal(int(self.values[0]),self.mapping[self.values[0]]))

class NPCApplyView(discord.ui.View):
    def __init__(self,i,page=0):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id;self.page=max(0,page)
        rows=available(self.gid,self.page)
        if rows:self.add_item(NPCSelect(rows))
        self.prev.disabled=self.page==0
        self.next.disabled=len(rows)<25
    @discord.ui.button(label='◀️ 上一頁',style=discord.ButtonStyle.secondary,row=1)
    async def prev(self,i,b):
        self.page=max(0,self.page-1)
        await i.response.edit_message(view=NPCApplyView(i,self.page))
    @discord.ui.button(label='下一頁 ▶️',style=discord.ButtonStyle.secondary,row=1)
    async def next(self,i,b):
        self.page+=1
        await i.response.edit_message(view=NPCApplyView(i,self.page))
    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid:
            await i.response.send_message('這不是你的認領介面。',ephemeral=True);return False
        return True

class ReviewNPCModal(discord.ui.Modal,title='NPC 申請雙階段審核'):
    def __init__(self,approve):
        super().__init__();self.approve=approve
        self.case=discord.ui.TextInput(label='NPC 申請編號',max_length=10)
        self.add_item(self.case)
    async def on_submit(self,i):
        from modules.official_center import has_staff_permission
        if not i.guild or not has_staff_permission(i,'reviewer'):
            return await i.response.send_message('沒有正式審核權限。',ephemeral=True)
        try:
            status,cid=decide(i.guild_id,int(self.case.value),i.user.id,i.guild.owner_id,self.approve)
            await i.response.send_message(f'✅ NPC 申請 #{self.case.value}：{status}'+(f'。角色 #{cid} 已通過二審，請由玩家完成啟用。' if cid else ''),ephemeral=True)
        except (ValueError,PermissionError,sqlite3.Error) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class NPCReviewView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid:
            await i.response.send_message('沒有使用權限。',ephemeral=True);return False
        return True
    @discord.ui.button(label='✅ 核准本階段',style=discord.ButtonStyle.success)
    async def approve(self,i,b):await i.response.send_modal(ReviewNPCModal(True))
    @discord.ui.button(label='❌ 退件並釋出',style=discord.ButtonStyle.danger)
    async def reject(self,i,b):await i.response.send_modal(ReviewNPCModal(False))

class ActivateNPCModal(discord.ui.Modal,title='啟用已通過二審的 NPC'):
    claimid=discord.ui.TextInput(label='申請編號',max_length=10)
    async def on_submit(self,i):
        try:
            cid,status=activate_claim(i.guild_id,i.user.id,int(self.claimid.value))
            await i.response.send_message(f'✅ 玩家 NPC #{cid} 已啟用。歷史履歷：{status}。新人禮包請依原有領取入口辦理（不自動開獎）。',ephemeral=True)
        except (ValueError,sqlite3.Error) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class AssetModal(discord.ui.Modal,title='歷史履歷素材新增／核准'):
    pool=discord.ui.TextInput(label='素材池 general 或 passerby',default='general',max_length=15)
    code=discord.ui.TextInput(label='素材編號，如 WN-0001',max_length=12)
    title_text=discord.ui.TextInput(label='素材名稱／事件內容',style=discord.TextStyle.paragraph,max_length=4000)
    tier=discord.ui.TextInput(label='等級與HB引用（如 B1;HB-0046）',required=False,max_length=25)
    rarity=discord.ui.TextInput(label='稀有度 R1～R5（非HE可空）',required=False,max_length=5)
    async def on_submit(self,i):
        if not i.guild or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('僅群主可正式核准素材。',ephemeral=True)
        try:
            tier_parts=str(self.tier.value or '').replace('；',';').split(';',1)
            grade=tier_parts[0].strip()
            hb=tier_parts[1].strip().upper() if len(tier_parts)>1 else ''
            code=str(self.code.value).strip().upper()
            if code.startswith('HE-') and 151 <= int(code.split('-')[1]) <= 230:
                if not hb:raise ValueError('歷史代言必須同時指定已核准的 HB 編號，例如 B1;HB-0046。')
                if not re.fullmatch(r'HB-\d{4}',hb):raise ValueError('HB 編號格式不正確。')
            label=str(self.title_text.value)
            meta={'hb_id':hb} if hb else {}
            if label.lstrip().startswith('{'):
                payload=json.loads(label)
                if not isinstance(payload,dict) or not isinstance(payload.get('title'),str):
                    raise ValueError('素材 JSON 必須具有 title 欄位。')
                label=payload['title']
                for key in ('work_type','occupations','roles'):
                    if key in payload:meta[key]=payload[key]
                if code.startswith('HE-') and payload.get('hb_id'):
                    meta['hb_id']=payload['hb_id']
            asset_put(i.guild_id,str(self.pool.value),code,label,grade,str(self.rarity.value or ''),meta=meta,approved=True)
            await i.response.send_message(f'✅ {self.code.value} 已登記為核准素材；不會因此發放任何數值。',ephemeral=True)
        except (ValueError,sqlite3.Error) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class AssetStateModal(discord.ui.Modal,title='停用／恢復素材'):
    pool=discord.ui.TextInput(label='general 或 passerby',default='general',max_length=15)
    code=discord.ui.TextInput(label='素材編號',max_length=12)
    enabled=discord.ui.TextInput(label='輸入 1 恢復，0 停用',max_length=1)
    async def on_submit(self,i):
        if not i.guild or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('僅群主可管理素材。',ephemeral=True)
        try:
            if str(self.enabled.value) not in ('0','1'):raise ValueError('只接受 0／1。')
            asset_disable(i.guild_id,str(self.pool.value),str(self.code.value),str(self.enabled.value)=='1')
            await i.response.send_message('✅ 素材狀態更新成功；既有角色歷史不變。',ephemeral=True)
        except (ValueError,sqlite3.Error) as exc:await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

class AssetBulkImportModal(discord.ui.Modal,title='批次匯入已核准的歷史素材'):
    pool=discord.ui.TextInput(label='素材庫 general 或 passerby',default='general',max_length=15)
    link=discord.ui.TextInput(label='Discord JSON 素材訊息連結',max_length=300)
    async def on_submit(self,i):
        if not i.guild or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('只有群主能批次匯入素材。',ephemeral=True)
        try:
            from modules.character_sheet_registration import fetch_sheet
            _channel,msg=await fetch_sheet(i,str(self.link.value))
            if msg.author.id!=i.guild.owner_id:raise ValueError('批次素材訊息必須由群主發表。')
            raw=msg.content.strip()
            if raw.startswith('```'):
                raw=raw.split('\n',1)[1].rsplit('```',1)[0]
            rows=json.loads(raw)
            count=put_batch(i.guild_id,rows,pool=str(self.pool.value).strip())
            await i.response.send_message(f'✅ 已驗證並處理 {count} 筆素材（相同內容不重複插入）。',ephemeral=True)
        except (ValueError,KeyError,TypeError,sqlite3.Error,discord.HTTPException) as exc:
            await i.response.send_message(f'⚠️ 整批匯入失敗：{exc}',ephemeral=True)

class VerifiedSeedButton(discord.ui.Button):
    def __init__(self):super().__init__(label='📌 匯入已核實5品牌＋5代言',style=discord.ButtonStyle.secondary,row=1)
    async def callback(self,i):
        if not i.guild or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('僅群主能正式匯入素材。',ephemeral=True)
        from pathlib import Path
        entries=json.loads((Path(__file__).resolve().parents[1]/'assets/history_v23/verified_five.json').read_text(encoding='utf-8'))
        added=0;skipped=0
        try:
            for x in entries:
                with get_connection() as db:
                    history_init(db)
                    old=db.execute('SELECT title,tier,rarity,metadata_json FROM rp_history_assets_v23 WHERE guild_id=? AND pool=? AND asset_id=?',
                        (str(i.guild_id),x['pool'],x['asset_id'])).fetchone()
                if old:
                    if old['title']!=x['title'] or old['tier']!=x['tier']:
                        raise ValueError(f'{x["asset_id"]} 已存在不同內容，拒絕自動覆蓋。')
                    skipped+=1;continue
                asset_put(i.guild_id,x['pool'],x['asset_id'],x['title'],x.get('tier',''),x.get('rarity',''),x.get('meta',{}),approved=True)
                added+=1
            await i.response.send_message(f'✅ 已匯入 {added} 筆已核實素材；{skipped} 筆既有相同編號略過。其他75則代言仍需要 HB 對照。',ephemeral=True)
        except (OSError,ValueError,sqlite3.Error) as exc:
            await i.response.send_message(f'⚠️ 匯入中止：{exc}。已匯入的既有資料不會自動覆蓋。',ephemeral=True)

class AssetManagerView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id
        self.add_item(VerifiedSeedButton())
    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not i.guild or i.user.id!=i.guild.owner_id:
            await i.response.send_message('僅群主能操作此素材管理面板。',ephemeral=True);return False
        return True
    @discord.ui.button(label='➕ 新增／核准素材',style=discord.ButtonStyle.success)
    async def add(self,i,b):await i.response.send_modal(AssetModal())
    @discord.ui.button(label='⏸️ 停用／恢復',style=discord.ButtonStyle.secondary)
    async def state(self,i,b):await i.response.send_modal(AssetStateModal())
    @discord.ui.button(label='📥 批次匯入',style=discord.ButtonStyle.secondary,row=1)
    async def bulk(self,i,b):await i.response.send_modal(AssetBulkImportModal())
    @discord.ui.button(label='🔄 補建已啟用角色',style=discord.ButtonStyle.secondary,row=2)
    async def rebuild(self,i,b):
        await HistoryRebuildButton().callback(i)
    @discord.ui.button(label='📊 雙素材庫統計',style=discord.ButtonStyle.primary)
    async def stats(self,i,b):
        lines=[]
        for pool in ('general','passerby'):
            summary=asset_stats(i.guild_id,pool)
            lines.append(f'**{pool}**：'+('、'.join(f'{x["category"]} {x["enabled"]}/{x["total"]}' for x in summary) if summary else '尚無已匯入素材'))
        await i.response.send_message('\n'.join(lines),ephemeral=True)

class HistoryRebuildButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🔄 補建待完成歷史',style=discord.ButtonStyle.secondary,row=2)
    async def callback(self,i):
        if not i.guild or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('只有群主可執行補建。',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        with get_connection() as db:
            history_init(db)
            rows=db.execute("""SELECT c.id FROM characters c LEFT JOIN rp_history_results_v23 h
                ON h.guild_id=c.guild_id AND h.character_id=c.id
                WHERE c.guild_id=? AND c.status='active' AND c.character_type IN ('player','player_npc','official_npc')
                GROUP BY c.id HAVING COUNT(h.slot)=0 ORDER BY c.id LIMIT 25""",(str(i.guild_id),)).fetchall()
        results={'locked':0,'pending_assets':0,'pending_seat':0,'already':0,'error':0}
        for row in rows:
            try:
                state=attempt_lock(i.guild_id,row['id']);results[state]=results.get(state,0)+1
            except (ValueError,sqlite3.Error):results['error']+=1
        await i.followup.send('📜 歷史補建檢查：'+('、'.join(f'{k} {v}' for k,v in results.items() if v) or '目前沒有待補建角色')+'。\n不重抽已有五筆紀錄、不發數值或新人禮包。',ephemeral=True)

class NPCRegisterButton(discord.ui.Button):
    def __init__(self):super().__init__(label='📋 匯入 NPC 預設人設',style=discord.ButtonStyle.secondary,row=2)
    async def callback(self,i):
        if not i.guild or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('僅群主可操作。',ephemeral=True)
        await i.response.send_modal(ImportNPCModal())

class NPCActivateButton(discord.ui.Button):
    def __init__(self):super().__init__(label='✅ 啟用已核准 NPC',style=discord.ButtonStyle.secondary,row=2)
    async def callback(self,i):await i.response.send_modal(ActivateNPCModal())

class NPCCreateButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🎭 NPC 申請',style=discord.ButtonStyle.primary,row=1)
    async def callback(self,i):
        v=NPCApplyView(i)
        await i.response.send_message('🎭 選擇尚未認領的玩家 NPC；只會要求填寫人設單中標記的空白／自定義欄位。' if any(isinstance(x,NPCSelect) for x in v.children) else '目前沒有可認領的玩家 NPC。',view=v,ephemeral=True)

class NPCReviewSelectButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🧾 NPC 認領審核',style=discord.ButtonStyle.secondary)
    async def callback(self,i):
        from modules.official_center import has_staff_permission
        if not has_staff_permission(i,'reviewer'):
            return await i.response.send_message('沒有審核權限。',ephemeral=True)
        rows=pending(i.guild_id)
        content='\n'.join(f'#{r["id"]}｜{r["name"]}｜{r["status"]}｜申請人 {r["applicant_id"]}' for r in rows)
        await i.response.send_message('🧾 NPC 待審案件\n'+(content or '目前無待審案件'),view=NPCReviewView(i),ephemeral=True)
