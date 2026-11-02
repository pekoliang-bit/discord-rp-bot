"""獎勵尚未議定前採用明確的『不發獎』政策；不得自行推定金額。"""
from __future__ import annotations
from modules.leaderboard_award_ledger import record_unconfigured

def settle_without_prizes(guild_id, cycle, index, board, ranked_rows):
    if cycle != '30d':
        return 0
    count = 0
    for row in ranked_rows:
        if int(row['rank']) <= 10:
            record_unconfigured(guild_id,cycle,index,board,row['entity_key'],int(row['rank']))
            count += 1
    return count
