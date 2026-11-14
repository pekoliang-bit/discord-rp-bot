"""Safely import only fully sourced historical assets. Dry-run is default."""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
DEFAULT=ROOT/'assets/history_v23/verified_import_ready.json'
PAT=re.compile(r'(WN|CN|HB|HE)-\d{4}\Z')
def validate_data(path):
    data=json.loads(Path(path).read_text(encoding='utf8'))
    if not isinstance(data,list) or not data:raise ValueError('Only non-empty JSON arrays can be imported')
    seen=set()
    for r in data:
        if not isinstance(r,dict):raise ValueError('Each item must be a JSON object')
        k=r.get('asset_id','')
        if not PAT.fullmatch(k) or k in seen:raise ValueError(f'Invalid or duplicate ID: {k}')
        seen.add(k)
        if (not isinstance(r.get('title'),str) or not r['title'].strip()
            or r.get('status') not in (None,'source_verified')
            or r.get('import_ready') is False or r.get('approved') is False
            or r.get('review_status')):
            raise ValueError(f'Asset is not source-verified and must not be imported: {k}')
        if r.get('pool','general')!='general':raise ValueError('Separate passerby assets are not imported here')
        if k.startswith('HE-') and int(k[-4:])>=151:
            m=r.get('meta',{})
            if not isinstance(m,dict) or not re.fullmatch(r'HB-\d{4}',str(m.get('hb_id',''))):
                raise ValueError(f'{k}: valid HB reference required')
            if not re.fullmatch(r'B[1-5]',str(r.get('tier',''))):raise ValueError(f'{k}: B1..B5 required')
    return data

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--guild',required=True);p.add_argument('--file',default=DEFAULT,type=Path)
    p.add_argument('--apply',action='store_true',help='Write to current SQLite (default dry run)')
    a=p.parse_args()
    if not re.fullmatch(r'\d{10,25}',a.guild):raise ValueError('Guild ID must be numeric')
    items=validate_data(a.file)
    if not a.apply:
        print(f'DRY RUN: {len(items)} confirmed source rows, no DB writes. Add --apply to import.')
        return
    from core.history_v23 import put_batch
    from core.database import get_connection
    from core.history_v23 import init
    refs={r['asset_id']:r for r in items}
    with get_connection() as db:
        init(db)
        for r in items:
            k=r['asset_id']
            if k.startswith('HE-') and int(k[-4:])>=151:
                hb=r['meta']['hb_id'];partner=refs.get(hb)
                if partner:
                    if partner.get('tier')!=r.get('tier'):raise ValueError('HB-tier mismatch: '+k)
                else:
                    old=db.execute('''SELECT approved,enabled,tier FROM rp_history_assets_v23
                        WHERE guild_id=? AND pool='general' AND asset_id=?''',(a.guild,hb)).fetchone()
                    if not old or not old['approved'] or not old['enabled'] or old['tier']!=r.get('tier'):
                        raise ValueError('HB not previously approved: '+hb)
    for start in range(0,len(items),250):put_batch(a.guild,items[start:start+250])
    print(f'Imported {len(items)} verified entries. No historical draws, bonuses or player values changed.')
if __name__=='__main__':main()
