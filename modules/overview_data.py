"""總覽唯讀資料來源。"""
from core.database import get_connection
import sqlite3

def query_rows(gid, section):
    if section == "extra_likes":
        from modules.npc_likes import leaderboard
        return leaderboard(gid)
    if section.startswith("event_"):
        status = section.split("_", 1)[1]
        sql = """SELECT id,title,description,capacity,channel_id,status
                 FROM rp_event_projects WHERE guild_id=? AND status=?
                 ORDER BY id DESC"""
        with get_connection() as db:
            return [dict(r) for r in db.execute(sql, (str(gid), status))]
    if section == "awards":
        with get_connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT id,kind,name,description,condition_json,rewards_json
                   FROM rp_award_definitions
                   WHERE guild_id=? AND active=1 AND hidden=0
                   ORDER BY id DESC""", (str(gid),))]
    if section == "tags":
        # 總覽是公開唯讀入口，不可順手執行 tag_library.seed() 寫入資料庫。
        # 首次開服尚未初始化資料表時，直接讀取已核准 CSV 目錄作為唯讀預覽。
        try:
            with get_connection() as db:
                return [dict(r) for r in db.execute(
                    """SELECT id,name,category FROM rp_tag_library
                       WHERE guild_id=? AND enabled=1 ORDER BY category,id""", (str(gid),))]
        except sqlite3.OperationalError as exc:
            if 'no such table: rp_tag_library' not in str(exc):
                raise
            from modules.tag_library import catalog
            return [{'id': index, 'name': entry['標籤名稱'], 'category': '大眾印象'}
                    for index, entry in enumerate(catalog(), start=1)]
    if section == "characters":
        with get_connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT id,public_code,name,character_type,occupation,status
                   FROM characters WHERE guild_id=? AND status IN ('active','retired')
                   ORDER BY id DESC""", (str(gid),))]
    return []

