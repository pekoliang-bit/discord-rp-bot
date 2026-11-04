"""每伺服器獨立的虛構娛樂圈傳聞定時發布服務。不建立公關案件、不更改角色數值。"""
from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

from core.database import get_connection, transaction

TEMPLATES_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'rumor_templates' / 'rumor_templates_100.json'
INTERVAL = 8 * 60 * 60
CHANNEL_RE = re.compile(r'^(?:https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/\d+/(\d+)|<#(\d+)>|(\d+))$')


def initialize():
    with transaction() as db:
        # 自動任務與 /傳聞 共用授權表；缺表時保守處理為無人符合資格。
        db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_optout (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,character_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_category_consent (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            category TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,character_id,category)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_autopost_settings (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT,
            enabled INTEGER NOT NULL DEFAULT 0,
            next_due INTEGER,
            updated_by TEXT,
            updated_at INTEGER NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_autopost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            template_id TEXT NOT NULL,
            character_ids_json TEXT NOT NULL,
            posted_text TEXT NOT NULL,
            discord_message_id TEXT,
            created_at INTEGER NOT NULL
        )''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_rumor_auto_history ON rp_rumor_autopost_log(guild_id,id)')


def templates():
    rows=json.loads(TEMPLATES_PATH.read_text(encoding='utf-8'))['templates']
    if len(rows)!=100 or len({r['id'] for r in rows})!=100:
        raise ValueError('傳聞模板必須有100則且編號不重複')
    if sum(r['polarity']=='positive' for r in rows)!=50 or sum(r['polarity']=='negative' for r in rows)!=50:
        raise ValueError('傳聞正負模板必須各50則')
    return rows


def parse_channel(value, guild_id):
    raw=str(value).strip()
    match=CHANNEL_RE.fullmatch(raw)
    if not match:
        raise ValueError('請提供 Discord 頻道連結、頻道提及或頻道 ID')
    if '/channels/' in raw:
        guild_part=re.search(r'/channels/(\d+)/',raw)
        if guild_part and guild_part.group(1)!=str(guild_id):
            raise ValueError('頻道連結不屬於目前伺服器')
    return str(next(piece for piece in match.groups() if piece))


def settings(guild_id):
    initialize()
    with get_connection() as db:
        row=db.execute('SELECT * FROM rp_rumor_autopost_settings WHERE guild_id=?',(str(guild_id),)).fetchone()
    return dict(row) if row else {'guild_id':str(guild_id),'channel_id':None,'enabled':0,'next_due':None}


def set_channel(guild_id, channel_id, actor_id):
    initialize()
    now=int(time.time())
    with transaction() as db:
        db.execute('''INSERT INTO rp_rumor_autopost_settings(guild_id,channel_id,enabled,next_due,updated_by,updated_at)
           VALUES (?,?,0,NULL,?,?) ON CONFLICT(guild_id) DO UPDATE SET
           channel_id=excluded.channel_id, enabled=0, next_due=NULL,
           updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
           (str(guild_id),str(channel_id),str(actor_id),now))
    # 更換頻道時先暫停，必須由群主再次啟用。


def set_enabled(guild_id, enabled, actor_id):
    initialize()
    now=int(time.time())
    with transaction() as db:
        row=db.execute('SELECT channel_id FROM rp_rumor_autopost_settings WHERE guild_id=?',(str(guild_id),)).fetchone()
        if enabled and (not row or not row['channel_id']):
            raise ValueError('請先設定傳聞發布頻道')
        if row:
            db.execute('''UPDATE rp_rumor_autopost_settings SET enabled=?,next_due=?,updated_by=?,updated_at=?
                WHERE guild_id=?''', (int(bool(enabled)),now+INTERVAL if enabled else None,str(actor_id),now,str(guild_id)))
        else:
            db.execute('''INSERT INTO rp_rumor_autopost_settings
                (guild_id,channel_id,enabled,next_due,updated_by,updated_at) VALUES (?,NULL,0,NULL,?,?)''',
                (str(guild_id),str(actor_id),now))


def due_guild_ids(now=None):
    initialize()
    now=int(time.time()) if now is None else int(now)
    with get_connection() as db:
        return [row['guild_id'] for row in db.execute('''SELECT guild_id FROM rp_rumor_autopost_settings
            WHERE enabled=1 AND channel_id IS NOT NULL AND next_due<=?''',(now,)).fetchall()]


def _eligible(db, guild_id, category):
    # 自動公開比一般傳聞要求更嚴格：角色必須主動開啟整體傳聞授權。
    # 任何整體或分類關閉都優先拒絕。
    rows=db.execute('''SELECT c.id,c.name FROM characters c
      JOIN rp_rumor_optout o ON o.guild_id=c.guild_id AND o.character_id=c.id AND o.enabled=1
      LEFT JOIN rp_rumor_category_consent x ON x.guild_id=c.guild_id AND x.character_id=c.id AND x.category=?
      WHERE c.guild_id=? AND c.status='active' AND (x.enabled IS NULL OR x.enabled=1)
      ORDER BY c.id''',(category,str(guild_id))).fetchall()
    return [dict(x) for x in rows]


def choose_entry(db, guild_id, *, rng=None):
    rng=rng or random.SystemRandom()
    recent={r['template_id'] for r in db.execute('''SELECT template_id FROM rp_rumor_autopost_log
        WHERE guild_id=? ORDER BY id DESC LIMIT 30''',(str(guild_id),))}
    possible=[]
    for item in templates():
        candidates=_eligible(db,guild_id,item['category'])
        if len(candidates)>=item['character_count']:
            possible.append((item,candidates))
    if not possible:
        return None
    fresh=[x for x in possible if x[0]['id'] not in recent]
    item,candidates=rng.choice(fresh or possible)
    selected=rng.sample(candidates,item['character_count'])
    names=[x['name'].replace('@','＠').replace('`','').replace('*','').replace('_','')[:80] for x in selected]
    content=item['text'].replace('{角色2}',names[1] if len(names)>1 else '').replace('{角色}',names[0])
    return {'template_id':item['id'],'polarity':item['polarity'],'category':item['category'],
        'character_ids':[x['id'] for x in selected],'text':content}


def claim(guild_id, *, now=None):
    """原子預約本次時段，避免多個 Railway 實例同時刊登。失敗時不補發歷史時段。"""
    initialize()
    now=int(time.time()) if now is None else int(now)
    with transaction() as db:
        setting=db.execute('SELECT * FROM rp_rumor_autopost_settings WHERE guild_id=?',(str(guild_id),)).fetchone()
        if not setting or not setting['enabled'] or not setting['channel_id'] or setting['next_due'] is None or setting['next_due']>now:
            return None
        # 若沒人同意參與，保留下一個時段，不會不停嘗試刷屏。
        selection=choose_entry(db,guild_id)
        db.execute('''UPDATE rp_rumor_autopost_settings SET next_due=?
           WHERE guild_id=? AND enabled=1 AND next_due<=?''',(now+INTERVAL,str(guild_id),now))
        if not selection:
            return None
        return {'guild_id':str(guild_id),'channel_id':setting['channel_id'], **selection}


def still_consented(guild_id, entry):
    with get_connection() as db:
        selected=entry['character_ids']
        available={x['id'] for x in _eligible(db,guild_id,entry['category'])}
        setting=db.execute('SELECT channel_id,enabled FROM rp_rumor_autopost_settings WHERE guild_id=?',(str(guild_id),)).fetchone()
        return bool(setting and setting['enabled'] and setting['channel_id']==entry['channel_id'] and all(x in available for x in selected))


def record_post(entry, message_id):
    with transaction() as db:
        db.execute('''INSERT INTO rp_rumor_autopost_log
            (guild_id,channel_id,template_id,character_ids_json,posted_text,discord_message_id,created_at)
            VALUES (?,?,?,?,?,?,?)''',(
                entry['guild_id'],entry['channel_id'],entry['template_id'],
                json.dumps(entry['character_ids']),entry['text'],str(message_id),int(time.time())))
