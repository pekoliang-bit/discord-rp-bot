"""Create the 580-slot historical asset audit without inventing missing content."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets/history_v23'
GROUPS=[('WN',1,200,'作品'),('CN',1,100,'劇中姓名'),('HB',1,50,'歷史品牌'),('HE',1,75,'正面經歷'),('HE',76,150,'負面經歷'),('HE',151,230,'歷史代言')]

def build():
    trusted=json.loads((OUT/'verified_five.json').read_text(encoding='utf8'))
    original={r['asset_id']:r for r in trusted}
    if len(original)!=len(trusted):raise ValueError('Verified list duplicate ID')
    result=[];summary=[]
    for prefix,start,end,label in GROUPS:
        section=[]
        for n in range(start,end+1):
            key=f'{prefix}-{n:04d}';r=original.get(key)
            section.append({'asset_id':key,'pool':'general','group':label,'title':r['title'] if r else None,
              'tier':r.get('tier') if r else None,'rarity':r.get('rarity') if r else None,'meta':r.get('meta',{}) if r else {},
              'status':'source_verified' if r else 'source_text_missing','import_ready':bool(r)})
        result.extend(section)
        (OUT/f'{prefix}_{start:04d}_{end:04d}_inventory.json').write_text(json.dumps(section,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        summary.append({'group':label,'range':f'{prefix}-{start:04d}～{prefix}-{end:04d}','expected':len(section),
          'verified':sum(r['import_ready'] for r in section),'missing':sum(not r['import_ready'] for r in section)})
    if len(result)!=580 or set(original)!={r['asset_id'] for r in result if r['import_ready']}:
        raise ValueError('Asset count mismatch')
    (OUT/'catalog_580_inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'verified_import_ready.json').write_text(json.dumps(trusted,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    audit={'expected':580,'source_verified':len(trusted),'missing_original_text':580-len(trusted), 'sections':summary,
           'warning':'未提供原始全文的條目只是索引，絕對不能用空標題核准或啟用，也不能以自行創作內容替代。'}
    (OUT/'IMPORT_AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return audit
if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
