"""排行榜結算通知橋接：沿用既有 /通知 outbox，不註冊新指令。

本批不啟動結算排程、不自行發獎；由日後已核准的結算器呼叫。
"""
from __future__ import annotations
from modules.notifications import queue_notification


def queue_rank_summary(guild_id:int,period_id:str,kind:str,board_name:str,lines:list[str]):
    """kind 必須是 14d 或 30d；同一期同榜通知防重複。"""
    if kind not in ('14d','30d'):
        raise ValueError('結算週期只能是 14d 或 30d')
    title='14天小結算' if kind=='14d' else '30天大結算'
    safe=[str(x).replace('@','＠')[:130] for x in lines[:30]]
    message=f'🏆 {title}｜{board_name}\n'+('\n'.join(safe) or '本期沒有合格排名')
    return queue_notification(guild_id,'main','system',message[:1800],
      dedupe_key=f'leaderboard:{period_id}:{kind}:{board_name}')


def queue_character_result(guild_id:int,period_id:str,character_id:int,message:str):
    """送角色專屬論壇通知帖；需既有 character_forum_links.notice_id。"""
    return queue_notification(guild_id,'private','role',message[:1800],
      character_id=character_id,
      dedupe_key=f'leaderboard:character:{period_id}:{character_id}')
