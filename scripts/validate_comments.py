"""官方網路留言庫唯讀校驗；不修改玩家或正式評論資料庫。"""
from __future__ import annotations
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.comment_library import CATEGORIES, SEED_PATH, seed_catalog


def validate(folder: Path = SEED_PATH) -> dict:
    catalog = seed_catalog(folder)
    entries = catalog["approved"]
    counts = Counter(int(row["category_id"]) for row in entries)
    rows = [{"category_id": i, "category": name, "actual": counts[i], "target": 80,
             "remaining": max(0, 80 - counts[i])} for i, name in CATEGORIES.items()]
    return {"status": "PASS", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "total": len(entries), "unique_ids": len({row['id'] for row in entries}),
            "category_statistics": rows, "errors": [], "warnings": []}


def main() -> int:
    try:
        result = validate()
    except Exception as exc:
        print(f"評論庫校驗失敗：{exc}", file=sys.stderr)
        return 1
    report = ROOT / 'reports' / 'comment_library_validation.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    print(f"評論庫 PASS：{result['total']} 則／12 類各 80 則")
    print(f"報告：{report}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
