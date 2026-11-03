"""新手指令事件及專屬「機器人使用」帖子檢查；不自行攔截全部 Discord 指令。"""
from __future__ import annotations
from core.database import get_connection
from modules.task_repository import record_event, get_events

REQUIRED_COMMANDS = ('查看','登記','公司','商城','申請','銀行','陣營','網路',
                     '劇情','傳聞','娛樂城','惡意','任務','排行','總覽','抽獎')

def character_owner(guild_id,character_id,user_id):
    with get_connection() as db:
        row=db.execute('''SELECT id FROM characters WHERE guild_id=? AND id=? AND owner_id=?
                       AND status IN ('active','approved') AND character_type='player' ''',
                       (str(guild_id),int(character_id),str(user_id))).fetchone()
        return row is not None

def bot_thread_id(guild_id,character_id):
    with get_connection() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='character_forum_links'").fetchone()
        if not exists:return None
        row=db.execute('SELECT bot_id FROM character_forum_links WHERE guild_id=? AND character_id=?',
                       (str(guild_id),int(character_id))).fetchone()
    return str(row['bot_id']) if row and row['bot_id'] else None

def completed_commands(guild_id,character_id):
    return {r['event_key'][len('newbie:'):] for r in get_events(guild_id,character_id)
            if r['event_key'].startswith('newbie:') and r['event_key'][len('newbie:'):] in REQUIRED_COMMANDS}

def is_graduated(guild_id,character_id):
    return len(completed_commands(guild_id,character_id)) == len(REQUIRED_COMMANDS)

def progress(guild_id,character_id):
    completed=completed_commands(guild_id,character_id)
    return {'done':len(completed),'total':len(REQUIRED_COMMANDS),
            'completed':sorted(completed),'remaining':[x for x in REQUIRED_COMMANDS if x not in completed],
            'graduated':len(completed)==len(REQUIRED_COMMANDS)}

def can_use_training_channel(guild_id,character_id,user_id,channel_id,command):
    """僅為教學瀏覽豁免；實際交易仍須系統自己的正常頻道驗證。"""
    if command not in REQUIRED_COMMANDS or not character_owner(guild_id,character_id,user_id):return False
    if is_graduated(guild_id,character_id):return False
    expected=bot_thread_id(guild_id,character_id)
    return expected is not None and str(channel_id)==expected

def record_success(guild_id,character_id,user_id,channel_id,command):
    """在指令成功顯示指定教學介面後由真正指令入口呼叫。"""
    if not can_use_training_channel(guild_id,character_id,user_id,channel_id,command):return False
    return record_event(guild_id,character_id,'newbie:'+command,'command:'+command)

def newbie_required_channel(guild_id, character_id, user_id, channel_id, command):
    """對新手必修指令採強制本人帖子；畢業後由原頻道規則決定。"""
    if command not in REQUIRED_COMMANDS: return None
    if not character_owner(guild_id, character_id, user_id): return False
    if is_graduated(guild_id, character_id): return None
    return can_use_training_channel(guild_id, character_id, user_id, channel_id, command)



def training_status_for_user(guild_id, user_id):
    """Read-only character-granular progress for UI and support."""
    from core.database import get_connection
    with get_connection() as db:
        rows = db.execute(
            "SELECT id FROM characters WHERE guild_id=? AND owner_id=? "
            "AND character_type='player' AND status IN ('active','approved')",
            (str(guild_id), str(user_id)),
        ).fetchall()
    return {int(r['id']): progress(guild_id, int(r['id'])) for r in rows}
