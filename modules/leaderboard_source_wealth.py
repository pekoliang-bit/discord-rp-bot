
"""金錢榜：核對完整資產構成，缺任何必要來源即不發布不完整排名。唯讀。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns

REQUIRED = {'wallet':'錢包', 'bank':'銀行活存', 'fixed':'定存', 'physical':'官方核價實體資產', 'debt':'未償債務'}

def inspect(guild_id=None):
    with get_connection() as db:
        account_cols = table_columns(db, 'accounts')
        ledger_cols = table_columns(db, 'ledger_entries')
        found = {'wallet': bool({'guild_id','owner_type','owner_id','account_type'} <= account_cols and {'account_id','amount'} <= ledger_cols),
                 'bank': False, 'fixed': False, 'physical': False, 'debt': False}
        return {'ready': all(found.values()), 'components': found,
                'missing': [REQUIRED[k] for k,v in found.items() if not v],
                'note':'帳戶流水帳存在不等於已確認所有銀行、定存、資產及負債來源；禁止僅以錢包代替總資產。'}

def query(guild_id, limit=100):
    return [], 'unavailable'
