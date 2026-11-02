"""不變更資料的診斷報告。"""
from modules.leaderboard_config import get_config
from modules.leaderboard_snapshot import collect
from modules.leaderboard_history import periods

def diagnose(guild_id):
    config=get_config(guild_id)
    available,missing=collect(guild_id)
    return {'configured':config is not None,'enabled':bool(config and config['enabled']),
            'available_boards':sorted(available),'unavailable_boards':missing,
            'recent_periods':periods(guild_id,5),
            'notes':['尚未掛接排程；需由現有 bot 啟動流程明確呼叫',
                     '獎勵尚未設定，不能自動發放',
                     '過期結算不得使用現值回填歷史']}
