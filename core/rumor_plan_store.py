"""傳聞公關獎懲方案：案件規則快照、不可變方案保存與完整性查核。

安裝：core/rumor_plan_store.py
純後端工具，不是 Discord Cog；不發獎、不扣款、不結案。

重要：在案件「建立時」呼叫 lock_case_rules() 固定規則版本；
完成評級後使用已保存的規則快照產生 RewardPlan，再呼叫
lock_reward_plan()。不可讓玩家控制這些函式的參數。

舊案沒有規則快照時不得擅自使用最新規則補填。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from typing import Any

from core.database import get_connection, transaction
from core.rumor_reward_planner import (
    RewardPlan, RuleConfig, _validate_rules, make_reward_plan,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _rule_data(rules: RuleConfig) -> dict:
    _validate_rules(rules)
    return {
        "version": rules.version,
        "grade_changes": {str(k): dict(v) for k, v in rules.grade_changes.items()},
        "tier_percent": {str(k): v for k, v in rules.tier_percent.items()},
        "caps": dict(rules.caps),
        "heat_reference": rules.heat_reference,
        "max_heat_bonus_permille": rules.max_heat_bonus_permille,
    }


def _rules_from_data(value: dict) -> RuleConfig:
    return RuleConfig(
        version=value["version"],
        grade_changes=value["grade_changes"],
        tier_percent={int(k): v for k, v in value["tier_percent"].items()},
        caps=value["caps"],
        heat_reference=value["heat_reference"],
        max_heat_bonus_permille=value["max_heat_bonus_permille"],
    )


def initialize() -> None:
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_rule_snapshots (
                guild_id TEXT NOT NULL,
                rumor_id INTEGER NOT NULL,
                rules_json TEXT NOT NULL,
                rules_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, rumor_id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_locked_plans (
                guild_id TEXT NOT NULL,
                rumor_id INTEGER NOT NULL,
                phase TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                plan_json TEXT NOT NULL,
                plan_hash TEXT NOT NULL,
                rules_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, rumor_id, phase, character_id)
            )
        """)


def _key(guild_id: str | int, rumor_id: int) -> tuple[str, int]:
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("伺服器或案件編號無效")
    return gid, rumor_id


def _assert_case(db, gid: str, rid: int, cid: int | None = None) -> None:
    row = db.execute(
        "SELECT character_id FROM rp_company_rumors WHERE guild_id=? AND id=?",
        (gid, rid),
    ).fetchone()
    if row is None:
        raise ValueError("傳聞案件不存在")
    if cid is not None and int(row["character_id"]) != cid:
        raise ValueError("獎懲角色與案件主要當事人不符")


def lock_case_rules(guild_id: str | int, rumor_id: int,
                    rules: RuleConfig, *, trusted: bool = False) -> str:
    """在建立案件的同一業務流程固定規則；相同內容重試安全。"""
    if not trusted:
        raise PermissionError("只有可信任的案件建立器能鎖定規則")
    gid, rid = _key(guild_id, rumor_id)
    payload = _rule_data(rules)
    encoded, digest = _json(payload), _digest(payload)
    initialize()
    with transaction() as db:
        _assert_case(db, gid, rid)
        db.execute("""
            INSERT OR IGNORE INTO rp_rumor_rule_snapshots
            (guild_id,rumor_id,rules_json,rules_hash,created_at)
            VALUES (?,?,?,?,?)
        """, (gid, rid, encoded, digest, int(time.time())))
        row = db.execute("""
            SELECT rules_json,rules_hash FROM rp_rumor_rule_snapshots
            WHERE guild_id=? AND rumor_id=?
        """, (gid, rid)).fetchone()
        if row["rules_json"] != encoded or row["rules_hash"] != digest:
            raise ValueError("案件規則已鎖定，不得以新版本覆蓋")
    return digest


def load_case_rules(guild_id: str | int, rumor_id: int) -> RuleConfig:
    """載入當初的規則快照，先驗證內容未改變。"""
    gid, rid = _key(guild_id, rumor_id)
    with get_connection() as db:
        row = db.execute("""
            SELECT rules_json,rules_hash FROM rp_rumor_rule_snapshots
            WHERE guild_id=? AND rumor_id=?
        """, (gid, rid)).fetchone()
        if row is None:
            raise LookupError("此案沒有規則快照；舊案須人工審核，不自動補填")
        data = json.loads(row["rules_json"])
        if _digest(data) != row["rules_hash"] or _json(data) != row["rules_json"]:
            raise RuntimeError("案件規則快照完整性檢查失敗")
        rules = _rules_from_data(data)
        _validate_rules(rules)
        return rules


def plan_from_locked_rules(guild_id: str | int, rumor_id: int,
                           character_id: int, grade: str,
                           tier: int, heat: int) -> RewardPlan:
    """只用案件既有規則快照計算；上游必須驗證評級等真實性。"""
    rules = load_case_rules(guild_id, rumor_id)
    return make_reward_plan(guild_id, rumor_id, character_id,
                            grade, tier, heat, rules)


def _plan_data(plan: RewardPlan) -> dict:
    return asdict(plan)


def lock_reward_plan(plan: RewardPlan, *, phase: str = "final",
                     trusted: bool = False) -> str:
    """保存正式方案，拒絕同一案件/階段/角色的不同內容。"""
    if not trusted:
        raise PermissionError("只有可信任的結算器能鎖定方案")
    if not isinstance(plan, RewardPlan):
        raise TypeError("必須傳入 RewardPlan")
    if not phase or len(phase) > 40 or not all(
        c.isascii() and (c.isalnum() or c in "_-") for c in phase
    ):
        raise ValueError("結算階段識別碼無效")
    gid, rid = _key(plan.guild_id, plan.rumor_id)
    # 從儲存的快照重算 fingerprint，禁止手動偽造 RewardPlan 內容。
    rules = load_case_rules(gid, rid)
    expected = make_reward_plan(gid, rid, plan.character_id,
                                plan.grade, plan.tier, plan.heat, rules)
    if expected != plan:
        raise ValueError("方案與案件鎖定的規則不符")
    data = _plan_data(plan)
    encoded, digest = _json(data), _digest(data)
    rule_hash = _digest(_rule_data(rules))
    initialize()
    with transaction() as db:
        _assert_case(db, gid, rid, plan.character_id)
        db.execute("""
            INSERT OR IGNORE INTO rp_rumor_locked_plans
            (guild_id,rumor_id,phase,character_id,plan_json,plan_hash,rules_hash,created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (gid, rid, phase, plan.character_id, encoded, digest,
              rule_hash, int(time.time())))
        row = db.execute("""
            SELECT plan_json,plan_hash,rules_hash FROM rp_rumor_locked_plans
            WHERE guild_id=? AND rumor_id=? AND phase=? AND character_id=?
        """, (gid, rid, phase, plan.character_id)).fetchone()
        if (row["plan_json"] != encoded or row["plan_hash"] != digest
                or row["rules_hash"] != rule_hash):
            raise ValueError("正式獎懲方案已鎖定，不允許更改或重算覆蓋")
    return digest


def load_reward_plan(guild_id: str | int, rumor_id: int,
                     character_id: int, *, phase: str = "final") -> RewardPlan:
    """讀取固定方案並進行完整性驗證；不會發放獎勵。"""
    gid, rid = _key(guild_id, rumor_id)
    with get_connection() as db:
        row = db.execute("""
            SELECT plan_json,plan_hash,rules_hash FROM rp_rumor_locked_plans
            WHERE guild_id=? AND rumor_id=? AND phase=? AND character_id=?
        """, (gid, rid, phase, character_id)).fetchone()
        if row is None:
            raise LookupError("沒有已核准的結算方案")
        data = json.loads(row["plan_json"])
        if _json(data) != row["plan_json"] or _digest(data) != row["plan_hash"]:
            raise RuntimeError("獎懲方案完整性檢查失敗")
    rules = load_case_rules(gid, rid)
    if _digest(_rule_data(rules)) != row["rules_hash"]:
        raise RuntimeError("方案所屬規則版本與快照不一致")
    actual = RewardPlan(**data)
    expected = make_reward_plan(gid, rid, character_id,
                                actual.grade, actual.tier, actual.heat, rules)
    if actual != expected:
        raise RuntimeError("方案內容與鎖定規則計算結果不一致")
    return actual
