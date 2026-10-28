
"""Discord RP｜稱號每日效果與商城折扣。

依賴：
    modules.achievement_core
    core.database

規則：
1. 首次穿戴後每滿 24 小時結算，最多七次。
2. 卸下不暫停、不重新計時。
3. 原定結算瞬間沒有穿戴，不發放也不補發。
4. 同一結算日的同類屬性效果只取最大值。
5. 隨機屬性不包含影響指數與追蹤數據。
6. 無法確認實際入帳欄位時，保留待發狀態。
7. 揮金如土在有效穿戴期間提供商城九折。

注意：
商城必須在結帳時計算折扣。
本模組不會自行修改商城價格。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)

from modules.achievement_core import (
    init_db as init_core,
)


LOG = logging.getLogger(__name__)

DURATION_HOURS = 168
MAX_CYCLES = 7

# 已知正式屬性。
# 追蹤數據如粉絲、認知度不可混入隨機屬性池。
ATTRIBUTE_NAMES = {
    "heat": "流量熱度",
    "business_value": "商業價值",
    "favor": "路人緣",
    "pr": "公關",
    "appearance": "容貌",
    "intelligence": "才智",
}

RANDOM_POOL = (
    "heat",
    "business_value",
    "favor",
    "pr",
    "appearance",
    "intelligence",
)

# 粉絲和大眾認知度須由追蹤數據模組入帳。
EXTERNAL_STATS = {
    "fans",
    "recognition",
}


# ==========================================
# 時間工具
# ==========================================

def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(value)

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(timezone.utc)


def iso(value):
    return parse_time(value).isoformat(
        timespec="seconds"
    )


# ==========================================
# 補充資料表
# ==========================================

def init_db():
    init_core()

    with transaction() as db:
        # 紀錄每個稱號每一輪是否已處理。
        # missed：原定時間未穿戴，永久錯過。
        # queued：已加入每日獎勵池。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_title_effect_cycles (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                achievement_id INTEGER NOT NULL,
                cycle_number INTEGER NOT NULL,
                due_at TEXT NOT NULL,
                state TEXT NOT NULL
                    CHECK(state IN (
                        'missed',
                        'queued'
                    )),
                processed_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    achievement_id,
                    cycle_number
                )
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_title_effect_cycles
            ON rp_title_effect_cycles (
                guild_id,
                character_id,
                due_at
            )
        """)


# ==========================================
# 穿戴歷史判定
# ==========================================

def worn_at(
    db,
    guild_id,
    character_id,
    achievement_id,
    moment,
):
    """原定時間點必須正在穿戴。"""

    target = iso(moment)

    row = db.execute(
        """
        SELECT 1
        FROM rp_title_wear_history
        WHERE guild_id=?
          AND character_id=?
          AND achievement_id=?
          AND worn_from <= ?
          AND (
              worn_until IS NULL
              OR worn_until > ?
          )
        LIMIT 1
        """,
        (
            str(guild_id),
            int(character_id),
            int(achievement_id),
            target,
            target,
        ),
    ).fetchone()

    return row is not None


# ==========================================
# 決定每日效果
# ==========================================

def deterministic_choice(
    guild_id,
    character_id,
    achievement_id,
    cycle_number,
    options,
):
    """固定隨機結果，避免重試時重抽。"""

    options = tuple(sorted(set(options)))

    if not options:
        return None

    identity = (
        f"{guild_id}:"
        f"{character_id}:"
        f"{achievement_id}:"
        f"{cycle_number}"
    )

    digest = hashlib.sha256(
        identity.encode("utf-8")
    ).digest()

    index = int.from_bytes(
        digest[:8],
        "big",
    ) % len(options)

    return options[index]


def calculate_effect(
    guild_id,
    character_id,
    achievement_id,
    cycle_number,
    definition,
):
    """回傳本輪加成：{英文屬性代碼: 數值}。"""

    effect_type = definition.get(
        "type",
        "none",
    )

    if effect_type == "daily":
        result = {}

        for key, amount in definition.get(
            "stats",
            {},
        ).items():
            if (
                key in ATTRIBUTE_NAMES
                or key in EXTERNAL_STATS
            ):
                if (
                    isinstance(amount, int)
                    and not isinstance(amount, bool)
                    and amount > 0
                ):
                    result[key] = amount

        return result

    if effect_type == "random_daily":
        amount = definition.get(
            "amount",
            0,
        )

        if (
            not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount <= 0
        ):
            return {}

        requested = definition.get(
            "pool",
            RANDOM_POOL,
        )

        excluded = set(
            definition.get("exclude", [])
        )

        # 隨機加成只使用正式白名單。
        # 不讓 influence 或 fans 進入抽選。
        choices = [
            key
            for key in requested
            if key in RANDOM_POOL
            and key not in excluded
        ]

        # 兼容既有設定中未指定 pool 的稱號。
        if not choices and "pool" not in definition:
            choices = [
                key
                for key in RANDOM_POOL
                if key not in excluded
            ]

        selected = deterministic_choice(
            guild_id,
            character_id,
            achievement_id,
            cycle_number,
            choices,
        )

        return (
            {selected: amount}
            if selected
            else {}
        )

    return {}


# ==========================================
# 同類效果去重
# ==========================================

def effect_day_key(due_at):
    """每日加成以 UTC 日期分組。

    注意：各稱號仍在各自首次穿戴後
    的第 24、48、72……小時產生資格。
    """

    return parse_time(
        due_at
    ).date().isoformat()


def record_best_effect(
    db,
    guild_id,
    character_id,
    day_key,
    stat_key,
    amount,
    achievement_id,
):
    """同一日同一屬性僅保留最大值。

    若舊值已成功發放，新值較高，
    後續只補足差額，不再次發完整數值。
    """

    row = db.execute(
        """
        SELECT
            granted_amount,
            state
        FROM rp_title_daily_claims
        WHERE guild_id=?
          AND character_id=?
          AND cycle_key=?
          AND stat_key=?
        """,
        (
            str(guild_id),
            int(character_id),
            day_key,
            stat_key,
        ),
    ).fetchone()

    if row is None:
        db.execute(
            """
            INSERT INTO rp_title_daily_claims (
                guild_id,
                character_id,
                cycle_key,
                stat_key,
                granted_amount,
                source_achievement_id,
                state
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                str(guild_id),
                int(character_id),
                day_key,
                stat_key,
                int(amount),
                int(achievement_id),
            ),
        )

        return

    if amount <= int(row["granted_amount"]):
        return

    # 不直接覆蓋已發金額，
    # 先保存新的目標最大值。
    db.execute(
        """
        UPDATE rp_title_daily_claims
        SET granted_amount=?,
            source_achievement_id=?,
            state='pending'
        WHERE guild_id=?
          AND character_id=?
          AND cycle_key=?
          AND stat_key=?
        """,
        (
            int(amount),
            int(achievement_id),
            str(guild_id),
            int(character_id),
            day_key,
            stat_key,
        ),
    )


# ==========================================
# 每輪資格計算
# ==========================================

def queue_due_cycles(
    guild_id,
    *,
    character_id=None,
    now=None,
):
    """掃描已到期的每日結算時刻。

    返回：
        processed：本次處理的輪數
        queued：符合穿戴條件的輪數
        missed：未穿戴而錯過的輪數
    """

    init_db()

    moment = parse_time(now or utc_now())

    gid = str(guild_id)

    summary = {
        "processed": 0,
        "queued": 0,
        "missed": 0,
    }

    with transaction() as db:
        sql = """
            SELECT
                e.character_id,
                e.achievement_id,
                e.first_activated_at,
                d.effect_json

            FROM rp_title_equipment e

            JOIN rp_achievement_definitions d
              ON d.id=e.achievement_id
             AND d.guild_id=e.guild_id

            WHERE e.guild_id=?
              AND e.first_activated_at IS NOT NULL
        """

        params = [gid]

        if character_id is not None:
            sql += " AND e.character_id=?"
            params.append(int(character_id))

        rows = db.execute(
            sql,
            params,
        ).fetchall()

        # 先收集資格，再按每個角色／日期／屬性
        # 記錄最高的加成。
        candidates = []

        for row in rows:
            cid = int(row["character_id"])
            aid = int(row["achievement_id"])

            first = parse_time(
                row["first_activated_at"]
            )

            definition = json.loads(
                row["effect_json"]
            )

            if definition.get("type") in (
                "none",
                "shop_discount",
            ):
                continue

            for cycle in range(
                1,
                MAX_CYCLES + 1,
            ):
                due_at = first + timedelta(
                    hours=24 * cycle
                )

                if due_at > moment:
                    break

                existing = db.execute(
                    """
                    SELECT state
                    FROM rp_title_effect_cycles
                    WHERE guild_id=?
                      AND character_id=?
                      AND achievement_id=?
                      AND cycle_number=?
                    """,
                    (
                        gid,
                        cid,
                        aid,
                        cycle,
                    ),
                ).fetchone()

                if existing is not None:
                    continue

                eligible = worn_at(
                    db,
                    gid,
                    cid,
                    aid,
                    due_at,
                )

                state = (
                    "queued"
                    if eligible
                    else "missed"
                )

                db.execute(
                    """
                    INSERT INTO rp_title_effect_cycles (
                        guild_id,
                        character_id,
                        achievement_id,
                        cycle_number,
                        due_at,
                        state
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        gid,
                        cid,
                        aid,
                        cycle,
                        iso(due_at),
                        state,
                    ),
                )

                summary["processed"] += 1

                if not eligible:
                    summary["missed"] += 1
                    continue

                summary["queued"] += 1

                values = calculate_effect(
                    gid,
                    cid,
                    aid,
                    cycle,
                    definition,
                )

                for key, amount in values.items():
                    candidates.append(
                        (
                            cid,
                            effect_day_key(due_at),
                            key,
                            amount,
                            aid,
                        )
                    )

        for (
            cid,
            day,
            stat,
            amount,
            aid,
        ) in candidates:
            record_best_effect(
                db,
                gid,
                cid,
                day,
                stat,
                amount,
                aid,
            )

    return summary


# ==========================================
# 安全發放每日屬性
# ==========================================

def init_payment_tracking():
    """保存已入帳的累計數值，以便只補差額。"""

    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_title_daily_paid (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                cycle_key TEXT NOT NULL,
                stat_key TEXT NOT NULL,
                paid_amount INTEGER NOT NULL DEFAULT 0,

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    cycle_key,
                    stat_key
                )
            )
        """)


def grant_pending_daily(
    guild_id,
    *,
    character_id=None,
):
    """發放已記錄的每日加成。

    正式屬性在同一筆 SQLite 交易更新。
    粉絲／認知度保持 pending，等待數據適配器。
    """

    init_db()
    init_payment_tracking()

    gid = str(guild_id)

    results = {
        "done": [],
        "pending": [],
        "errors": [],
    }

    sql = """
        SELECT
            character_id,
            cycle_key,
            stat_key
        FROM rp_title_daily_claims
        WHERE guild_id=?
          AND state='pending'
    """

    params = [gid]

    if character_id is not None:
        sql += " AND character_id=?"
        params.append(int(character_id))

    with get_connection() as db:
        rows = db.execute(
            sql,
            params,
        ).fetchall()

    for item in rows:
        cid = int(item["character_id"])
        day = item["cycle_key"]
        stat = item["stat_key"]

        if stat not in ATTRIBUTE_NAMES:
            results["pending"].append(
                (cid, day, stat)
            )
            continue

        try:
            with transaction() as db:
                claim = db.execute(
                    """
                    SELECT granted_amount, state
                    FROM rp_title_daily_claims
                    WHERE guild_id=?
                      AND character_id=?
                      AND cycle_key=?
                      AND stat_key=?
                    """,
                    (
                        gid,
                        cid,
                        day,
                        stat,
                    ),
                ).fetchone()

                if (
                    claim is None
                    or claim["state"] != "pending"
                ):
                    continue

                paid_row = db.execute(
                    """
                    SELECT paid_amount
                    FROM rp_title_daily_paid
                    WHERE guild_id=?
                      AND character_id=?
                      AND cycle_key=?
                      AND stat_key=?
                    """,
                    (
                        gid,
                        cid,
                        day,
                        stat,
                    ),
                ).fetchone()

                previous = (
                    int(paid_row["paid_amount"])
                    if paid_row
                    else 0
                )

                target = int(
                    claim["granted_amount"]
                )

                delta = max(
                    0,
                    target - previous,
                )

                if delta:
                    name = ATTRIBUTE_NAMES[stat]

                    # 沿用既有正式屬性表。
                    db.execute(
                        """
                        INSERT INTO attributes (
                            guild_id,
                            character_id,
                            name,
                            value
                        )
                        VALUES (?, ?, ?, ?)

                        ON CONFLICT (
                            guild_id,
                            character_id,
                            name
                        )
                        DO UPDATE SET
                            value=value+excluded.value,
                            version=version+1
                        """,
                        (
                            gid,
                            cid,
                            name,
                            delta,
                        ),
                    )

                db.execute(
                    """
                    INSERT INTO rp_title_daily_paid (
                        guild_id,
                        character_id,
                        cycle_key,
                        stat_key,
                        paid_amount
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (
                        guild_id,
                        character_id,
                        cycle_key,
                        stat_key
                    )
                    DO UPDATE SET
                        paid_amount=excluded.paid_amount
                    """,
                    (
                        gid,
                        cid,
                        day,
                        stat,
                        target,
                    ),
                )

                db.execute(
                    """
                    UPDATE rp_title_daily_claims
                    SET state='done'
                    WHERE guild_id=?
                      AND character_id=?
                      AND cycle_key=?
                      AND stat_key=?
                    """,
                    (
                        gid,
                        cid,
                        day,
                        stat,
                    ),
                )

            results["done"].append(
                (cid, day, stat)
            )

        except sqlite3.Error as exc:
            LOG.exception(
                "稱號每日加成入帳失敗"
            )
            results["errors"].append(
                (cid, day, stat, str(exc))
            )

    return results


# ==========================================
# 一次執行完整每日結算
# ==========================================

def settle_daily_effects(
    guild_id,
    *,
    character_id=None,
    now=None,
):
    queued = queue_due_cycles(
        guild_id,
        character_id=character_id,
        now=now,
    )

    granted = grant_pending_daily(
        guild_id,
        character_id=character_id,
    )

    return {
        "cycles": queued,
        "rewards": granted,
    }


# ==========================================
# 商城九折
# ==========================================

def get_shop_discount(
    guild_id,
    character_id,
    *,
    now=None,
):
    """回傳應使用的價格倍率。

    1.0 = 原價
    0.9 = 九折

    只認目前穿戴且尚未過期的效果。
    """

    init_db()

    moment = parse_time(
        now or utc_now()
    )

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT
                e.achievement_id,
                e.first_activated_at,
                e.expires_at,
                d.effect_json

            FROM rp_title_equipment e

            JOIN rp_achievement_definitions d
              ON d.id=e.achievement_id
             AND d.guild_id=e.guild_id

            WHERE e.guild_id=?
              AND e.character_id=?
              AND e.equipped=1
              AND d.enabled=1
            """,
            (
                str(guild_id),
                int(character_id),
            ),
        ).fetchall()

        best_rate = 1.0

        for row in rows:
            if (
                not row["first_activated_at"]
                or not row["expires_at"]
            ):
                continue

            if not (
                parse_time(
                    row["first_activated_at"]
                ) <= moment
                < parse_time(
                    row["expires_at"]
                )
            ):
                continue

            if not worn_at(
                db,
                guild_id,
                character_id,
                row["achievement_id"],
                moment,
            ):
                continue

            effect = json.loads(
                row["effect_json"]
            )

            if effect.get("type") != "shop_discount":
                continue

            rate = effect.get(
                "rate",
                1.0,
            )

            if (
                isinstance(rate, (int, float))
                and not isinstance(rate, bool)
                and 0 < rate <= 1
            ):
                best_rate = min(
                    best_rate,
                    float(rate),
                )

    return best_rate


def calculate_discounted_price(
    guild_id,
    character_id,
    original_price,
    *,
    now=None,
):
    """以整數金幣計價，折扣後無條件捨去小數。"""

    if (
        not isinstance(original_price, int)
        or isinstance(original_price, bool)
        or original_price < 0
    ):
        raise ValueError(
            "原價必須是非負整數。"
        )

    rate = get_shop_discount(
        guild_id,
        character_id,
        now=now,
    )

    # 固定九折使用整數運算，
    # 避免浮點誤差。
    if abs(rate - 0.9) < 0.000001:
        return original_price * 9 // 10

    if rate == 1.0:
        return original_price

    return int(
        original_price * rate
    )


# ==========================================
# Discord 模組入口
# ==========================================

async def setup(bot: commands.Bot):
    init_db()
    init_payment_tracking()

    LOG.info(
        "稱號每日效果與商城折扣模組已載入。"
    )
