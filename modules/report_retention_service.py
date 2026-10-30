"""Discord RP 舉報保存管理（第 10 檔）。

安裝於 modules/report_retention_service.py。
依賴：core.database、modules.report_repository、modules.report_permission_service。
本檔不註冊指令，也不啟動常駐排程；由受信任的排程器呼叫
run_retention_cleanup()。所有群主互動式設定都核對 guild.owner_id。

安全規則：
* 未結案、正在申訴、等待群主批准、處分尚有效或執行未確認的案件不清理。
* 已排隊的私密通知未送達時不清理，避免舉報人通知遺失。
* 人工保留（legal/investigation hold）及重大案件延長保存優先於到期日。
* 清理採同一 SQLite 交易；只留下不含舉報人 ID 或證據的稽核摘要。
* 系統不能替外部訊息、截圖、備份及 Discord 聊天紀錄自動刪除資料。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord

from core.database import get_connection, transaction
from modules import report_permission_service as permissions
from modules import report_repository as repo
from modules import report_service as report_service

DEFAULT_NORMAL_DAYS = 180
DEFAULT_MAJOR_DAYS = 365
MIN_DAYS = 1
MAX_DAYS = 3650
OPEN_STATUSES = frozenset({"pending", "investigating", "needs_info", "pending_owner", "substantiated"})
OPEN_APPEAL_STATUSES = frozenset({"pending", "reviewing", "reversing", "reversal_manual"})
BLOCKED_SANCTION_STATUSES = frozenset({
    "proposed", "pending_owner", "approved", "executing", "needs_reconciliation",
    "reversal_manual",
})


class RetentionError(ValueError):
    pass


@dataclass(frozen=True)
class RetentionPolicy:
    guild_id: str
    normal_days: int
    major_days: int
    updated_by: Optional[str]
    updated_at: Optional[str]


@dataclass(frozen=True)
class CleanupResult:
    guild_id: str
    examined: int
    deleted: int
    skipped: int
    dry_run: bool
    report_ids: tuple[int, ...]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _guild(guild_id: int | str) -> str:
    value = str(guild_id)
    if not value.isdecimal() or int(value) <= 0:
        raise RetentionError("伺服器 ID 無效。")
    return value


def _days(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_DAYS <= value <= MAX_DAYS:
        raise RetentionError(f"{name} 必須是 {MIN_DAYS}～{MAX_DAYS} 的整數。")
    return value


def install_retention_schema() -> None:
    """安全、冪等地擴充第 1 檔資料表，保留既有保存政策。"""
    report_service.install_service_schema()
    with transaction() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_report_retention_holds (
            report_id INTEGER PRIMARY KEY REFERENCES rp_reports(id) ON DELETE CASCADE,
            reason TEXT NOT NULL,
            set_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            released_at TEXT
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_report_retention_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            action TEXT NOT NULL,
            report_id INTEGER,
            actor_user_id TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_retention_audit_guild "
                     "ON rp_report_retention_audit(guild_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_reports_retention_due "
                     "ON rp_reports(guild_id, retention_until, status)")


def _audit(conn: sqlite3.Connection, guild_id: str, action: str, *,
           report_id: Optional[int] = None, actor_id: Optional[int] = None,
           details: Optional[dict] = None) -> None:
    """禁止傳入證據、說明、舉報人 ID 等敏感內容。"""
    safe = details or {}
    allowed = {"normal_days", "major_days", "until", "severity", "reason_code",
               "deleted_count", "hold", "status", "source"}
    if set(safe) - allowed:
        raise RetentionError("稽核資料包含不允許的欄位。")
    conn.execute(
        "INSERT INTO rp_report_retention_audit "
        "(guild_id,action,report_id,actor_user_id,detail_json,created_at) VALUES (?,?,?,?,?,?)",
        (guild_id, action, report_id, str(actor_id) if actor_id is not None else None,
         json.dumps(safe, ensure_ascii=False), _iso(_now())),
    )


def _get_policy(conn: sqlite3.Connection, gid: str) -> RetentionPolicy:
    row = conn.execute("SELECT * FROM rp_report_retention WHERE guild_id=?", (gid,)).fetchone()
    if row is None:
        return RetentionPolicy(gid, DEFAULT_NORMAL_DAYS, DEFAULT_MAJOR_DAYS, None, None)
    return RetentionPolicy(gid, int(row["normal_days"]), int(row["major_days"]),
                           row["updated_by"], row["updated_at"])


def get_policy(guild_id: int | str) -> RetentionPolicy:
    install_retention_schema()
    with get_connection() as conn:
        return _get_policy(conn, _guild(guild_id))


async def set_retention_policy(interaction: discord.Interaction, *,
                               normal_days: int, major_days: int) -> RetentionPolicy:
    """群主修改新政策；既有人工延長時間不縮短。"""
    permissions.require_guild_owner(interaction)
    n, m = _days(normal_days, "一般案件期限"), _days(major_days, "重大案件期限")
    if m < n:
        raise RetentionError("重大案件的保存期限不能短於一般案件。")
    install_retention_schema()
    gid, actor, now = _guild(interaction.guild.id), interaction.user.id, _iso(_now())
    with transaction() as conn:
        conn.execute("""INSERT INTO rp_report_retention
            (guild_id,normal_days,major_days,updated_by,updated_at) VALUES (?,?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET normal_days=excluded.normal_days,
            major_days=excluded.major_days,updated_by=excluded.updated_by,
            updated_at=excluded.updated_at""", (gid, n, m, str(actor), now))
        _audit(conn, gid, "policy_changed", actor_id=actor,
               details={"normal_days": n, "major_days": m})
        # 不立即強制刪除舊案件；過期日在清理時依新政策重新計算。
    return get_policy(gid)


def _case(conn: sqlite3.Connection, gid: str, report_id: int) -> dict:
    row = conn.execute("SELECT * FROM rp_reports WHERE guild_id=? AND id=?",
                       (gid, int(report_id))).fetchone()
    if row is None:
        raise LookupError("找不到此伺服器的案件。")
    return dict(row)


async def extend_case_retention(interaction: discord.Interaction, *,
                                report_id: int, extra_days: int,
                                reason: str) -> str:
    """群主延長個別案件保存：在既有到期日或今日較晚者上加天數。"""
    permissions.require_guild_owner(interaction)
    extra = _days(extra_days, "延長天數")
    if not reason.strip():
        raise RetentionError("必須填寫延長保存原因。")
    install_retention_schema()
    gid, actor = _guild(interaction.guild.id), interaction.user.id
    with transaction() as conn:
        case = _case(conn, gid, report_id)
        policy = _get_policy(conn, gid)
        created = _parse(case["created_at"]) or _now()
        base = created + timedelta(days=policy.major_days if _major(case) else policy.normal_days)
        current = _parse(case["retention_until"])
        until = max(_now(), base, current or base) + timedelta(days=extra)
        conn.execute("UPDATE rp_reports SET retention_until=?,updated_at=? WHERE id=? AND guild_id=?",
                     (_iso(until), _iso(_now()), report_id, gid))
        _audit(conn, gid, "retention_extended", report_id=report_id, actor_id=actor,
               details={"until": _iso(until)})
    return _iso(until)


async def set_case_hold(interaction: discord.Interaction, *, report_id: int,
                        enabled: bool, reason: str) -> None:
    """群主啟用/解除人工保留；未解除時任何到期清理都跳過。"""
    permissions.require_guild_owner(interaction)
    if enabled and not reason.strip():
        raise RetentionError("必須填寫保留原因。")
    install_retention_schema()
    gid, actor, now = _guild(interaction.guild.id), interaction.user.id, _iso(_now())
    with transaction() as conn:
        _case(conn, gid, report_id)
        if enabled:
            conn.execute("""INSERT INTO rp_report_retention_holds
                (report_id,reason,set_by,created_at,released_at) VALUES (?,?,?,?,NULL)
                ON CONFLICT(report_id) DO UPDATE SET reason=excluded.reason,
                set_by=excluded.set_by,created_at=excluded.created_at,released_at=NULL""",
                (report_id, reason.strip()[:1000], str(actor), now))
        else:
            conn.execute("UPDATE rp_report_retention_holds SET released_at=? WHERE report_id=?",
                         (now, report_id))
        _audit(conn, gid, "hold_enabled" if enabled else "hold_released",
               report_id=report_id, actor_id=actor, details={"hold": bool(enabled)})


def _major(case: dict) -> bool:
    return str(case.get("severity") or "normal").lower() in {"major", "severe", "critical", "high"}


def _eligible(conn: sqlite3.Connection, case: dict, policy: RetentionPolicy,
              now: datetime) -> bool:
    """任何不確定狀況都不刪除（fail closed）。"""
    rid = int(case["id"])
    if case["status"] != "closed" or case.get("closed_at") is None:
        return False
    created = _parse(case["created_at"])
    closed = _parse(case["closed_at"])
    if created is None or closed is None:
        return False
    due = max(created, closed) + timedelta(days=policy.major_days if _major(case) else policy.normal_days)
    explicit_due = _parse(case.get("retention_until"))
    if case.get("retention_until") and explicit_due is None:
        return False
    if explicit_due:
        due = max(due, explicit_due)
    if now < due:
        return False
    if conn.execute("SELECT 1 FROM rp_report_retention_holds WHERE report_id=? AND released_at IS NULL",
                    (rid,)).fetchone():
        return False
    # 待發通知若刪除，將失去向當事人交代結果的機會。
    if conn.execute("SELECT 1 FROM rp_report_notifications WHERE report_id=? AND status NOT IN ('sent','cancelled') LIMIT 1",
                    (rid,)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM rp_report_private_notifications WHERE report_id=? AND status NOT IN ('sent','cancelled') LIMIT 1",
                    (rid,)).fetchone():
        return False
    if conn.execute("""SELECT 1 FROM rp_report_appeals a
        JOIN rp_report_sanctions s ON a.sanction_id=s.id
        WHERE s.report_id=? AND a.status NOT IN ('upheld','revoked','reduced') LIMIT 1""",
        (rid,)).fetchone():
        return False
    # 確認處分已終止。對任何未知狀態採保守不清理。
    sanctions = conn.execute("SELECT * FROM rp_report_sanctions WHERE report_id=?", (rid,)).fetchall()
    for sanction in sanctions:
        state = str(sanction["approval_status"] or "")
        if state not in {"rejected", "revoked", "reverted", "expired", "cancelled", "executed"}:
            return False
        if state == "executed":
            kind = str(sanction["sanction_type"] or "")
            if kind in {"ban", "permanent_ban", "temporary_ban", "short_timeout", "long_timeout", "timeout"}:
                # 避免刪掉用來恢復持續限制的唯一紀錄。
                return False
            expiry = _parse(sanction["expires_at"]) if "expires_at" in sanction.keys() else None
            if expiry is not None and expiry > now:
                return False
    return True


def run_retention_cleanup(*, guild_id: int | str, limit: int = 50,
                          dry_run: bool = True) -> CleanupResult:
    """只供機器人可信任的內部排程呼叫；預設僅預覽不刪除。

    呼叫前應先透過 Discord guild.id 確認伺服器有效，並在部署設定
    明確授權啟用刪除模式。不要把此函式直接接到玩家可調用的互動按鈕。
    """
    gid = _guild(guild_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise RetentionError("每批檢查數必須介於 1～500。")
    install_retention_schema()
    now = _now()
    deleted: list[int] = []
    examined = 0
    with transaction() as conn:
        policy = _get_policy(conn, gid)
        candidates = conn.execute(
            "SELECT * FROM rp_reports WHERE guild_id=? AND status='closed' "
            "ORDER BY closed_at DESC LIMIT ?", (gid, limit)
        ).fetchall()
        for row in candidates:
            examined += 1
            case = dict(row)
            if not _eligible(conn, case, policy, now):
                continue
            rid = int(case["id"])
            deleted.append(rid)
            if dry_run:
                continue
            # 不在稽核摘要存舉報人身分、原始文字、證據 URL。
            _audit(conn, gid, "case_purged", report_id=rid,
                   details={"severity": "major" if _major(case) else "normal", "status": "closed"})
            # 申訴/處分沒有 ON DELETE CASCADE；先明確刪除依賴資料。
            conn.execute("DELETE FROM rp_report_appeals WHERE sanction_id IN "
                         "(SELECT id FROM rp_report_sanctions WHERE report_id=?)", (rid,))
            conn.execute("DELETE FROM rp_report_sanctions WHERE report_id=?", (rid,))
            conn.execute("DELETE FROM rp_reports WHERE id=? AND guild_id=?", (rid, gid))
            # rp_reporter_secrets / evidence / events / notifications / holds 使用 CASCADE。
    return CleanupResult(gid, examined, len(deleted), examined-len(deleted),
                         bool(dry_run), tuple(deleted))


async def cleanup_as_owner(interaction: discord.Interaction, *, limit: int = 50,
                           dry_run: bool = True) -> CleanupResult:
    """群主手動執行或預覽清理；正式刪除須在 UI 再次確認。"""
    permissions.require_guild_owner(interaction)
    return run_retention_cleanup(guild_id=interaction.guild.id, limit=limit, dry_run=dry_run)


def list_retention_audit(guild_id: int | str, *, limit: int = 50) -> list[dict]:
    """僅供已通過群主權限驗證的內部呼叫；不要直接公開成指令。"""
    install_retention_schema()
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM rp_report_retention_audit WHERE guild_id=? "
                            "ORDER BY id DESC LIMIT ?", (_guild(guild_id), max(1,min(int(limit),200)))).fetchall()
    return [dict(row) for row in rows]


async def get_retention_audit_as_owner(interaction: discord.Interaction, *,
                                       limit: int = 50) -> list[dict]:
    permissions.require_guild_owner(interaction)
    return list_retention_audit(interaction.guild.id, limit=limit)
