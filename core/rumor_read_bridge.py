
"""Discord RP 傳聞案件唯讀橋接。

從既有 rp_company_rumors / rp_rumor_case_meta 讀取，
只將可確認的公開狀態轉成網友反應事件。
不根據證據數量推斷傳聞真偽；不呼叫建表函式；不發文。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from core.config import DATABASE_PATH
from core.rumor_user_reactions import RumorReactionEvent


@dataclass(frozen=True)
class RumorReadResult:
    event: RumorReactionEvent
    title: str
    source_type: str
    status: str
    visibility: str
    evidence_state: str
    requires_review: bool


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(
            f"資料庫不存在：{path}"
        )

    uri = (
        "file:"
        + quote(str(path.resolve()), safe="/:\\")
        + "?mode=ro"
    )

    db = sqlite3.connect(
        uri,
        uri=True,
        timeout=15,
    )

    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")

    return db


def _has_table(
    db: sqlite3.Connection,
    name: str,
) -> bool:
    return db.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table'
          AND name=?
        """,
        (name,),
    ).fetchone() is not None


def read_rumor_event(
    guild_id: str | int,
    rumor_id: int,
    *,
    disclosure_authorized: bool = False,
    evidence_state: str = "unverified",
    evidence_review_approved: bool = False,
    public_response_confirmed: bool = False,
    db_path: str | Path | None = None,
) -> RumorReadResult:
    """以現有案件資料建立網友反應快照。

    disclosure_authorized、evidence_review_approved
    必須由可信的管理員審核／角色授權流程提供，
    不能直接取自玩家輸入。

    即使案件狀態為 settled，也不代表內容已證實。
    """

    gid = str(guild_id).strip()

    if (
        not gid
        or type(rumor_id) is not int
        or rumor_id <= 0
    ):
        raise ValueError(
            "guild_id 或 rumor_id 無效"
        )

    if evidence_state not in {
        "unverified",
        "verified",
        "disproved",
    }:
        raise ValueError(
            "證據狀態無效"
        )

    if (
        evidence_state != "unverified"
        and not evidence_review_approved
    ):
        raise PermissionError(
            "證據結論必須通過獨立審核"
        )

    path = Path(
        db_path
        if db_path is not None
        else DATABASE_PATH
    ).expanduser()

    with _open_readonly(path) as db:
        if not _has_table(
            db,
            "rp_company_rumors",
        ):
            raise RuntimeError(
                "找不到既有傳聞案件表"
            )

        meta_exists = _has_table(
            db,
            "rp_rumor_case_meta",
        )

        if meta_exists:
            row = db.execute(
                """
                SELECT
                    r.id,
                    r.guild_id,
                    r.character_id,
                    r.title,
                    r.source_type,
                    r.status,
                    COALESCE(
                        m.visibility,
                        'private'
                    ) AS visibility

                FROM rp_company_rumors r

                LEFT JOIN rp_rumor_case_meta m
                  ON m.guild_id = r.guild_id
                 AND m.rumor_id = r.id

                WHERE r.guild_id = ?
                  AND r.id = ?
                """,
                (gid, rumor_id),
            ).fetchone()

        else:
            row = db.execute(
                """
                SELECT
                    id,
                    guild_id,
                    character_id,
                    title,
                    source_type,
                    status,
                    'private' AS visibility

                FROM rp_company_rumors

                WHERE guild_id = ?
                  AND id = ?
                """,
                (gid, rumor_id),
            ).fetchone()

    if row is None:
        raise LookupError(
            "指定的傳聞案件不存在"
        )

    status = str(row["status"])

    if status == "active":
        phase = "active"

    elif status == "settled":
        phase = "settled"

    else:
        # 未知狀態採取封鎖而非預設公開。
        phase = "cancelled"

    visibility = str(
        row["visibility"]
    )

    public_authorized = (
        bool(disclosure_authorized)
        and visibility == "public"
        and phase != "cancelled"
    )

    event = RumorReactionEvent(
        guild_id=gid,
        rumor_id=rumor_id,
        character_id=int(
            row["character_id"]
        ),
        phase=phase,
        evidence_state=evidence_state,
        public_authorized=public_authorized,
        case_verified=True,
        # 僅表示資料庫中確實存在該案件，
        # 並不代表指控成立。
        public_response=bool(
            public_response_confirmed
            and public_authorized
        ),
    )

    return RumorReadResult(
        event=event,
        title=str(row["title"]),
        source_type=str(
            row["source_type"]
        ),
        status=status,
        visibility=visibility,
        evidence_state=evidence_state,
        requires_review=(
            not public_authorized
            or evidence_state == "unverified"
        ),
    )
