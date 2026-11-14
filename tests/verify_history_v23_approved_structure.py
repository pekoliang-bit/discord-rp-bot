
"""Check previously approved V2.3 quotas without inventing unavailable individual texts."""
from __future__ import annotations
import json
from pathlib import Path
A=Path(__file__).resolve().parents[1]/'assets/history_v23'
structure=json.loads((A/'approved_structure_v23.json').read_text(encoding='utf-8'))
rarities=json.loads((A/'HE_0151_0230_approved_rarity_mapping.json').read_text(encoding='utf-8'))
assert sum(i['count'] for i in structure['wn_work_groups'])==200
assert sum(structure['hb_brand_group_counts'].values())==50
assert sum(i['positive'] for i in structure['he_event_categories'])==75
assert sum(i['negative'] for i in structure['he_event_categories'])==75
assert sum(structure['he_150_rarity_totals'].values())==150
assert sum(structure['he_positive_rarity_counts'].values())==75
assert sum(structure['he_negative_rarity_counts'].values())==75
from collections import Counter
assert dict(Counter(x['rarity'] for x in rarities))==structure['he_80_rarity_counts']
assert structure['five_history_slots']=={'guaranteed_work':1,'guaranteed_endorsement':1,'mixed_extra':3}
assert sum(structure['extra_three_weights_percent'].values())==100
print('PASS: Approved work / brand / event quotas; 80 per-ID rarity map; 5-slot mix preserved')
