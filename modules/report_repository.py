"""舉報資料存取層（第 1 檔）。放在 modules/report_repository.py。

規則：
- 舉報審核員、紀律人員及其他官方人員，一律轉群主專屬案件。
- 舉報人 Discord ID 僅保存於隔離表；普通案件查詢不回傳身分。
- 使用現有 core.database.transaction()，不覆蓋既有資料表。
- 本檔不建立 Discord 指令、不直接處分玩家、不控制私人頻道權限。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable

from core.database import get_connection, transaction

URL_PATTERN = re.compile(r"^https://[^\s<>]{1,2000}$", re.I)
REPORT_TYPES = {"player", "character", "staff", "other"}
STATES = {
    "pending", "investigating", "needs_info", "pending_owner",
    "substantiated", "unsubstantiated", "withdrawn", "closed",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(value: int | str) -> str:
    result = str(value)
    if not result.isdecimal() or int(result) <= 0:
        raise ValueError("Discord ID 必須為正整數。")
    return result


def _json(value: dict | None = None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def install_schema() -> None:
    """安全新增資料表；以單一 transaction 執行，避免 executescript 隱含提交。"""
    ddl = [
        """CREATE TABLE IF NOT EXISTS rp_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            target_user_id TEXT NOT NULL,
            target_character_id INTEGER,
            report_type TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            description TEXT NOT NULL,
            occurrence_note TEXT NOT NULL DEFAULT '',
            owner_only INTEGER NOT NULL DEFAULT 0 CHECK(owner_only IN (0,1)),
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_reviewer_id TEXT,
            severity TEXT NOT NULL DEFAULT 'normal',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT,
            retention_until TEXT,
            CHECK(report_type IN ('player','character','staff','other')),
            CHECK(status IN ('pending','investigating','needs_info',
                'pending_owner','substantiated','unsubstantiated','withdrawn','closed')),
            UNIQUE(guild_id, id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_rp_reports_queue ON rp_reports(guild_id, owner_only, status, id)",
        """CREATE TABLE IF NOT EXISTS rp_reporter_secrets (
            report_id INTEGER PRIMARY KEY REFERENCES rp_reports(id) ON DELETE CASCADE,
            reporter_user_id TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_rp_reporter_lookup ON rp_reporter_secrets(reporter_user_id,report_id)",
        """CREATE TABLE IF NOT EXISTS rp_report_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES rp_reports(id) ON DELETE CASCADE,
            evidence_url TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(report_id,evidence_url)
        )""",
        """CREATE TABLE IF NOT EXISTS rp_report_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES rp_reports(id) ON DELETE CASCADE,
            actor_user_id TEXT,
            event_type TEXT NOT NULL,
            previous_status TEXT,
            new_status TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS rp_report_sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES rp_reports(id),
            target_user_id TEXT NOT NULL,
            sanction_type TEXT NOT NULL,
            duration_seconds INTEGER,
            reason TEXT NOT NULL,
            owner_approval_required INTEGER NOT NULL DEFAULT 0,
            approval_status TEXT NOT NULL DEFAULT 'proposed',
            proposed_by TEXT NOT NULL,
            approved_by TEXT,
            execution_key TEXT NOT NULL UNIQUE,
            executed_at TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS rp_report_appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sanction_id INTEGER NOT NULL UNIQUE REFERENCES rp_report_sanctions(id),
            appellant_user_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            reviewer_user_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            decision TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS rp_report_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES rp_reports(id) ON DELETE CASCADE,
            recipient_user_id TEXT NOT NULL,
            notification_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            dedupe_key TEXT NOT NULL UNIQUE,
            sent_at TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS rp_report_retention (
            guild_id TEXT PRIMARY KEY,
            normal_days INTEGER NOT NULL DEFAULT 180 CHECK(normal_days BETWEEN 1 AND 3650),
            major_days INTEGER NOT NULL DEFAULT 365 CHECK(major_days BETWEEN 1 AND 3650),
            updated_by TEXT,
            updated_at TEXT
        )""",
    ]
    with transaction() as conn:
        for statement in ddl:
            conn.execute(statement)


def _read_public_row(conn: sqlite3.Connection, guild_id: str, report_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM rp_reports WHERE guild_id=? AND id=?",
        (guild_id, report_id),
    ).fetchone()
    if row is None:
        raise LookupError("找不到舉報案件。")
    return dict(row)  # 絕不 join 舉報人身分表


def create_report(
    *, guild_id: int | str, reporter_user_id: int | str,
    target_user_id: int | str, report_type: str, reason_code: str,
    description: str, evidence_urls: Iterable[str],
    target_character_id: int | None = None, occurrence_note: str = "",
    owner_only: bool = False,
) -> int:
    """新建案件，初次證據 1～10 筆；官方人員身分須由上層驗證後傳入 owner_only。"""
    gid, reporter, target = map(_id, (guild_id, reporter_user_id, target_user_id))
    if report_type not in REPORT_TYPES:
        raise ValueError("無效的舉報類型。")
    if report_type == "staff":
        owner_only = True
    if report_type == "character" and not target_character_id:
        raise ValueError("舉報指定角色時必須選擇角色。")
    if target_character_id is not None and (not isinstance(target_character_id, int) or target_character_id <= 0):
        raise ValueError("角色編號不正確。")
    if not reason_code.strip() or not description.strip():
        raise ValueError("違規分類和事件經過不得為空。")
    urls = list(dict.fromkeys(u.strip() for u in evidence_urls if u and u.strip()))
    if not 1 <= len(urls) <= 10 or any(not URL_PATTERN.fullmatch(u) for u in urls):
        raise ValueError("首次必須提供 1～10 個有效 HTTPS 證據連結。")
    install_schema()
    now = utc_now()
    with transaction() as conn:
        # 若指定角色，必須確實屬於被舉報玩家，且不能跨伺服器。
        if target_character_id is not None:
            c = conn.execute(
                "SELECT 1 FROM characters WHERE guild_id=? AND id=? AND owner_id=?",
                (gid, target_character_id, target),
            ).fetchone()
            if not c:
                raise ValueError("指定角色不屬於被舉報玩家。")
        cursor = conn.execute(
            """INSERT INTO rp_reports
            (guild_id,target_user_id,target_character_id,report_type,reason_code,
             description,occurrence_note,owner_only,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (gid, target, target_character_id, report_type, reason_code.strip(),
             description.strip(), occurrence_note.strip(), int(owner_only),
             "pending", now, now),
        )
        rid = cursor.lastrowid
        conn.execute(
            "INSERT INTO rp_reporter_secrets(report_id,reporter_user_id) VALUES (?,?)",
            (rid, reporter),
        )
        conn.executemany(
            "INSERT INTO rp_report_evidence(report_id,evidence_url,created_at) VALUES (?,?,?)",
            [(rid, url, now) for url in urls],
        )
        conn.execute(
            """INSERT INTO rp_report_events
            (report_id,event_type,details_json,created_at) VALUES (?,?,?,?)""",
            (rid, "submitted", _json({"evidence_count": len(urls)}), now),
        )
    return int(rid)


def get_report_for_reviewer(*, guild_id: int | str, report_id: int,
                            is_owner: bool, is_discipline: bool) -> dict:
    """給審核介面用：回傳不含舉報人身分的資料。"""
    if not (is_owner or is_discipline):
        raise PermissionError("無舉報審核權限。")
    install_schema()
    with get_connection() as conn:
        row = _read_public_row(conn, _id(guild_id), report_id)
        if row["owner_only"] and not is_owner:
            raise PermissionError("此案件僅限群主查看。")
        return row


def get_reporter_for_owner(*, guild_id: int | str, report_id: int,
                           is_guild_owner: bool) -> str:
    """僅供驗證過 Discord guild.owner_id 的群主使用，不能以 admin 權限代替。"""
    if not is_guild_owner:
        raise PermissionError("只有伺服器群主能查看舉報人身分。")
    install_schema()
    with get_connection() as conn:
        _read_public_row(conn, _id(guild_id), report_id)
        row = conn.execute(
            "SELECT reporter_user_id FROM rp_reporter_secrets WHERE report_id=?",
            (report_id,),
        ).fetchone()
        if row is None:
            raise LookupError("舉報人身分紀錄不存在。")
        return str(row["reporter_user_id"])


def append_evidence(*, guild_id: int | str, report_id: int,
                    urls: Iterable[str], actor_user_id: int | str,
                    authorized: bool) -> int:
    """授權判定由服務層完成；此函式不允許沒有授權的追加。"""
    if not authorized:
        raise PermissionError("沒有補充證據的權限。")
    entries = list(dict.fromkeys(u.strip() for u in urls if u and u.strip()))
    if not 1 <= len(entries) <= 10 or any(not URL_PATTERN.fullmatch(u) for u in entries):
        raise ValueError("每次可追加 1～10 個有效 HTTPS 連結。")
    install_schema()
    now = utc_now()
    with transaction() as conn:
        _read_public_row(conn, _id(guild_id), report_id)
        count = 0
        for url in entries:
            count += conn.execute(
                "INSERT OR IGNORE INTO rp_report_evidence(report_id,evidence_url,created_at) VALUES (?,?,?)",
                (report_id, url, now),
            ).rowcount
        conn.execute(
            """INSERT INTO rp_report_events
            (report_id,actor_user_id,event_type,details_json,created_at)
            VALUES (?,?,?,?,?)""",
            (report_id, _id(actor_user_id), "evidence_added", _json({"count": count}), now),
        )
    return count


def list_pending_reports(*, guild_id: int | str, is_owner: bool,
                         is_discipline: bool, limit: int = 25) -> list[dict]:
    if not (is_owner or is_discipline):
        raise PermissionError("沒有查看權限。")
    install_schema()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id,guild_id,target_user_id,target_character_id,report_type,
                      reason_code,status,owner_only,created_at FROM rp_reports
               WHERE guild_id=? AND status IN ('pending','investigating','needs_info','pending_owner')
                 AND (?=1 OR owner_only=0)
               ORDER BY id ASC LIMIT ?""",
            (_id(guild_id), int(is_owner), max(1, min(int(limit), 100))),
        ).fetchall()
        return [dict(r) for r in rows]


# 模組只負責資料，不包含 setup()，不會建立額外指令。
