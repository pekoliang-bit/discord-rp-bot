
"""傳聞規則版本登錄與啟用管理（純後端）。

安裝：core/rumor_rule_registry.py
不屬於 Discord extension。正式 Discord 管理權限由上游驗證。
本模組不會自動啟用任何獎懲規則，也不補寫舊案件。
"""
from __future__ import annotations

import hashlib
import json
import time

from core.database import get_connection, transaction
from core.rumor_reward_planner import RuleConfig, _validate_rules
from core.rumor_plan_store import lock_case_rules


def _json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _payload(rule: RuleConfig) -> dict:
    _validate_rules(rule)
    return {
        "version": rule.version,
        "grade_changes": {
            k: dict(v)
            for k, v in rule.grade_changes.items()
        },
        "tier_percent": {
            str(k): v
            for k, v in rule.tier_percent.items()
        },
        "caps": dict(rule.caps),
        "heat_reference": rule.heat_reference,
        "max_heat_bonus_permille": rule.max_heat_bonus_permille,
    }


def _hash(blob: str) -> str:
    return hashlib.sha256(
        blob.encode("utf-8")
    ).hexdigest()


def initialize() -> None:
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_rule_versions (
                guild_id TEXT NOT NULL,
                version TEXT NOT NULL,
                rules_json TEXT NOT NULL,
                rules_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, version)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_active_rules (
                guild_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                activated_at INTEGER NOT NULL
            )
        """)


def _gid(guild_id: str | int) -> str:
    value = str(guild_id).strip()
    if not value:
        raise ValueError("伺服器識別碼不可為空")
    return value


def register_rules(
    guild_id: str | int,
    rules: RuleConfig,
    *,
    trusted: bool = False,
) -> str:
    """登錄已核准規則，不會自動啟用。"""
    if not trusted:
        raise PermissionError(
            "需要可信任的官方管理端"
        )

    gid = _gid(guild_id)
    blob = _json(_payload(rules))
    digest = _hash(blob)

    initialize()

    with transaction() as db:
        db.execute("""
            INSERT OR IGNORE INTO rp_rumor_rule_versions
            (guild_id,version,rules_json,rules_hash,created_at)
            VALUES (?,?,?,?,?)
        """, (
            gid,
            rules.version,
            blob,
            digest,
            int(time.time()),
        ))

        row = db.execute("""
            SELECT rules_json,rules_hash
            FROM rp_rumor_rule_versions
            WHERE guild_id=? AND version=?
        """, (gid, rules.version)).fetchone()

        if (
            row["rules_json"] != blob
            or row["rules_hash"] != digest
        ):
            raise ValueError(
                "此版本名稱已登錄不同內容，請建立新版本"
            )

    return digest


def _decode(row) -> RuleConfig:
    blob = row["rules_json"]

    if _hash(blob) != row["rules_hash"]:
        raise RuntimeError(
            "規則版本完整性校驗失敗"
        )

    data = json.loads(blob)

    if _json(data) != blob:
        raise RuntimeError(
            "規則資料格式遭修改"
        )

    rules = RuleConfig(
        version=data["version"],
        grade_changes=data["grade_changes"],
        tier_percent={
            int(k): v
            for k, v in data["tier_percent"].items()
        },
        caps=data["caps"],
        heat_reference=data["heat_reference"],
        max_heat_bonus_permille=data[
            "max_heat_bonus_permille"
        ],
    )

    _validate_rules(rules)
    return rules


def activate_rules(
    guild_id: str | int,
    version: str,
    *,
    trusted: bool = False,
) -> None:
    """啟用已登錄版本，只影響新案件。"""
    if not trusted:
        raise PermissionError(
            "需要可信任的官方管理端"
        )

    gid = _gid(guild_id)
    initialize()

    with transaction() as db:
        row = db.execute("""
            SELECT rules_json,rules_hash
            FROM rp_rumor_rule_versions
            WHERE guild_id=? AND version=?
        """, (gid, version)).fetchone()

        if row is None:
            raise LookupError(
                "找不到此規則版本"
            )

        _decode(row)

        db.execute("""
            INSERT INTO rp_rumor_active_rules
            (guild_id,version,activated_at)
            VALUES (?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET
              version=excluded.version,
              activated_at=excluded.activated_at
        """, (
            gid,
            version,
            int(time.time()),
        ))


def get_active_rules(
    guild_id: str | int,
) -> RuleConfig:
    """取得目前啟用的規則，絕不預設套用未核准版本。"""
    gid = _gid(guild_id)
    initialize()

    with get_connection() as db:
        row = db.execute("""
            SELECT v.rules_json,v.rules_hash
            FROM rp_rumor_active_rules a
            JOIN rp_rumor_rule_versions v
              ON v.guild_id=a.guild_id
             AND v.version=a.version
            WHERE a.guild_id=?
        """, (gid,)).fetchone()

    if row is None:
        raise LookupError(
            "尚未啟用正式傳聞獎懲規則"
        )

    return _decode(row)


def snapshot_new_case(
    guild_id: str | int,
    rumor_id: int,
    *,
    trusted: bool = False,
) -> str:
    """供案件建立流程保存規則快照。

    正式上線前，應把案件新增與快照寫入
    整合在同一筆資料庫交易。

    若快照失敗，上游不可啟用該案件。
    """
    if not trusted:
        raise PermissionError(
            "需要可信任的案件建立器"
        )

    rules = get_active_rules(guild_id)

    return lock_case_rules(
        guild_id,
        rumor_id,
        rules,
        trusted=True,
    )
