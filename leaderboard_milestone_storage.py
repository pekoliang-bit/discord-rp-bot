"""獨立里程碑待派發帳本，不直接改動資產。"""
import json
from core.database import transaction

def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS lb_v11_milestones(
      guild_id TEXT NOT NULL, entity_key TEXT NOT NULL, board TEXT NOT NULL,
      kind TEXT NOT NULL, dimension TEXT NOT NULL, threshold INTEGER NOT NULL,
      achieved_idx INTEGER NOT NULL, reward_json TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending_adapter',
      created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,entity_key,board,kind,dimension,threshold))""")

def store(guild_id, entity_key, board, kind, dimension, idx, rewards):
    with transaction() as db:
        initialize(db)
        for reward in rewards:
            db.execute("""INSERT OR IGNORE INTO lb_v11_milestones
              (guild_id,entity_key,board,kind,dimension,threshold,achieved_idx,reward_json)
              VALUES(?,?,?,?,?,?,?,?)""",
              (str(guild_id),str(entity_key),board,kind,dimension,
               reward["threshold"],idx,json.dumps(reward,ensure_ascii=False)))
