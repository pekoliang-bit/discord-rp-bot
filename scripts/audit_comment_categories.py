"""唯讀核對網路評論庫分類與審核來源。

用法：python -m scripts.audit_comment_categories
只讀 assets/internet_comment_library 的 manifest 與分檔；
只寫 reports/comment_category_audit.json 和 .csv，不接觸 SQLite。

注意：REFERENCE 僅收錄目前對話可逐條核實的 C074、C309～C420；
C001～C308 其他條目不會由人工統計數量推測或自動改分類。
"""
from __future__ import annotations

import collections
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'assets' / 'internet_comment_library'
DEST = ROOT / 'reports'
NAMES = {1:'日常生活',2:'影視作品',3:'演藝活動',4:'商業活動',5:'粉絲互動',6:'感情關係／CP',7:'爭議事件',8:'公關回應',9:'熱搜話題',10:'營銷號',11:'社會議題',12:'特殊事件'}
# 人工彙總不是逐條審核證據；此數字只用於找出統計落差。
PRIOR_TOTALS = {1:26,2:23,3:54,4:12,5:47,6:16,7:55,8:54,9:21,10:30,11:10,12:71}


def reviewed_reference() -> dict[str, int]:
    """只編碼本對話中有逐條原審核標籤的 ID。"""
    result = {'C074': 3}
    for n in range(309, 330): result[f'C{n:03d}'] = 12
    for n in range(330, 345): result[f'C{n:03d}'] = 1
    groups = [
        (345,355,7),(356,356,9),(357,357,8),(358,359,7),
        (360,360,8),(361,361,7),(362,362,8),(363,363,7),
        (364,364,9),(365,367,7),(368,368,12),(369,372,3),
        (373,373,12),(374,374,3),(375,375,2),(376,379,3),
        (380,380,2),(381,381,3),(382,382,12),(383,386,3),
        (387,387,12),(388,405,3),(406,409,12),(410,410,3),
        (411,415,2),(416,416,5),(417,417,3),(418,419,12),
        (420,420,3),
    ]
    for start, end, cat in groups:
        for n in range(start, end + 1):
            key = f'C{n:03d}'
            if key in result: raise AssertionError(f'重疊參考編號：{key}')
            result[key] = cat
    return result


def load(folder: Path) -> tuple[list[dict], list[str]]:
    problems = []
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8-sig'))
    filenames = manifest.get('parts')
    if not isinstance(filenames, list) or not filenames:
        raise ValueError('manifest.json 缺少 parts 清單')
    if len(filenames) != len(set(filenames)):
        raise ValueError('manifest.parts 存在重複檔名')
    records = []
    for filename in filenames:
        if not isinstance(filename, str) or not re.fullmatch(r'comments_part_\d{2}\.json', filename):
            raise ValueError(f'不合法分檔名：{filename!r}')
        payload = json.loads((folder / filename).read_text(encoding='utf-8-sig'))
        entries = payload.get('approved')
        if not isinstance(entries, list): raise ValueError(f'{filename} 缺少 approved 陣列')
        for row in entries:
            if not isinstance(row, dict): raise ValueError(f'{filename} 存在非物件資料')
            records.append({'file':filename, **row})
    ids = [r.get('id') for r in records]
    dupes = sorted(k for k, count in collections.Counter(ids).items() if count > 1)
    if dupes: problems.append(f'重複 ID：{dupes}')
    if 'C074' not in ids: problems.append('找不到 C074')
    if 'C300' in ids: problems.append('已排除的 C300 意外出現')
    if len(records) != 419: problems.append(f'評論總數不等於 419：{len(records)}')
    for r in records:
        if r.get('category_id') not in NAMES: problems.append(f"{r.get('id')}: 無效主分類")
    return records, problems


def audit(folder: Path = SOURCE) -> dict:
    records, problems = load(folder)
    refs = reviewed_reference()
    by_id = {r['id']:r for r in records}
    mismatches = []
    verified = []
    no_source = []
    reconstructed = []
    for r in records:
        cid = r['id']
        expected = refs.get(cid)
        if expected is None:
            no_source.append(cid)
        elif r['category_id'] != expected:
            mismatches.append({'id':cid,'file':r['file'],'text':r.get('text',''),
              'json_category_id':r['category_id'],'json_category':r.get('category',''),
              'reviewed_category_id':expected,'reviewed_category':NAMES[expected],
              'decision':'待群主確認；不得自動修改'})
        else:
            verified.append(cid)
        if r.get('metadata_provenance') == 'reviewed_category_with_reconstructed_descriptors':
            reconstructed.append(cid)
    counts = collections.Counter(r['category_id'] for r in records)
    summary = [{'id':i,'name':NAMES[i],'json_count':counts[i],
                'prior_chat_count':PRIOR_TOTALS[i],
                'delta':counts[i]-PRIOR_TOTALS[i],
                'missing_to_80':max(0,80-counts[i])} for i in NAMES]
    # 統計差額只代表總量有差異，不能據此識別哪一筆分類錯誤。
    return {'status':'ERROR' if problems else 'REVIEW_NEEDED',
      'total':len(records),'reference_covered':len(refs),
      'matched_reviewed_categories':len(verified),
      'mismatched_reviewed_categories':mismatches,
      'not_individually_verifiable_ids':no_source,
      'reconstructed_descriptor_ids':reconstructed,
      'category_summary':summary,'problems':problems,
      'note':'分類參考只覆蓋 C074、C309～C420；其他 ID 需原審核紀錄，不能自動推測。'}


def main() -> int:
    result = audit()
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST/'comment_category_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (DEST/'comment_category_audit.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f, fieldnames=['id','file','text','json_category_id','json_category','reviewed_category_id','reviewed_category','decision'])
        writer.writeheader(); writer.writerows(result['mismatched_reviewed_categories'])
    print('評論總數：', result['total'])
    print('逐條有審核分類可核對：', result['reference_covered'])
    print('分類一致：',result['matched_reviewed_categories'])
    print('分類不一致：',len(result['mismatched_reviewed_categories']))
    print('尚無逐條原審核對照：',len(result['not_individually_verifiable_ids']))
    print('細項標籤標記為重新整理：',len(result['reconstructed_descriptor_ids']))
    print('分類差異：')
    for row in result['category_summary']:
        if row['delta']: print(f"  {row['id']:02d} {row['name']}: JSON {row['json_count']} / 原統計 {row['prior_chat_count']} ({row['delta']:+d})")
    print('報告：reports/comment_category_audit.json / .csv')
    return 1 if result['problems'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
