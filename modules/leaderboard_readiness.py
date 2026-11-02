"""不以猜測值填補缺少的排行榜資料；提供可供管理端使用的完整性報告。"""
from __future__ import annotations
from modules.leaderboard_catalog import BOARDS
from modules.leaderboard_service import leaderboard

def report(guild_id):
    result = {'ready': [], 'empty': [], 'unavailable': [], 'errors': {}}
    for key, name in BOARDS:
        try:
            _, status = leaderboard(guild_id, key, limit=1)
            result[status if status in ('ready','empty') else 'unavailable'].append((key,name))
        except Exception as exc:
            result['unavailable'].append((key,name))
            result['errors'][key] = type(exc).__name__
    return result
