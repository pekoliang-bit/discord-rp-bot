"""排行榜快照標準化；未知或模糊身分整榜標記 unavailable，禁止猜測。"""
from __future__ import annotations
from modules.leaderboard_catalog import BOARDS
from modules.leaderboard_service import leaderboard
from modules.leaderboard_entity_identity import make_identity,parse_identity
from modules.leaderboard_payout_identity import expected_type,validate_identity
FIELDS={'character':('character_id',),'company':('company_id','business_id'),'group':('group_id',),'shop':('shop_id',),'faction':('faction_id',),'cp':('pair_id','cp_id'),'social_account':('account_id','social_account_id')}
NAMES=('character_name','display_name','name','title')
class AmbiguousRecipient(ValueError):pass
def recipient_key(board,row):
    if not isinstance(row,dict):raise AmbiguousRecipient('資料來源必須為字典')
    kind=expected_type(board)
    typed=row.get('entity_key')
    if typed is not None:
        try:validate_identity(board,typed)
        except (TypeError,ValueError) as exc:raise AmbiguousRecipient(str(exc)) from exc
        return str(typed)
    values=[str(row[k]) for k in FIELDS[kind] if row.get(k) is not None and str(row[k]).strip()]
    if len(set(values))>1:raise AmbiguousRecipient('多個身分欄位相互衝突')
    # 原有排行榜讀取器通常只有 id 欄位。必須先辨識為唯一 ID，
    # 且不能和別種單位的明確欄位混用。
    if not values and row.get('id') is not None and str(row['id']).strip():
        foreign=[k for name,fields in FIELDS.items() if name!=kind for k in fields if row.get(k) is not None]
        if foreign:raise AmbiguousRecipient('一般 ID 與其他類型身分欄位混用')
        values=[str(row['id'])]
    if not values:raise AmbiguousRecipient('缺少可確認的獲獎身分')
    return make_identity(kind,values[0])
def normalize_board(board,rows):
    entries=[];seen=set()
    for row in rows:
        key=recipient_key(board,row)
        if key in seen:raise AmbiguousRecipient('榜單包含重複身分')
        seen.add(key)
        score=float(row['score']);rank=int(row['rank'])
        if rank<1 or not __import__('math').isfinite(score):raise AmbiguousRecipient('非法名次或分數')
        name=next((str(row[k]) for k in NAMES if row.get(k) is not None),key)
        entries.append((rank,key,name,score))
    return entries
def collect(guild_id):
    result={};unavailable=[]
    for board,_ in BOARDS:
        rows,status=leaderboard(guild_id,board,limit=500)
        if status not in ('ready','empty'):
            unavailable.append(board);continue
        try:result[board]=normalize_board(board,rows)
        except (ValueError,TypeError,KeyError,OverflowError):unavailable.append(board)
    return result,unavailable
