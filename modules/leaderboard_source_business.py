"""企業/集團排行榜前置來源核對。尚無核准的標準化常數時不產生排名。"""
from __future__ import annotations
from core.database import get_connection
from modules.leaderboard_source_common import table_columns

BUSINESS={'guild_id','business_id','kind','name','status','parent_id','brand_tier'}
BRAND_SALES={'guild_id','brand_id','kind','amount','created_at'}
HEAT={'guild_id','brand_id','points','created_at'}

def inspect(guild_id=None):
    with get_connection() as db:
        cols={t:table_columns(db,t) for t in ('rp_business_entities','rp_brand_sales','rp_brand_heat_events')}
        return {'business_entities_ready':BUSINESS <= cols['rp_business_entities'],
                'brand_sales_ready':BRAND_SALES <= cols['rp_brand_sales'],
                'brand_heat_ready':HEAT <= cols['rp_brand_heat_events'],
                'fixed_normalization_ready':False,
                'group_net_assets_ready':False,
                'note':'不得將銷售總額冒充淨營業額；尚須核對企業成本、規模、集團淨資產與固定基準。'}

def query_business(guild_id,limit=100):
    return [],'unavailable'

def query_group(guild_id,limit=100):
    return [],'unavailable'
