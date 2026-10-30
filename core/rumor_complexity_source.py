"""傳聞複雜度：官方指定、建立後不可變更的案件資料。
位置：core/rumor_complexity_source.py；純工具，不是 Discord extension。
與 T1～T5 嚴重程度分開儲存；不預設任何複雜度或評分扣減。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from core.database import get_connection


@dataclass(frozen=True)
class RumorComplexity:
    guild_id: str
    rumor_id: int
    complexity: int
    source_version: str


def initialize(db) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_complexities (
            guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL,
            complexity INTEGER NOT NULL CHECK(complexity BETWEEN 1 AND 5),
            source_version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id, rumor_id)
        )
    """)


def lock_complexity_in_transaction(
    db, guild_id: str | int, rumor_id: int, complexity: int,
    *, source_version: str, trusted: bool = False
) -> RumorComplexity:
    """由伺服器可信任的案件建構器、在既有交易內呼叫。"""
    if not trusted:
        raise PermissionError("只有可信任的案件建立器可鎖定複雜度")
    if not db.in_transaction:
        raise RuntimeError("必須在建立案件的同一筆交易內呼叫")
    gid, version = str(guild_id).strip(), str(source_version).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件識別碼無效")
    if type(complexity) is not int or complexity not in (1, 2, 3, 4, 5):
        raise ValueError("複雜度必須介於1～5")
    if not version or len(version) > 80:
        raise ValueError("來源版本無效")
    initialize(db)
    case = db.execute(
        "SELECT 1 FROM rp_company_rumors WHERE guild_id=? AND id=?", (gid, rumor_id)
    ).fetchone()
    if case is None:
        raise LookupError("案件不存在")
    db.execute(
        "INSERT OR IGNORE INTO rp_rumor_complexities "
        "(guild_id,rumor_id,complexity,source_version,created_at) VALUES (?,?,?,?,?)",
        (gid, rumor_id, complexity, version, int(time.time())),
    )
    row = db.execute(
        "SELECT complexity,source_version FROM rp_rumor_complexities "
        "WHERE guild_id=? AND rumor_id=?", (gid, rumor_id)
    ).fetchone()
    if int(row["complexity"]) != complexity or row["source_version"] != version:
        raise ValueError("案件已鎖定不同複雜度，不得覆蓋")
    return RumorComplexity(gid, rumor_id, complexity, version)


def read_complexity(guild_id: str | int, rumor_id: int) -> RumorComplexity:
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件識別碼無效")
    with get_connection() as db:
        row = db.execute(
            "SELECT complexity,source_version FROM rp_rumor_complexities "
            "WHERE guild_id=? AND rumor_id=?", (gid, rumor_id)
        ).fetchone()
    if row is None:
        raise LookupError("案件未鎖定複雜度，不能推測")
    return RumorComplexity(gid, rumor_id, int(row["complexity"]), row["source_version"])
