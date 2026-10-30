"""傳聞評級：從正式角色屬性讀取公關能力（唯讀）。
位置：core/rumor_pr_ability_source.py；純工具，不是 Discord extension。
不接受玩家提交的能力數字，不修改角色資料。
"""
from __future__ import annotations

from dataclasses import dataclass
from core.database import get_connection


@dataclass(frozen=True)
class PRAbility:
    guild_id: str
    character_id: int
    attribute_name: str
    value: int
    attribute_version: int


def read_pr_ability_in_transaction(db, guild_id: str | int, character_id: int) -> PRAbility:
    """使用呼叫者的連線，供結算流程保持讀取一致性。"""
    gid = str(guild_id).strip()
    if not gid or type(character_id) is not int or character_id <= 0:
        raise ValueError("無效的伺服器或角色編號")
    char = db.execute(
        "SELECT status FROM characters WHERE guild_id=? AND id=?", (gid, character_id)
    ).fetchone()
    if char is None or char["status"] not in ("active", "approved"):
        raise ValueError("角色不存在或未核准")
    row = db.execute(
        "SELECT value,version FROM attributes WHERE guild_id=? AND character_id=? AND name=?",
        (gid, character_id, "公關"),
    ).fetchone()
    if row is None:
        raise LookupError("角色尚無正式『公關』屬性，不能假設為零")
    value, version = int(row["value"]), int(row["version"])
    if value < 0 or version < 0:
        raise RuntimeError("角色公關屬性資料異常")
    return PRAbility(gid, character_id, "公關", value, version)


def read_pr_ability(guild_id: str | int, character_id: int) -> PRAbility:
    with get_connection() as db:
        return read_pr_ability_in_transaction(db, guild_id, character_id)
