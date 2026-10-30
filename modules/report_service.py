"""Discord RP 舉報案件流程服務（第 3 檔）。

位置：modules/report_service.py
相依：modules.report_repository、modules.report_permission_service、core.database。
不註冊 slash command、不直接發 Discord 通知或執行處分。

重要安全界線：
- 舉報人 ID 僅由隔離的 rp_reporter_secrets 查詢；一般事件一律不記錄舉報人 ID。
- 不使用第一檔 repo.append_evidence() 處理舉報人自行補件，避免 actor_user_id 外洩。
- 非群主的紀律人員只能看到非 owner_only 案件。
- 給舉報人的通知使用獨立的私密待發佇列，不將其 ID 放入公開事件與一般通知表。
- 本服務不會主動通知被舉報者；須由之後的審核流程明確觸發。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import discord

from core.database import get_connection, transaction
from modules import report_repository as repo
from modules import report_permission_service as perms


# 每次操作支援的證據數；後續可分批追加。
MAX_EVIDENCE_PER_OPERATION = 10
TERMINAL_STATES = frozenset({"closed", "withdrawn"})
REPORTER_EDITABLE_STATES = frozenset({"pending", "investigating", "needs_info", "pending_owner"})
REVIEWER_TRANSITIONS = {
    "pending": frozenset({"investigating", "needs_info", "unsubstantiated", "pending_owner"}),
    "investigating": frozenset({"needs_info", "pending_owner", "substantiated", "unsubstantiated"}),
    "needs_info": frozenset({"investigating", "pending_owner", "unsubstantiated"}),
    "pending_owner": frozenset({"investigating", "substantiated", "unsubstantiated"}),
    "substantiated": frozenset({"closed"}),
    "unsubstantiated": frozenset({"closed"}),
    "withdrawn": frozenset({"closed"}),
    "closed": frozenset(),
}


@dataclass(frozen=True)
class ReportSubmission:
    target_user_id: int
    report_type: str
    reason_code: str
    description: str
    evidence_urls: tuple[str, ...]
    target_character_id: int | None = None
    occurrence_note: str = ""


@dataclass(frozen=True)
class ReportSummary:
    report_id: int
    status: str
    owner_only: bool
    evidence_count: int


def _safe_details(details: dict | None = None) -> str:
    """僅接受本模組產生的安全資料，不接受任意 user_id / raw evidence。"""
    return json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))


def _ensure_private_queue() -> None:
    """隔離通知：僅由具備身分權限的交付器讀取，不暴露於審核列表。"""
    with transaction() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_report_private_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES rp_reports(id) ON DELETE CASCADE,
            notification_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            dedupe_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            delivered_at TEXT
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS idx_report_private_notifications
            ON rp_report_private_notifications(status, id)""")


def install_service_schema() -> None:
    repo.install_schema()
    _ensure_private_queue()


def _get_case(conn, guild_id: str, report_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM rp_reports WHERE guild_id=? AND id=?",
        (guild_id, report_id),
    ).fetchone()
    if row is None:
        raise LookupError("找不到舉報案件。")
    return dict(row)  # 不 join rp_reporter_secrets


def _write_event(conn, *, report_id: int, kind: str, old: str | None = None,
                 new: str | None = None, actor_id: int | None = None,
                 details: dict | None = None) -> None:
    conn.execute("""INSERT INTO rp_report_events
        (report_id,actor_user_id,event_type,previous_status,new_status,details_json,created_at)
        VALUES (?,?,?,?,?,?,?)""",
        (report_id, str(actor_id) if actor_id is not None else None,
         kind, old, new, _safe_details(details), repo.utc_now()),
    )


def _queue_reporter_notice(conn, report_id: int, kind: str, dedupe_key: str) -> None:
    """不儲存收件人 ID，交付時才由僅限群主/授權系統的秘密表解析。"""
    conn.execute("""INSERT OR IGNORE INTO rp_report_private_notifications
        (report_id,notification_type,dedupe_key,created_at) VALUES (?,?,?,?)""",
        (report_id, kind, dedupe_key, repo.utc_now()),
    )


def _normalize_urls(values: Iterable[str]) -> list[str]:
    urls = list(dict.fromkeys(str(url).strip() for url in values if str(url).strip()))
    if not 1 <= len(urls) <= MAX_EVIDENCE_PER_OPERATION:
        raise ValueError("每次必須提交 1～10 筆證據連結。")
    if any(repo.URL_PATTERN.fullmatch(url) is None for url in urls):
        raise ValueError("證據必須是有效 HTTPS 連結。")
    return urls


async def submit_report(interaction: discord.Interaction,
                        submission: ReportSubmission) -> ReportSummary:
    """由第二檔驗證角色/官方身分分流，再交第一檔原子建立。"""
    if interaction.guild is None:
        raise perms.ReportPermissionError("請在伺服器內提交舉報。")
    urls = _normalize_urls(submission.evidence_urls)
    report_id = await perms.create_report_checked(
        interaction,
        target_user_id=submission.target_user_id,
        report_type=submission.report_type,
        reason_code=submission.reason_code,
        description=submission.description,
        evidence_urls=urls,
        target_character_id=submission.target_character_id,
        occurrence_note=submission.occurrence_note,
    )
    # repo.create_report() 已完成建案交易；通知交易失敗不應導致重新建案。
    # 重新呼叫 ensure 具有冪等性。
    install_service_schema()
    with transaction() as conn:
        case = _get_case(conn, str(interaction.guild.id), report_id)
        _queue_reporter_notice(conn, report_id, "report_submitted", f"report:{report_id}:submitted")
        _write_event(conn, report_id=report_id, kind="notification_queued",
                     details={"type": "report_submitted"})
    return ReportSummary(report_id, case["status"], bool(case["owner_only"]), len(urls))


async def add_reporter_evidence(interaction: discord.Interaction, *,
                                report_id: int, evidence_urls: Iterable[str]) -> int:
    """舉報人補件，公開事件 actor 一律 NULL，以免揭露身分。"""
    if interaction.guild is None:
        raise perms.ReportPermissionError("請在伺服器內操作。")
    perms.require_report_owner(interaction, report_id)
    urls = _normalize_urls(evidence_urls)
    install_service_schema()
    with transaction() as conn:
        case = _get_case(conn, str(interaction.guild.id), report_id)
        if case["status"] not in REPORTER_EDITABLE_STATES:
            raise ValueError("此案件目前不接受補充證據。")
        now = repo.utc_now()
        inserted = 0
        for url in urls:
            inserted += conn.execute("""INSERT OR IGNORE INTO rp_report_evidence
                (report_id,evidence_url,created_at) VALUES (?,?,?)""",
                (report_id, url, now),
            ).rowcount
        if inserted:
            _write_event(conn, report_id=report_id, kind="reporter_evidence_added",
                         details={"count": inserted})
            conn.execute("UPDATE rp_reports SET updated_at=? WHERE id=?", (now, report_id))
        return inserted


async def withdraw_report(interaction: discord.Interaction, *, report_id: int) -> bool:
    """登記撤回；調查中的嚴重案件保留調查，由群主/紀律判定是否結案。

    不刪證據、不清除歷史、不洩漏原舉報人 ID。
    """
    if interaction.guild is None:
        raise perms.ReportPermissionError("請在伺服器內操作。")
    perms.require_report_owner(interaction, report_id)
    install_service_schema()
    with transaction() as conn:
        case = _get_case(conn, str(interaction.guild.id), report_id)
        if case["status"] in TERMINAL_STATES or case["status"] in {"substantiated", "unsubstantiated"}:
            return False
        repeated = conn.execute("""SELECT 1 FROM rp_report_events
            WHERE report_id=? AND event_type='reporter_withdrawal_requested' LIMIT 1""",
            (report_id,),
        ).fetchone()
        if repeated:
            return False
        _write_event(conn, report_id=report_id, kind="reporter_withdrawal_requested",
                     old=case["status"], details={"investigation_may_continue": True})
        # 僅待受理的普通案件可自動撤回；正在調查者僅記錄撤回意願。
        if case["status"] == "pending" and case["severity"] == "normal":
            conn.execute("""UPDATE rp_reports SET status='withdrawn',updated_at=?,closed_at=?
                WHERE id=?""", (repo.utc_now(), repo.utc_now(), report_id))
            _write_event(conn, report_id=report_id, kind="status_changed",
                         old="pending", new="withdrawn")
        _queue_reporter_notice(conn, report_id, "withdrawal_recorded",
                               f"report:{report_id}:withdrawn")
    return True


async def change_report_status(interaction: discord.Interaction, *, report_id: int,
                               new_status: str, note: str = "") -> dict:
    """審核人員狀態轉換：每一步重新驗證群主專屬/本人迴避。"""
    if len(note) > 2000:
        raise ValueError("審核備註不得超過 2000 字。")
    case = await perms.get_reviewable_report(interaction, report_id)
    if new_status not in repo.STATES:
        raise ValueError("無效的案件狀態。")
    if new_status not in REVIEWER_TRANSITIONS.get(case["status"], ()):
        raise ValueError(f"案件不可由 {case['status']} 直接變更為 {new_status}。")
    if new_status == "pending_owner" and not perms.is_guild_owner(interaction):
        # 允許正式紀律人員將普通案件上呈群主，不允許其直接批准處分。
        if not perms.is_discipline_staff(interaction):
            raise perms.ReportPermissionError("無權移交案件。")
    if new_status == "closed" and case["status"] == "substantiated":
        # 正式處分是否完成由後續 report_sanction_service 查證。
        raise ValueError("舉報成立案件必須先完成處分／核准程序才能結案。")
    install_service_schema()
    with transaction() as conn:
        fresh = _get_case(conn, str(interaction.guild.id), report_id)
        if fresh["status"] != case["status"]:
            raise RuntimeError("案件已被其他審核人員修改，請重新整理。")
        now = repo.utc_now()
        owner_only = int(fresh["owner_only"])
        if new_status == "pending_owner":
            owner_only = 1  # 移交後不可再由紀律人員閱讀
        changed = conn.execute("""UPDATE rp_reports
            SET status=?,owner_only=?,updated_at=?,closed_at=?
            WHERE guild_id=? AND id=? AND status=?""",
            (new_status, owner_only, now, now if new_status == "closed" else fresh["closed_at"],
             str(interaction.guild.id), report_id, fresh["status"]),
        ).rowcount
        if changed != 1:
            raise RuntimeError("案件狀態競爭衝突，請重試。")
        _write_event(conn, report_id=report_id, kind="status_changed",
                     old=fresh["status"], new=new_status,
                     actor_id=interaction.user.id,
                     details={"note": note.strip()})
        _queue_reporter_notice(conn, report_id, "report_status_changed",
                               f"report:{report_id}:status:{new_status}:{now}")
        return _get_case(conn, str(interaction.guild.id), report_id)


async def get_my_report(interaction: discord.Interaction, *, report_id: int) -> dict:
    if interaction.guild is None:
        raise perms.ReportPermissionError("請在伺服器內操作。")
    perms.require_report_owner(interaction, report_id)
    with get_connection() as conn:
        case = _get_case(conn, str(interaction.guild.id), report_id)
        count = conn.execute(
            "SELECT COUNT(*) FROM rp_report_evidence WHERE report_id=?", (report_id,),
        ).fetchone()[0]
    return {"id": case["id"], "status": case["status"],
            "created_at": case["created_at"], "evidence_count": int(count)}


async def list_my_reports(interaction: discord.Interaction, *, limit: int = 25) -> list[dict]:
    """只回傳當前玩家提交的案件，不把身分欄位帶出。"""
    if interaction.guild is None:
        raise perms.ReportPermissionError("請在伺服器內操作。")
    install_service_schema()
    with get_connection() as conn:
        rows = conn.execute("""SELECT r.id,r.report_type,r.status,r.created_at,r.updated_at
            FROM rp_reports r JOIN rp_reporter_secrets s ON s.report_id=r.id
            WHERE r.guild_id=? AND s.reporter_user_id=?
            ORDER BY r.id DESC LIMIT ?""",
            (str(interaction.guild.id), str(interaction.user.id), max(1, min(100, int(limit)))),
        ).fetchall()
    return [dict(row) for row in rows]


def get_private_notification_for_delivery(*, guild_id: int, notification_id: int,
                                          guild_owner_id: int, authorized_worker: bool) -> dict:
    """僅內部通知交付器可用，應於受信任背景工作中呼叫。

    authorized_worker 必須由後續整合層內部權限驗證，**絕不能**直接使用玩家輸入。
    此函式會回傳舉報人 ID；不得將結果發到紀律頻道或日誌。
    """
    if not authorized_worker:
        raise PermissionError("僅允許可信任的系統通知工作器存取。")
    install_service_schema()
    with get_connection() as conn:
        row = conn.execute("""SELECT n.id,n.report_id,n.notification_type,n.status,
                                   s.reporter_user_id
            FROM rp_report_private_notifications n
            JOIN rp_reports r ON r.id=n.report_id
            JOIN rp_reporter_secrets s ON s.report_id=n.report_id
            WHERE n.id=? AND r.guild_id=?""", (notification_id, str(guild_id))).fetchone()
    if row is None:
        raise LookupError("找不到待發送的私人通知。")
    # guild_owner_id 由機器人 guild.owner_id 取得，用於上層信任界線檢查。
    if int(guild_owner_id) <= 0:
        raise ValueError("群主 ID 無效。")
    return dict(row)


def mark_private_notification_delivered(*, notification_id: int,
                                         authorized_worker: bool) -> None:
    if not authorized_worker:
        raise PermissionError("只有系統通知工作器可以更新投遞狀態。")
    with transaction() as conn:
        conn.execute("""UPDATE rp_report_private_notifications
            SET status='sent',delivered_at=? WHERE id=? AND status='pending'""",
            (repo.utc_now(), notification_id),
        )


# 本檔是服務層，沒有 setup(bot)，不會與既有 /申請、/審核 重複註冊。
