"""企業/集團/社群排行榜固定基準計分工具。基準須經群主正式核准後傳入。"""
from __future__ import annotations
from math import isfinite

def fixed_ratio(value, baseline):
    value, baseline = float(value), float(baseline)
    if not isfinite(value) or not isfinite(baseline) or baseline <= 0:
        raise ValueError('固定基準必須為正且有限的數值')
    return max(0.0, value) / baseline

def industry(revenue, heat, scale, *, revenue_base, heat_base, scale_base):
    return 40*fixed_ratio(revenue,revenue_base)+25*fixed_ratio(heat,heat_base)+35*fixed_ratio(scale,scale_base)

def group(subsidiaries, net_assets, heat, *, subsidiaries_base, assets_base, heat_base):
    return 40*fixed_ratio(subsidiaries,subsidiaries_base)+30*fixed_ratio(net_assets,assets_base)+30*fixed_ratio(heat,heat_base)

def social(traffic_30d, interactions_30d, followers, *, traffic_base, interactions_base, followers_base):
    return 40*fixed_ratio(traffic_30d,traffic_base)+30*fixed_ratio(interactions_30d,interactions_base)+30*fixed_ratio(followers,followers_base)
