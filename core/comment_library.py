
"""已審核網路評論庫：只負責安全的持久化、查詢及模板挑選。

不發佈 Discord 訊息、不修改玩家數值、不觸發傳聞或熱搜。
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path
from typing import Any

from core.database import get_connection


SEED_PATH = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "internet_comment_library"
    / "comments.json"
)


DDL = (
    """
    CREATE TABLE IF NOT EXISTS internet_comment_templates (
        id TEXT PRIMARY KEY,
        number INTEGER NOT NULL UNIQUE,
        batch INTEGER NOT NULL,
        raw_text TEXT NOT NULL,
        category_id INTEGER NOT NULL
            CHECK(category_id BETWEEN 1 AND 12),
        category TEXT NOT NULL,
        subcategory TEXT NOT NULL,
        speaker TEXT NOT NULL,
        emotion TEXT NOT NULL,
        scene TEXT NOT NULL,
        trigger_limit TEXT NOT NULL,
        source TEXT NOT NULL,
        canonical_id TEXT NOT NULL,
        requires_authorization INTEGER NOT NULL DEFAULT 0,
        approved INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_internet_comments_category
    ON internet_comment_templates(category_id, approved)
    """,
    """
    CREATE TABLE IF NOT EXISTS internet_comment_import_audit (
        import_id INTEGER PRIMARY KEY AUTOINCREMENT,
        seed_version TEXT NOT NULL,
        inserted_count INTEGER NOT NULL,
        existing_count INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
)


def seed_catalog(path: Path = SEED_PATH) -> dict[str, Any]:
    """讀取並驗證已審核的評論資料。"""
    data = json.loads(
        Path(path).read_text(encoding="utf-8")
    )

    approved = data["approved"]

    ids = [item["id"] for item in approved]

    if len(ids) != len(set(ids)) or "C300" in ids:
        raise ValueError(
            "種子資料 ID 重複或包含已移除的 C300"
        )

    if len(approved) != 419 or "C074" not in ids:
        raise ValueError(
            "審核清單不是預期的 419 則或缺少 C074"
        )

    return data


def initialize(conn: sqlite3.Connection) -> None:
    """建立評論庫資料表，不刪除既有資料。"""
    for ddl in DDL:
        conn.execute(ddl)


def import_approved(
    path: Path = SEED_PATH,
) -> dict[str, int]:
    """匯入已確認評論。

    可重複執行。
    已存在的評論 ID 不會被覆蓋。
    """
    catalog = seed_catalog(path)

    inserted = 0
    existing = 0

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        initialize(conn)

        for item in catalog["approved"]:
            payload = (
                item["id"],
                item["number"],
                item["batch"],
                item["text"],
                item["category_id"],
                item["category"],
                item["subcategory"],
                item["speaker"],
                item["emotion"],
                item["scene"],
                item["trigger_limit"],
                item["source"],
                item["canonical_id"],
                int(item["requires_authorization"]),
            )

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO internet_comment_templates (
                    id,
                    number,
                    batch,
                    raw_text,
                    category_id,
                    category,
                    subcategory,
                    speaker,
                    emotion,
                    scene,
                    trigger_limit,
                    source,
                    canonical_id,
                    requires_authorization
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

            inserted += cur.rowcount
            existing += 1 - cur.rowcount

        conn.execute(
            """
            INSERT INTO internet_comment_import_audit (
                seed_version,
                inserted_count,
                existing_count
            )
            VALUES (?, ?, ?)
            """,
            (
                catalog["catalog_version"],
                inserted,
                existing,
            ),
        )

    return {
        "inserted": inserted,
        "unchanged": existing,
        "total": inserted + existing,
    }


def list_templates(
    category_id: int | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    allow_authorized: bool = False,
) -> list[dict[str, Any]]:
    """查詢指定分類中已審核的評論。"""

    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError(
            "limit 必須為 1～500 且 offset 非負"
        )

    if category_id is not None:
        if not 1 <= category_id <= 12:
            raise ValueError("主分類須為 1～12")

    conditions = ["approved=1"]
    params: list[Any] = []

    if not allow_authorized:
        conditions.append("requires_authorization=0")

    if category_id is not None:
        conditions.append("category_id=?")
        params.append(category_id)

    where = " AND ".join(conditions)

    with get_connection() as conn:
        initialize(conn)

        rows = conn.execute(
            f"""
            SELECT *
            FROM internet_comment_templates
            WHERE {where}
            ORDER BY number
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()

    return [dict(row) for row in rows]


def stats() -> dict[int, int]:
    """統計 12 大分類中已確認的評論數量。"""

    with get_connection() as conn:
        initialize(conn)

        rows = conn.execute(
            """
            SELECT
                category_id,
                COUNT(*) AS n
            FROM internet_comment_templates
            WHERE approved=1
            GROUP BY category_id
            """
        ).fetchall()

    return {
        i: next(
            (
                row["n"]
                for row in rows
                if row["category_id"] == i
            ),
            0,
        )
        for i in range(1, 13)
    }


def choose_template(
    category_id: int,
    *,
    rng: random.Random | None = None,
    allow_authorized: bool = False,
    scene_verified: bool = False,
    allowed_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """從符合條件的已審核評論中隨機抽選。

    必須由外部事件引擎完成情境檢查。

    scene_verified:
        是否已確認事件情境。

    allowed_ids:
        已通過逐條觸發限制檢查的評論 ID。

    未驗證時不抽取評論。
    """

    if not scene_verified or not allowed_ids:
        return None

    rows = list_templates(
        category_id,
        limit=500,
        allow_authorized=allow_authorized,
    )

    rows = [
        row
        for row in rows
        if row["id"] in allowed_ids
    ]

    # C074 與 C038 存在相同的「美神降臨」變體。
    # 保留原始 ID，但抽選時避免相同輸出重複加權。
    by_text: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = (
            "美神降臨"
            if row["id"] in ("C038", "C074")
            else row["raw_text"]
        )

        if key not in by_text:
            by_text[key] = row

    if not by_text:
        return None

    return (rng or random).choice(
        list(by_text.values())
    )


def render_template(
    raw_text: str,
    *,
    primary_name: str | None = None,
    secondary_name: str | None = None,
    nickname: str | None = None,
    fan_name: str | None = None,
    animal: str | None = None,
    company_name: str | None = None,
) -> str:
    """替換評論中的角色與名稱變數。

    不觸發 Discord 真實 mention。
    """

    if "xxx" in raw_text:
        if (
            not secondary_name
            or secondary_name == primary_name
        ):
            raise ValueError(
                "xxx 必須提供且不同於 ooo"
            )

    mapping = {
        "ooo": primary_name,
        "xxx": secondary_name,
        "（暱稱）": nickname,
        "（粉絲名）": fan_name,
        "（動物）": animal,
        "@（公司）": company_name,
        "@（其他角色）": secondary_name,
    }

    result = raw_text

    for token, value in mapping.items():
        if token not in result:
            continue

        if value is None or not str(value).strip():
            raise ValueError(
                f"缺少模板變數 {token}"
            )

        clean = (
            str(value)
            .replace("@", "＠")
            .replace("<", "〈")
            .replace(">", "〉")
        )

        result = result.replace(token, clean)

    result = result.replace(
        "@everyone",
        "＠everyone",
    )

    result = result.replace(
        "@here",
        "＠here",
    )

    result = re.sub(
        r"<@!?\d+>",
        "[角色]",
        result,
    )

    return result
