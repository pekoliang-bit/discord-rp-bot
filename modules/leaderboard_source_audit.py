"""檢查第二批來源能否安全使用；不執行寫入。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns


def inspect_sources():
    names = ('characters','attributes','character_skills','accounts','ledger_entries',
             'productions','production_completion','contracts','rp_endorsement_contracts')
    with get_connection() as db:
        columns = {name: sorted(table_columns(db,name)) for name in names}
    return {
        'tables': {name: bool(cols) for name,cols in columns.items()},
        'works_publication_ready': 'published_at' in columns['productions'],
        'endorsement_term_ready': {'starts_at','ends_at'} <= set(columns['rp_endorsement_contracts']),
        'wealth_ready': False,  # 尚未核對實物資產、定存與債務的完整正式來源
        'newcomer_ready': False,  # 尚未核對正式啟用基準快照
        'notes': '不符合必要條件的榜單須顯示資料未接妥，禁止虛構排名。',
    }
