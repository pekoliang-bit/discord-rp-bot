"""角色代理身分與通告狀態；角色正式編號直接使用 characters.id。"""
from __future__ import annotations
import json


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_character_proxy_identity (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
      state TEXT NOT NULL DEFAULT 'approved',
      announced INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_character_proxy_messages (
      guild_id TEXT NOT NULL, message_id TEXT NOT NULL,
      character_id INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,message_id))''')


def approve(db,guild_id,character_id):
    initialize(db)
    db.execute('''INSERT OR IGNORE INTO rp_character_proxy_identity(guild_id,character_id,state)
      VALUES(?,?,'approved')''',(str(guild_id),int(character_id)))


def activate(db,guild_id,character_id):
    approve(db,guild_id,character_id)
    db.execute("UPDATE rp_character_proxy_identity SET state='active' WHERE guild_id=? AND character_id=?",
               (str(guild_id),int(character_id)))


def claim_notice(db,guild_id,character_id):
    initialize(db)
    changed = db.execute('''UPDATE rp_character_proxy_identity SET announced=1
      WHERE guild_id=? AND character_id=? AND state='active' AND announced=0''',
      (str(guild_id),int(character_id)))
    return changed.rowcount == 1


def unclaim_notice(db,guild_id,character_id):
    db.execute("UPDATE rp_character_proxy_identity SET announced=0 WHERE guild_id=? AND character_id=?",
               (str(guild_id),int(character_id)))


def eligible(db,guild_id,character_id,user_id):
    row=db.execute('''SELECT c.name,c.owner_id,c.status,c.settings_json,c.public_code,
      p.state FROM characters c JOIN rp_character_proxy_identity p
      ON p.guild_id=c.guild_id AND p.character_id=c.id
      WHERE c.guild_id=? AND c.id=?''',(str(guild_id),int(character_id))).fetchone()
    return row if row and str(row['owner_id']) == str(user_id) and row['status']=='active' and row['state']=='active' else None


def display(row):
    try:
        data=json.loads(row['settings_json'] or '{}')
    except (ValueError,TypeError):
        data={}
    if not isinstance(data,dict):data={}
    return str(data.get('stage_name') or row['name'])[:80],str(data.get('portrait_url') or '')
