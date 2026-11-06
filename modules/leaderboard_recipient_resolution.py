"""不同排行榜的收款身分解析，僅產生操作計畫，不動金流。"""
from __future__ import annotations
from modules.leaderboard_payout_identity import expected_type,validate_identity
class RecipientNotReady(ValueError):pass
def resolve(board,entity_key):
    parsed=validate_identity(board,entity_key)
    recipient_type=parsed['entity_type'];rid=str(parsed['entity_id'])
    if not rid.strip():raise RecipientNotReady('獲獎單位缺少 ID')
    # 帳戶類型不可猜成 character wallet。所有非角色獎勵先維持封鎖。
    route={'character':'character_wallet','company':'company_treasury','group':'group_treasury','shop':'shop_ledger','faction':'faction_treasury','cp':'pair_split','social_account':'social_account_ledger'}[recipient_type]
    return {'board':board,'entity_type':recipient_type,'entity_id':rid,'route':route,'requires_verified_adapter':recipient_type!='character'}
