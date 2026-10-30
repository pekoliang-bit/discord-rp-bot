"""Discord RP 舉報審核服務（第 6 檔）。

安裝：modules/report_review_service.py
相依：report_repository、report_permission_service、report_service、core.database。
不註冊指令／View，不執行 Discord 處分，也不直接發送通知。

安全規則：
- 審核前、交易提交前重新驗證紀律/群主權限與案件可見性。
- 被舉報的審核員、紀律員及其他官方人員使用 owner_only，只能由群主處理。
- 舉報者本人不得承辦自己所提交的案件（內部秘密表比對，但不洩漏身分）。
- 事件及通知不得含有舉報人 ID；補件透過 report_service 的隔離佇列通知。
- 重大處分在此僅產生 proposal，等待第 8 檔批准與執行。
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4
from typing import Optional

import discord

from core.database import get_connection, transaction
from modules import report_permission_service as permissions
from modules import report_repository as repository
from modules import report_service as service


class ReportReviewError(ValueError):
    """案件狀態、內容或審核操作不合法。"""


@dataclass(frozen=True)
class ReviewOutcome:
    report_id: int
    status: str
    assigned_reviewer_id: Optional[str]
    owner_only: bool


# 不在一般通知中存儲舉報者帳號，且避免重複通知。
def _reporter_notice(conn, report_id: int, kind: str, suffix: str) -> None:
    service._queue_reporter_notice(
        conn, report_id, kind, f"review:{report_id}:{kind}:{suffix}"
    )


def _row(conn, guild_id: int, report_id: int) -> dict:
    return service._get_case(conn, str(guild_id), int(report_id))


def _is_reporter(conn, report_id: int, user_id: int) -> bool:
    """只傳回布林值，不外流報案者 ID。"""
    return conn.execute(
        "SELECT 1 FROM rp_reporter_secrets WHERE report_id=? AND reporter_user_id=?",
        (report_id, str(user_id)),
    ).fetchone() is not None


async def _guard(interaction: discord.Interaction, report_id: int) -> dict:
    """對公開操作的第一層權限檢查；仍需在交易內再次檢查。"""
    if interaction.guild is None:
        raise permissions.ReportPermissionError("必須在伺服器內審核舉報。")
    permissions.require_discipline_or_owner(interaction)
    # 這個接口保證非群主無法取得 owner_only 案件。
    return_check = await permissions.get_reviewable_report(interaction, report_id)
    return return_check


async def _authorized_case(interaction: discord.Interaction, report_id: int) -> dict:
    case = await _guard(interaction, report_id)
    # 避免「職務在提交案件後改變」所造成的安全性缺口。
    if not permissions.is_guild_owner(interaction):
        if await permissions.must_route_to_owner(
            interaction.guild, int(case["target_user_id"]),
            report_type=case["report_type"],
        ):
            raise permissions.ReportPermissionError("此案件必須由群主專屬處理。")
    return case


def _inside_transaction(conn, interaction: discord.Interaction, report_id: int,
                        expected_status: str) -> dict:
    """再次檢查資料、當事人迴避、負責人及狀態衝突。"""
    fresh = _row(conn, interaction.guild.id, report_id)
    owner = permissions.is_guild_owner(interaction)
    if fresh["owner_only"] and not owner:
        raise permissions.ReportPermissionError("此案件僅限群主處理。")
    if str(fresh["target_user_id"]) == str(interaction.user.id) and not owner:
        raise permissions.ReportPermissionError("不能處理自己被舉報的案件。")
    if _is_reporter(conn, report_id, interaction.user.id) and not owner:
        raise permissions.ReportPermissionError("不得審核自己提交的舉報。")
    if fresh["status"] != expected_status:
        raise ReportReviewError("案件已由其他審核者更新，請重新整理。")
    if fresh["status"] in {"closed", "withdrawn"}:
        raise ReportReviewError("案件已結束或撤回，不能再處理。")
    current = fresh.get("assigned_reviewer_id")
    if current and current != str(interaction.user.id) and not owner:
        raise permissions.ReportPermissionError("案件已由其他紀律人員承辦。")
    return fresh


def _event(conn, report_id: int, interaction: discord.Interaction,
           kind: str, *, old: Optional[str] = None,
           new: Optional[str] = None, details: Optional[dict] = None) -> None:
    # 避免任意 metadata 夾帶 Discord 使用者身分／證據文字。
    service._write_event(
        conn, report_id=report_id, kind=kind, old=old, new=new,
        actor_id=interaction.user.id, details=details or {},
    )


def _outcome(row: dict) -> ReviewOutcome:
    return ReviewOutcome(
        report_id=int(row["id"]), status=str(row["status"]),
        assigned_reviewer_id=row.get("assigned_reviewer_id"),
        owner_only=bool(row["owner_only"]),
    )


def _note(value: str, limit: int = 1500) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ReportReviewError("請填寫處理原因或補件問題。")
    if len(cleaned) > limit:
        raise ReportReviewError(f"內容不得超過 {limit} 字。")
    return cleaned


async def list_review_queue(interaction: discord.Interaction, *,
                            limit: int = 25) -> list[dict]:
    """紀律人員列表無 owner_only、無舉報人 ID；群主可見全部。"""
    rows = await permissions.list_reviewable_reports(interaction, limit=limit)
    if permissions.is_guild_owner(interaction):
        return rows
    safe_rows: list[dict] = []
    for row in rows:
        # 額外檢查提交之後升任的審核員／紀律員。
        if await permissions.must_route_to_owner(
            interaction.guild, int(row["target_user_id"]),
            report_type=row["report_type"],
        ):
            continue
        with get_connection() as conn:
            if _is_reporter(conn, int(row["id"]), interaction.user.id):
                continue
        safe_rows.append(row)
    return safe_rows


async def get_review_details(interaction: discord.Interaction, report_id: int) -> dict:
    """詳情與證據；從不 join/report 身分秘密資料表。"""
    case = await _authorized_case(interaction, report_id)
    with get_connection() as conn:
        if _is_reporter(conn, report_id, interaction.user.id) and not permissions.is_guild_owner(interaction):
            raise permissions.ReportPermissionError("不能以紀律人員身分調閱自己的舉報。")
        evidences = conn.execute(
            "SELECT id,evidence_url,note,created_at FROM rp_report_evidence "
            "WHERE report_id=? ORDER BY id", (report_id,),
        ).fetchall()
    return {"report": case, "evidence": [dict(item) for item in evidences]}


async def accept_report(interaction: discord.Interaction, report_id: int,
                        *, note: str = "") -> ReviewOutcome:
    """待受理 -> 調查中；只有一名紀律人員承辦。"""
    case = await _authorized_case(interaction, report_id)
    if case["status"] != "pending":
        raise ReportReviewError("只有待受理案件可以接案。")
    service.install_service_schema()
    with transaction() as conn:
        fresh = _inside_transaction(conn, interaction, report_id, "pending")
        now = repository.utc_now()
        conn.execute(
            "UPDATE rp_reports SET status='investigating',assigned_reviewer_id=?,updated_at=? "
            "WHERE id=?", (str(interaction.user.id), now, report_id),
        )
        _event(conn, report_id, interaction, "review_accepted", old="pending",
               new="investigating", details={"has_note": bool(note.strip())})
        _reporter_notice(conn, report_id, "report_accepted", now)
        return _outcome(_row(conn, interaction.guild.id, report_id))


async def request_more_information(interaction: discord.Interaction, report_id: int,
                                   *, question: str) -> ReviewOutcome:
    """向舉報者要求補件；問題存在事件，但無舉報人 ID。"""
    question = _note(question)
    case = await _authorized_case(interaction, report_id)
    if case["status"] not in {"pending", "investigating", "needs_info"}:
        raise ReportReviewError("目前狀態無法要求補件。")
    service.install_service_schema()
    with transaction() as conn:
        fresh = _inside_transaction(conn, interaction, report_id, case["status"])
        now = repository.utc_now()
        conn.execute(
            "UPDATE rp_reports SET status='needs_info',updated_at=? WHERE id=?",
            (now, report_id),
        )
        _event(conn, report_id, interaction, "review_information_requested",
               old=fresh["status"], new="needs_info",
               details={"question": question})
        _reporter_notice(conn, report_id, "report_needs_information", now)
        return _outcome(_row(conn, interaction.guild.id, report_id))


async def resume_investigation(interaction: discord.Interaction, report_id: int,
                               *, note: str = "") -> ReviewOutcome:
    """補件後恢復調查，或群主從 pending_owner 收回案件。"""
    case = await _authorized_case(interaction, report_id)
    if case["status"] not in {"needs_info", "pending_owner"}:
        raise ReportReviewError("案件目前無法恢復調查。")
    if case["status"] == "pending_owner" and not permissions.is_guild_owner(interaction):
        raise permissions.ReportPermissionError("移交群主的案件只有群主可以恢復。")
    service.install_service_schema()
    with transaction() as conn:
        _inside_transaction(conn, interaction, report_id, case["status"])
        now = repository.utc_now()
        conn.execute(
            "UPDATE rp_reports SET status='investigating',updated_at=? WHERE id=?",
            (now, report_id),
        )
        _event(conn, report_id, interaction, "review_resumed", old=case["status"],
               new="investigating", details={"has_note": bool(note.strip())})
        _reporter_notice(conn, report_id, "investigation_resumed", now)
        return _outcome(_row(conn, interaction.guild.id, report_id))


async def refer_to_owner(interaction: discord.Interaction, report_id: int,
                         *, reason: str) -> ReviewOutcome:
    """移交群主並永久鎖定 owner_only；不自動對被舉報者發通知。"""
    reason = _note(reason)
    case = await _authorized_case(interaction, report_id)
    if case["status"] not in {"pending", "investigating", "needs_info"}:
        raise ReportReviewError("此案件無法移交。")
    service.install_service_schema()
    with transaction() as conn:
        _inside_transaction(conn, interaction, report_id, case["status"])
        now = repository.utc_now()
        conn.execute(
            "UPDATE rp_reports SET status='pending_owner',owner_only=1,"
            "assigned_reviewer_id=NULL,updated_at=? WHERE id=?", (now, report_id),
        )
        _event(conn, report_id, interaction, "referred_to_owner",
               old=case["status"], new="pending_owner", details={"reason": reason})
        _reporter_notice(conn, report_id, "referred_to_owner", now)
        return _outcome(_row(conn, interaction.guild.id, report_id))


async def decide_report(interaction: discord.Interaction, report_id: int, *,
                        substantiated: bool, reason: str) -> ReviewOutcome:
    """作出調查結論，但不執行處分。"""
    reason = _note(reason)
    case = await _authorized_case(interaction, report_id)
    if case["status"] not in {"investigating", "pending_owner"}:
        raise ReportReviewError("僅調查中或群主待決案件可以作出結論。")
    if case["status"] == "pending_owner" and not permissions.is_guild_owner(interaction):
        raise permissions.ReportPermissionError("只有群主可裁決已移交案件。")
    new_status = "substantiated" if substantiated else "unsubstantiated"
    service.install_service_schema()
    with transaction() as conn:
        _inside_transaction(conn, interaction, report_id, case["status"])
        now = repository.utc_now()
        conn.execute(
            "UPDATE rp_reports SET status=?,updated_at=? WHERE id=?",
            (new_status, now, report_id),
        )
        _event(conn, report_id, interaction, "investigation_decided",
               old=case["status"], new=new_status, details={"reason": reason})
        _reporter_notice(conn, report_id, "investigation_decided", now)
        return _outcome(_row(conn, interaction.guild.id, report_id))


# 以下處分為「待批准／待執行提案」，由第 8 檔負責正式批准及執行。
MAJOR_SANCTIONS = frozenset({"long_timeout", "kick", "ban", "temporary_ban", "suspend"})
ALLOWED_SANCTIONS = MAJOR_SANCTIONS | {"warning", "short_timeout"}


async def propose_sanction(interaction: discord.Interaction, report_id: int, *,
                           sanction_type: str, reason: str,
                           duration_seconds: Optional[int] = None) -> int:
    """調查成立後建立處分提案；重大處分只能由群主核准後執行。"""
    reason = _note(reason)
    if sanction_type not in ALLOWED_SANCTIONS:
        raise ReportReviewError("不支援的處分種類。")
    if duration_seconds is not None and (duration_seconds <= 0 or duration_seconds > 28 * 86400):
        # Discord timeout 有最長 28 天限制；永久 ban 用 None。
        raise ReportReviewError("時間必須介於 1 秒至 28 天，永久處分不填時間。")
    if sanction_type in {"short_timeout", "long_timeout", "temporary_ban"} and duration_seconds is None:
        raise ReportReviewError("此處分需要指定時間長度。")
    case = await _authorized_case(interaction, report_id)
    if case["status"] != "substantiated":
        raise ReportReviewError("必須先確認舉報成立，才能提出處分。")
    service.install_service_schema()
    with transaction() as conn:
        fresh = _inside_transaction(conn, interaction, report_id, "substantiated")
        existing = conn.execute(
            "SELECT id FROM rp_report_sanctions WHERE report_id=? "
            "AND approval_status IN ('proposed','pending_owner','approved','executed') LIMIT 1",
            (report_id,),
        ).fetchone()
        if existing:
            raise ReportReviewError("此案件已有處分提案或已執行處分，請先處理原提案。")
        owner_required = sanction_type in MAJOR_SANCTIONS
        now = repository.utc_now()
        cursor = conn.execute(
            """INSERT INTO rp_report_sanctions
            (report_id,target_user_id,sanction_type,duration_seconds,reason,
             owner_approval_required,approval_status,proposed_by,execution_key,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (report_id, fresh["target_user_id"], sanction_type, duration_seconds,
             reason, int(owner_required), "pending_owner" if owner_required else "proposed",
             str(interaction.user.id), f"report:{report_id}:sanction:{uuid4().hex}", now),
        )
        sanction_id = int(cursor.lastrowid)
        _event(conn, report_id, interaction, "sanction_proposed",
               details={"sanction_id": sanction_id, "type": sanction_type,
                        "requires_owner_approval": owner_required})
        _reporter_notice(conn, report_id, "sanction_proposed", str(sanction_id))
        return sanction_id


async def close_unsubstantiated(interaction: discord.Interaction, report_id: int,
                                *, note: str = "") -> ReviewOutcome:
    """只有未成立／已撤回案件可以在此安全結案。"""
    case = await _authorized_case(interaction, report_id)
    if case["status"] not in {"unsubstantiated", "withdrawn"}:
        raise ReportReviewError("僅不成立或撤回案件可由此直接結案。")
    service.install_service_schema()
    with transaction() as conn:
        fresh = _row(conn, interaction.guild.id, report_id)
        if fresh["status"] != case["status"]:
            raise ReportReviewError("案件已更新，請重新整理。")
        if _is_reporter(conn, report_id, interaction.user.id) and not permissions.is_guild_owner(interaction):
            raise permissions.ReportPermissionError("不能審核自己提交的案件。")
        now = repository.utc_now()
        conn.execute(
            "UPDATE rp_reports SET status='closed',closed_at=?,updated_at=? WHERE id=?",
            (now, now, report_id),
        )
        _event(conn, report_id, interaction, "review_closed",
               old=case["status"], new="closed", details={"has_note": bool(note.strip())})
        _reporter_notice(conn, report_id, "report_closed", now)
        return _outcome(_row(conn, interaction.guild.id, report_id))


# 本服務不註冊任何指令，不會與 /申請、/審核 或其它 View 衝突。
