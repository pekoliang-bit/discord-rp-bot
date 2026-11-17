"""內測角色安全生命週期。測試資源不繼承至正式角色。"""
from __future__ import annotations
import json
import random
import sqlite3
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

def _setup_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_beta_state (
        guild_id TEXT PRIMARY KEY, ended INTEGER NOT NULL DEFAULT 0,
        ended_by TEXT, ended_at TEXT
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_beta_characters (
        guild_id TEXT NOT NULL, owner_id TEXT NOT NULL,
        character_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        draft_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT, PRIMARY KEY(guild_id, owner_id),
        UNIQUE(guild_id, character_id),
        CHECK(status IN ('active','ended','drafted'))
    )""")
    cols = {r[1] for r in db.execute('PRAGMA table_info(rp_beta_characters)')}
    if 'assigned_name' not in cols:
        db.execute('ALTER TABLE rp_beta_characters ADD COLUMN assigned_name TEXT')
    if 'draw_no' not in cols:
        db.execute('ALTER TABLE rp_beta_characters ADD COLUMN draw_no INTEGER')
    # 本輪已抽中的名字；整池重置後可重新使用，平常不因改名而釋出。
    db.execute("""CREATE TABLE IF NOT EXISTS rp_beta_used_names (
        guild_id TEXT NOT NULL, name TEXT NOT NULL,
        first_owner_id TEXT NOT NULL, first_character_id INTEGER NOT NULL,
        assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id, name)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_beta_reset_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL, owner_id TEXT NOT NULL,
        character_id INTEGER NOT NULL, assigned_name TEXT,
        previous_status TEXT NOT NULL, reset_by TEXT NOT NULL,
        reset_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    # 僅回填目前仍有效的測試角；整池重置時會解除舊角色綁定。
    names = set(MALE_NAMES + FEMALE_NAMES)
    reserved = {(str(r['guild_id']), str(r['name'])) for r in db.execute(
        'SELECT guild_id,name FROM rp_beta_used_names')}
    for row in db.execute("""SELECT b.guild_id,b.owner_id,b.character_id,
            b.assigned_name,c.name AS current_name
        FROM rp_beta_characters b JOIN characters c
          ON c.guild_id=b.guild_id AND c.id=b.character_id""").fetchall():
        name = row['assigned_name'] or (row['current_name'] if row['current_name'] in names else None)
        if name is None: continue
        if row['assigned_name'] is None and (str(row['guild_id']),name) not in reserved:
            db.execute('UPDATE rp_beta_characters SET assigned_name=? WHERE guild_id=? AND owner_id=?',
                       (name,row['guild_id'],row['owner_id']))
        db.execute("""INSERT OR IGNORE INTO rp_beta_used_names
            (guild_id,name,first_owner_id,first_character_id) VALUES (?,?,?,?)""",
            (row['guild_id'],name,row['owner_id'],row['character_id']))
        reserved.add((str(row['guild_id']),name))
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_beta_assigned_name
        ON rp_beta_characters(guild_id, assigned_name)
        WHERE assigned_name IS NOT NULL""")


def setup():
    with transaction() as db:
        _setup_tables(db)


def remaining_names(guild_id):
    """每個名字抽中後從本輪可抽池移除；整池重置可恢復。"""
    with transaction() as db:
        _setup_tables(db)
        used={r[0] for r in db.execute(
            'SELECT name FROM rp_beta_used_names WHERE guild_id=?',(str(guild_id),))}
    return tuple(x for x in MALE_NAMES + FEMALE_NAMES if x not in used)


def can_claim(guild_id):
    with transaction() as db:
        _setup_tables(db)
        state=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(str(guild_id),)).fetchone()
        if state and state['ended']: return False
        used={r[0] for r in db.execute('SELECT name FROM rp_beta_used_names WHERE guild_id=?',
                                      (str(guild_id),))}
        return any(name not in used for name in MALE_NAMES + FEMALE_NAMES)


def has_beta(guild_id, owner_id):
    with transaction() as db:
        _setup_tables(db)
        return bool(db.execute('SELECT 1 FROM rp_beta_characters WHERE guild_id=? AND owner_id=?',
            (str(guild_id),str(owner_id))).fetchone())


def beta_ended(guild_id):
    with transaction() as db:
        _setup_tables(db)
        row=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',
                       (str(guild_id),)).fetchone()
        return bool(row and row['ended'])


def claim(guild_id, owner_id):
    """每位玩家每服本輪一隻測試角；姓名在本輪內不可重複。"""
    gid,uid=str(guild_id),str(owner_id)
    with transaction() as db:
        _setup_tables(db)
        state=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(gid,)).fetchone()
        if state and state['ended']:
            raise ValueError('內測已結束，不再發放測試角色。')
        row=db.execute('SELECT character_id FROM rp_beta_characters WHERE guild_id=? AND owner_id=?',
                       (gid,uid)).fetchone()
        if row: return int(row['character_id']),False
        db.execute('INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)',(gid,))
        used={r[0] for r in db.execute('SELECT name FROM rp_beta_used_names WHERE guild_id=?',(gid,))}
        options=[(gender,name) for gender,pool in (('男',MALE_NAMES),('女',FEMALE_NAMES))
                 for name in pool if name not in used]
        if not options:
            raise ValueError('所有測試角色姓名已抽完，請管理員先補充名字池。')
        gender,label=random.choice(options)
        settings_json=json.dumps({'gender':gender},ensure_ascii=False)
        c=db.execute("""INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,
            stamina,max_stamina,character_type,settings_json)
            VALUES (?,?,?,?,?,'active',?,?,'player',?)""",
            (gid,uid,label,random.randint(18,35),'內測角色',1000000,1000000,settings_json))
        draw_no = db.execute('SELECT COALESCE(MAX(draw_no),0)+1 FROM rp_beta_characters WHERE guild_id=?',
                             (gid,)).fetchone()[0]
        db.execute('INSERT INTO rp_beta_characters(guild_id,owner_id,character_id,assigned_name,draw_no) VALUES (?,?,?,?,?)',
                   (gid,uid,c.lastrowid,label,draw_no))
        db.execute("""INSERT INTO rp_beta_used_names
            (guild_id,name,first_owner_id,first_character_id) VALUES (?,?,?,?)""",
            (gid,label,uid,c.lastrowid))
        return int(c.lastrowid),True


def resettable_players(guild_id):
    """回傳目前仍綁定測試角色、可由管理員重抽的玩家。"""
    with transaction() as db:
        _setup_tables(db)
        rows=db.execute("""SELECT b.owner_id,b.character_id,b.assigned_name,
                b.status,c.name,c.status AS character_status,b.draft_id
            FROM rp_beta_characters b JOIN characters c
              ON b.guild_id=c.guild_id AND b.character_id=c.id
            WHERE b.guild_id=? ORDER BY b.created_at,b.owner_id""",
            (str(guild_id),)).fetchall()
        return [dict(r) for r in rows if r['character_status'] != 'deleted']


def reset_claims(guild_id, operator_id, owner_ids):
    """指定成員重抽：舊角標記刪除、姓名維持本輪占用、留下稽核紀錄。"""
    gid,admin=str(guild_id),str(operator_id)
    users=[str(x) for x in owner_ids]
    if not users or len(users)>20 or len(set(users))!=len(users):
        raise ValueError('請一次勾選1～20位不重複成員。')
    with transaction() as db:
        _setup_tables(db)
        state=db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?',(gid,)).fetchone()
        if state and state['ended']:
            raise ValueError('內測已結束，不允許重抽。')
        placeholders=','.join('?' for _ in users)
        rows=db.execute(f"""SELECT b.owner_id,b.character_id,b.assigned_name,
            b.status,b.draft_id,c.name AS character_name,c.status AS character_status
            FROM rp_beta_characters b JOIN characters c
              ON b.guild_id=c.guild_id AND b.character_id=c.id
            WHERE b.guild_id=? AND b.owner_id IN ({placeholders})""",
            (gid,*users)).fetchall()
        if len(rows)!=len(users):
            raise ValueError('部分成員已沒有可重抽的測試角色，請重新整理。')
        if any(r['draft_id'] is not None or r['status']=='drafted' for r in rows):
            raise ValueError('已有轉正草稿的測試角不可重抽。')
        if any(r['character_status']=='deleted' for r in rows):
            raise ValueError('選取包含已刪除角色，請重新整理。')
        for r in rows:
            db.execute("""INSERT INTO rp_beta_reset_history
                (guild_id,owner_id,character_id,assigned_name,previous_status,reset_by)
                VALUES (?,?,?,?,?,?)""",
                (gid,r['owner_id'],r['character_id'],r['assigned_name'],r['status'],admin))
            if r['assigned_name']:
                db.execute("""INSERT OR IGNORE INTO rp_beta_used_names
                    (guild_id,name,first_owner_id,first_character_id) VALUES (?,?,?,?)""",
                    (gid,r['assigned_name'],r['owner_id'],r['character_id']))
            db.execute("""UPDATE characters SET status='deleted',version=version+1
                WHERE guild_id=? AND id=?""",(gid,r['character_id']))
            db.execute('DELETE FROM rp_beta_characters WHERE guild_id=? AND owner_id=?',
                       (gid,r['owner_id']))
        return len(rows)



def draw_number(guild_id, character_id):
    """當輪測試抽角序號；不是 characters.id（後者為全服共用主鍵）。"""
    with transaction() as db:
        _setup_tables(db)
        row = db.execute("SELECT draw_no FROM rp_beta_characters WHERE guild_id=? AND character_id=?",
                         (str(guild_id), int(character_id))).fetchone()
        return row[0] if row else None


def reset_entire_pool(guild_id, operator_id):
    """重置伺服器整個抽角池，清除歷史抽取記錄，下一輪從序號1開始。

    只處理此伺服器的測試角。外部系統可能引用 characters.id，
    故舊角色採刪除狀態與清除人設，不重置全服共用的資料庫主鍵。
    """
    gid = str(guild_id)
    with transaction() as db:
        _setup_tables(db)
        state = db.execute('SELECT ended FROM rp_beta_state WHERE guild_id=?', (gid,)).fetchone()
        if state and state['ended']:
            raise ValueError('內測已結束，不能重置姓名池。')
        # 涵蓋舊輪次已刪除的測試角，避免遺留角色論壇映射。
        historical = db.execute('SELECT character_id FROM rp_beta_reset_history WHERE guild_id=?', (gid,)).fetchall()
        current = db.execute('SELECT character_id,draft_id,status FROM rp_beta_characters WHERE guild_id=?',
                             (gid,)).fetchall()
        if any(r['draft_id'] is not None or r['status'] == 'drafted' for r in current):
            raise ValueError('有測試角色已建立轉正草稿，為保護正式角色資料，不能重置。')
        ids = sorted(set(int(r[0]) for r in historical) | set(int(r[0]) for r in current))
        for cid in ids:
            # 只刪此角色的論壇資料庫綁定，不碰 Discord 上其他人的論壇。
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='character_forum_links'").fetchone():
                db.execute('DELETE FROM character_forum_links WHERE guild_id=? AND character_id=?', (gid,cid))
            # 無其他系統依賴時真正刪除舊測試角色；如有 FK 關聯則使用
            # 安全占位，避免破壞公司、任務、正式玩家等既有資料。
            db.execute('SAVEPOINT erase_old_beta')
            try:
                db.execute('DELETE FROM characters WHERE guild_id=? AND id=?', (gid, cid))
                db.execute('RELEASE erase_old_beta')
            except sqlite3.IntegrityError:
                db.execute('ROLLBACK TO erase_old_beta')
                db.execute('RELEASE erase_old_beta')
                db.execute("""UPDATE characters SET status='deleted',name=?,age=NULL,
                    occupation='',settings_json='{}',version=version+1
                    WHERE guild_id=? AND id=?""", (f'已重置測試角-{cid}', gid, cid))
        db.execute('DELETE FROM rp_beta_characters WHERE guild_id=?', (gid,))
        db.execute('DELETE FROM rp_beta_used_names WHERE guild_id=?', (gid,))
        db.execute('DELETE FROM rp_beta_reset_history WHERE guild_id=?', (gid,))
        # 注意：characters 自增主鍵與 Discord 已建立論壇不能重置或直接清空；
        # 下次抽角 draw_no 由新一輪 rp_beta_characters 的 MAX(draw_no)+1 產生，必定為1。
        return len(ids), len(MALE_NAMES) + len(FEMALE_NAMES)

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
