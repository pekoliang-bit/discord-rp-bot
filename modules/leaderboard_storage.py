"""排行榜快照及結算資料庫。使用既有交易管理，不修改既有表。"""
import json
from core.database import transaction,get_connection

def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS lb_v11_config(guild_id TEXT PRIMARY KEY,anchor_utc TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS lb_v11_periods(guild_id TEXT NOT NULL,cycle TEXT NOT NULL,idx INTEGER NOT NULL,end_utc TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,cycle,idx))""")
        db.execute("""CREATE TABLE IF NOT EXISTS lb_v11_snapshots(guild_id TEXT NOT NULL,cycle TEXT NOT NULL,idx INTEGER NOT NULL,board TEXT NOT NULL,rank INTEGER NOT NULL,entity_key TEXT NOT NULL,display_name TEXT NOT NULL,score REAL NOT NULL,PRIMARY KEY(guild_id,cycle,idx,board,entity_key))""")
        db.execute("""CREATE INDEX IF NOT EXISTS lb_v11_snapshots_period ON lb_v11_snapshots(guild_id,cycle,idx,board,rank)""")
        db.execute("""CREATE TABLE IF NOT EXISTS lb_v11_delivery(guild_id TEXT NOT NULL,cycle TEXT NOT NULL,idx INTEGER NOT NULL,board TEXT NOT NULL,kind TEXT NOT NULL,entity_key TEXT NOT NULL DEFAULT '',state TEXT NOT NULL DEFAULT 'pending',updated_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,cycle,idx,board,kind,entity_key))""")
        db.execute("""CREATE TABLE IF NOT EXISTS lb_v11_awards(guild_id TEXT NOT NULL,cycle TEXT NOT NULL,idx INTEGER NOT NULL,board TEXT NOT NULL,entity_key TEXT NOT NULL,rank INTEGER NOT NULL,reward_json TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'unconfigured',updated_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(guild_id,cycle,idx,board,entity_key))""")
        db.execute("""CREATE TABLE IF NOT EXISTS lb_v11_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL,created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

def audit(guild_id,action,details):
    initialize()
    with transaction() as db:db.execute('INSERT INTO lb_v11_audit(guild_id,action,details) VALUES(?,?,?)',(str(guild_id),action,json.dumps(details,ensure_ascii=False,default=str)))
