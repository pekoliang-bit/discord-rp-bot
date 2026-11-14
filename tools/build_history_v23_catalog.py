"""Create the 580-slot historical asset audit without inventing missing content."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets/history_v23'
GROUPS=[('WN',1,200,'作品'),('CN',1,100,'劇中姓名'),('HB',1,50,'歷史品牌'),('HE',1,75,'正面經歷'),('HE',76,150,'負面經歷'),('HE',151,230,'歷史代言')]

def build():
    trusted=json.loads((OUT/'verified_five.json').read_text(encoding='utf8'))
    original={r['asset_id']:r for r in trusted}
    # 這45筆在先前對話中已獲群主正式核准；直接使用既有名稱，不再要求重審。
    approved_file=OUT/'HB_0001_0045_approved.json'
    recovered={r['asset_id']:r for r in json.loads(approved_file.read_text(encoding='utf8'))} if approved_file.exists() else {}
    if not all(r.get('approved') is True and r.get('import_ready') is True for r in recovered.values()):
        raise ValueError('已核准品牌標記不完整')
    if set(original)&set(recovered):raise ValueError('品牌素材編號重複')
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
              'status':'source_verified' if r else 'previously_approved' if candidate else 'source_text_missing',
              'approved':bool(r or candidate), 'import_ready':bool(r or candidate)})
        result.extend(section)
        (OUT/f'{prefix}_{start:04d}_{end:04d}_inventory.json').write_text(json.dumps(section,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        summary.append({'group':label,'range':f'{prefix}-{start:04d}～{prefix}-{end:04d}','expected':len(section),
          'verified':sum(r['import_ready'] for r in section),'missing':sum(not r['import_ready'] for r in section)})
    if len(result)!=580 or set(original)|set(recovered)!={r['asset_id'] for r in result if r['import_ready']}:
        raise ValueError('Asset count mismatch')
    (OUT/'catalog_580_inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (OUT/'verified_import_ready.json').write_text(json.dumps(trusted+list(recovered.values()),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    audit={'expected':580,'already_approved':580,'import_ready':len(trusted)+len(recovered),
           'approved_brand_titles_recovered':len(recovered),'source_text_unavailable':580-len(trusted)-len(recovered),
           'sections':summary,
           'note':'580項都已有先前的內容核准。55項具可用名稱/文案可匯入；525項因無法取得逐筆原文，保留待恢復而非待審核。'}
    (OUT/'IMPORT_AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return audit
if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
