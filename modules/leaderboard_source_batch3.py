"""第三批來源整合入口：提供真實可用的 CP/商鋪榜與其他榜單狀態。"""
from __future__ import annotations
from modules import leaderboard_source_cp, leaderboard_source_shops
from modules import leaderboard_source_wealth, leaderboard_source_business

ALIASES={'cp':'cp','best_cp':'cp','最佳CP':'cp',
         'shops':'shops','shop':'shops','商鋪排名':'shops',
         'wealth':'wealth','money':'wealth','金錢':'wealth',
         'business':'business','產業經營':'business',
         'group':'group','集團競逐':'group'}

def query(guild_id,board,limit=100):
    key=ALIASES.get(board,board)
    if key=='cp': return leaderboard_source_cp.query(guild_id,limit)
    if key=='shops': return leaderboard_source_shops.query(guild_id,limit)
    if key=='wealth': return leaderboard_source_wealth.query(guild_id,limit)
    if key=='business': return leaderboard_source_business.query_business(guild_id,limit)
    if key=='group': return leaderboard_source_business.query_group(guild_id,limit)
    return [],'unavailable'

def readiness(guild_id=None):
    return {'wealth':leaderboard_source_wealth.inspect(guild_id),
            'business':leaderboard_source_business.inspect(guild_id),
            'cp':'需 rp_cp_pairs、rp_relationship_scores 與角色資料表',
            'shops':'需 personal_shops 與 personal_shop_sales'}
