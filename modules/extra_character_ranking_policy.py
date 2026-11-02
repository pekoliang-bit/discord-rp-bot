"""所有榜單共用的路人篩選（資料完整保留，僅影響排行資格）。"""
from __future__ import annotations
import json
from modules import extra_character_store as store
def allow_extra(gid):return bool(store.get_config(gid)['allow_ranking'])
def filter_rows(gid, rows):
    if allow_extra(gid):return rows
    # 回傳資料的角色 id 若無法辨識，保守保留，不改變非角色榜單。
    from core.database import get_connection
    with get_connection() as db:
        excluded={r[0] for r in db.execute("SELECT id FROM characters WHERE guild_id=? AND json_extract(settings_json,'$.character_category')='extra'",(str(gid),)).fetchall()}
    return [r for r in rows if (r.get('character_id') if isinstance(r,dict) else None) not in excluded]
