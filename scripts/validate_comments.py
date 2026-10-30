"""Discord RP 評論分檔唯讀校驗器。

在專案根目錄執行：python scripts/validate_comments.py
不連接資料庫、不改寫 JSON、不呼叫 Discord。
"""
from __future__ import annotations

import collections
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / 'assets' / 'internet_comment_library'
REPORT_FOLDER = ROOT / 'reports'
CATEGORY_NAMES = {
    1: '日常生活', 2: '影視作品', 3: '演藝活動', 4: '商業活動',
    5: '粉絲互動', 6: '感情關係／CP', 7: '爭議事件',
    8: '公關回應', 9: '熱搜話題', 10: '營銷號',
    11: '社會議題', 12: '特殊事件',
}
# 舊的對話人工統計，僅供檢查差異，不作為自動更正依據。
REFERENCE = {1: 26, 2: 23, 3: 54, 4: 12, 5: 47, 6: 16,
             7: 55, 8: 54, 9: 21, 10: 30, 11: 10, 12: 71}
REQUIRED = ('id', 'number', 'batch', 'text', 'category_id', 'category',
            'subcategory', 'speaker', 'emotion', 'scene', 'trigger_limit',
            'source', 'canonical_id', 'requires_authorization')


def read_json(path: Path):
    with path.open('r', encoding='utf-8-sig') as stream:
        return json.load(stream)


def validate(folder: Path = FOLDER) -> dict:
    errors, warnings = [], []
    manifest_path = folder / 'manifest.json'
    if not manifest_path.is_file():
        raise FileNotFoundError(f'缺少 manifest.json：{manifest_path}')
    manifest = read_json(manifest_path)
    parts = manifest.get('parts')
    if not isinstance(parts, list) or not parts:
        raise ValueError('manifest.parts 必須是非空陣列')
    if len(parts) != len(set(parts)):
        errors.append('manifest.parts 包含重複檔名')

    entries = []
    for filename in parts:
        if not isinstance(filename, str) or not re.fullmatch(r'comments_part_\d{2}\.json', filename):
            errors.append(f'不合法的分檔名稱：{filename!r}')
            continue
        path = folder / filename
        if not path.is_file():
            errors.append(f'缺少檔案：{filename}')
            continue
        try:
            data = read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f'無法讀取 {filename}：{exc}')
            continue
        if not isinstance(data, dict) or not isinstance(data.get('approved'), list):
            errors.append(f'{filename} 缺少 approved 陣列')
            continue
        for index, item in enumerate(data['approved'], 1):
            if not isinstance(item, dict):
                errors.append(f'{filename} 第 {index} 筆不是物件')
                continue
            entries.append((filename, item))

    id_sites = collections.defaultdict(list)
    numbers = collections.defaultdict(list)
    texts = collections.defaultdict(list)
    counts = collections.Counter()
    reconstructed = []
    auth_count = 0
    for filename, item in entries:
        identifier = item.get('id')
        id_sites[str(identifier)].append(filename)
        missing = [name for name in REQUIRED if name not in item]
        if missing:
            errors.append(f'{identifier} 缺少欄位：{missing}')
            continue
        if not isinstance(identifier, str) or not re.fullmatch(r'C\d{3,}', identifier):
            errors.append(f'無效評論 ID：{identifier!r}')
        else:
            num = int(identifier[1:])
            if type(item['number']) is not int or item['number'] != num:
                errors.append(f'{identifier} 的 number 與 ID 不符')
            numbers[num].append(identifier)
        if not isinstance(item['text'], str) or not item['text'].strip():
            errors.append(f'{identifier} 缺少非空原文')
        else:
            texts[item['text']].append(identifier)
        for key in ('subcategory', 'speaker', 'emotion', 'scene', 'trigger_limit'):
            if not isinstance(item[key], str) or not item[key].strip():
                errors.append(f'{identifier} 的 {key} 為空或格式錯誤')
        cat = item['category_id']
        if type(cat) is not int or cat not in CATEGORY_NAMES:
            errors.append(f'{identifier} 的 category_id 無效：{cat!r}')
        else:
            counts[cat] += 1
            # 保留第 06 類既有兩種標籤；只檢查主分類編號與名稱是否矛盾。
            names = (CATEGORY_NAMES[cat], '感情關係') if cat == 6 else (CATEGORY_NAMES[cat],)
            if not any(str(item['category']).strip() in (name, f'{cat:02d} {name}') for name in names):
                warnings.append(f'{identifier} 的分類文字與 category_id 不一致')
        if item.get('status', 'approved') != 'approved':
            errors.append(f'{identifier} 在已審核資料中並非 approved')
        if type(item['requires_authorization']) is not bool:
            errors.append(f'{identifier} requires_authorization 必須是 JSON 布林值')
        if item['requires_authorization'] is True:
            auth_count += 1
        if item.get('metadata_provenance') == 'reviewed_category_with_reconstructed_descriptors':
            reconstructed.append(identifier)
    for identifier, locations in id_sites.items():
        if len(locations) > 1:
            errors.append(f'ID 重複：{identifier}，出現在 {locations}')
    for text, ids in texts.items():
        if len(ids) > 1:
            warnings.append(f'完全相同原文：{ids}')

    id_set = set(id_sites)
    if 'C074' not in id_set:
        errors.append('缺少已恢復的 C074')
    if 'C300' in id_set:
        errors.append('已刪除的 C300 仍在資料中')
    expected = manifest.get('expected_count')
    if type(expected) is not int or len(entries) != expected:
        errors.append(f'數量與 manifest 不符：實際 {len(entries)}／宣告 {expected}')
    if manifest.get('next_id') == 'C421' and len(entries) == 419:
        expected_ids = {f'C{n:03d}' for n in range(1, 421)} - {'C300'}
        if id_set != expected_ids:
            errors.append(f'編號範圍不符；缺少 {sorted(expected_ids - id_set)}；多出 {sorted(id_set - expected_ids)}')
    if reconstructed:
        warnings.append(f'{len(reconstructed)} 則細項標記為重新整理，未證明逐條核對原審核版本')

    rows = []
    for cat in range(1, 13):
        actual = counts[cat]
        previous = REFERENCE[cat]
        rows.append({'category_id': cat, 'category': CATEGORY_NAMES[cat], 'actual': actual,
                     'previous_reference': previous, 'difference': actual - previous,
                     'target': int(manifest.get('target_per_category', 80)),
                     'remaining': max(0, int(manifest.get('target_per_category', 80)) - actual)})
    mismatches = [r for r in rows if r['difference'] != 0]
    if mismatches:
        warnings.append(f'{len(mismatches)} 類數量與先前人工統計不同，不應自動重分類')
    return {'status': 'FAIL' if errors else 'PASS_WITH_WARNINGS' if warnings else 'PASS',
            'checked_at_utc': datetime.now(timezone.utc).isoformat(),
            'folder': str(folder), 'part_files': len(parts), 'total': len(entries),
            'unique_ids': len(id_sites), 'authorized_templates': auth_count,
            'reconstructed_metadata_count': len(reconstructed),
            'category_statistics': rows, 'errors': errors, 'warnings': warnings}


def main() -> int:
    try:
        result = validate()
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f'評論庫檢查中止：{exc}', file=sys.stderr)
        return 2
    print(f"檢查結果：{result['status']} | {result['total']} 則 | {result['part_files']} 個分檔")
    for row in result['category_statistics']:
        print(f"{row['category_id']:02d} {row['category']:<12} {row['actual']:>3}/80 缺 {row['remaining']:>2} "+
              f"與原統計差 {row['difference']:+d}")
    for issue in result['errors']:
        print('錯誤：' + issue)
    for issue in result['warnings']:
        print('提醒：' + issue)
    REPORT_FOLDER.mkdir(parents=True, exist_ok=True)
    target = REPORT_FOLDER / 'comment_library_validation.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('報告：' + str(target))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
