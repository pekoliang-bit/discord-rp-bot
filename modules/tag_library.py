"""大眾印象標籤：162 項資料目錄、初始名額、無總持有上限、歷史與傳聞接口。"""
from __future__ import annotations
import sqlite3
import csv
import re
from pathlib import Path
from core.database import get_connection, transaction


_SEEDED_GUILDS = set()
_CATALOG_FILE = Path(__file__).resolve().parent.parent / 'assets' / 'impression_tags' / 'catalog_v2_1.csv'


def catalog():
    """162 個已正式核准的 V2.1 標籤及效果，不推測未明列的數值。"""
    with _CATALOG_FILE.open('r', encoding='utf-8-sig', newline='') as source:
        entries = [dict(row) for row in csv.DictReader(source)]
    if len(entries) != 162 or len({x['編號'] for x in entries}) != 162:
        raise RuntimeError('大眾印象標籤 V2.1 目錄不完整')
    return entries


def numeric_rules(name):
    """精確擷取 V2.1 該標籤的正負百分比及其文字適用條件。

    數字是相對倍率，而非永久屬性點。呼叫端必須使用已核准的情境觸發，
    不得將全文敘述當作可以任意影響金錢、粉絲或影響指數的權限。
    """
    row = next((item for item in catalog() if item['標籤名稱'] == name), None)
    if not row:
        return []
    clauses = re.split(r'[；;，,]', row['加成／風險候選'])
    rules = []
    for clause in clauses:
        match = re.search(r'(?<!\d)([+\-−])\s*(\d{1,3})\s*%', clause)
        if match:
            rules.append({
                'context': clause[:match.start()].strip(' ：:（('),
                'relative_percent': (1 if match.group(1) == '+' else -1) * int(match.group(2)),
                'source_code': row['編號'],
                'rule_index': len(rules),
                'approval': row['效果審核狀態'],
            })
    return rules


def apply_relative(base, percent):
    """已核准事件使用的相對增減，20% 基率加10% -> 22% 而非30%。"""
    return float(base) * (1.0 + float(percent) / 100.0)


def effect_metadata(gid, name, *, privileged=False):
    """隱藏詳細效果；已核准效果必須由具有明確事件情境的處理器套用。"""
    row = next((x for x in catalog() if x['標籤名稱'] == name), None)
    if row is None:
        return None
    return {
        'code': row['編號'],
        'name': name,
        'theme': row['適配主題候選'],
        'effect': row['加成／風險候選'] if privileged else None,
        'approval': row['效果審核狀態'] if privileged else None,
        'numeric_effect_active': row['效果審核狀態'] == '已核准',
        'numeric_rules': numeric_rules(name) if privileged else None,
    }


def representative(gid, cid, limit=5):
    """顯示最多五個代表標籤；不是角色總持有上限。"""
    return held(gid, cid)[:max(0, min(int(limit), 5))]


def history(gid, cid, limit=50):
    with get_connection() as db:
        return [dict(row) for row in db.execute(
            'SELECT tag_name,action,source,source_ref,created_at FROM rp_tag_history '
            'WHERE guild_id=? AND character_id=? ORDER BY id DESC LIMIT ?',
            (str(gid), int(cid), max(1, min(int(limit), 200))),
        )]


def init_db():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_tag_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '其他',
            enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,name))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_character_tags (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'initial', source_ref TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,character_id,tag_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_tag_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL, tag_id INTEGER, tag_name TEXT NOT NULL,
            action TEXT NOT NULL, source TEXT NOT NULL, actor_id TEXT,
            source_ref TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


def seed(gid):
    """保留既有持有資料；V2.1 162 項取代舊版 90 項作為預設清單。"""
    gid = str(gid)
    if gid in _SEEDED_GUILDS:
        return
    init_db()
    entries = catalog()
    accepted = {entry['標籤名稱'] for entry in entries}
    with transaction() as db:
        # 舊名稱更新時保留標籤 ID、角色持有、來源及歷史；若新名稱已存在則合併。
        for old_name, new_name in (
            ('表管教科書', '表情管理教科書'),
            ('魅粉擔當', '寵粉擔當'),
        ):
            old = db.execute('SELECT id FROM rp_tag_library WHERE guild_id=? AND name=?',
                             (gid, old_name)).fetchone()
            new = db.execute('SELECT id FROM rp_tag_library WHERE guild_id=? AND name=?',
                             (gid, new_name)).fetchone()
            if old and not new:
                db.execute('UPDATE rp_tag_library SET name=? WHERE id=? AND guild_id=?',
                           (new_name, old['id'], gid))
            elif old and new:
                db.execute('''INSERT OR IGNORE INTO rp_character_tags
                    (guild_id,character_id,tag_id,source,source_ref,created_at)
                    SELECT guild_id,character_id,?,source,source_ref,created_at
                    FROM rp_character_tags WHERE guild_id=? AND tag_id=?''',
                    (new['id'], gid, old['id']))
                db.execute('DELETE FROM rp_character_tags WHERE guild_id=? AND tag_id=?',
                           (gid, old['id']))
                db.execute('DELETE FROM rp_tag_library WHERE guild_id=? AND id=?',
                           (gid, old['id']))
            db.execute('UPDATE rp_tag_history SET tag_name=? WHERE guild_id=? AND tag_name=?',
                       (new_name, gid, old_name))
        for entry in entries:
            name = entry['標籤名稱']
            db.execute(
                'INSERT OR IGNORE INTO rp_tag_library(guild_id,name,category) VALUES(?,?,?)',
                (str(gid), name, '大眾印象'),
            )
        # 舊版預設標籤若尚未被角色持有，停止在新角色的選單出現；
        # 已持有者與管理員自行新增的標籤不被刪除或改寫。
        old_categories = (
            '性格特質', '社交形象', '外界評價', '才能與能力', '行事風格',
            '特殊印象', '容貌與氣質', '才能與技藝', '藝能與舞台',
        )
        # Retire old defaults by category without deleting any character ownership/history.
        # Custom administrator categories outside the retired defaults stay unchanged.
        placeholders = ','.join('?' for _ in old_categories)
        db.execute(f"""UPDATE rp_tag_library SET enabled=0
            WHERE guild_id=? AND category IN ({placeholders})
              AND name NOT IN (SELECT name FROM rp_tag_library
                               WHERE guild_id=? AND category='大眾印象')
              AND NOT EXISTS (SELECT 1 FROM rp_character_tags ct
                  WHERE ct.guild_id=rp_tag_library.guild_id
                    AND ct.tag_id=rp_tag_library.id)""",
            (gid, *old_categories, gid))
    _SEEDED_GUILDS.add(gid)


def tags(gid, enabled_only=True, query=''):
    seed(gid)
    sql = 'SELECT * FROM rp_tag_library WHERE guild_id=? AND name IN (SELECT name FROM rp_tag_library WHERE guild_id=? AND category=?)'
    args = [str(gid), str(gid), '大眾印象']
    if enabled_only:
        sql += ' AND enabled=1'
    if query:
        sql += ' AND name LIKE ?'
        args.append('%'+query+'%')
    sql += ' ORDER BY id'
    with get_connection() as db:
        return [dict(r) for r in db.execute(sql,args)]


def held(gid,cid):
    with get_connection() as db:
        return [dict(r) for r in db.execute('''SELECT t.*,ct.source,ct.source_ref FROM rp_character_tags ct
          JOIN rp_tag_library t ON t.id=ct.tag_id AND t.guild_id=ct.guild_id
          WHERE ct.guild_id=? AND ct.character_id=? ORDER BY ct.created_at,ct.tag_id''',(str(gid),int(cid)))]


def initial_limit(kind):
    return 3 if kind in ('player_npc','official_npc','npc') else 2


def replace_initial(gid,cid,owner_id,actor_id,names,*,force=False):
    """替換初始標籤，不覆蓋傳聞標籤；群主可 force。"""
    unique=list(dict.fromkeys(x.strip() for x in names if x.strip()))
    seed(gid)
    with transaction() as db:
        char=db.execute('SELECT owner_id,character_type FROM characters WHERE guild_id=? AND id=?',(str(gid),int(cid))).fetchone()
        if not char: raise ValueError('找不到角色')
        if str(char['owner_id']) != str(owner_id): raise PermissionError('角色擁有者不符')
        if not force and str(actor_id)!=str(owner_id): raise PermissionError('只有角色擁有者可選初始標籤')
        if len(unique)>initial_limit(char['character_type']): raise ValueError('超過此角色的初始標籤上限')
        allrows=db.execute("SELECT id,name FROM rp_tag_library WHERE guild_id=? AND enabled=1 AND category='大眾印象'",(str(gid),)).fetchall()
        lookup={r['name']:int(r['id']) for r in allrows}
        unknown=[n for n in unique if n not in lookup]
        if unknown: raise ValueError('標籤庫未收錄：'+'、'.join(unknown))
        previous=db.execute("SELECT ct.tag_id,t.name FROM rp_character_tags ct JOIN rp_tag_library t ON t.id=ct.tag_id AND t.guild_id=ct.guild_id WHERE ct.guild_id=? AND ct.character_id=? AND ct.source='initial'",(str(gid),int(cid))).fetchall()
        noninitial=db.execute("SELECT tag_id FROM rp_character_tags WHERE guild_id=? AND character_id=? AND source!='initial'",(str(gid),int(cid))).fetchall()
        other_ids={int(r['tag_id']) for r in noninitial}
        new_ids={lookup[n] for n in unique}
        for old in previous:
            if int(old['tag_id']) not in new_ids:
                db.execute("DELETE FROM rp_character_tags WHERE guild_id=? AND character_id=? AND tag_id=? AND source='initial'",(str(gid),int(cid),old['tag_id']))
                db.execute('INSERT INTO rp_tag_history(guild_id,character_id,tag_id,tag_name,action,source,actor_id) VALUES(?,?,?,?,?,?,?)',(str(gid),int(cid),old['tag_id'],old['name'],'remove','initial',str(actor_id)))
        current={int(r['tag_id']) for r in previous}
        for name in unique:
            tid=lookup[name]
            if tid in current or tid in other_ids: continue
            db.execute("INSERT INTO rp_character_tags(guild_id,character_id,tag_id,source) VALUES(?,?,?,'initial')",(str(gid),int(cid),tid))
            db.execute('INSERT INTO rp_tag_history(guild_id,character_id,tag_id,tag_name,action,source,actor_id) VALUES(?,?,?,?,?,?,?)',(str(gid),int(cid),tid,name,'add','initial',str(actor_id)))
    return unique


def rumor_change(gid,cid,*,add=None,remove=None,source_ref,actor_id=None):
    """預留傳聞接口：add/remove 名稱；每次異動保留稽核。"""
    add=list(dict.fromkeys(add or [])); remove=list(dict.fromkeys(remove or []))
    if not source_ref: raise ValueError('傳聞變更須有來源編號')
    seed(gid)
    with transaction() as db:
        existing=db.execute('SELECT tag_id FROM rp_character_tags WHERE guild_id=? AND character_id=?',(str(gid),int(cid))).fetchall()
        ids={int(r['tag_id']) for r in existing}
        names={r['name']:int(r['id']) for r in db.execute('SELECT id,name FROM rp_tag_library WHERE guild_id=?',(str(gid),))}
        if any(n not in names for n in add+remove): raise ValueError('傳聞使用了未收錄標籤')
        result=(ids - {names[n] for n in remove}) | {names[n] for n in add}
        for name in remove:
            tid=names[name]
            if tid in ids:
                db.execute('DELETE FROM rp_character_tags WHERE guild_id=? AND character_id=? AND tag_id=?',(str(gid),int(cid),tid))
                db.execute('INSERT INTO rp_tag_history(guild_id,character_id,tag_id,tag_name,action,source,actor_id,source_ref) VALUES(?,?,?,?,?,?,?,?)',(str(gid),int(cid),tid,name,'remove','rumor',str(actor_id) if actor_id else None,str(source_ref)))
        for name in add:
            tid=names[name]
            if tid not in ids:
                db.execute("INSERT INTO rp_character_tags(guild_id,character_id,tag_id,source,source_ref) VALUES(?,?,?,'rumor',?)",(str(gid),int(cid),tid,str(source_ref)))
                db.execute('INSERT INTO rp_tag_history(guild_id,character_id,tag_id,tag_name,action,source,actor_id,source_ref) VALUES(?,?,?,?,?,?,?,?)',(str(gid),int(cid),tid,name,'add','rumor',str(actor_id) if actor_id else None,str(source_ref)))


async def setup(bot):
    init_db()
