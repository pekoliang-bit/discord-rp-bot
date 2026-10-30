
"""Discord RP SQLite 安全備份工具。

執行：
    python -m scripts.backup_database

使用 SQLite Backup API 建立一致性備份。

不修改原始資料庫。
不刪除舊備份。
不啟動 Discord 機器人。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import DATABASE_PATH


BACKUP_DIR = ROOT / "backups"


def resolve_database() -> Path:
    path = Path(DATABASE_PATH).expanduser()

    if not path.is_absolute():
        path = ROOT / path

    path = path.resolve()

    if not path.is_file():
        raise FileNotFoundError(
            f"找不到既有資料庫：{path}"
        )

    return path


def database_hash(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def open_source(path: Path) -> sqlite3.Connection:
    # mode=ro 防止資料庫不存在時被建立。
    uri = (
        "file:"
        + quote(str(path), safe="/:")
        + "?mode=ro"
    )

    connection = sqlite3.connect(
        uri,
        uri=True,
        timeout=30,
    )

    connection.execute(
        "PRAGMA busy_timeout=30000"
    )

    return connection


def verify_backup(path: Path) -> None:
    conn = sqlite3.connect(
        str(path),
        timeout=30,
    )

    try:
        result = conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()

        if not result or result[0] != "ok":
            raise RuntimeError(
                f"備份完整性檢查失敗：{result}"
            )

    finally:
        conn.close()


def backup() -> Path:
    source_path = resolve_database()

    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S_%f")

    destination = (
        BACKUP_DIR
        / f"discord_rp_{timestamp}.sqlite3"
    )

    source = None
    target = None

    try:
        source = open_source(source_path)

        target = sqlite3.connect(
            str(destination),
            timeout=30,
        )

        source.backup(
            target,
            pages=100,
            sleep=0.1,
        )

        target.commit()

    except Exception:
        if destination.exists():
            destination.unlink()
        raise

    finally:
        if target is not None:
            target.close()

        if source is not None:
            source.close()

    try:
        verify_backup(destination)
    except Exception:
        destination.unlink(
            missing_ok=True
        )
        raise

    metadata = {
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_database": str(source_path),
        "backup_file": destination.name,
        "size_bytes": destination.stat().st_size,
        "sha256": database_hash(destination),
        "integrity_check": "ok",
    }

    metadata_path = destination.with_suffix(
        ".json"
    )

    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return destination


def main() -> int:
    print("=" * 55)
    print("Discord RP 正式資料庫備份")
    print("=" * 55)

    try:
        destination = backup()

        print("[OK] SQLite 備份成功")
        print(f"備份位置：{destination}")
        print("[OK] 完整性檢查通過")
        print("[OK] 已建立 SHA-256 校驗紀錄")

        return 0

    except Exception as exc:
        print(
            f"[ERROR] {type(exc).__name__}: {exc}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
