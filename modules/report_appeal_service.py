"""Discord RP 舉報申訴服務（第 9 檔）。

放置位置：modules/report_appeal_service.py
依賴：前述 report_* 服務、core.database、discord.py 2.x。
本檔不註冊 slash command；由後續 UI 呼叫。

安全規則：每個處分僅能提交一次申訴；原調查、裁決、提案及核准者迴避；
群主專屬案件仍只允許群主審查，無合格獨立審查者時不繞權限。
撤銷 Discord 封禁/禁言前核對是否有其他有效處分，結果不確定則人工核對。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import discord

from core.database import get_connection, transaction
from modules import report_repository as repo
from modules import report_permission_service as permissions
from modules import report_service as service
from modules import report_review_service as reviews
from modules import report_sanction_service as sanctions


class AppealError(ValueError):
    """申訴資格、審查權限或處分狀態不符。"""


@dataclass(frozen=True)
class AppealResult:
    appeal_id: int
    sanction_id: int
    status: str
    decision: Optional[str] = None
    message: str = ""


_DECISIONS = frozenset({"uphold", "revoke", "reduce"})
_FINAL = frozenset({"upheld", "revoked", "reduced", "reversal_manual"})
_ACTIVE = frozenset({"executed", "executing", "needs_reconciliation"})


def install_appeal_schema() -> None:
    """兼容第 1、8 檔資料庫，保留既有申訴內容與唯一限制。"""
    sanctions.install_sanction_schema()
    with transaction() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(rp_report_appeals)")}
        additions = {
            "updated_at": "TEXT", "decided_at": "TEXT", "decision_reason": "TEXT",
            "requested_decision": "TEXT", "review_started_at": "TEXT",
            "reversal_started_at": "TEXT", "reversal_error": "TEXT",
            "reversal_completed_at": "TEXT", "original_end_at": "TEXT",
        }
        for name, ddl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE rp_report_appeals ADD COLUMN {name} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_appeals_review "
                     "ON rp_report_appeals(status,reviewer_user_id)")


def _case(conn, guild_id: int, sanction_id: int) -> tuple[dict, dict]:
    return sanctions._load(conn, guild_id, sanction_id)


def _appeal(conn, guild_id: int, appeal_id: int) -> tuple[dict, dict, dict]:
    row = conn.execute(
        "SELECT a.* FROM rp_report_appeals a "
        "JOIN rp_report_sanctions s ON s.id=a.sanction_id "
        "JOIN rp_reports r ON r.id=s.report_id "
        "WHERE a.id=? AND r.guild_id=?", (int(appeal_id), str(guild_id))
    ).fetchone()
    if row is None:
        raise AppealError("找不到此伺服器的申訴紀錄。")
    appeal = dict(row)
    sanction, report = _case(conn, guild_id, appeal["sanction_id"])
    return appeal, sanction, report


def _event(conn, report_id: int, actor_id: Optional[int], kind: str, appeal_id: int, **values) -> None:
    service._write_event(conn, report_id=report_id, kind=kind, actor_id=actor_id,
                         details={"appeal_id": appeal_id, **values})


def _notice(conn, report_id: int, appeal_id: int, name: str) -> None:
    service._queue_reporter_notice(conn, report_id, name, f"appeal:{appeal_id}:{name}")


def _prior_decision_makers(conn, sanction: dict, report: dict) -> set[str]:
    """原案件承辦、處分提案及批准者均迴避；不讀取舉報人 ID。"""
    people = {str(x) for x in (
        report.get("assigned_reviewer_id"), sanction.get("proposed_by"),
        sanction.get("approved_by")) if x}
    rows = conn.execute(
        "SELECT actor_user_id,event_type FROM rp_report_events "
        "WHERE report_id=? AND actor_user_id IS NOT NULL", (report["id"],)
    ).fetchall()
    relevant = {"report_accepted", "report_substantiated", "report_unsubstantiated",
                "report_decided", "sanction_proposed", "major_sanction_approved",
                "sanction_executed", "sanction_execution_claimed", "investigation_resumed",
                "report_referred_to_owner", "review_accepted", "review_decided",
                "investigation_decided", "review_resumed", "referred_to_owner",
                "review_information_requested", "review_closed"}
    for row in rows:
        if row["event_type"] in relevant:
            people.add(str(row["actor_user_id"]))
    return people


async def submit_appeal(interaction: discord.Interaction, *, sanction_id: int,
                        reason: str) -> AppealResult:
    """由受處分玩家本人提出；SQL UNIQUE(sanction_id) 保證只能一次。"""
    if interaction.guild is None:
        raise AppealError("只能在原伺服器提出申訴。")
    if not 10 <= len(reason.strip()) <= 3000:
        raise AppealError("申訴理由須介於 10～3000 字元。")
    install_appeal_schema()
    with transaction() as conn:
        s, report = _case(conn, interaction.guild.id, sanction_id)
        if str(s["target_user_id"]) != str(interaction.user.id):
            raise permissions.ReportPermissionError("只有受處分本人能提出申訴。")
        if s["approval_status"] != "executed":
            raise AppealError("只有已正式執行的處分可以申訴。")
        if conn.execute("SELECT 1 FROM rp_report_appeals WHERE sanction_id=?", (sanction_id,)).fetchone():
            raise AppealError("此處分已使用一次申訴機會，不能重複提交。")
        now = repo.utc_now()
        cur = conn.execute(
            "INSERT INTO rp_report_appeals "
            "(sanction_id,appellant_user_id,reason,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (sanction_id, str(interaction.user.id), reason.strip(), "pending", now, now)
        )
        aid = int(cur.lastrowid)
        _event(conn, s["report_id"], None, "appeal_submitted", aid)
        _notice(conn, s["report_id"], aid, "appeal_submitted")
        return AppealResult(aid, sanction_id, "pending", message="申訴已提出，等待獨立審查。")


async def get_my_appeals(interaction: discord.Interaction, *, limit: int = 25) -> list[dict]:
    if interaction.guild is None:
        raise AppealError("請在伺服器內操作。")
    install_appeal_schema()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT a.id,a.sanction_id,a.status,a.decision,a.created_at,a.decided_at "
            "FROM rp_report_appeals a JOIN rp_report_sanctions s ON s.id=a.sanction_id "
            "JOIN rp_reports r ON r.id=s.report_id WHERE r.guild_id=? "
            "AND a.appellant_user_id=? ORDER BY a.id DESC LIMIT ?",
            (str(interaction.guild.id), str(interaction.user.id), max(1, min(limit, 100)))
        ).fetchall()
        return [dict(row) for row in rows]


async def _authorize_reviewer(interaction: discord.Interaction, report_id: int) -> None:
    await reviews._authorized_case(interaction, report_id)
    if not (permissions.is_guild_owner(interaction) or permissions.is_discipline_staff(interaction)):
        raise permissions.ReportPermissionError("缺少申訴審查權限。")


def _check_independent(conn, actor_id: int, sanction: dict, report: dict) -> None:
    if str(actor_id) == str(sanction["target_user_id"]):
        raise permissions.ReportPermissionError("受處分本人不能審核自己的申訴。")
    if str(actor_id) in _prior_decision_makers(conn, sanction, report):
        raise permissions.ReportPermissionError("原案件審核、裁決或處分人員必須迴避本次申訴。")
    if reviews._is_reporter(conn, int(report["id"]), actor_id):
        raise permissions.ReportPermissionError("舉報者不能審理本案申訴。")


async def list_appeal_queue(interaction: discord.Interaction, *, limit: int = 25) -> list[dict]:
    permissions.require_discipline_or_owner(interaction)
    install_appeal_schema()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT a.id,a.sanction_id,a.status,s.report_id,s.target_user_id,r.owner_only "
            "FROM rp_report_appeals a JOIN rp_report_sanctions s ON s.id=a.sanction_id "
            "JOIN rp_reports r ON r.id=s.report_id "
            "WHERE r.guild_id=? AND a.status IN ('pending','reviewing','reversal_manual') "
            "ORDER BY a.id DESC LIMIT ?",
            (str(interaction.guild.id), max(1, min(limit, 100)))
        ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            if item["owner_only"] and not permissions.is_guild_owner(interaction):
                continue
            sanction, report = _case(conn, interaction.guild.id, item["sanction_id"])
            if str(interaction.user.id) == str(sanction["target_user_id"]):
                continue
            if str(interaction.user.id) in _prior_decision_makers(conn, sanction, report):
                continue
            if reviews._is_reporter(conn, int(report["id"]), interaction.user.id):
                continue
            results.append(item)
        return results


async def claim_appeal(interaction: discord.Interaction, *, appeal_id: int) -> AppealResult:
    permissions.require_discipline_or_owner(interaction)
    install_appeal_schema()
    with get_connection() as conn:
        a, s, report = _appeal(conn, interaction.guild.id, appeal_id)
    await _authorize_reviewer(interaction, s["report_id"])
    with transaction() as conn:
        a, s, report = _appeal(conn, interaction.guild.id, appeal_id)
        _check_independent(conn, interaction.user.id, s, report)
        if a["status"] != "pending":
            raise AppealError("申訴已被受理或結案。")
        now = repo.utc_now()
        conn.execute("UPDATE rp_report_appeals SET reviewer_user_id=?,status='reviewing',"
                     "review_started_at=?,updated_at=? WHERE id=? AND status='pending'",
                     (str(interaction.user.id), now, now, appeal_id))
        _event(conn, s["report_id"], interaction.user.id, "appeal_claimed", appeal_id)
        return AppealResult(appeal_id, a["sanction_id"], "reviewing")


def _other_active(conn, sanction: dict, *, kind: set[str]) -> bool:
    """其它有效處分存在時禁止自動解除 Discord 狀態。"""
    row = conn.execute(
        "SELECT 1 FROM rp_report_sanctions s JOIN rp_reports r ON r.id=s.report_id "
        "WHERE r.guild_id=(SELECT guild_id FROM rp_reports WHERE id=?) "
        "AND s.target_user_id=? AND s.id<>? AND s.sanction_type IN ({}) "
        "AND s.approval_status IN ('executed','executing','needs_reconciliation') "
        "AND s.reverted_at IS NULL LIMIT 1".format(",".join("?" for _ in kind)),
        (sanction["report_id"], sanction["target_user_id"], sanction["id"], *sorted(kind))
    ).fetchone()
    return row is not None


async def _reverse_discord(guild: discord.Guild, sanction: dict) -> str:
    kind = sanction["sanction_type"]
    uid = int(sanction["target_user_id"])
    with get_connection() as conn:
        if kind in {"ban", "temporary_ban"} and _other_active(conn, sanction, kind={"ban", "temporary_ban"}):
            raise AppealError("其他封禁仍可能有效，請人工核對，不能自動解封。")
        if kind in {"short_timeout", "long_timeout"} and _other_active(conn, sanction, kind={"short_timeout", "long_timeout"}):
            raise AppealError("其他禁言仍可能有效，請人工核對，不能自動解除。")
    if kind == "warning":
        return "警告已撤銷（歷史紀錄保留）"
    if kind in {"kick", "suspend"}:
        raise AppealError("踢出無法自動恢復成員；自訂停權須使用專屬模組手動處理。")
    if kind in {"ban", "temporary_ban"}:
        if not guild.me or not guild.me.guild_permissions.ban_members:
            raise AppealError("機器人缺少解除封禁權限。")
        try:
            await guild.fetch_ban(discord.Object(id=uid))
        except discord.NotFound as exc:
            raise AppealError("目前沒有此封禁，需人工核對後結案。") from exc
        await guild.unban(discord.Object(id=uid), reason=f"Appeal #{sanction['id']} granted")
        return "已解除 Discord 封禁"
    if kind in {"short_timeout", "long_timeout"}:
        member = guild.get_member(uid)
        if member is None:
            member = await guild.fetch_member(uid)
        if member.timed_out_until is None or member.timed_out_until <= datetime.now(timezone.utc):
            raise AppealError("目前沒有有效禁言，需人工核對後結案。")
        if not guild.me or not guild.me.guild_permissions.moderate_members:
            raise AppealError("機器人缺少解除禁言權限。")
        if member.top_role >= guild.me.top_role:
            raise AppealError("機器人身分組層級不足。")
        await member.timeout(None, reason=f"Appeal #{sanction['id']} granted")
        return "已解除 Discord 禁言"
    raise AppealError("無法自動撤銷此類處分。")


async def decide_appeal(interaction: discord.Interaction, *, appeal_id: int,
                        decision: str, reason: str) -> AppealResult:
    """維持、撤銷、減輕；減輕先作待人工處理，不自動改寫原處分。"""
    permissions.require_discipline_or_owner(interaction)
    if decision not in _DECISIONS or not 5 <= len(reason.strip()) <= 2000:
        raise AppealError("決定須為 uphold/revoke/reduce，理由至少 5 字元。")
    install_appeal_schema()
    with get_connection() as conn:
        a, s, report = _appeal(conn, interaction.guild.id, appeal_id)
    await _authorize_reviewer(interaction, s["report_id"])
    with transaction() as conn:
        a, s, report = _appeal(conn, interaction.guild.id, appeal_id)
        _check_independent(conn, interaction.user.id, s, report)
        if a["status"] != "reviewing" or str(a["reviewer_user_id"]) != str(interaction.user.id):
            raise AppealError("必須由已受理的獨立審查者裁決。")
        now = repo.utc_now()
        if decision == "uphold":
            conn.execute("UPDATE rp_report_appeals SET status='upheld',decision='uphold',"
                         "decision_reason=?,decided_at=?,updated_at=? WHERE id=?",
                         (reason.strip(), now, now, appeal_id))
            _event(conn, s["report_id"], interaction.user.id, "appeal_upheld", appeal_id)
            _notice(conn, s["report_id"], appeal_id, "appeal_upheld")
            return AppealResult(appeal_id, s["id"], "upheld", "uphold")
        conn.execute("UPDATE rp_report_appeals SET status='reversing',requested_decision=?,"
                     "decision_reason=?,decided_at=?,reversal_started_at=?,updated_at=? WHERE id=?",
                     (decision, reason.strip(), now, now, now, appeal_id))
        _event(conn, s["report_id"], interaction.user.id, "appeal_reversal_claimed", appeal_id,
               decision=decision)
    # 不在資料庫鎖內執行網路請求。減輕需明確訂出新處分，先轉人工核對。
    if decision == "reduce":
        return _mark_manual(appeal_id, s, "減輕處分需核准具體的新期限／措施，尚未改動 Discord 狀態。")
    if s["approval_status"] != "executed":
        return _mark_manual(appeal_id, s, "原處分不在已執行狀態，須人工確認。")
    try:
        message = await _reverse_discord(interaction.guild, s)
    except Exception as exc:
        return _mark_manual(appeal_id, s, f"{type(exc).__name__}: {str(exc)[:450]}")
    with transaction() as conn:
        a, current, report = _appeal(conn, interaction.guild.id, appeal_id)
        if a["status"] != "reversing":
            raise AppealError("申訴狀態被外部更改，請人工檢查。")
        now = repo.utc_now()
        conn.execute("UPDATE rp_report_sanctions SET reverted_at=?,reversion_error=NULL,updated_at=? "
                     "WHERE id=? AND reverted_at IS NULL", (now, now, s["id"]))
        conn.execute("UPDATE rp_report_appeals SET status='revoked',decision='revoke',"
                     "reversal_completed_at=?,updated_at=? WHERE id=?", (now, now, appeal_id))
        _event(conn, s["report_id"], interaction.user.id, "appeal_revoked", appeal_id)
        _notice(conn, s["report_id"], appeal_id, "appeal_revoked")
    return AppealResult(appeal_id, s["id"], "revoked", "revoke", message)


def _mark_manual(appeal_id: int, sanction: dict, note: str) -> AppealResult:
    with transaction() as conn:
        now = repo.utc_now()
        conn.execute("UPDATE rp_report_appeals SET status='reversal_manual',reversal_error=?,"
                     "updated_at=? WHERE id=? AND status='reversing'",
                     (note[:1000], now, appeal_id))
        _event(conn, sanction["report_id"], None, "appeal_reversal_manual", appeal_id)
    return AppealResult(appeal_id, sanction["id"], "reversal_manual", message=note)


async def complete_manual_reversal(interaction: discord.Interaction, *, appeal_id: int,
                                   completed: bool, note: str) -> AppealResult:
    """群主核對 Discord 與其他處分後登記人工結果，不代替實際 API 操作。"""
    permissions.require_guild_owner(interaction)
    if len(note.strip()) < 5:
        raise AppealError("請記錄核對依據及操作結果。")
    install_appeal_schema()
    with transaction() as conn:
        a, s, report = _appeal(conn, interaction.guild.id, appeal_id)
        if a["status"] != "reversal_manual":
            raise AppealError("此申訴不在人工核對狀態。")
        if completed:
            # 先有實際核對及撤銷操作，才可標示完成。不得盲目移除其他處分。
            now = repo.utc_now()
            decision = a["requested_decision"]
            status = "revoked" if decision == "revoke" else "reduced"
            conn.execute("UPDATE rp_report_appeals SET status=?,decision=?,"
                         "reversal_completed_at=?,reversal_error=NULL,updated_at=? WHERE id=?",
                         (status, decision, now, now, appeal_id))
            if decision == "revoke":
                conn.execute("UPDATE rp_report_sanctions SET reverted_at=?,updated_at=? WHERE id=?",
                             (now, now, s["id"]))
            _event(conn, s["report_id"], interaction.user.id, "appeal_manual_completed", appeal_id,
                   decision=decision)
            _notice(conn, s["report_id"], appeal_id, "appeal_manual_completed")
        else:
            conn.execute("UPDATE rp_report_appeals SET reversal_error=?,updated_at=? WHERE id=?",
                         (note.strip()[:1000], repo.utc_now(), appeal_id))
            _event(conn, s["report_id"], interaction.user.id, "appeal_manual_pending", appeal_id)
            status = "reversal_manual"
    return AppealResult(appeal_id, s["id"], status, a["requested_decision"], note.strip())


# 不註冊指令；申訴入口與按鈕將由後續 report_appeal_view / report_commands 接入。
