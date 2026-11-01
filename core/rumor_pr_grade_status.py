
"""公關評級狀態唯讀查詢。

放置：core/rumor_pr_grade_status.py

不核定評級、不修改案件、不發放獎勵。
只允許角色本人查詢。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from core.rumor_grade_service import load_grade
from core.rumor_pr_score_summary import summarize_pr_score


@dataclass(frozen=True)
class PRGradeStatus:
    rumor_id: int
    character_id: int
    case_status: str
    stage: str
    action_score: int
    action_count: int
    final_grade: str | None
    final_score: int | None
    grade_rule_version: str | None


def get_pr_grade_status(
    guild_id: str | int,
    actor_id: str | int,
    rumor_id: int,
    character_id: int,
) -> PRGradeStatus:
    """查詢本人角色案件與已鎖定的正式公關評級。"""

    gid = str(guild_id).strip()
    uid = str(actor_id).strip()

    if not gid or not uid:
        raise ValueError(
            "缺少伺服器或操作者編號"
        )

    if (
        type(rumor_id) is not int
        or rumor_id <= 0
        or type(character_id) is not int
        or character_id <= 0
    ):
        raise ValueError(
            "案件或角色編號必須為正整數"
        )

    with get_connection() as db:
        character = db.execute(
            "SELECT owner_id "
            "FROM characters "
            "WHERE guild_id=? AND id=?",
            (gid, character_id),
        ).fetchone()

        if (
            character is None
            or str(character["owner_id"]) != uid
        ):
            raise PermissionError(
                "只能查看自己角色的公關評級"
            )

        case = db.execute(
            "SELECT character_id, status "
            "FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?",
            (gid, rumor_id),
        ).fetchone()

        if (
            case is None
            or int(case["character_id"])
            != character_id
        ):
            raise LookupError(
                "找不到指定角色的傳聞案件"
            )

        status = str(case["status"])

        if status not in ("active", "settled"):
            raise ValueError(
                "目前案件狀態不支援公關評級查詢"
            )

        has_grade_table = (
            db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' "
                "AND name='rp_rumor_grade_snapshots'"
            ).fetchone()
            is not None
        )

    # 目前公關行動累積分數。
    # 不是正式最終評級。
    summary = summarize_pr_score(
        gid,
        rumor_id,
        character_id,
    )

    snapshot = None

    # 舊資料庫沒有評級快照表時不自動建表。
    if has_grade_table:
        try:
            snapshot = load_grade(
                gid,
                rumor_id,
                character_id,
            )
        except LookupError:
            pass

    if snapshot is None:
        if status == "settled":
            raise RuntimeError(
                "案件已結案，但缺少正式評級快照，"
                "請管理員核對"
            )

        return PRGradeStatus(
            rumor_id,
            character_id,
            status,
            "pending",
            summary.score,
            summary.action_count,
            None,
            None,
            None,
        )

    if (
        snapshot.guild_id != gid
        or snapshot.rumor_id != rumor_id
        or snapshot.character_id != character_id
    ):
        raise RuntimeError(
            "正式評級快照歸屬不一致"
        )

    return PRGradeStatus(
        rumor_id,
        character_id,
        status,
        (
            "settled"
            if status == "settled"
            else "locked"
        ),
        summary.score,
        summary.action_count,
        snapshot.grade,
        snapshot.score,
        snapshot.rule_version,
    )
