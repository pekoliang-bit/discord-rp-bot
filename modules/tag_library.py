"""官方角色標籤庫：90 初始標籤、持有上限、歷史與傳聞接口。"""
from __future__ import annotations
import sqlite3
from core.database import get_connection, transaction

GROUPS = {
'性格特質':'溫文爾雅 天真爛漫 冷若冰霜 桀驁不馴 心思縝密 我行我素 謹言慎行 直言不諱 優柔寡斷 殺伐果斷',
'社交形象':'八面玲瓏 平易近人 高不可攀 神秘莫測 左右逢源 孤芳自賞 口蜜腹劍 風趣幽默 不近人情 深藏不露',
'外界評價':'德高望重 臭名昭著 眾星捧月 聲名狼藉 年少有為 徒有虛名 備受爭議 萬人景仰 特立獨行 名不見經傳',
'才能與能力':'才華橫溢 足智多謀 巧舌如簧 過目不忘 多才多藝 商業奇才 藝術天才 談判高手 洞察人心 紙上談兵',
'行事風格':'雷厲風行 不擇手段 步步為營 見義勇為 明哲保身 膽大妄為 深謀遠慮 睚眥必報 隨遇而安 行事乖張',
'特殊印象':'命運寵兒 麻煩製造者 傳聞中心 謎團重重 天選之人 災厄體質 幸運之星 亡命之徒 雙面人生 不祥之兆',
'容貌與氣質':'眉目如畫 明艷動人 清麗脫俗 風華絕代 氣宇軒昂 儀態萬方 清冷矜貴 妖冶魅惑 其貌不揚 不修邊幅',
'才能與技藝':'琴棋書畫 能歌善舞 妙筆生花 匠心獨運 天賦異稟 勤能補拙 技藝超群 博學多聞 天馬行空 大器晚成',
'藝能與舞台':'天生偶像 舞台王者 歌聲動人 演技精湛 鏡頭寵兒 時尚寵兒 創作鬼才 控場高手 話題人物 曇花一現',
}
assert sum(len(v.split()) for v in GROUPS.values()) == 90


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
    init_db()
    with transaction() as db:
        for category, names in GROUPS.items():
            for name in names.split():
                db.execute('INSERT OR IGNORE INTO rp_tag_library(guild_id,name,category) VALUES(?,?,?)', (str(gid),name,category))


def tags(gid, enabled_only=True, query=''):
    seed(gid)
    sql = 'SELECT * FROM rp_tag_library WHERE guild_id=?'
    args = [str(gid)]
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
    with transaction() as db:
        char=db.execute('SELECT owner_id,character_type FROM characters WHERE guild_id=? AND id=?',(str(gid),int(cid))).fetchone()
        if not char: raise ValueError('找不到角色')
        if str(char['owner_id']) != str(owner_id): raise PermissionError('角色擁有者不符')
        if not force and str(actor_id)!=str(owner_id): raise PermissionError('只有角色擁有者可選初始標籤')
        if len(unique)>initial_limit(char['character_type']): raise ValueError('超過此角色的初始標籤上限')
        allrows=db.execute('SELECT id,name FROM rp_tag_library WHERE guild_id=? AND enabled=1',(str(gid),)).fetchall()
        lookup={r['name']:int(r['id']) for r in allrows}
        unknown=[n for n in unique if n not in lookup]
        if unknown: raise ValueError('標籤庫未收錄：'+'、'.join(unknown))
        previous=db.execute("SELECT ct.tag_id,t.name FROM rp_character_tags ct JOIN rp_tag_library t ON t.id=ct.tag_id WHERE ct.guild_id=? AND ct.character_id=? AND ct.source='initial'",(str(gid),int(cid))).fetchall()
        noninitial=db.execute("SELECT tag_id FROM rp_character_tags WHERE guild_id=? AND character_id=? AND source!='initial'",(str(gid),int(cid))).fetchall()
        other_ids={int(r['tag_id']) for r in noninitial}
        new_ids={lookup[n] for n in unique}
        if len(other_ids | new_ids)>5: raise ValueError('角色最多持有5個標籤')
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
    with transaction() as db:
        existing=db.execute('SELECT tag_id FROM rp_character_tags WHERE guild_id=? AND character_id=?',(str(gid),int(cid))).fetchall()
        ids={int(r['tag_id']) for r in existing}
        names={r['name']:int(r['id']) for r in db.execute('SELECT id,name FROM rp_tag_library WHERE guild_id=?',(str(gid),))}
        if any(n not in names for n in add+remove): raise ValueError('傳聞使用了未收錄標籤')
        result=(ids - {names[n] for n in remove}) | {names[n] for n in add}
        if len(result)>5: raise ValueError('最多持有5個標籤；請先指定替換或移除的標籤')
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
