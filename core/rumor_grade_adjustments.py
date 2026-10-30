"""案件公關評級修正快照（開發版，純後端）。

位置：core/rumor_grade_adjustments.py
本模組不讀取角色技能，且不自行判斷案件複雜度。
數據須由可信任的伺服器端能力／案件來源核對後，在同一個資料庫交易中保存。
未接上來源核對前，正式結算不得使用玩家輸入的修正數值。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from core.database import get_connection


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(blob: str) -> str:
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GradeAdjustment:
    guild_id: str
    rumor_id: int
    character_id: int
    ability_bonus: int
    complexity_penalty: int
    heat_modifier: int
    source_version: str
    adjusted_total: int | None = None


def initialize(db) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_grade_adjustments (
            guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            digest TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id, rumor_id, character_id)
        )
    """)


def record_adjustment_in_transaction(
    db, guild_id: str | int, rumor_id: int, character_id: int, *,
    ability_bonus: int, complexity_penalty: int, heat_modifier: int,
    source_version: str, trusted: bool = False,
) -> GradeAdjustment:
    """在現有交易內鎖定一組已由伺服器端核對的評級修正。

    注意 trusted 只作內部隔離，不能替代 Discord 權限與來源驗證。
    """
    if not trusted:
        raise PermissionError("禁止玩家直接寫入評級修正")
    if not db.in_transaction:
        raise RuntimeError("須在既有的資料庫交易內使用")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0 or type(character_id) is not int or character_id <= 0:
        raise ValueError("案件或角色識別碼無效")
    for label, value in (("能力加成", ability_bonus), ("複雜度懲罰", complexity_penalty), ("熱度修正", heat_modifier)):
        if type(value) is not int or abs(value) > 100000:
            raise ValueError(f"{label} 超出允許範圍")
    if ability_bonus < 0 or complexity_penalty < 0:
        raise ValueError("能力加成與複雜度懲罰不得為負")
    version = str(source_version).strip()
    if not version or len(version) > 80:
        raise ValueError("修正來源版本無效")
    initialize(db)
    row = db.execute(
        "SELECT character_id, status FROM rp_company_rumors WHERE guild_id=? AND id=?",
        (gid, rumor_id),
    ).fetchone()
    if row is None or int(row["character_id"]) != character_id or row["status"] != "active":
        raise ValueError("案件不存在、已結案或主要當事人不一致")
    payload = dict(guild_id=gid, rumor_id=rumor_id, character_id=character_id,
                   ability_bonus=ability_bonus, complexity_penalty=complexity_penalty,
                   heat_modifier=heat_modifier, source_version=version)
    blob = _canonical(payload)
    digest = _digest(blob)
    db.execute(
        "INSERT OR IGNORE INTO rp_rumor_grade_adjustments "
        "(guild_id,rumor_id,character_id,payload,digest,created_at) VALUES (?,?,?,?,?,?)",
        (gid, rumor_id, character_id, blob, digest, int(time.time())),
    )
    saved = db.execute(
        "SELECT payload,digest FROM rp_rumor_grade_adjustments "
        "WHERE guild_id=? AND rumor_id=? AND character_id=?",
        (gid, rumor_id, character_id),
    ).fetchone()
    if saved["payload"] != blob or saved["digest"] != digest:
        raise ValueError("案件已有不同的修正快照，禁止覆蓋")
    return GradeAdjustment(**payload)


def load_adjustment(guild_id: str | int, rumor_id: int, character_id: int) -> GradeAdjustment:
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0 or type(character_id) is not int or character_id <= 0:
        raise ValueError("查詢參數無效")
    with get_connection() as db:
        row = db.execute(
            "SELECT payload,digest FROM rp_rumor_grade_adjustments "
            "WHERE guild_id=? AND rumor_id=? AND character_id=?",
            (gid, rumor_id, character_id),
        ).fetchone()
    if row is None:
        raise LookupError("案件尚無可信任的評級修正快照")
    if _digest(row["payload"]) != row["digest"]:
        raise RuntimeError("評級修正摘要不符")
    payload = json.loads(row["payload"])
    if _canonical(payload) != row["payload"] or payload["guild_id"] != gid or payload["rumor_id"] != rumor_id or payload["character_id"] != character_id:
        raise RuntimeError("評級修正快照與案件不一致")
    return GradeAdjustment(**payload)


def adjusted_score(base_score: int, adjustment: GradeAdjustment) -> int:
    if type(base_score) is not int:
        raise ValueError("公關基礎分數須為整數")
    return base_score + adjustment.ability_bonus - adjustment.complexity_penalty + adjustment.heat_modifier
