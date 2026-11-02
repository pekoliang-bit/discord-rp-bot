
"""角色粉絲、黑粉、影響指數、主攻的安全來源檢查；不臆測欄位。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns, ranked

# 必須存在精確且唯一的數值名稱才採用，避免把熱度誤作影響指數。
ATTRIBUTE_KEYS = {'impact': ('影響指數',), 'fans': ('粉絲', '粉絲數'),
                  'antis': ('黑粉', '黑粉數'), 'main_skill': ()}


def query(guild_id, board, limit=100):
    from modules.extra_character_store import initialize as _initialize_extras
    _initialize_extras()
    if board not in ATTRIBUTE_KEYS:
        return [], 'unavailable'
    if board == 'main_skill':
        # 主攻類型設定可能儲存在角色 JSON；未完成映射前不得拿最高技能冒充主攻。
        return [], 'unavailable'
    keys = ATTRIBUTE_KEYS[board]
    with get_connection() as db:
        if not {'guild_id', 'character_id', 'name', 'value'} <= table_columns(db, 'attributes'):
            return [], 'unavailable'
        marks = ','.join('?' for _ in keys)
        data = db.execute(f'''SELECT c.id AS character_id,c.name AS character_name,
                     MAX(a.value) AS score FROM characters c JOIN attributes a
                     ON a.guild_id=c.guild_id AND a.character_id=c.id
                     WHERE c.guild_id=? AND c.status='active' AND (COALESCE(json_extract(c.settings_json,'$.character_category'),'') != 'extra' OR COALESCE((SELECT allow_ranking FROM extra_settings WHERE guild_id=c.guild_id),1)=1) AND a.name IN ({marks})
                     GROUP BY c.id,c.name''', (str(guild_id), *keys)).fetchall()
    return (ranked([dict(r) for r in data], limit), 'ready') if data else ([], 'empty')
