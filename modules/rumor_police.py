"""RP 傳聞報警與系統警方：玩家僅能報案、遞交證據及查詢。

此處的「警方」是遊戲內虛構流程，不會聯繫現實執法機關。
案件推進只能由伺服器排程呼叫 `settle_police_cases`；不得暴露為玩家指令。
"""

from __future__ import annotations

import hashlib
import time

from core.database import get_connection, transaction
from modules.pr_rumor import initialize as initialize_rumors

MIN_INVESTIGATION_SECONDS = 48 * 3600
MAX_INVESTIGATION_SECONDS = 7 * 24 * 3600
POLICE_STATUSES = ("investigating", "closed_supported", "closed_insufficient")


def initialize():
    initialize_rumors()
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_police_schedule (
            guild_id TEXT NOT NULL, police_case_id INTEGER NOT NULL,
            due_at INTEGER NOT NULL, verdict TEXT NOT NULL,
            PRIMARY KEY(guild_id,police_case_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_police_evidence (
            guild_id TEXT NOT NULL, police_case_id INTEGER NOT NULL,
            evidence_id INTEGER NOT NULL, submitted_by TEXT NOT NULL,
            submitted_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,police_case_id,evidence_id)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_rumor_police_due
            ON rp_rumor_police_schedule(due_at)""")
        db.commit()


def _owned_character(db, gid, uid, character_id):
    row = db.execute("""SELECT id FROM characters
        WHERE guild_id=? AND id=? AND owner_id=? AND status='active'""",
        (gid, character_id, uid)).fetchone()
    if row is None:
        raise PermissionError("只能用自己已啟用的角色報案或提交證據。")


def _evidence_owned_for_rumor(db, gid, rumor_id, character_id, evidence_id):
    row = db.execute("""SELECT id FROM rp_rumor_evidence
        WHERE guild_id=? AND rumor_id=? AND holder_character_id=? AND id=?""",
        (gid, rumor_id, character_id, evidence_id)).fetchone()
    if row is None:
        raise PermissionError("只能提交目前由自己角色持有、且屬於此傳聞的證據。")


def _deterministic_schedule(gid, rumor_id, case_id, created_at):
    """案件建立後固定時程，玩家無法靠重複提交資料重抽。"""
    digest = hashlib.sha256(f"{gid}:{rumor_id}:{case_id}:police-v1".encode()).digest()
    span = MAX_INVESTIGATION_SECONDS - MIN_INVESTIGATION_SECONDS
    due_at = created_at + MIN_INVESTIGATION_SECONDS + int.from_bytes(digest[:4], "big") % (span + 1)
    # 證據目前不直接決定有罪／無罪；結案標籤僅表示虛構案件是否獲得足夠線索。
    verdict = "closed_supported" if digest[4] % 2 == 0 else "closed_insufficient"
    return due_at, verdict


def report_rumor(guild_id, actor_id, rumor_id, reporter_character_id, evidence_id=None):
    """同一角色對同一傳聞只能建立一件警方案件，重複報案不重置時鐘。"""
    initialize()
    gid, uid = str(guild_id), str(actor_id)
    rid, cid = int(rumor_id), int(reporter_character_id)
    eid = int(evidence_id) if evidence_id is not None else None
    with transaction() as db:
        _owned_character(db, gid, uid, cid)
        rumor = db.execute("SELECT id FROM rp_company_rumors WHERE guild_id=? AND id=?", (gid, rid)).fetchone()
        if rumor is None:
            raise ValueError("找不到指定傳聞。")
        existing = db.execute("""SELECT id FROM rp_rumor_police_cases
            WHERE guild_id=? AND rumor_id=? AND reporter_character_id=?
            ORDER BY id LIMIT 1""", (gid, rid, cid)).fetchone()
        if existing:
            raise ValueError(f"角色已報過本案（警方案件 #{existing['id']}）。")
        if eid is not None:
            _evidence_owned_for_rumor(db, gid, rid, cid, eid)
        now = int(time.time())
        cur = db.execute("""INSERT INTO rp_rumor_police_cases
            (guild_id,rumor_id,reporter_character_id,status,created_at)
            VALUES (?,?,?,'investigating',?)""", (gid, rid, cid, now))
        case_id = cur.lastrowid
        due_at, verdict = _deterministic_schedule(gid, rid, case_id, now)
        db.execute("""INSERT INTO rp_rumor_police_schedule
            (guild_id,police_case_id,due_at,verdict) VALUES (?,?,?,?)""",
            (gid, case_id, due_at, verdict))
        if eid is not None:
            db.execute("""INSERT INTO rp_rumor_police_evidence
                (guild_id,police_case_id,evidence_id,submitted_by,submitted_at)
                VALUES (?,?,?,?,?)""", (gid, case_id, eid, uid, now))
    return {"police_case_id": case_id, "due_at": due_at, "status": "investigating"}


def submit_evidence(guild_id, actor_id, police_case_id, holder_character_id, evidence_id):
    """證據可以補交，但不改變警方結案時間、進度或預定結果。"""
    initialize()
    gid, uid = str(guild_id), str(actor_id)
    pid, cid, eid = int(police_case_id), int(holder_character_id), int(evidence_id)
    with transaction() as db:
        _owned_character(db, gid, uid, cid)
        case = db.execute("""SELECT rumor_id,status FROM rp_rumor_police_cases
            WHERE guild_id=? AND id=?""", (gid, pid)).fetchone()
        if not case:
            raise ValueError("找不到警方案件。")
        if case["status"] != "investigating":
            raise ValueError("警方案件已結案，不能再遞交證據。")
        _evidence_owned_for_rumor(db, gid, case["rumor_id"], cid, eid)
        cur = db.execute("""INSERT OR IGNORE INTO rp_rumor_police_evidence
            (guild_id,police_case_id,evidence_id,submitted_by,submitted_at)
            VALUES (?,?,?,?,?)""", (gid, pid, eid, uid, int(time.time())))
        return cur.rowcount == 1


def police_case_status(guild_id, actor_id, police_case_id, reporter_character_id):
    """僅允許報案角色持有人查詢私人警方案件。"""
    initialize()
    gid, uid = str(guild_id), str(actor_id)
    pid, cid = int(police_case_id), int(reporter_character_id)
    with get_connection() as db:
        _owned_character(db, gid, uid, cid)
        row = db.execute("""SELECT p.id,p.rumor_id,p.status,p.created_at,p.finished_at,
            s.due_at,COUNT(e.evidence_id) AS evidence_count
            FROM rp_rumor_police_cases AS p
            LEFT JOIN rp_rumor_police_schedule AS s
            ON s.guild_id=p.guild_id AND s.police_case_id=p.id
            LEFT JOIN rp_rumor_police_evidence AS e
            ON e.guild_id=p.guild_id AND e.police_case_id=p.id
            WHERE p.guild_id=? AND p.id=? AND p.reporter_character_id=?
            GROUP BY p.id""", (gid, pid, cid)).fetchone()
        if not row:
            raise ValueError("找不到此角色的警方案件。")
        # 不提供可由玩家操作的「完成百分比」。
        return dict(row)


def settle_police_cases(now=None):
    """只由機器人背景排程呼叫；與傳聞 72 小時自然結算互不影響。"""
    initialize()
    current = int(time.time()) if now is None else int(now)
    with transaction() as db:
        due = db.execute("""SELECT p.id,p.guild_id,s.verdict
            FROM rp_rumor_police_cases AS p JOIN rp_rumor_police_schedule AS s
            ON p.guild_id=s.guild_id AND p.id=s.police_case_id
            WHERE p.status='investigating' AND s.due_at<=?""", (current,)).fetchall()
        for row in due:
            if row["verdict"] not in POLICE_STATUSES[1:]:
                continue
            db.execute("""UPDATE rp_rumor_police_cases SET status=?,finished_at=?
                WHERE guild_id=? AND id=? AND status='investigating'""",
                (row["verdict"], current, row["guild_id"], row["id"]))
    return len(due)
