"""Discord RP 已審核評論庫：分檔載入、SQLite 儲存與安全抽選。"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path
from typing import Any

from core.database import get_connection

SEED_PATH = Path(__file__).resolve().parent.parent / "assets" / "internet_comment_library"
INITIAL_TARGET = 80
EXPECTED_COUNT = 960
CATEGORIES = {1:"日常生活",2:"影視作品",3:"演藝活動",4:"商業活動",5:"粉絲互動",6:"感情關係／CP",7:"爭議事件",8:"公關回應",9:"熱搜話題",10:"營銷號",11:"社會議題",12:"特殊事件"}

DDL = (
    """CREATE TABLE IF NOT EXISTS internet_comment_templates (
        id TEXT PRIMARY KEY, number INTEGER NOT NULL UNIQUE,
        batch INTEGER NOT NULL, raw_text TEXT NOT NULL,
        category_id INTEGER NOT NULL CHECK(category_id BETWEEN 1 AND 12),
        category TEXT NOT NULL, subcategory TEXT NOT NULL,
        speaker TEXT NOT NULL, emotion TEXT NOT NULL,
        scene TEXT NOT NULL, trigger_limit TEXT NOT NULL,
        source TEXT NOT NULL, canonical_id TEXT NOT NULL,
        requires_authorization INTEGER NOT NULL DEFAULT 0,
        approved INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE INDEX IF NOT EXISTS idx_internet_comments_category
       ON internet_comment_templates(category_id, approved)""",
    """CREATE TABLE IF NOT EXISTS internet_comment_import_audit (
        import_id INTEGER PRIMARY KEY AUTOINCREMENT,
        seed_version TEXT NOT NULL,
        inserted_count INTEGER NOT NULL,
        existing_count INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
)

FIELDS = ("id","number","batch","text","category_id","category",
          "subcategory","speaker","emotion","scene","trigger_limit",
          "source","canonical_id","requires_authorization")


def _source_files(path: Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"評論資料路徑不存在：{path}")
    manifest = path / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        parts = data.get("parts", [])
        filenames = [p if isinstance(p, str) else p.get("file", p.get("filename")) for p in parts]
        if not filenames or any(not isinstance(n, str) for n in filenames):
            raise ValueError("manifest.json 的 parts 格式不正確")
        if len(set(filenames)) != len(filenames):
            raise ValueError("manifest.json 存在重複分檔")
        if any(Path(n).name != n or not n.startswith("comments_part_") for n in filenames):
            raise ValueError("manifest.json 指向不允許的分檔路徑")
        files = [path / name for name in filenames]
        discovered = set(path.glob("comments_part_*.json"))
        if set(files) != discovered:
            raise ValueError("manifest.json 與資料夾內分檔不一致")
    else:
        files = sorted(path.glob("comments_part_*.json"))
    if not files or any(not p.is_file() for p in files):
        raise FileNotFoundError("找不到完整評論分檔")
    return files


def seed_catalog(path: Path = SEED_PATH) -> dict[str, Any]:
    """讀取分檔/單檔；先完整驗證，失敗時不修改資料庫。"""
    files = _source_files(Path(path))
    approved: list[dict[str, Any]] = []
    seen_parts: set[int] = set()
    for filename in files:
        obj = json.loads(filename.read_text(encoding="utf-8-sig"))
        if not isinstance(obj, dict) or not isinstance(obj.get("approved"), list):
            raise ValueError(f"資料結構不正確：{filename.name}")
        if "part" in obj:
            part = int(obj["part"])
            if part in seen_parts:
                raise ValueError(f"重複 part 編號：{part}")
            seen_parts.add(part)
        approved.extend(obj["approved"])
    ids = set()
    nums = set()
    for entry in approved:
        if not isinstance(entry, dict) or not all(key in entry for key in FIELDS):
            raise ValueError("評論缺少必要欄位")
        cid, num = entry["id"], entry["number"]
        if not isinstance(num, int) or cid != f"C{num:03d}":
            raise ValueError(f"編號格式不符：{cid}")
        if cid in ids or num in nums:
            raise ValueError(f"重複評論編號：{cid}")
        if entry["category_id"] not in CATEGORIES:
            raise ValueError(f"主分類無效：{cid}")
        if not isinstance(entry["text"], str) or not entry["text"].strip():
            raise ValueError(f"評論原文不可為空：{cid}")
        if entry.get("status", "approved") != "approved":
            raise ValueError(f"包含未核准評論：{cid}")
        ids.add(cid)
        nums.add(num)
    # 舊版419則仍可使用；新版必須完整960則且每類80則，拒絕半套啟用。
    manifest_path = Path(path) / "manifest.json" if Path(path).is_dir() else None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path and manifest_path.exists() else {}
    declared = int(manifest.get("expected_approved_count", 419))
    if declared not in (419, EXPECTED_COUNT) or len(approved) != declared:
        raise ValueError(f"種子數量不一致：manifest={declared}，實際={len(approved)}")
    if "C074" not in ids or "C300" in ids:
        raise ValueError("C074 必須存在，C300 必須排除")
    required_ids = {f"C{i:03d}" for i in range(1, (962 if declared == 960 else 421)) if i != 300}
    if ids != required_ids:
        raise ValueError("種子 ID 缺少或包含未經核准的編號")
    if declared == 960:
        from collections import Counter
        counts = Counter(row["category_id"] for row in approved)
        if any(counts.get(i, 0) != 80 for i in CATEGORIES):
            raise ValueError(f"12類評論數量須各80則：{dict(counts)}")
        if manifest.get("chat_text_reconciled") is not True:
            raise ValueError("新版960則尚未完成聊天核准原文核對；禁止匯入")
        approved_text_file = SEED_PATH.parent / "internet_comment_pending_review" / "approved_chat_texts.json"
        if not approved_text_file.is_file():
            raise ValueError("缺少 C421～C961 群主核准原文對照，拒絕匯入")
        approved_texts = json.loads(approved_text_file.read_text(encoding="utf-8-sig"))
        expected_new = {f"C{i:03d}" for i in range(421, 962)}
        if not isinstance(approved_texts, dict) or set(approved_texts) != expected_new:
            raise ValueError("核准原文對照缺少編號或包含額外編號")
        for entry in approved:
            if entry["number"] >= 421 and entry["text"] != approved_texts[entry["id"]]:
                raise ValueError(f"核准原文與正式分檔不一致：{entry['id']}")
    approved.sort(key=lambda v: v["number"])
    return {"catalog_version": manifest.get("catalog_version", "reviewed-419-split-v1"), "approved": approved}


def initialize(conn: sqlite3.Connection) -> None:
    for statement in DDL:
        conn.execute(statement)


def import_approved(path: Path = SEED_PATH) -> dict[str, int]:
    catalog = seed_catalog(path)
    conn = get_connection()
    inserted = existing = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        initialize(conn)
        for item in catalog["approved"]:
            payload = tuple(item[key] for key in FIELDS)
            payload = payload[:-1] + (int(payload[-1]),)
            previous = conn.execute(
                "SELECT raw_text, category_id FROM internet_comment_templates WHERE id=?",
                (item["id"],),
            ).fetchone()
            if previous is not None:
                if previous["raw_text"] != item["text"] or previous["category_id"] != item["category_id"]:
                    raise ValueError(f"資料庫中的 {item['id']} 與已核准種子資料不同；拒絕覆蓋")
                existing += 1
                continue
            conn.execute(
                """INSERT INTO internet_comment_templates (
                id, number, batch, raw_text, category_id, category, subcategory,
                speaker, emotion, scene, trigger_limit, source, canonical_id,
                requires_authorization) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload,
            )
            inserted += 1
        conn.execute(
            """INSERT INTO internet_comment_import_audit
            (seed_version, inserted_count, existing_count) VALUES (?,?,?)""",
            (catalog["catalog_version"], inserted, existing),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"inserted": inserted, "unchanged": existing, "total": inserted + existing}


def list_templates(category_id: int | None = None, *, limit: int = 50,
                   offset: int = 0, allow_authorized: bool = False) -> list[dict[str, Any]]:
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("limit 須為1～500，offset 不可為負")
    if category_id is not None and category_id not in CATEGORIES:
        raise ValueError("分類須為1～12")
    clauses = ["approved=1"]
    params: list[Any] = []
    if not allow_authorized:
        clauses.append("requires_authorization=0")
    if category_id is not None:
        clauses.append("category_id=?")
        params.append(category_id)
    conn = get_connection()
    try:
        initialize(conn)
        rows = conn.execute(
            "SELECT * FROM internet_comment_templates WHERE " + " AND ".join(clauses)
            + " ORDER BY number LIMIT ? OFFSET ?", (*params, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def stats() -> dict[int, int]:
    conn = get_connection()
    try:
        initialize(conn)
        rows = conn.execute("""SELECT category_id, COUNT(*) AS n
            FROM internet_comment_templates WHERE approved=1 GROUP BY category_id""").fetchall()
        return {i: next((r["n"] for r in rows if r["category_id"] == i), 0) for i in CATEGORIES}
    finally:
        conn.close()


def choose_template(category_id: int, *, rng: random.Random | None = None,
                    allow_authorized: bool = False, scene_verified: bool = False,
                    allowed_ids: set[str] | None = None) -> dict[str, Any] | None:
    if not scene_verified or not allowed_ids:
        return None
    rows = list_templates(category_id, limit=500, allow_authorized=allow_authorized)
    unique = {}
    for row in rows:
        if row["id"] not in allowed_ids:
            continue
        key = "美神降臨" if row["id"] in {"C038", "C074"} else row["raw_text"]
        unique.setdefault(key, row)
    return (rng or random).choice(list(unique.values())) if unique else None


def render_template(raw_text: str, *, primary_name: str | None = None,
                    secondary_name: str | None = None, nickname: str | None = None,
                    fan_name: str | None = None, animal: str | None = None,
                    company_name: str | None = None,
                    staff_name: str | None = None) -> str:
    if "xxx" in raw_text and (not secondary_name or secondary_name == primary_name):
        raise ValueError("xxx 必須是不同於 ooo 的另一個角色")
    values = {"ooo": primary_name, "xxx": secondary_name,
              "（暱稱）": nickname, "(稱呼）": nickname,
              "（粉絲名）": fan_name, "（動物）": animal,
              "（工作人員）": staff_name, "@（公司）": company_name,
              "@（其他角色）": secondary_name}
    result = raw_text
    for token in sorted(values, key=len, reverse=True):
        if token in result:
            value = values[token]
            if value is None or not str(value).strip():
                raise ValueError(f"缺少模板變數：{token}")
            safe = str(value).replace("@", "＠").replace("<", "〈").replace(">", "〉")
            result = result.replace(token, safe)
    result = result.replace("@everyone", "＠everyone").replace("@here", "＠here")
    result = re.sub(r"<@!?\d+>", "[角色]", result)
    return result
