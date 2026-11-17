"""內測角色安全生命週期。測試資源不繼承至正式角色。"""
from __future__ import annotations
import json
import random
from core.database import transaction

# 自訂內測角色姓名池；使用完整姓名，不拆字、不加前後綴。
MALE_NAMES = (
    '志明', '建國', '正勇', '明輝', '志強', '志勇', '建軍', '冠宇',
    '冠廷', '冠永', '健宏', '家豪', '龍傲天', '國偉',
    '詹姆斯・傑克・安東尼・馮・埃里克森・瓦倫・亨利十世',
    '顧北辰', '✨閃耀の萌え萌え軟糖啾💋',
)
FEMALE_NAMES = (
    '晏韶', '常曦', '君璽', '知月', '灼華', '執宸', '九霄',
    '承熙', '昭寧', '伊莉莎白', '維多利亞', '葉卡捷琳娜',
    '玉骨', '芙蕖', '扶光', '靈澤', '拂曉',
)

def random_beta_identity():
    """男／女各50%抽取，姓名池內每個名字等機率。"""
    gender, pool = random.choice((('男', MALE_NAMES), ('女', FEMALE_NAMES)))
    return gender, random.choice(pool)

def setup():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_beta_state (
            guild_id TEXT PRIMARY KEY,
            ended INTEGER NOT NULL DEFAULT 0,
            ended_by TEXT,
            ended_at TEXT
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_beta_characters (
            guild_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            draft_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            PRIMARY KEY(guild_id, owner_id),
            UNIQUE(guild_id, character_id),
            CHECK(status IN ('active','ended','drafted'))
        )""")
        cols = {row[1] for row in db.execute('PRAGMA table_info(rp_beta_characters)')}
        if 'assigned_name' not in cols:
            db.execute('ALTER TABLE rp_beta_characters ADD COLUMN assigned_name TEXT')
        # 已領取且仍保留原始抽中姓名的舊測試角色，補記名稱占用。
        names = set(MALE_NAMES + FEMALE_NAMES)
        assigned = {(str(r['guild_id']), str(r['assigned_name'])) for r in db.execute(
            'SELECT guild_id, assigned_name FROM rp_beta_characters WHERE assigned_name IS NOT NULL')}
        for row in db.execute("""SELECT b.guild_id,b.owner_id,c.name
            FROM rp_beta_characters b JOIN characters c
              ON c.guild_id=b.guild_id AND c.id=b.character_id
            WHERE b.assigned_name IS NULL""").fetchall():
            if row['name'] in names and (str(row['guild_id']),str(row['name'])) not in assigned:
                assigned.add((str(row['guild_id']),str(row['name'])))
                db.execute("""UPDATE rp_beta_characters SET assigned_name=?
                    WHERE guild_id=? AND owner_id=?""",
                    (row['name'],row['guild_id'],row['owner_id']))
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_beta_assigned_name
            ON rp_beta_characters(guild_id, assigned_name)
            WHERE assigned_name IS NOT NULL""")

def remaining_names(guild_id):
    """每服每個抽中姓名只能領取一次，已使用的不重複釋出。"""
    with transaction() as db:
        used = {r[0] for r in db.execute(
            'SELECT assigned_name FROM rp_beta_characters WHERE guild_id=? AND assigned_name IS NOT NULL',
            (str(guild_id),))}
    return tuple(x for x in MALE_NAMES+FEMALE_NAMES if x not in used)

def can_claim(guild_id):
    with transaction() as db:
        ended=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(str(guild_id),)).fetchone()
        if ended and ended['ended']: return False
        used={r[0] for r in db.execute(
            'SELECT assigned_name FROM rp_beta_characters WHERE guild_id=? AND assigned_name IS NOT NULL',
            (str(guild_id),))}
        return any(name not in used for name in MALE_NAMES+FEMALE_NAMES)

def has_beta(guild_id, owner_id):
    with transaction() as db:
        return bool(db.execute('SELECT 1 FROM rp_beta_characters WHERE guild_id=? AND owner_id=?',
            (str(guild_id),str(owner_id))).fetchone())

def beta_ended(guild_id):
    with transaction() as db:
        row=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(str(guild_id),)).fetchone()
        return bool(row and row['ended'])

def claim(guild_id, owner_id):
    """每位玩家每服一隻。真正的經濟／行動無限豁免需各模組連動。"""
    gid,uid=str(guild_id),str(owner_id)
    with transaction() as db:
        state=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(gid,)).fetchone()
        if state and state['ended']:
            raise ValueError('內測已結束，不再發放測試角色。')
        row=db.execute('SELECT character_id FROM rp_beta_characters WHERE guild_id=? AND owner_id=?',(gid,uid)).fetchone()
        if row:return int(row['character_id']),False
        db.execute('INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)',(gid,))
        used={r[0] for r in db.execute(
            'SELECT assigned_name FROM rp_beta_characters WHERE guild_id=? AND assigned_name IS NOT NULL',(gid,))}
        options=[(gender,name) for gender,pool in (('男',MALE_NAMES),('女',FEMALE_NAMES))
                 for name in pool if name not in used]
        if not options:
            raise ValueError('所有測試角色姓名已領取完畢，請管理員補充名字池。')
        # 在剩餘姓名中公平抽選，姓名不重覆分配。
        gender,label = random.choice(options)
        # 性別寫入原本的角色設定欄位；姓名完整保留、不加隨機編號。
        settings_json = json.dumps({'gender': gender}, ensure_ascii=False)
        c=db.execute("""INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,
                      stamina,max_stamina,character_type,settings_json)
                      VALUES (?,?,?,?,?,'active',?,?, 'player',?)""",
                     (gid,uid,label,random.randint(18,35),'內測角色',1000000,1000000,
                      settings_json))
        db.execute('INSERT INTO rp_beta_characters(guild_id,owner_id,character_id,assigned_name) VALUES (?,?,?,?)',
                   (gid,uid,c.lastrowid,label))
        return int(c.lastrowid),True

def edit(guild_id,owner_id,name,age,occupation,biography):
    gid,uid=str(guild_id),str(owner_id)
    name=name.strip();occupation=occupation.strip()
    age=int(age)
    if not 1 <= len(name) <= 80 or not 0 <= age <= 200:
        raise ValueError('角色名稱需1～80字、年齡需0～200歲。')
    if len(occupation)>80 or len(biography)>1800:
        raise ValueError('職業或人物設定過長。')
    with transaction() as db:
        row=db.execute("""SELECT b.character_id,b.status,c.settings_json
            FROM rp_beta_characters b JOIN characters c
            ON c.guild_id=b.guild_id AND c.id=b.character_id
            WHERE b.guild_id=? AND b.owner_id=?""",(gid,uid)).fetchone()
        state=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(gid,)).fetchone()
        if not row or row['status'] not in ('active','ended','drafted'):
            raise ValueError('目前沒有可編輯的內測角色。')
        try: settings=json.loads(row['settings_json'] or '{}')
        except (TypeError,ValueError):settings={}
        if not isinstance(settings,dict):settings={}
        settings['biography']=biography
        db.execute("""UPDATE characters SET name=?,age=?,occupation=?,settings_json=?
            WHERE guild_id=? AND id=?""",
            (name,age,occupation,json.dumps(settings,ensure_ascii=False),gid,row['character_id']))
        return int(row['character_id'])

def is_beta_character(db, guild_id, character_id):
    row=db.execute("""SELECT b.status, COALESCE(s.ended,0) ended
        FROM rp_beta_characters b LEFT JOIN rp_beta_state s ON s.guild_id=b.guild_id
        WHERE b.guild_id=? AND b.character_id=?""",(str(guild_id),int(character_id))).fetchone()
    return bool(row and row['status'] in ('active','ended','drafted'))

def finish(guild_id, operator_id):
    """群主核准後執行。測試角色停用；不是直接轉正。"""
    gid=str(guild_id)
    with transaction() as db:
        row=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(gid,)).fetchone()
        if row and row['ended']:return 0
        db.execute("""INSERT INTO rp_beta_state(guild_id,ended,ended_by,ended_at)
            VALUES (?,1,?,CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET ended=1,ended_by=excluded.ended_by,
                ended_at=CURRENT_TIMESTAMP""",(gid,str(operator_id)))
        ids=db.execute("SELECT character_id FROM rp_beta_characters WHERE guild_id=? AND status='active'",(gid,)).fetchall()
        db.execute("UPDATE rp_beta_characters SET status='ended',updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND status='active'",(gid,))
        # 測試角色仍維持 active，後續可在獨立沙盒進行遊玩功能測試。
        # 不得將其列入正式排行榜、結算或劇情進度。
        return len(ids)

def convert_to_draft(guild_id,owner_id):
    """安全轉正：建立全新的原創草稿，不搬移測試經濟、物品或屬性。"""
    gid,uid=str(guild_id),str(owner_id)
    with transaction() as db:
        row=db.execute("""SELECT b.status,b.draft_id,c.name,c.age,c.occupation,c.settings_json
            FROM rp_beta_characters b JOIN characters c
              ON b.guild_id=c.guild_id AND b.character_id=c.id
            WHERE b.guild_id=? AND b.owner_id=?""",(gid,uid)).fetchone()
        state=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(gid,)).fetchone()
        if not state or not state['ended']:
            raise ValueError('群主尚未宣布內測結束。')
        if not row:raise ValueError('沒有可轉正的內測角色。')
        if row['draft_id']:return int(row['draft_id']),False
        if row['status'] not in ('ended','drafted'):raise ValueError('內測角色狀態不允許轉正。')
        # 保留人物敘事設定，其餘遊戲財產、活動、徽章、任務、數值完全不沿用。
        try:settings=json.loads(row['settings_json'] or '{}')
        except (TypeError,ValueError):settings={}
        if not isinstance(settings,dict):settings={}
        safe={k:v for k,v in settings.items() if k in (
            'gender','nationality','height_cm','weight_kg','art_url','art_source',
            'education','languages','stage_name','public_persona','private_personality',
            'biography','notes','special_notes','theme_color',
        ) and isinstance(v,(str,int,float,bool,list,dict))}
        c=db.execute("""INSERT INTO characters(guild_id,owner_id,name,age,occupation,
            status,character_type,settings_json) VALUES (?,?,?,?,?,'draft','player',?)""",
            (gid,uid,row['name'],row['age'],'' if row['occupation']=='內測角色' else row['occupation'],
             json.dumps(safe,ensure_ascii=False)))
        db.execute("""UPDATE rp_beta_characters SET status='drafted',draft_id=?,
            updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND owner_id=?""",(c.lastrowid,gid,uid))
        return int(c.lastrowid),True


def is_test_only(db, guild_id, character_id):
    """無論內測進行中／結束／已建立轉正草稿，都維持測試隔離。"""
    return is_beta_character(db, guild_id, character_id)


def test_character_ids(db, guild_id):
    return {int(row['character_id']) for row in db.execute(
        'SELECT character_id FROM rp_beta_characters WHERE guild_id=?', (str(guild_id),)
    )}
