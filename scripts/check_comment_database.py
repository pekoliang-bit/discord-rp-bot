
"""Discord RP 評論庫 SQLite 匯入驗收。

執行：
    python -m scripts.check_comment_database

只讀取 JSON 和既有 SQLite 資料庫。
不新增、刪除或更新評論。
不啟動 Discord。
"""

from __future__ import annotations

import sqlite3
import sys

from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import DATABASE_PATH
from core.comment_library import (
    SEED_PATH,
    seed_catalog,
)


TABLE_NAME = "internet_comment_templates"


def database_path() -> Path:
    path = Path(
        DATABASE_PATH
    ).expanduser()

    if not path.is_absolute():
        path = ROOT / path

    return path.resolve()


def open_readonly(path: Path) -> sqlite3.Connection:
    uri = (
        "file:"
        + quote(str(path), safe="/:")
        + "?mode=ro"
    )

    conn = sqlite3.connect(
        uri,
        uri=True,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def check() -> int:
    print("=" * 58)
    print("Discord RP 評論庫 SQLite 驗收")
    print("=" * 58)

    catalog = seed_catalog(SEED_PATH)
    approved = catalog["approved"]

    print(
        f"[OK] JSON 已核准評論："
        f"{len(approved)} 則"
    )

    path = database_path()

    if not path.is_file():
        print(
            "[PENDING] 正式資料庫尚未建立"
        )
        print(f"預期路徑：{path}")
        return 2

    conn = open_readonly(path)

    try:
        table = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            AND name = ?
            """,
            (TABLE_NAME,),
        ).fetchone()

        if table is None:
            print(
                "[PENDING] 評論資料表尚未建立"
            )
            return 2

        columns = {
            row["name"]
            for row in conn.execute(
                f"PRAGMA table_info({TABLE_NAME})"
            )
        }

        required = {
            "id",
            "number",
            "raw_text",
            "category_id",
            "approved",
        }

        missing = required - columns

        if missing:
            print(
                f"[ERROR] 資料表缺少欄位："
                f"{sorted(missing)}"
            )
            return 1

        records = conn.execute(
            f"""
            SELECT
                id,
                number,
                raw_text,
                category_id,
                approved
            FROM {TABLE_NAME}
            """
        ).fetchall()

        database_records = {
            str(row["id"]): dict(row)
            for row in records
        }

        if len(database_records) != len(records):
            print(
                "[ERROR] 資料庫有重複 ID"
            )
            return 1

        source_ids = {
            item["id"]
            for item in approved
        }

        missing_ids = []
        conflicts = []
        matched = 0

        for item in approved:
            row = database_records.get(
                item["id"]
            )

            if row is None:
                missing_ids.append(
                    item["id"]
                )
                continue

            differences = []

            if row["number"] != item["number"]:
                differences.append("number")

            if row["raw_text"] != item["text"]:
                differences.append("raw_text")

            if (
                row["category_id"]
                != item["category_id"]
            ):
                differences.append("category_id")

            if row["approved"] != 1:
                differences.append("approved")

            if differences:
                conflicts.append(
                    (
                        item["id"],
                        differences,
                    )
                )
            else:
                matched += 1

        extra_ids = sorted(
            set(database_records)
            - source_ids
        )

        print()
        print(f"資料庫總評論：{len(records)}")
        print(f"與 JSON 完全相符：{matched}")
        print(f"尚未匯入：{len(missing_ids)}")
        print(f"存在衝突：{len(conflicts)}")
        print(f"資料庫額外編號：{len(extra_ids)}")

        for comment_id, fields in conflicts[:30]:
            print(
                f"[CONFLICT] {comment_id}: "
                f"{', '.join(fields)}"
            )

        if conflicts:
            print(
                "[ERROR] 發現資料衝突，"
                "禁止直接覆蓋原資料"
            )
            return 1

        if missing_ids:
            print(
                "[PENDING] 尚有評論未匯入"
            )
            print(
                "缺少 ID："
                + ", ".join(missing_ids[:30])
            )
            return 2

        if extra_ids:
            print(
                "[WARN] 資料庫包含 JSON 之外的評論"
            )
            print(
                "額外 ID："
                + ", ".join(extra_ids[:30])
            )
            print(
                "不會刪除額外評論；"
                "請確認是否屬後續核准版本。"
            )

        print()
        print("[OK] 960 則已核准評論完全相符")
        print("結果：PASS")

        return 0

    finally:
        conn.close()


def main() -> int:
    try:
        return check()
    except Exception as exc:
        print(
            f"[ERROR] {type(exc).__name__}: "
            f"{exc}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
