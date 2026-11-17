"""新手指令事件及專屬「機器人使用」帖子檢查；不自行攔截全部 Discord 指令。"""
from __future__ import annotations
import logging
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
    fresh = record_event(guild_id,character_id,'newbie:'+command,'command:'+command)
    # 已完成的指令仍可重試尚未入帳的獎勵；真正的帳本都會以唯一來源鍵防重複。
    try:
        from modules.task_newbie_rewards import retry_completed
        retry_completed(guild_id,character_id,user_id)
    except Exception:
        logging.getLogger(__name__).exception('新手完成後發獎／補發異常')
    return fresh

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


def is_test_character(guild_id, character_id):
    """判斷指定角色是否由內測抽角建立；缺少內測資料表時安全回傳 False。

    以 (伺服器, 角色 ID) 查內測綁定，不能只依角色名稱或職業判斷；
    即使內測已結束，原測試角色也不得被當成正式角色發獎。
    """
    if guild_id is None or character_id is None:
        return False
    with get_connection() as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_beta_characters'"
        ).fetchone()
        if not exists:
            return False
        row = db.execute(
            "SELECT status FROM rp_beta_characters WHERE guild_id=? AND character_id=?",
            (str(guild_id), int(character_id)),
        ).fetchone()
        return bool(row and row['status'] in ('active', 'ended', 'drafted'))
