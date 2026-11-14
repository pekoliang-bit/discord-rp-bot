"""Import exact approved historical assets into one guild's isolated V2.3 pool.

Dry-run is default. --apply requires a database backup and explicit confirmation.
Never imports newly rewritten candidate texts or touches character values.
"""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
DEFAULT=ROOT/'assets/history_v23/verified_import_ready.json'
PAT=re.compile(r'(WN|CN|HB|HE)-\d{4}\Z')

def validate_data(path,pool='general'):
    from core.history_v23 import validate_asset,POOLS
    if pool not in POOLS:raise ValueError('無效的素材庫。')
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,list) or not data:raise ValueError('僅接受非空 JSON 陣列。')
    if len(data)>580:raise ValueError('一批最多580筆，避免錯誤覆蓋。')
    seen=set()
    for r in data:
        if not isinstance(r,dict):raise ValueError('每筆必須是 JSON 物件。')
        k=r.get('asset_id','')
        if not isinstance(k,str) or not PAT.fullmatch(k) or k in seen:
            raise ValueError(f'素材編號有錯誤或重複：{k}')
        seen.add(k)
        if (r.get('approved') is False or r.get('import_ready') is False
           or r.get('review_status') or r.get('status') not in (None,'source_verified','previously_approved')):
            raise ValueError(f'Asset is not source-verified and must not be imported: {k}')
        if r.get('pool','general')!=pool:raise ValueError(f'{k} 素材池錯誤。')
        tier=r.get('tier') or '';rarity=r.get('rarity') or '';meta=r.get('meta') or {}
        validate_asset(k,r.get('title'),tier,rarity,meta,pool=pool)
    return data

def audit_candidates():
    loc=ROOT/'assets/history_v23/IMPORT_AUDIT.json'
    audit=json.loads(loc.read_text(encoding='utf-8'))
    print(json.dumps(audit,ensure_ascii=False,indent=2))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guild',help='正式 Discord 伺服器 ID')
    parser.add_argument('--file',type=Path,action='append',default=None,help='有原文的已核准素材 JSON；可重複使用以一次提交多個檔案')
    parser.add_argument('--pool',choices=['general','passerby'],default='general')
    parser.add_argument('--apply',action='store_true',help='經備份、在測試環境確認後才使用')
    parser.add_argument('--audit',action='store_true',help='只查看原文與候選內容數量')
    args=parser.parse_args()
    if args.audit:
        audit_candidates();return
    if not args.guild or not re.fullmatch(r'\d{10,25}',args.guild):
        parser.error('--guild 必須是 Discord 伺服器數字 ID。')
    paths=args.file or [DEFAULT]
    rows=[]
    for path in paths:
        rows.extend(validate_data(path,args.pool))
    if len(rows)>580 or len({r['asset_id'] for r in rows})!=len(rows):
        raise ValueError('合併來源有重複編號，或超過 580 筆上限。')
    if not args.apply:
        print(f'DRY RUN: {len(rows)} 筆來源有效；沒有寫入資料庫。用 --apply 才會正式寫入。')
        return
    from core.history_v23 import put_batch
    from core.database import get_connection
    from core.history_v23 import init
    refs={r['asset_id']:r for r in rows}
    with get_connection() as db:
        init(db)
        for r in rows:
            if r['asset_id'].startswith('HE-') and 151<=int(r['asset_id'][3:])<=230:
                hb=r['meta']['hb_id'];partner=refs.get(hb)
                if partner:
                    if partner.get('tier')!=r['tier']:raise ValueError('HB B 級不一致：'+r['asset_id'])
                else:
                    prev=db.execute('''SELECT tier,approved,enabled FROM rp_history_assets_v23
                        WHERE guild_id=? AND pool=? AND asset_id=?''',
                        (args.guild,args.pool,hb)).fetchone()
                    if not prev or not prev['approved'] or not prev['enabled'] or prev['tier']!=r['tier']:
                        raise ValueError('尚未匯入合法 HB：'+hb)
    inserted=put_batch(args.guild,rows,pool=args.pool)
    print(f'成功新增 {inserted} 筆；已存在同內容 {len(rows)-inserted} 筆，未重複匯入。')
    print('不觸發任何角色歷史抽取、金錢、數值或新人禮包。')

if __name__=='__main__':main()
