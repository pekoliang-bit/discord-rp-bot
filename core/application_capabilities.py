"""集中申請與審核的能力宣告。

只有列於 GENERAL_AUTO_EXECUTORS 的一般申請才可在核准時直接執行。
其他類型保留收件與駁回功能，但不得假裝業務已完成。
沒有 Discord 依賴，供 /申請、/審核與部署前測試共用。
"""
from __future__ import annotations

GENERAL_AUTO_EXECUTORS = frozenset({
    "company_bank", "shop_bank", "item", "faction_join", "faction_exit",
    "faction_change", "character_edit", "leave", "cp_register", "action_expand",
})

GENERAL_PENDING_EXECUTORS = frozenset({
    "item_create", "role_retire",
    "npc_resign", "npc_switch",
    "location_add", "channel_add",
})

SPECIAL_ROUTES = {
    "report_private": "保密舉報核心",
    "report_appeal": "處分申訴核心",
    "event_new": "活動企劃核心",
    "event_submit": "活動企劃核心",
    "event_edit": "活動企劃核心",
    "shop": "個人店鋪審核核心",
    "loan": "銀行貸款審核核心",
    "credit": "信用卡審核核心",
    "black": "黑卡審核核心",
    "company": "商業申請審核核心",
    "brand": "商業申請審核核心",
    "group": "商業申請審核核心",
}


def general_status(kind: str) -> str:
    if kind in GENERAL_AUTO_EXECUTORS:
        return "automatic"
    if kind in GENERAL_PENDING_EXECUTORS:
        return "pending_executor"
    return "unknown"


def general_can_approve(kind: str) -> bool:
    return general_status(kind) == "automatic"


def general_status_text(kind: str) -> str:
    state = general_status(kind)
    if state == "automatic":
        return "已接通核准後執行器：核准將同步執行正式業務。"
    if state == "pending_executor":
        return "已開放收件，但正式執行器尚未接通；可以駁回，暫不可核准。"
    return "未知申請種類；不能核准。"
