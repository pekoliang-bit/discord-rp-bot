
"""30-day award staging for every qualified snapshot participant.

Never directly mutate wallets, character stats, or organization treasuries.
"""
from __future__ import annotations
from modules.leaderboard_award_ledger import stage_rewards

def settle_without_prizes(guild_id, cycle, index, board, ranked_rows):
    """Historical API name retained for compatibility with existing settlement code."""
    if cycle != '30d':
        return 0
    return stage_rewards(guild_id, cycle, index, board, ranked_rows)
