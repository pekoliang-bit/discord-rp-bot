"""評論類別數量查核（只讀）；依當前正式分檔，不使用過時的419則統計。"""
from __future__ import annotations
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.comment_library import CATEGORIES, SEED_PATH, seed_catalog


def audit(folder: Path = SEED_PATH) -> dict:
    rows = seed_catalog(folder)['approved']
    counts = Counter(r['category_id'] for r in rows)
    category_summary = [{"id": i, "name": name, "count": counts[i], "target": 80,
                         "remaining": max(0, 80 - counts[i])} for i, name in CATEGORIES.items()]
    return {"status": "PASS" if len(rows) == 960 and all(counts[i] == 80 for i in CATEGORIES) else "REVIEW_NEEDED",
            "total": len(rows), "category_summary": category_summary}


def main() -> int:
    data = audit()
    dest = ROOT/'reports'
    dest.mkdir(parents=True, exist_ok=True)
    (dest/'comment_category_audit.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    with (dest/'comment_category_audit.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=['id', 'name', 'count', 'target', 'remaining'])
        writer.writeheader()
        writer.writerows(data['category_summary'])
    print(data['status'],data['total'],'則')
    return 0 if data['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
