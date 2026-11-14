"""Regenerate all 580 slots, preserving the exact approved source/candidate separation."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets/history_v23'
GROUPS=[('WN',1,200,'作品'),('CN',1,100,'劇中姓名'),('HB',1,50,'歷史品牌'),
        ('HE',1,75,'正面經歷'),('HE',76,150,'負面經歷'),('HE',151,230,'歷史代言')]

def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def write(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def build():
    five=read('verified_five.json');brands=read('HB_0001_0045_approved.json')
    if len(five)!=10 or len(brands)!=45:raise ValueError('已核准品牌或五則錨定代言來源數不符。')
    official={r['asset_id']:r for r in five+brands}
    if len(official)!=55 or any(not r.get('title') for r in official.values()):
        raise ValueError('正式可入庫素材的編號／文字有重複或空缺。')
    candidates=read('candidate_text_index_517.json')
    candidate_by_id={r['asset_id']:r for r in candidates}
    if len(candidates)!=517 or len(candidate_by_id)!=517:
        raise ValueError('新編候選數量／編號異常。')
    if any(x['content_origin']!='newly_rewritten_not_restored_original' for x in candidates):
        raise ValueError('候選文字來源標籤遭到改寫。')
    if set(official)&set(candidate_by_id):raise ValueError('候選與正式編號重疊，需先核對。')
    rarity={r['asset_id']:r for r in read('HE_0151_0230_approved_rarity_mapping.json')}
    if len(rarity)!=80:raise ValueError('HE 歷史代言80則稀有度對照未完成。')
    items=[];sections=[]
    for prefix,lo,hi,group in GROUPS:
        block=[]
        for n in range(lo,hi+1):
            key=f'{prefix}-{n:04d}'
            src=official.get(key)
            cand=candidate_by_id.get(key)
            # title is ONLY previously approved text; candidate names live in separate files.
            status=('source_verified' if src and key in {x['asset_id'] for x in five}
                    else 'previously_approved' if src else 'source_text_missing')
            row={'asset_id':key,'pool':'general','group':group,
                 'title':src['title'] if src else None,
                 'tier':src.get('tier') if src else rarity[key]['tier'] if key in rarity else None,
                 'rarity':src.get('rarity') if src and src.get('rarity') else rarity[key]['rarity'] if key in rarity else None,
                 'meta':src.get('meta',{}) if src else {},
                 'status':status,'previously_approved':True,
                 'original_text_restored':bool(src),'approved':bool(src),'import_ready':bool(src),
                 'candidate_available':bool(cand),
                 'candidate_source_file':cand['candidate_file'] if cand else None}
            block.append(row)
        write(f'{prefix}_{lo:04d}_{hi:04d}_inventory.json',block)
        items+=block
        sections.append({'group':group,'range':f'{prefix}-{lo:04d}～{prefix}-{hi:04d}',
                         'expected':len(block),'import_ready':sum(r['import_ready'] for r in block),
                         'candidate_only':sum(r['candidate_available'] for r in block),
                         'original_missing':sum(not r['import_ready'] for r in block)})
    assert len(items)==580 and len({r['asset_id'] for r in items})==580
    if sum(r['import_ready'] for r in items)!=55 or sum(r['candidate_available'] for r in items)!=517:
        raise ValueError('正式／候選數量不符合核准紀錄。')
    write('catalog_580_inventory.json',items)
    write('verified_import_ready.json',five+brands)
    audit={'total_slots':580,'historically_approved_spec':580,
           'original_text_import_ready':55,'candidate_text_only_not_approved_original':517,
           'no_available_full_text':8,'awaiting_original_text':525,
           'endorsement_rarity_mapped':80,'sections':sections,
           'warning':'580項曾核准；已找回可忠實入庫原文僅55。517項為後來新編候選，與先前核准原文不同，不能直接匯入。WN0193-0200無可解析完整候選。'}
    write('IMPORT_AUDIT.json',audit)
    return audit

if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
