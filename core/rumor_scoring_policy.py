
"""傳聞公關評分換算規則；純後端，不是 Discord extension。

由已驗證的群主後端呼叫 register_policy/activate_policy。
每個版本不可覆寫；不得把此處的 trusted 當作 Discord 權限驗證。
政策數字為官方填入，本模組不提供未核准的預設倍率。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from core.database import get_connection, transaction


def _dump(obj):
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(blob):
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScoringPolicy:
    version: str
    ability_per_points: int
    ability_bonus_per_step: int
    complexity_penalty_per_level: int
    heat_per_points: int
    heat_modifier_per_step: int
    maximum_heat_modifier: int


def _validate(policy):
    if not isinstance(policy, ScoringPolicy):
        raise TypeError("必須提供 ScoringPolicy")

    if (
        not policy.version
        or len(policy.version) > 64
        or not all(
            c.isascii() and (c.isalnum() or c in "_-.")
            for c in policy.version
        )
    ):
        raise ValueError("版本名稱不合法")

    for key in (
        "ability_per_points",
        "ability_bonus_per_step",
        "complexity_penalty_per_level",
        "heat_per_points",
        "heat_modifier_per_step",
        "maximum_heat_modifier",
    ):
        number = getattr(policy, key)
        minimum = 1 if key.endswith("per_points") else 0

        if (
            type(number) is not int
            or not minimum <= number <= 100000
        ):
            raise ValueError(f"規則數值不合法：{key}")

    return policy


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_scoring_policies (
            guild_id TEXT NOT NULL,
            version TEXT NOT NULL,
            payload TEXT NOT NULL,
            digest TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id, version)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_scoring_active (
            guild_id TEXT PRIMARY KEY,
            version TEXT NOT NULL,
            changed_at INTEGER NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_case_scoring_policy (
            guild_id TEXT NOT NULL,
            rumor_id INTEGER NOT NULL,
            version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id, rumor_id)
        )
    """)


def _read(db, gid, version):
    row = db.execute(
        """
        SELECT payload, digest
        FROM rp_rumor_scoring_policies
        WHERE guild_id=? AND version=?
        """,
        (gid, version),
    ).fetchone()

    if row is None:
        raise LookupError("找不到指定的評分政策版本")

    if _hash(row["payload"]) != row["digest"]:
        raise RuntimeError("評分政策完整性檢查失敗")

    obj = json.loads(row["payload"])

    if _dump(obj) != row["payload"]:
        raise RuntimeError("評分政策序列化資料異常")

    return _validate(ScoringPolicy(**obj))


def register_policy(guild_id, policy, *, trusted=False):
    if not trusted:
        raise PermissionError("只有經群主驗證的後端可以登錄")

    _validate(policy)
    gid = str(guild_id).strip()

    if not gid:
        raise ValueError("伺服器編號不得空白")

    blob = _dump(vars(policy))
    digest = _hash(blob)

    with transaction() as db:
        initialize(db)

        db.execute(
            """
            INSERT OR IGNORE INTO rp_rumor_scoring_policies
            (guild_id, version, payload, digest, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                gid,
                policy.version,
                blob,
                digest,
                int(time.time()),
            ),
        )

        saved = db.execute(
            """
            SELECT payload, digest
            FROM rp_rumor_scoring_policies
            WHERE guild_id=? AND version=?
            """,
            (gid, policy.version),
        ).fetchone()

        if (
            saved["payload"] != blob
            or saved["digest"] != digest
        ):
            raise ValueError("同一版本已存在不同內容，禁止覆蓋")

    return digest


def activate_policy(guild_id, version, *, trusted=False):
    if not trusted:
        raise PermissionError("只有經群主驗證的後端可以啟用")

    gid = str(guild_id).strip()

    if not gid:
        raise ValueError("伺服器編號不得空白")

    with transaction() as db:
        initialize(db)
        _read(db, gid, version)

        db.execute(
            """
            INSERT INTO rp_rumor_scoring_active
            (guild_id, version, changed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                version=excluded.version,
                changed_at=excluded.changed_at
            """,
            (gid, version, int(time.time())),
        )


def read_active_policy_in_transaction(db, guild_id):
    gid = str(guild_id).strip()
    initialize(db)

    row = db.execute(
        """
        SELECT version
        FROM rp_rumor_scoring_active
        WHERE guild_id=?
        """,
        (gid,),
    ).fetchone()

    if row is None:
        raise LookupError("尚未啟用正式評分換算政策")

    return _read(db, gid, row["version"])


def calculate_adjustments(
    policy, *, ability, complexity, heat
):
    _validate(policy)

    if any(
        type(v) is not int
        for v in (ability, complexity, heat)
    ):
        raise ValueError("能力、複雜度、熱度須為整數")

    if (
        ability < 0
        or complexity not in range(1, 6)
        or not 0 <= heat <= 10**12
    ):
        raise ValueError("能力、複雜度、熱度不合法")

    bonus = (
        ability // policy.ability_per_points
    ) * policy.ability_bonus_per_step

    penalty = (
        complexity - 1
    ) * policy.complexity_penalty_per_level

    heat_bonus = min(
        policy.maximum_heat_modifier,
        (
            heat // policy.heat_per_points
        ) * policy.heat_modifier_per_step,
    )

    if max(bonus, penalty, heat_bonus) > 100000:
        raise ValueError("評分修正超出安全界限")

    return bonus, penalty, heat_bonus


def lock_case_policy_in_transaction(
    db, guild_id, rumor_id, *, trusted=False
):
    """在創案交易中固定評分政策；既有快照不能改寫。"""

    if not trusted:
        raise PermissionError(
            "僅可信任的案件建立流程可鎖定政策"
        )

    if not db.in_transaction:
        raise RuntimeError("必須使用創案的同一筆交易")

    gid = str(guild_id).strip()

    if (
        not gid
        or type(rumor_id) is not int
        or rumor_id <= 0
    ):
        raise ValueError("案件識別資料無效")

    policy = read_active_policy_in_transaction(db, gid)

    exists = db.execute(
        """
        SELECT 1
        FROM rp_company_rumors
        WHERE guild_id=? AND id=?
        """,
        (gid, rumor_id),
    ).fetchone()

    if exists is None:
        raise LookupError("案件不存在")

    initialize(db)

    db.execute(
        """
        INSERT OR IGNORE INTO rp_rumor_case_scoring_policy
        (guild_id, rumor_id, version, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            gid,
            rumor_id,
            policy.version,
            int(time.time()),
        ),
    )

    saved = db.execute(
        """
        SELECT version
        FROM rp_rumor_case_scoring_policy
        WHERE guild_id=? AND rumor_id=?
        """,
        (gid, rumor_id),
    ).fetchone()

    if saved["version"] != policy.version:
        raise ValueError("本案已鎖定其他政策版本")

    return policy.version


def read_case_policy_in_transaction(
    db, guild_id, rumor_id
):
    """只讀取案件建立時鎖定的政策版本。"""

    gid = str(guild_id).strip()

    if (
        not gid
        or type(rumor_id) is not int
        or rumor_id <= 0
    ):
        raise ValueError("案件識別資料無效")

    row = db.execute(
        """
        SELECT version
        FROM rp_rumor_case_scoring_policy
        WHERE guild_id=? AND rumor_id=?
        """,
        (gid, rumor_id),
    ).fetchone()

    if row is None:
        raise LookupError("案件缺少創建時的評分政策快照")

    return _read(db, gid, row["version"])
