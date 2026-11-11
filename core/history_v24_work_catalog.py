"""V2.4 第 1 區塊：作品／綜藝素材的靜態驗證，不寫入正式資料庫。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

WORK_TYPES = frozenset({
    "電影", "電視劇", "網劇", "短劇", "舞台劇", "音樂劇", "2.5次元舞台劇",
    "綜藝", "音樂作品", "MV", "紀錄片", "動畫", "配音作品", "時尚作品", "其他作品",
})
TYPE_ALIASES = {
    "院線電影": "電影", "獨立電影": "電影", "電影短片": "電影", "網路劇": "網劇",
    "網絡劇": "網劇", "微短劇": "短劇", "網路短劇": "短劇", "實境節目": "綜藝",
    "競賽綜藝": "綜藝", "訪談綜藝": "綜藝", "綜藝節目": "綜藝",
}
ALLOWED_RARITIES = frozenset({"", "R1", "R2", "R3", "R4", "R5"})
ID_PATTERN = re.compile(r"^WN-\d{4,}$")


@dataclass(frozen=True)
class CatalogReport:
    valid: bool
    total: int
    type_counts: dict[str, int]
    errors: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid, "total": self.total, "type_counts": self.type_counts,
            "errors": self.errors, "warnings": self.warnings,
            "imported": False, "awarded": False,
        }


def canonical_type(raw: str) -> str:
    value = raw.strip()
    return TYPE_ALIASES.get(value, value)


def _text_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(v, str) and bool(v.strip()) for v in value
    )


def validate_works(items: Any) -> CatalogReport:
    """僅驗證；未知作品、無職業或不合法席位不會被自動補造。"""
    errors: list[str] = []
    warnings: list[str] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    if not isinstance(items, list):
        return CatalogReport(False, 0, {}, ["素材檔案頂層必須是 JSON 陣列"], [])
    for n, item in enumerate(items, 1):
        p = f"第 {n} 筆"
        if not isinstance(item, dict):
            errors.append(f"{p}：必須是物件")
            continue
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, str) or not ID_PATTERN.fullmatch(asset_id):
            errors.append(f"{p}：作品 ID 必須是 WN-0001 形式")
        elif asset_id in seen:
            errors.append(f"{p}：作品 ID 重複：{asset_id}")
        else:
            seen.add(asset_id)
        if item.get("pool", "general") != "general":
            errors.append(f"{p}：本區塊只檢查 general 作品池")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            errors.append(f"{p}：作品名稱不可空白")
        if item.get("rarity", "") not in ALLOWED_RARITIES:
            errors.append(f"{p}：稀有度只允許 R1～R5，或空值待核准")
        meta = item.get("meta")
        if not isinstance(meta, dict):
            errors.append(f"{p}：必須提供 meta 物件")
            continue
        raw_type = meta.get("work_type")
        if not isinstance(raw_type, str) or canonical_type(raw_type) not in WORK_TYPES:
            errors.append(f"{p}：未核准的作品類型：{raw_type!r}")
            continue
        kind = canonical_type(raw_type)
        counts[kind] += 1
        if not _text_list(meta.get("occupations")):
            errors.append(f"{p}：occupations 至少要有一個適用職業")
        if kind != "綜藝" and not _text_list(meta.get("roles")):
            warnings.append(f"{p}：尚未提供作品席位 roles，入正式抽選前須補齊或核准免席位")
        if kind == "綜藝" and not _text_list(meta.get("roles")):
            warnings.append(f"{p}：綜藝須核對參與身分（主持／常駐／嘉賓等）")
        if "reward" in item or "bonus" in item or "stat_changes" in item:
            errors.append(f"{p}：數值加成要等全部素材入庫後才處理，不得寫入此批")
    if not items:
        warnings.append("尚無已核准作品可檢查；空清單不是正式素材入庫完成")
    return CatalogReport(not errors, len(items), dict(sorted(counts.items())), errors, warnings)


def validate_file(path: str | Path) -> CatalogReport:
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return validate_works(json.load(handle))
