"""跨伺服器角色公開編號（未正式開群前不核發）。

格式 P-01-0418-01：類型／全域伺服器序號／角色生日 MMDD／同組序號。
只在正式開群確認流程顯式呼叫 `confirm_official_launch` 後核發。
現有 characters.id 仍是唯一業務主鍵；public_code 只是對外識別。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timezone

from core.database import transaction

TYPE_CODES = {"player": "P", "player_npc": "N", "official_npc": "O"}
CODE_RE = re.compile(r"^([PNOR])-(\d{2,})-(\d{4})-(\d{2,})$")


def _schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS character_number_servers (
        guild_id TEXT PRIMARY KEY, server_serial INTEGER NOT NULL UNIQUE CHECK(server_serial >= 1),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS character_number_launches (
        guild_id TEXT PRIMARY KEY,
        launched_at TEXT NOT NULL,
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
    )""")
    # tombstone: codes permanently reserved, including deleted characters.
    db.execute("""CREATE TABLE IF NOT EXISTS character_number_registry (
        code TEXT PRIMARY KEY,
        guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id, character_id)
    )""")


def normalize_birthday(value: object) -> str | None:
    """2000 是閏年，接受 02/29。不可用假的生日佔位。"""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d{1,2})[/\-](\d{1,2})\s*", value)
    if not match:
        return None
    month, day = (int(x) for x in match.groups())
    try:
        date(2000, month, day)
    except ValueError:
        return None
    return f"{month:02d}{day:02d}"


def _birthday_for(row: sqlite3.Row) -> str | None:
    try:
        settings = json.loads(row["settings_json"] or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(settings, dict):
        return None
    return normalize_birthday(settings.get("birthday"))


def _server_serial(db: sqlite3.Connection, guild_id: str) -> int:
    row = db.execute(
        "SELECT server_serial FROM character_number_servers WHERE guild_id=?", (guild_id,)
    ).fetchone()
    if row:
        return int(row[0])
    # Caller must hold BEGIN IMMEDIATE. Avoid any possibility of concurrent duplicates.
    next_number = db.execute(
        "SELECT COALESCE(MAX(server_serial), 0) + 1 FROM character_number_servers"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO character_number_servers(guild_id, server_serial) VALUES (?, ?)",
        (guild_id, next_number),
    )
    return int(next_number)


def _role_code(row: sqlite3.Row) -> str | None:
    try:
        settings = json.loads(row["settings_json"] or "{}")
        if isinstance(settings, dict) and (
            settings.get("created_as_extra") or settings.get("character_category") == "extra"
        ):
            return "R"
    except (TypeError, ValueError):
        pass
    return TYPE_CODES.get(str(row["character_type"]))


def issue_in_transaction(db: sqlite3.Connection, guild_id: int | str, character_id: int) -> str | None:
    """需要外層 transaction()；未開群、缺生日、未核准則不核發。"""
    gid = str(guild_id)
    _schema(db)
    launched = db.execute(
        "SELECT 1 FROM character_number_launches WHERE guild_id=?", (gid,)
    ).fetchone()
    if not launched:
        return None
    row = db.execute(
        "SELECT id, public_code, character_type, settings_json, status FROM characters "
        "WHERE guild_id=? AND id=?", (gid, int(character_id))
    ).fetchone()
    if row is None:
        return None
    if row["public_code"]:
        return str(row["public_code"])  # Do not rewrite older codes.
    if row["status"] not in ("approved", "active"):
        return None
    role = _role_code(row)
    birthday = _birthday_for(row)
    if role is None or birthday is None:
        return None
    serial = _server_serial(db, gid)
    stem = f"{role}-{serial:02d}-{birthday}-"
    number = 1
    while True:
        code = f"{stem}{number:02d}"
        in_registry = db.execute(
            "SELECT 1 FROM character_number_registry WHERE code=?", (code,)
        ).fetchone()
        in_characters = db.execute(
            "SELECT 1 FROM characters WHERE public_code=? LIMIT 1", (code,)
        ).fetchone()
        if not in_registry and not in_characters:
            break
        number += 1
    db.execute(
        "INSERT INTO character_number_registry(code,guild_id,character_id) VALUES(?,?,?)",
        (code, gid, int(character_id)),
    )
    updated = db.execute(
        "UPDATE characters SET public_code=? WHERE guild_id=? AND id=? AND public_code IS NULL",
        (code, gid, int(character_id)),
    )
    if updated.rowcount != 1:
        raise RuntimeError("角色編號分配遇到競態，交易已取消")
    return code


def issue_if_launched(guild_id: int | str, character_id: int) -> str | None:
    with transaction() as db:
        return issue_in_transaction(db, guild_id, character_id)


def backfill_in_transaction(db: sqlite3.Connection, guild_id: int | str) -> dict:
    """有效角色補號；缺生日者列出 ID，供管理員追補後重試。"""
    gid = str(guild_id)
    _schema(db)
    rows = db.execute(
        "SELECT id FROM characters WHERE guild_id=? AND status IN ('approved','active') "
        "AND public_code IS NULL ORDER BY id", (gid,)
    ).fetchall()
    result = {"issued": [], "missing_birthday_or_type": []}
    for row in rows:
        cid = int(row["id"])
        code = issue_in_transaction(db, gid, cid)
        if code:
            result["issued"].append({"character_id": cid, "code": code})
        else:
            result["missing_birthday_or_type"].append(cid)
    return result


def confirm_official_launch(guild_id: int | str, *, launched_at: str | None = None) -> dict:
    """供未來正式開群「確認」按鈕呼叫；重按不會重置時間／編號。"""
    gid = str(guild_id)
    stamp = launched_at or datetime.now(timezone.utc).isoformat()
    with transaction() as db:
        _schema(db)
        db.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES(?)", (gid,))
        existing = db.execute(
            "SELECT launched_at FROM character_number_launches WHERE guild_id=?", (gid,)
        ).fetchone()
        if not existing:
            db.execute(
                "INSERT INTO character_number_launches(guild_id,launched_at) VALUES(?,?)",
                (gid, stamp),
            )
        result = backfill_in_transaction(db, gid)
        result["already_launched"] = bool(existing)
        result["launched_at"] = existing[0] if existing else stamp
        result["server_serial"] = _server_serial(db, gid)
        return result


def number_status(guild_id: int | str) -> dict:
    with transaction() as db:
        _schema(db)
        gid = str(guild_id)
        row = db.execute(
            "SELECT launched_at FROM character_number_launches WHERE guild_id=?", (gid,)
        ).fetchone()
        return {"launched": bool(row), "launched_at": row[0] if row else None}
