"""傳聞公關 S～D 評級：採核准門檻、不可變的評級快照。

放置 core/rumor_grade_service.py；純後端模組，不能當作 Cog 載入。
此模組不自動決定原始熱度、難度修正或門檻；它們須來自可信任的
案件狀態與已核准規則。尚未接上官方配置前不得對真實玩家案件啟用。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Mapping

from core.database import get_connection, transaction

GRADES = ("S", "A", "B", "C", "D")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GradeSnapshot:
    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    score: int
    heat: int
    tier: int
    rule_version: str
    fingerprint: str


def initialize() -> None:
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_grade_snapshots (
                guild_id TEXT NOT NULL,
                rumor_id INTEGER NOT NULL,
                character_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, rumor_id, character_id)
            )
        """)


def calculate_grade(score: int, thresholds: Mapping[str, int]) -> str:
    """官方門檻必須包含 S/A/B/C；其餘結果為 D。

    門檻必須嚴格下降，不接受由玩家提交的門檻。
    """
    if type(score) is not int:
        raise ValueError("公關總分必須是整數")
    if set(thresholds) != {"S", "A", "B", "C"}:
        raise ValueError("必須提供核准的 S、A、B、C 門檻")
    vals = [thresholds[g] for g in ("S", "A", "B", "C")]
    if any(type(v) is not int for v in vals) or not all(
        vals[i] > vals[i + 1] for i in range(3)
    ):
        raise ValueError("門檻須為嚴格遞減的整數")
    for grade in ("S", "A", "B", "C"):
        if score >= thresholds[grade]:
            return grade
    return "D"


def lock_grade(
    guild_id: str | int, rumor_id: int, *,
    character_id: int, score: int, heat: int, tier: int,
    rule_version: str, thresholds: Mapping[str, int],
    trusted: bool = False,
) -> GradeSnapshot:
    """固定一次核定的結果；未授權的來源不得呼叫。

    上游須先核對 score/heat/tier 都取自同一案件的可信任資料，
    且 thresholds 與 rule_version 為案件建立時鎖定的官方設定。
    """
    if not trusted:
        raise PermissionError("只有可信任的結算入口能鎖定評級")
    gid = str(guild_id).strip()
    if (not gid or type(rumor_id) is not int or rumor_id <= 0 or
        type(character_id) is not int or character_id <= 0 or
        type(heat) is not int or heat < 0 or
        type(tier) is not int or tier not in range(1, 6) or
        not isinstance(rule_version, str) or not rule_version.strip()):
        raise ValueError("案件資料不完整")
    grade = calculate_grade(score, thresholds)
    payload = {
        "guild_id": gid, "rumor_id": rumor_id,
        "character_id": character_id, "grade": grade,
        "score": score, "heat": heat, "tier": tier,
        "rule_version": rule_version,
    }
    blob, fingerprint = _json(payload), _digest(payload)
    initialize()
    with transaction() as db:
        case = db.execute(
            "SELECT character_id,status FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?", (gid, rumor_id)
        ).fetchone()
        if case is None or int(case["character_id"]) != character_id:
            raise ValueError("案件與當事人不一致")
        if case["status"] != "active":
            raise ValueError("只有尚未結案的傳聞可以首次鎖定評級")
        tier_row = db.execute(
            "SELECT tier FROM rp_rumor_case_tiers WHERE guild_id=? AND rumor_id=?",
            (gid, rumor_id)
        ).fetchone()
        if tier_row is None or int(tier_row["tier"]) != tier:
            raise ValueError("案件等級與鎖定資料不一致")
        db.execute(
            "INSERT OR IGNORE INTO rp_rumor_grade_snapshots "
            "(guild_id,rumor_id,character_id,payload,fingerprint,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (gid, rumor_id, character_id, blob, fingerprint, int(time.time()))
        )
        row = db.execute(
            "SELECT payload,fingerprint FROM rp_rumor_grade_snapshots "
            "WHERE guild_id=? AND rumor_id=? AND character_id=?",
            (gid, rumor_id, character_id)
        ).fetchone()
        if row["payload"] != blob or row["fingerprint"] != fingerprint:
            raise ValueError("此案件評級已鎖定其他內容，拒絕覆寫")
    return GradeSnapshot(**payload, fingerprint=fingerprint)


def load_grade(guild_id: str | int, rumor_id: int, character_id: int) -> GradeSnapshot:
    gid = str(guild_id).strip()
    with get_connection() as db:
        row = db.execute(
            "SELECT payload,fingerprint FROM rp_rumor_grade_snapshots "
            "WHERE guild_id=? AND rumor_id=? AND character_id=?",
            (gid, rumor_id, character_id)
        ).fetchone()
    if row is None:
        raise LookupError("本案尚未鎖定公關評級")
    payload = json.loads(row["payload"])
    if _json(payload) != row["payload"] or _digest(payload) != row["fingerprint"]:
        raise RuntimeError("公關評級快照完整性驗證失敗")
    if (payload["guild_id"] != gid or payload["rumor_id"] != rumor_id or
        payload["character_id"] != character_id or payload["grade"] not in GRADES):
        raise RuntimeError("公關評級快照與案件資料不一致")
    return GradeSnapshot(**payload, fingerprint=row["fingerprint"])
