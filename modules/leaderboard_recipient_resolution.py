"""依正式排行榜身分識別收款路由；不執行任何金流變更。"""
from __future__ import annotations
from modules.leaderboard_payout_identity import validate_identity

class RecipientNotReady(ValueError):
    pass

ROUTES = {'character':'character_wallet','company':'company_treasury',
          'group':'group_treasury','shop':'shop_ledger','faction':'faction_treasury',
          'cp':'pair_split','social_account':'social_account_ledger'}


def resolve(board,entity_key,*,db=None,guild_id=None):
    parsed=validate_identity(board,entity_key)
    kind=parsed['entity_type'];eid=str(parsed['entity_id'])
    if not eid.strip():raise RecipientNotReady('獲獎單位缺少 ID')
    plan={'board':board,'entity_type':kind,'entity_id':eid,'route':ROUTES[kind],
          'requires_verified_adapter':kind!='character'}
    if db is not None:
        if guild_id is None:raise ValueError('查詢真實收款帳戶時必須提供 guild_id')
        from modules.leaderboard_account_adapter import inspect_account
        plan['account']=inspect_account(db,guild_id,board,entity_key)
    return plan
