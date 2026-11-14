"""Create the 580-slot historical asset audit without inventing missing content."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets/history_v23'
GROUPS=[('WN',1,200,'作品'),('CN',1,100,'劇中姓名'),('HB',1,50,'歷史品牌'),('HE',1,75,'正面經歷'),('HE',76,150,'負面經歷'),('HE',151,230,'歷史代言')]

def build():
    trusted=json.loads((OUT/'verified_five.json').read_text(encoding='utf8'))
    original={r['asset_id']:r for r in trusted}
    # 先前核准規格摘要恢復的45筆名稱，只用於人工對照，不可自動匯入。
    review_file=OUT/'HB_0001_0045_recovered_review_only.json'
    recovered={r['asset_id']:r for r in json.loads(review_file.read_text(encoding='utf8'))} if review_file.exists() else {}
    if set(original)&set(recovered):raise ValueError('Review IDs conflict with verified source')
    if len(original)!=len(trusted):raise ValueError('Verified list duplicate ID')
    result=[];summary=[]
    for prefix,start,end,label in GROUPS:
        section=[]
        for n in range(start,end+1):
            key=f'{prefix}-{n:04d}';r=original.get(key);candidate=recovered.get(key) if not r else None
            shown=r or candidate
            section.append({'asset_id':key,'pool':'general','group':label,'title':shown['title'] if shown else None,
              'tier':shown.get('tier') if shown else None,'rarity':shown.get('rarity') if shown else None,
              'meta':shown.get('meta',{}) if shown else {},
              'status':'source_verified' if r else 'recovered_summary_needs_original_text_review' if candidate else 'source_text_missing',
              'import_ready':bool(r)})
        result.extend(section)
        (OUT/f'{prefix}_{start:04d}_{end:04d}_inventory.json').write_text(json.dumps(section,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        summary.append({'group':label,'range':f'{prefix}-{start:04d}～{prefix}-{end:04d}','expected':len(section),
          'verified':sum(r['import_ready'] for r in section),'missing':sum(not r['import_ready'] for r in section)})
    if len(result)!=580 or set(original)!={r['asset_id'] for r in result if r['import_ready']}:
        raise ValueError('Asset count mismatch')
    (OUT/'catalog_580_inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'verified_import_ready.json').write_text(json.dumps(trusted,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    audit={'expected':580,'source_verified':len(trusted),'missing_original_text':580-len(trusted),
           'recovered_brand_titles_review_only':len(recovered),'still_no_title_at_all':580-len(trusted)-len(recovered),
           'sections':summary,
           'warning':'先前核准摘要恢復的45個品牌名稱尚需對照原始核准全文。只有verified_import_ready.json內10筆可直接匯入。'}
    (OUT/'IMPORT_AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return audit
if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
