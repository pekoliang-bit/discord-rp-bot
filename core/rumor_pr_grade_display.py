
"""公關評級狀態顯示文字。

安裝：core/rumor_pr_grade_display.py
不查詢、不修改資料，也不執行結算。
"""

from __future__ import annotations


def format_pr_grade_status(data) -> str:
    """清楚區分尚未核定、已鎖定及已結案。"""

    header = (
        f"📣 傳聞 #{data.rumor_id}｜"
        f"角色 #{data.character_id}\n"
        f"已記錄公關行動："
        f"{data.action_count} 次\n"
        f"目前公關行動累積："
        f"{data.action_score} 分\n"
    )

    if data.stage == "pending":
        return header + (
            "狀態：尚未核定正式評級\n"
            "正式 S～D 評級：尚無\n"
            "※ 累積分數不是結算等級。"
        )

    if data.stage not in (
        "locked",
        "settled",
    ):
        raise ValueError(
            "未知的公關評級狀態"
        )

    grade = data.final_grade

    if (
        grade not in ("S", "A", "B", "C", "D")
        or data.final_score is None
    ):
        raise RuntimeError(
            "正式評級快照資料不完整"
        )

    if (
        data.stage == "settled"
        and data.case_status != "settled"
    ):
        raise RuntimeError(
            "案件狀態與結案評級不一致"
        )

    label = (
        "已結案"
        if data.stage == "settled"
        else "已鎖定評級，尚未結案"
    )

    version = (
        data.grade_rule_version
        or "未提供"
    )

    return header + (
        f"狀態：{label}\n"
        f"正式公關評級：{grade}\n"
        f"正式評級分數："
        f"{data.final_score}\n"
        f"評級規則版本：{version}\n"
        "※ 正式評級採用已鎖定快照，"
        "不以即時累積分數代替。"
    )
