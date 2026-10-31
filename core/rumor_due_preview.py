
"""到期傳聞評級批次預覽；純唯讀，不鎖評級、不核定方案、不結案或發獎。

安裝：core/rumor_due_preview.py
此模組不是 Discord Cog，不需加入 main.py 自動載入。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.rumor_settlement_queue import inspect_due_cases
from core.rumor_grade_pipeline import lock_prepared_grade


@dataclass(frozen=True)
class PreviewItem:
    guild_id: str
    rumor_id: int
    character_id: int
    queue_state: str
    preview_grade: str | None
    preview_score: int | None
    result: str
    detail: str


def preview_due_cases(
    guild_id: str | int | None = None,
    *,
    now: int | None = None,
    limit: int = 50,
    trusted: bool = False,
) -> list[PreviewItem]:
    """依到期順序預覽；單一案件失敗不中斷其他案件。"""

    if not trusted:
        raise PermissionError(
            "僅已授權的結算後端能取得內部評級預覽"
        )

    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit 必須為 1～200")

    if now is not None and type(now) is not int:
        raise ValueError("now 必須是 Unix 秒數整數")

    due = inspect_due_cases(
        guild_id, now=now, limit=limit
    )

    results: list[PreviewItem] = []

    for case in due:
        if case.state != "needs_grade":
            results.append(
                PreviewItem(
                    case.guild_id,
                    case.rumor_id,
                    case.character_id,
                    case.state,
                    None,
                    None,
                    "skipped",
                    case.explanation,
                )
            )
            continue

        try:
            grade = lock_prepared_grade(
                case.guild_id,
                case.rumor_id,
                trusted=True,
                dry_run=True,
                now=now,
            )

            if not grade.dry_run:
                raise RuntimeError(
                    "下游未維持唯讀模式"
                )

            if grade.character_id != case.character_id:
                raise RuntimeError(
                    "評級回傳的角色不符"
                )

            results.append(
                PreviewItem(
                    case.guild_id,
                    case.rumor_id,
                    case.character_id,
                    case.state,
                    grade.grade,
                    grade.score,
                    "ready_for_review",
                    "僅預覽；尚未正式鎖定",
                )
            )

        except (
            LookupError,
            ValueError,
            RuntimeError,
        ) as exc:
            results.append(
                PreviewItem(
                    case.guild_id,
                    case.rumor_id,
                    case.character_id,
                    case.state,
                    None,
                    None,
                    "blocked",
                    f"{type(exc).__name__}: {exc}",
                )
            )

    return results
