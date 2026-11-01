"""Discord RP 通知公告九類固定分類與顯示順序。無 Discord 依賴。"""
from __future__ import annotations

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("story", "📖 攻略劇情"),
    ("system", "⚙️ 系統增加／修改"),
    ("activity", "🎉 活動更新"),
    ("character", "👤 角色／NPC 更新"),
    ("shop", "🏬 商城／商品更新"),
    ("company", "🏢 公司／品牌／集團更新"),
    ("faction", "⚔️ 陣營更新"),
    ("world", "🌍 世界觀更新"),
    ("other", "📌 其他更新"),
)
LABELS = dict(CATEGORIES)
ACTIONS = ("新增", "更新", "移除")
LEGACY_CATEGORY = {
    "system": "system", "role": "character", "company": "company",
    "bank": "other", "faction": "faction", "activity": "activity",
    "shop": "shop", "rumor": "other", "content": "other", "other": "other",
}


def ordered_active_categories(keys) -> list[str]:
    """只回傳有更新的分類；永遠按群主確認的九類順序。"""
    active = set(keys)
    return [code for code, _ in CATEGORIES if code in active]
