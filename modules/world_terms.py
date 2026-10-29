"""Discord RP 世界觀顯示稱呼讀取器。

使用方式：from modules.world_terms import term, organization_terms
不修改資料庫，不修改任何既有公司/組織記錄。
"""
from __future__ import annotations
import json
import logging
from core.database import get_connection

LOGGER = logging.getLogger(__name__)
DEFAULT_TERMS = {
    "company": "公司", "group": "集團", "brand": "品牌",
    "faction": "陣營", "position": "職位", "currency": "金幣",
}


def organization_terms(guild_id: int | str) -> dict[str, str]:
    result = DEFAULT_TERMS.copy()
    try:
        with get_connection() as db:
            row = db.execute(
                "SELECT config_json FROM world_configs WHERE guild_id=?",
                (str(guild_id),),
            ).fetchone()
        if not row:
            return result
        data = json.loads(row[0])
        terms = data.get("terms", {}) if isinstance(data, dict) else {}
        if not isinstance(terms, dict):
            return result
        for key in DEFAULT_TERMS:
            value = terms.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()[:30]
    except Exception as exc:
        if "no such table" not in str(exc).lower():
            LOGGER.warning("World terms read failed for guild %s: %s", guild_id, exc)
    return result


def term(guild_id: int | str, key: str) -> str:
    """取得稱呼，例如 term(guild_id, 'company')。"""
    if key not in DEFAULT_TERMS:
        raise KeyError(f"Unknown world term: {key}")
    return organization_terms(guild_id)[key]
async def setup(bot):
    """世界觀稱呼讀取器，不需要註冊獨立指令。"""
    pass
