"""已核准完工且公開作品的榜單來源；無公開證據時保守不計。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns, ranked


def query(guild_id, limit=100):
    with get_connection() as db:
        required = {'guild_id', 'production_id', 'status'}
        if not required <= table_columns(db, 'production_completion'):
            return [], 'unavailable'
        # 正式「公開發表」欄位目前尚未核實；approved 僅代表完工審核，不能等同公開。
        publication_columns = table_columns(db, 'productions')
        if 'published_at' not in publication_columns:
            return [], 'unavailable'
        rows = db.execute('''SELECT c.id AS character_id,c.name AS character_name,
                    COUNT(DISTINCT p.id) AS score FROM characters c
                    JOIN contracts k ON k.guild_id=c.guild_id AND k.character_id=c.id
                    JOIN productions p ON p.guild_id=k.guild_id AND p.id=k.production_id
                    JOIN production_completion pc ON pc.guild_id=p.guild_id AND pc.production_id=p.id
                    WHERE c.guild_id=? AND c.status='active' AND pc.status='approved'
                    AND p.published_at IS NOT NULL AND k.status IN ('active','completed')
                    GROUP BY c.id,c.name''', (str(guild_id),)).fetchall()
    return (ranked([dict(r) for r in rows],limit), 'ready') if rows else ([], 'empty')
