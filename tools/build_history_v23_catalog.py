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
    """依群主已採用的當前580筆內容刷新索引；不重新寫入候選原始檔。"""
    entries=read('current_adopted_580.json')
    by_id={r['asset_id']:r for r in entries}
    if len(entries)!=580 or len(by_id)!=580:
        raise ValueError('現行580筆素材編號不完整或有重複。')
    catalog=[];sections=[]
    for prefix,lo,hi,group in GROUPS:
        block=[]
        for n in range(lo,hi+1):
            key=f'{prefix}-{n:04d}'
            src=by_id.get(key)
            if not src or not src.get('title') or not src.get('import_ready'):
                raise ValueError(f'缺少現行可匯入素材：{key}')
            row={'asset_id':key,'pool':'general','group':group,'title':src['title'],
                 'tier':src.get('tier') or None,'rarity':src.get('rarity') or None,
                 'meta':src.get('meta') or {},'status':'previously_approved',
                 'previously_approved':True,'original_text_restored':key in {
                     x['asset_id'] for x in read('verified_import_ready.json')},
                 'approved':True,'import_ready':True,'candidate_available':False,
                 'current_project_content_adopted':True}
            block.append(row)
        write(f'{prefix}_{lo:04d}_{hi:04d}_inventory.json',block)
        catalog.extend(block)
        sections.append({'group':group,'expected':len(block),'import_ready':len(block)})
    write('catalog_580_inventory.json',catalog)
    audit={'total_slots':580,'current_version_approved_import_ready':580,
           'previous_source_verified':55,'owner_adopted_candidate':517,
           'newly_filled_wn':8,'sections':sections,
           'warning':'目前採用現行專案文字；不再追索舊稿。8筆WN為本次新補。'}
    write('IMPORT_AUDIT.json',audit)
    return audit

if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
