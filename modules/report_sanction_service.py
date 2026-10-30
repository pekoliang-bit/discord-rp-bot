"""Discord RP 舉報處分服務（第 8 檔）。

放置於 modules/report_sanction_service.py；不註冊 Slash Command。
相依：core.database、前六個舉報服務模組、discord.py 2.x。

安全原則：
* 只有調查成立的案件可以處分；重大處分必須由 Discord 伺服器群主批准。
* 先在資料庫認領執行權，再呼叫 Discord API；未知結果不盲目重試。
* 處分不會讀取或洩漏舉報人的身分；每次操作都留存紀錄。
* temporary_ban 必須由後續排程呼叫 process_expired_temporary_bans() 才會解封。
* 不會從聊天按鈕直接執行；由已驗證的審核 UI 觸發服務函式。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord

from core.database import get_connection, transaction
from modules import report_permission_service as permissions
from modules import report_repository as repository
from modules import report_review_service as reviews
from modules import report_service as service


class SanctionError(ValueError):
    """資料狀態不允許處分操作。"""


@dataclass(frozen=True)
class SanctionResult:
    sanction_id: int
    report_id: int
    status: str
    action: str
    details: str = ""


ALLOWED = reviews.ALLOWED_SANCTIONS
MAJOR = reviews.MAJOR_SANCTIONS
TIMEOUT_TYPES = frozenset({"short_timeout", "long_timeout"})


def install_sanction_schema() -> None:
    """對第 1 檔既有處分表做不破壞資料的擴充。"""
    service.install_service_schema()
    with transaction() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(rp_report_sanctions)")}
        additions = {
            "execution_started_at": "TEXT",
            "execution_error": "TEXT",
            "execution_note": "TEXT",
            "expires_at": "TEXT",
            "reverted_at": "TEXT",
            "reversion_error": "TEXT",
            "updated_at": "TEXT",
        }
        for column, decl in additions.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE rp_report_sanctions ADD COLUMN {column} {decl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_sanction_status "
                     "ON rp_report_sanctions(approval_status,expires_at)")


def _load(conn: sqlite3.Connection, guild_id: int, sanction_id: int) -> tuple[dict, dict]:
    row = conn.execute(
        "SELECT s.* FROM rp_report_sanctions s JOIN rp_reports r ON r.id=s.report_id "
        "WHERE s.id=? AND r.guild_id=?", (int(sanction_id), str(guild_id))
    ).fetchone()
    if row is None:
        raise SanctionError("找不到此伺服器的處分提案。")
    sanction = dict(row)
    report = service._get_case(conn, str(guild_id), sanction["report_id"])
    return sanction, report


async def _authorize(interaction: discord.Interaction, report_id: int) -> dict:
    if interaction.guild is None:
        raise permissions.ReportPermissionError("請在 Discord 伺服器內操作。")
    case = await reviews._authorized_case(interaction, report_id)
    with get_connection() as conn:
        if reviews._is_reporter(conn, report_id, interaction.user.id) and not permissions.is_guild_owner(interaction):
            raise permissions.ReportPermissionError("不能處理自己提交的舉報案件。")
    return case


def _event(conn, report_id: int, user_id: Optional[int], kind: str, details: dict) -> None:
    service._write_event(conn, report_id=report_id, kind=kind, actor_id=user_id, details=details)


def _reporter_notice(conn, report_id: int, kind: str, sanction_id: int) -> None:
    service._queue_reporter_notice(conn, report_id, kind, f"sanction:{sanction_id}:{kind}")


async def get_sanction(interaction: discord.Interaction, sanction_id: int) -> dict:
    """只回傳處分內容，不 join 舉報人身分。"""
    install_sanction_schema()
    with get_connection() as conn:
        sanction, _ = _load(conn, interaction.guild.id if interaction.guild else 0, sanction_id)
    await _authorize(interaction, int(sanction["report_id"]))
    return sanction


async def approve_major_sanction(interaction: discord.Interaction, sanction_id: int, *, note: str = "") -> SanctionResult:
    """群主二次批准重大處分；批准不等於執行。"""
    permissions.require_guild_owner(interaction)
    install_sanction_schema()
    with get_connection() as conn:
        sanction, _ = _load(conn, interaction.guild.id, sanction_id)
    await _authorize(interaction, sanction["report_id"])
    with transaction() as conn:
        s, report = _load(conn, interaction.guild.id, sanction_id)
        if report["status"] != "substantiated":
            raise SanctionError("案件尚未判定成立。")
        if not s["owner_approval_required"] or s["sanction_type"] not in MAJOR:
            raise SanctionError("此處分不是重大處分提案。")
        if s["approval_status"] != "pending_owner":
            raise SanctionError("處分不在待群主批准狀態。")
        now = repository.utc_now()
        conn.execute("UPDATE rp_report_sanctions SET approval_status='approved',approved_by=?,"
                     "updated_at=?,execution_note=? WHERE id=?",
                     (str(interaction.user.id), now, note[:1000], sanction_id))
        _event(conn, s["report_id"], interaction.user.id, "major_sanction_approved", {"sanction_id": sanction_id})
        _reporter_notice(conn, s["report_id"], "sanction_approved", sanction_id)
    return SanctionResult(sanction_id, s["report_id"], "approved", s["sanction_type"])


async def reject_major_sanction(interaction: discord.Interaction, sanction_id: int, *, reason: str) -> SanctionResult:
    permissions.require_guild_owner(interaction)
    if not reason.strip():
        raise SanctionError("請填寫拒絕原因。")
    install_sanction_schema()
    with get_connection() as conn:
        sanction, _ = _load(conn, interaction.guild.id, sanction_id)
    await _authorize(interaction, sanction["report_id"])
    with transaction() as conn:
        s, _ = _load(conn, interaction.guild.id, sanction_id)
        if not s["owner_approval_required"] or s["approval_status"] != "pending_owner":
            raise SanctionError("只能拒絕尚未批准的重大處分。")
        conn.execute("UPDATE rp_report_sanctions SET approval_status='rejected',approved_by=?,"
                     "execution_note=?,updated_at=? WHERE id=?",
                     (str(interaction.user.id), reason.strip()[:1000], repository.utc_now(), sanction_id))
        _event(conn, s["report_id"], interaction.user.id, "major_sanction_rejected", {"sanction_id": sanction_id})
        _reporter_notice(conn, s["report_id"], "sanction_rejected", sanction_id)
    return SanctionResult(sanction_id, s["report_id"], "rejected", s["sanction_type"])


def _validate_bot_permissions(guild: discord.Guild, kind: str, target: discord.Member | None) -> None:
    bot = guild.me
    if bot is None:
        raise SanctionError("無法取得機器人在伺服器中的身分。")
    perms = bot.guild_permissions
    if kind in TIMEOUT_TYPES and not perms.moderate_members:
        raise SanctionError("機器人缺少 Moderate Members 權限。")
    if kind == "kick" and not perms.kick_members:
        raise SanctionError("機器人缺少 Kick Members 權限。")
    if kind in {"ban", "temporary_ban"} and not perms.ban_members:
        raise SanctionError("機器人缺少 Ban Members 權限。")
    if target is not None:
        if target.id == guild.owner_id:
            raise SanctionError("不能對 Discord 伺服器擁有者執行成員處分。")
        if kind != "warning" and target.top_role >= bot.top_role:
            raise SanctionError("機器人的身分組層級不足以處理此成員。")


async def _apply_discord(guild: discord.Guild, sanction: dict) -> tuple[str, Optional[str]]:
    """回傳結果與到期時間；不進行 DB 操作。"""
    kind = sanction["sanction_type"]
    uid = int(sanction["target_user_id"])
    reason = f"RP report #{sanction['report_id']}: {sanction['reason'][:350]}"
    member = guild.get_member(uid)
    if kind in TIMEOUT_TYPES or kind == "kick":
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except discord.NotFound as exc:
                raise SanctionError("目標已不在伺服器，不能執行禁言或踢出。") from exc
    _validate_bot_permissions(guild, kind, member)
    if kind == "warning":
        return "recorded", None  # 警告只建立正式處分紀錄；通知由後續通知模組發送
    if kind in TIMEOUT_TYPES:
        seconds = sanction["duration_seconds"]
        if not seconds or not 1 <= int(seconds) <= 28 * 86400:
            raise SanctionError("Discord 禁言時長應介於 1 秒至 28 天。")
        expiry = datetime.now(timezone.utc) + timedelta(seconds=int(seconds))
        await member.timeout(expiry, reason=reason)
        return "applied", expiry.isoformat(timespec="seconds")
    if kind == "kick":
        await member.kick(reason=reason)
        return "applied", None
    if kind in {"ban", "temporary_ban"}:
        seconds = sanction["duration_seconds"]
        if kind == "temporary_ban" and (not seconds or int(seconds) <= 0):
            raise SanctionError("暫時封禁需要指定有效期限。")
        expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(seconds))).isoformat(timespec="seconds") if kind == "temporary_ban" else None
        await guild.ban(discord.Object(id=uid), reason=reason, delete_message_seconds=0)
        return "applied", expiry
    # suspend 是專案自訂的停權類型；需要特定身分組/帳戶控制，不能假裝已執行。
    raise SanctionError("suspend 需先連結正式停權系統，不可直接執行。")


async def execute_sanction(interaction: discord.Interaction, sanction_id: int) -> SanctionResult:
    """執行一次。未知結果會鎖為 needs_reconciliation，不會盲目再執行。"""
    install_sanction_schema()
    if interaction.guild is None:
        raise permissions.ReportPermissionError("必須在伺服器內執行。")
    with get_connection() as conn:
        sanction, _ = _load(conn, interaction.guild.id, sanction_id)
    await _authorize(interaction, sanction["report_id"])
    if sanction["owner_approval_required"]:
        permissions.require_guild_owner(interaction)  # 重大處分只允許群主執行
    if sanction["sanction_type"] == "suspend":
        raise SanctionError("停權功能尚未綁定實際角色／帳戶停權服務。")
    with transaction() as conn:
        s, report = _load(conn, interaction.guild.id, sanction_id)
        if report["status"] != "substantiated":
            raise SanctionError("只有已成立的舉報可以執行處分。")
        expected = "approved" if s["owner_approval_required"] else "proposed"
        if s["approval_status"] == "executed":
            return SanctionResult(sanction_id, s["report_id"], "executed", s["sanction_type"], "已執行，不重複處分。")
        if s["approval_status"] != expected:
            raise SanctionError("處分尚未取得核准、已被其他人認領或需要人工核對。")
        now = repository.utc_now()
        updated = conn.execute(
            "UPDATE rp_report_sanctions SET approval_status='executing',execution_started_at=?,updated_at=? "
            "WHERE id=? AND approval_status=?", (now, now, sanction_id, expected)
        ).rowcount
        if updated != 1:
            raise SanctionError("處分已被其他程序認領。")
        _event(conn, s["report_id"], interaction.user.id, "sanction_execution_claimed", {"sanction_id": sanction_id})
    try:
        outcome, expiry = await _apply_discord(interaction.guild, s)
    except Exception as exc:
        # Discord API 的結果可能在逾時或連線中斷後不明；切勿自動重試。
        error_text = f"{type(exc).__name__}: {str(exc)[:500]}"
        with transaction() as conn:
            conn.execute("UPDATE rp_report_sanctions SET approval_status='needs_reconciliation',"
                         "execution_error=?,updated_at=? WHERE id=? AND approval_status='executing'",
                         (error_text, repository.utc_now(), sanction_id))
            _event(conn, s["report_id"], interaction.user.id, "sanction_execution_uncertain", {"sanction_id": sanction_id})
        raise SanctionError("處分未能確認完成，已鎖定等待人工核對，請勿再次點擊執行。") from exc
    with transaction() as conn:
        conn.execute("UPDATE rp_report_sanctions SET approval_status='executed',executed_at=?,"
                     "expires_at=?,execution_error=NULL,updated_at=? WHERE id=? AND approval_status='executing'",
                     (repository.utc_now(), expiry, repository.utc_now(), sanction_id))
        _event(conn, s["report_id"], interaction.user.id, "sanction_executed", {"sanction_id": sanction_id, "type": s["sanction_type"]})
        _reporter_notice(conn, s["report_id"], "sanction_executed", sanction_id)
    return SanctionResult(sanction_id, s["report_id"], "executed", s["sanction_type"], outcome)


async def reconcile_sanction(interaction: discord.Interaction, sanction_id: int, *,
                             action: str, note: str) -> SanctionResult:
    """群主人工核對後標記已執行或未執行；不得自動重放 API。"""
    permissions.require_guild_owner(interaction)
    if action not in {"mark_executed", "mark_not_executed"} or not note.strip():
        raise SanctionError("請選擇核對結果並填寫理由。")
    install_sanction_schema()
    with transaction() as conn:
        s, _ = _load(conn, interaction.guild.id, sanction_id)
        if s["approval_status"] not in {"needs_reconciliation", "executing"}:
            raise SanctionError("處分不在待核對狀態。")
        # 對 kick/ban 的人工核對須先由群主確認 Discord 實際狀態。
        new = "executed" if action == "mark_executed" else "cancelled"
        now = repository.utc_now()
        conn.execute("UPDATE rp_report_sanctions SET approval_status=?,execution_note=?,"
                     "executed_at=CASE WHEN ?='executed' THEN COALESCE(executed_at,?) ELSE executed_at END,"
                     "updated_at=? WHERE id=?",
                     (new, note.strip()[:1000], new, now, now, sanction_id))
        _event(conn, s["report_id"], interaction.user.id, "sanction_reconciled", {"sanction_id": sanction_id, "result": new})
    return SanctionResult(sanction_id, s["report_id"], new, s["sanction_type"])


async def process_expired_temporary_bans(guild: discord.Guild, *, limit: int = 25) -> int:
    """後續排程每隔一段時間呼叫；只處理本模組建立且已到期的暫時封禁。

    Discord 解封失敗保留錯誤，後續可再次執行（unban 為狀態收斂操作）。
    """
    install_sanction_schema()
    if guild.me is None or not guild.me.guild_permissions.ban_members:
        raise SanctionError("機器人沒有解除封禁的權限。")
    now = repository.utc_now()
    with get_connection() as conn:
        items = conn.execute(
            "SELECT s.id,s.report_id,s.target_user_id FROM rp_report_sanctions s "
            "JOIN rp_reports r ON r.id=s.report_id WHERE r.guild_id=? "
            "AND s.sanction_type='temporary_ban' AND s.approval_status='executed' "
            "AND s.reverted_at IS NULL AND s.expires_at IS NOT NULL AND s.expires_at<=? "
            "ORDER BY s.expires_at LIMIT ?", (str(guild.id), now, max(1, min(limit, 100)))
        ).fetchall()
    completed = 0
    for item in items:
        sid = int(item["id"])
        try:
            await guild.unban(discord.Object(id=int(item["target_user_id"])), reason=f"RP report #{item['report_id']} temporary ban expired")
        except discord.NotFound:
            pass  # 已解封，視為完成
        except (discord.Forbidden, discord.HTTPException) as exc:
            with transaction() as conn:
                conn.execute("UPDATE rp_report_sanctions SET reversion_error=?,updated_at=? WHERE id=?",
                             (f"{type(exc).__name__}: {str(exc)[:400]}", repository.utc_now(), sid))
            continue
        with transaction() as conn:
            conn.execute("UPDATE rp_report_sanctions SET reverted_at=?,reversion_error=NULL,updated_at=? "
                         "WHERE id=? AND reverted_at IS NULL", (repository.utc_now(), repository.utc_now(), sid))
            _event(conn, int(item["report_id"]), None, "temporary_ban_expired", {"sanction_id": sid})
        completed += 1
    return completed


# 本模組不註冊 slash command、不自動啟動排程、不會自行對玩家執行處分。
