"""通知公告摘要格式與分類詳情文字。按鈕會在下一批接入 Discord UI。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from modules.notification_categories import CATEGORIES, LABELS, ACTIONS, ordered_active_categories


def _field(row, name, default=None):
    try:
        result = row[name]
        return default if result is None else result
    except (KeyError, IndexError, TypeError):
        return default


def _clean(text, maximum=100):
    s = str(text or "").replace("@", "＠")
    for char in "`*_[]<>\\\r\n":
        s = s.replace(char, " ")
    return s.strip()[:maximum]


def format_announcement(rows, *, date: datetime | None = None):
    """rows: 已正式發布、尚未公告的 notification_update_items 資料列。

    回傳 (公告文字, 有更新的分類代碼)。若超出 Discord 2000 字限制，
    摘要改列分類總計；詳細資訊由分類按鈕後續查看。
    """
    now = date or datetime.now(ZoneInfo("Asia/Taipei"))
    counts = defaultdict(lambda: defaultdict(int))
    story = defaultdict(lambda: defaultdict(int))
    for row in rows:
        category, action = _field(row, "category"), _field(row, "action")
        if category not in LABELS or action not in ACTIONS:
            continue
        counts[category][action] += 1
        if category == "story":
            story[_clean(_field(row, "character_name") or "未分類角色", 60)][action] += 1
    active = ordered_active_categories(counts)
    header = f"📢 **{now:%Y年%m月%d日} 更新**"
    if not active:
        return header + "\n\n本期沒有待公告的更新。", []
    lines = [header]
    for code, label in CATEGORIES:
        if code not in active:
            continue
        lines += ["", f"**【{label}】**"]
        if code == "story":
            for name in sorted(story):
                detail = "、".join(f"{a} {story[name][a]} 條" for a in ACTIONS if story[name][a])
                lines.append(f"{name}：{detail}")
        else:
            lines.append("、".join(f"{a} {counts[code][a]} 項" for a in ACTIONS if counts[code][a]))
    footer = "\n\n👇 點擊下方分類按鈕，查看詳細更新與原始連結。"
    result = "\n".join(lines) + footer
    if len(result) > 1950:
        lines = [header]
        for code, label in CATEGORIES:
            if code in active:
                n = sum(counts[code].values())
                lines.append(f"【{label}】共 {n} 項更新")
        result = "\n".join(lines) + footer
    return result, active


def format_category_details(rows, category: str) -> list[str]:
    """將某分類更新整理為每段不超過 1850 字的內容；由 Discord UI 發 ephemeral。"""
    if category not in LABELS:
        raise ValueError("未知分類")
    heading = f"**【{LABELS[category]}】詳細更新**"
    chunks, current = [], heading
    for row in rows:
        if _field(row, "category") != category:
            continue
        name = _clean(_field(row, "character_name"), 45)
        title = _clean(_field(row, "title"), 100)
        action = _field(row, "action")
        url = _field(row, "source_url")
        link = f" [前往內容]({url})" if url else "（尚無來源連結）"
        line = f"• {name + '｜' if name else ''}{action}：{title}{link}"
        if len(current) + len(line) + 1 > 1850:
            chunks.append(current)
            current = heading
        current += "\n" + line
    chunks.append(current if current != heading else heading + "\n本期沒有詳細更新。")
    return chunks
