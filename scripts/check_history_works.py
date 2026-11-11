"""python scripts/check_history_works.py assets/history_v24/approved_works.json"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.history_v24_work_catalog import validate_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="V2.4 作品及綜藝素材唯讀檢查")
    parser.add_argument("path", nargs="?", default="assets/history_v24/approved_works.json")
    args = parser.parse_args()
    try:
        result = validate_file(ROOT / args.path if not Path(args.path).is_absolute() else args.path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
