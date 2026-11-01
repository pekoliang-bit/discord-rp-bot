"""總覽唯讀資料來源。"""
from core.database import get_connection

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
        from modules.tag_library import seed
        seed(gid)
        with get_connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT id,name,category FROM rp_tag_library
                   WHERE guild_id=? AND enabled=1 ORDER BY category,id""", (str(gid),))]
    if section == "characters":
        with get_connection() as db:
            return [dict(r) for r in db.execute(
                """SELECT id,public_code,name,character_type,occupation,status
                   FROM characters WHERE guild_id=? AND status IN ('active','retired')
                   ORDER BY id DESC""", (str(gid),))]
    return []

