
"""Discord RP｜稱號效果系統 V3。

功能：
1. 首次穿戴後每滿24小時結算，最多七次。
2. 卸下不暫停計時、不重新計算期限。
3. 到期當下未穿戴，該輪永久錯過。
4. 同一天、同數值只取最高加成。
5. 已領取較低加成時，只補發差額。
6. 全部每日數值透過 core.stat_rewards 發放。
7. 隨機加成禁止影響指數及追蹤數據。
8. 保留所有通用商城折扣與期限規則。
9. 多種折扣只取最高優惠，不疊乘。
10. 支援取得生效、穿戴生效及永久折扣。

部署注意：
不要同時載入舊版 achievement_daily_metrics.py，
否則兩個結算器會競爭處理相同資料。
"""

from __future__ import annotations

import hashlib
import json
import logging
from threading import RLock

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR

from discord.ext import commands

from core.database import get_connection, transaction
from core.stat_rewards import (
    init_db as init_stat_rewards,
    grant_stats,
)
from modules.achievement_core import (
    init_db as init_core,
)


LOG = logging.getLogger(__name__)

MAX_CYCLES = 7
DEFAULT_DURATION_HOURS = 168
SETTLEMENT_LOCK = RLock()


# ==========================================
# 正式數值名稱
# ==========================================

ATTRIBUTE_NAMES = {
    "heat": "流量熱度",
    "business_value": "商業價值",
    "favor": "路人緣",
    "pr": "公關",
    "appearance": "容貌",
    "intelligence": "才智",
    "fans": "粉絲",
    "recognition": "大眾認知度",
}

EXTERNAL_STATS = {
    "fans",
    "recognition",
}

SUPPORTED_DAILY_STATS = set(ATTRIBUTE_NAMES)

# 隨機池特別排除影響指數與追蹤數據。
RANDOM_POOL = (
    "heat",
    "business_value",
    "favor",
    "pr",
    "appearance",
    "intelligence",
)

SUPPORTED_DISCOUNT_SCOPES = {
    "all",
    "category",
    "brand",
    "shop",
    "faction",
    "event",
}


# ==========================================
# 時間處理
# ==========================================

def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value))

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def iso(value):
    return parse_time(value).isoformat(
        timespec="seconds"
    )


# ==========================================
# 資料庫
# ==========================================

def init_db():
    init_core()
    init_stat_rewards()

    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_title_effect_cycles (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                achievement_id INTEGER NOT NULL,
                cycle_number INTEGER NOT NULL,
                due_at TEXT NOT NULL,
                state TEXT NOT NULL
                    CHECK(state IN ('missed', 'queued')),
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


# ==========================================
# 穿戴歷史檢查
# ==========================================

def worn_at(
    db,
    guild_id,
    character_id,
    achievement_id,
    moment,
):
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
# 固定隨機選擇
# ==========================================

def deterministic_choice(
    guild_id,
    character_id,
    achievement_id,
    cycle_number,
    options,
):
    options = sorted(set(options))

    if not options:
        return None

    source = (
        f"{guild_id}:"
        f"{character_id}:"
        f"{achievement_id}:"
        f"{cycle_number}"
    )

    digest = hashlib.sha256(
        source.encode("utf-8")
    ).digest()

    number = int.from_bytes(
        digest[:8],
        "big",
    )

    return options[number % len(options)]


# ==========================================
# 每日效果計算
# ==========================================

def calculate_daily_effect(
    guild_id,
    character_id,
    achievement_id,
    cycle_number,
    definition,
):
    effect_type = definition.get("type", "none")

    if effect_type == "daily":
        values = definition.get("stats", {})

        if not isinstance(values, dict):
            return {}

        result = {}

        for key, amount in values.items():
            if (
                key in SUPPORTED_DAILY_STATS
                and type(amount) is int
                and amount > 0
            ):
                result[key] = amount

        return result

    if effect_type == "random_daily":
        amount = definition.get("amount", 0)

        if (
            type(amount) is not int
            or amount <= 0
        ):
            return {}

        requested = definition.get(
            "pool",
            RANDOM_POOL,
        )

        if not isinstance(requested, (list, tuple)):
            return {}

        raw_excluded = definition.get("exclude", [])

        if not isinstance(
            raw_excluded,
            (list, tuple, set),
        ):
            raw_excluded = []

        excluded = set(raw_excluded)

        options = [
            key
            for key in requested
            if key in RANDOM_POOL
            and key not in excluded
        ]

        selected = deterministic_choice(
            guild_id,
            character_id,
            achievement_id,
            cycle_number,
            options,
        )

        if selected is None:
            return {}

        return {selected: amount}

    return {}


# ==========================================
# 同日只保留最高獎勵
# ==========================================

def record_best_daily(
    db,
    guild_id,
    character_id,
    date_key,
    stat_key,
    amount,
    achievement_id,
):
    if stat_key not in SUPPORTED_DAILY_STATS:
        return False

    if type(amount) is not int or amount <= 0:
        return False

    gid = str(guild_id)
    cid = int(character_id)

    row = db.execute(
        """
        SELECT granted_amount
        FROM rp_title_daily_claims
        WHERE guild_id=?
          AND character_id=?
          AND cycle_key=?
          AND stat_key=?
        """,
        (gid, cid, date_key, stat_key),
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
                gid,
                cid,
                date_key,
                stat_key,
                amount,
                int(achievement_id),
            ),
        )
        return True

    if int(row["granted_amount"]) >= amount:
        return False

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
            amount,
            int(achievement_id),
            gid,
            cid,
            date_key,
            stat_key,
        ),
    )

    return True


# ==========================================
# 建立到期輪次
# ==========================================

def queue_due_cycles(
    guild_id,
    *,
    character_id=None,
    now=None,
):
    init_db()

    moment = parse_time(now or utc_now())
    gid = str(guild_id)

    result = {
        "processed": 0,
        "queued": 0,
        "missed": 0,
    }

    with transaction() as db:
        query = """
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
            query += " AND e.character_id=?"
            params.append(int(character_id))

        rows = db.execute(query, params).fetchall()

        for row in rows:
            cid = int(row["character_id"])
            aid = int(row["achievement_id"])

            first = parse_time(
                row["first_activated_at"]
            )

            try:
                effect = json.loads(
                    row["effect_json"] or "{}"
                )
            except (ValueError, TypeError):
                LOG.warning(
                    "稱號效果 JSON 錯誤：%s",
                    aid,
                )
                continue

            if not isinstance(effect, dict):
                continue

            if effect.get("type") not in (
                "daily",
                "random_daily",
            ):
                continue

            for cycle in range(1, MAX_CYCLES + 1):
                due = first + timedelta(
                    hours=cycle * 24
                )

                if due > moment:
                    break

                existing = db.execute(
                    """
                    SELECT 1
                    FROM rp_title_effect_cycles
                    WHERE guild_id=?
                      AND character_id=?
                      AND achievement_id=?
                      AND cycle_number=?
                    """,
                    (gid, cid, aid, cycle),
                ).fetchone()

                if existing is not None:
                    continue

                eligible = worn_at(
                    db,
                    gid,
                    cid,
                    aid,
                    due,
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
                        iso(due),
                        state,
                    ),
                )

                result["processed"] += 1

                if not eligible:
                    result["missed"] += 1
                    continue

                result["queued"] += 1

                bonuses = calculate_daily_effect(
                    gid,
                    cid,
                    aid,
                    cycle,
                    effect,
                )

                date_key = due.date().isoformat()

                for key, amount in bonuses.items():
                    record_best_daily(
                        db,
                        gid,
                        cid,
                        date_key,
                        key,
                        amount,
                        aid,
                    )

    return result


# ==========================================
# 讀取單筆待發加成
# ==========================================

def read_daily_claim(
    guild_id,
    character_id,
    cycle_key,
    stat_key,
):
    with get_connection() as db:
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
                str(guild_id),
                int(character_id),
                str(cycle_key),
                str(stat_key),
            ),
        ).fetchone()

        if claim is None:
            return None

        paid = db.execute(
            """
            SELECT paid_amount
            FROM rp_title_daily_paid
            WHERE guild_id=?
              AND character_id=?
              AND cycle_key=?
              AND stat_key=?
            """,
            (
                str(guild_id),
                int(character_id),
                str(cycle_key),
                str(stat_key),
            ),
        ).fetchone()

    return {
        "target": int(claim["granted_amount"]),
        "state": claim["state"],
        "previous": (
            int(paid["paid_amount"])
            if paid is not None
            else 0
        ),
    }


# ==========================================
# 保存已發最高金額
# ==========================================

def save_daily_paid(
    db,
    guild_id,
    character_id,
    cycle_key,
    stat_key,
    target,
):
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
            paid_amount=MAX(
                paid_amount,
                excluded.paid_amount
            )
        """,
        (
            str(guild_id),
            int(character_id),
            str(cycle_key),
            str(stat_key),
            int(target),
        ),
    )


# ==========================================
# 單筆每日獎勵發放
# ==========================================

def settle_one_daily(
    guild_id,
    character_id,
    cycle_key,
    stat_key,
):
    """以差額方式發放每日最高獎勵。

    例如：
    原本最高10，已領10。
    後來最高改為20，只補發10。

    每次差額獨立使用固定 source_id，
    即使發獎後、標記前中斷，也能安全重試。

    本函式使用程序內鎖。
    正式部署請維持單一結算程序，
    避免多個 Railway 副本同時結算。
    """

    if stat_key not in ATTRIBUTE_NAMES:
        return "skipped"

    gid = str(guild_id)
    cid = int(character_id)
    cycle_key = str(cycle_key)

    with SETTLEMENT_LOCK:
        claim = read_daily_claim(
            gid,
            cid,
            cycle_key,
            stat_key,
        )

        if claim is None:
            return "skipped"

        if claim["state"] != "pending":
            return "already_done"

        target = claim["target"]
        previous = claim["previous"]

        if target < 0 or previous < 0:
            raise ValueError(
                "每日加成金額不可為負數。"
            )

        delta = max(0, target - previous)

        if delta > 0:
            source_id = (
                f"title_daily:"
                f"{gid}:"
                f"{cid}:"
                f"{cycle_key}:"
                f"{stat_key}:"
                f"{previous}:"
                f"{target}"
            )

            # 此處由統一核心保護所有數值。
            # 每日加成不允許影響指數。
            #
            # 固定與隨機結果均已在
            # calculate_daily_effect 內驗證
            # 可發放數值清單。
            grant_stats(
                gid,
                cid,
                {
                    ATTRIBUTE_NAMES[stat_key]: delta
                },
                source="system_rule",
                source_id=source_id,
                reason=(
                    f"稱號每日效果 "
                    f"{cycle_key} "
                    f"{ATTRIBUTE_NAMES[stat_key]}"
                    f" +{delta}"
                ),
                random=False,
                purchased=False,
            )

        # 發放成功後才更新已發記錄。
        # 若此前機器人中斷，下次以相同
        # source_id 重試，不會重複入帳。
        with transaction() as db:
            current = db.execute(
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
                    cycle_key,
                    stat_key,
                ),
            ).fetchone()

            if current is None:
                return "changed"

            save_daily_paid(
                db,
                gid,
                cid,
                cycle_key,
                stat_key,
                target,
            )

            # 若結算期間出現更高額獎勵，
            # 保留 pending，下次補足差額。
            if (
                current["state"] == "pending"
                and int(current["granted_amount"])
                <= target
            ):
                db.execute(
                    """
                    UPDATE rp_title_daily_claims
                    SET state='done'
                    WHERE guild_id=?
                      AND character_id=?
                      AND cycle_key=?
                      AND stat_key=?
                      AND state='pending'
                      AND granted_amount<=?
                    """,
                    (
                        gid,
                        cid,
                        cycle_key,
                        stat_key,
                        target,
                    ),
                )

                return "done"

        return "changed"


# ==========================================
# 批次發放
# ==========================================

def grant_pending_daily(
    guild_id,
    *,
    character_id=None,
):
    init_db()

    gid = str(guild_id)

    result = {
        "done": [],
        "pending": [],
        "errors": [],
    }

    query = """
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
        query += " AND character_id=?"
        params.append(int(character_id))

    query += """
        ORDER BY
            character_id,
            cycle_key,
            stat_key
    """

    with get_connection() as db:
        rows = db.execute(
            query,
            params,
        ).fetchall()

    for item in rows:
        cid = int(item["character_id"])
        date_key = item["cycle_key"]
        stat_key = item["stat_key"]

        try:
            outcome = settle_one_daily(
                gid,
                cid,
                date_key,
                stat_key,
            )

            if outcome == "done":
                result["done"].append(
                    (cid, date_key, stat_key)
                )
            else:
                result["pending"].append(
                    (cid, date_key, stat_key)
                )

        except Exception as exc:
            LOG.exception(
                "稱號每日加成結算失敗："
                "guild=%s character=%s "
                "date=%s stat=%s",
                gid,
                cid,
                date_key,
                stat_key,
            )

            result["errors"].append({
                "character_id": cid,
                "cycle_key": date_key,
                "stat_key": stat_key,
                "error": str(exc),
            })

    return result


# ==========================================
# 執行完整結算
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
# 通用商城折扣解析
# 以下保留 V2 的折扣功能
# ==========================================

def extract_discounts(effect):
    """支援舊格式與新版多折扣格式。"""

    if not isinstance(effect, dict):
        return []

    result = []

    if effect.get("type") == "shop_discount":
        result.append(effect)

    extra = effect.get(
        "discounts",
        [],
    )

    if isinstance(extra, list):
        result.extend(
            item
            for item in extra
            if isinstance(item, dict)
            and item.get("type")
            == "shop_discount"
        )

    return result


def discount_multiplier(discount):
    """將折扣轉換為付款倍率。"""

    for key in (
        "percent_off",
        "discount_percent",
    ):
        if key not in discount:
            continue

        try:
            percent = Decimal(
                str(discount[key])
            )
        except Exception:
            return None

        if not (
            Decimal("0") <= percent
            < Decimal("100")
        ):
            return None

        return (
            Decimal("100") - percent
        ) / Decimal("100")

    try:
        rate = Decimal(
            str(discount.get("rate", 1))
        )
    except Exception:
        return None

    if not (
        Decimal("0") < rate
        <= Decimal("1")
    ):
        return None

    return rate


# ==========================================
# 折扣商品範圍
# ==========================================

def discount_matches(
    discount,
    *,
    shop_type=None,
    category_id=None,
    brand_id=None,
    shop_id=None,
    faction_id=None,
    event_id=None,
):
    scope = discount.get(
        "scope",
        "all",
    )

    target = discount.get(
        "target",
        discount.get("target_id"),
    )

    if scope not in SUPPORTED_DISCOUNT_SCOPES:
        return False

    if scope == "all":
        return True

    if target is None or str(target).strip() == "":
        return False

    target = str(target)

    if scope == "category":
        return (
            category_id is not None
            and str(category_id) == target
        )

    if scope == "brand":
        return (
            brand_id is not None
            and str(brand_id) == target
        )

    if scope == "shop":
        return (
            shop_id is not None
            and str(shop_id) == target
        )

    if scope == "faction":
        return (
            shop_type == "faction"
            and faction_id is not None
            and str(faction_id) == target
        )

    if scope == "event":
        return (
            event_id is not None
            and str(event_id) == target
        )

    return False


# ==========================================
# 折扣有效期限
# ==========================================

def discount_is_active(
    discount,
    *,
    now,
    obtained_at,
    first_activated_at,
    equipped,
    historical_equipped,
):
    """支援取得生效、穿戴生效及永久折扣。"""

    activation = discount.get(
        "activation",
        "equipped",
    )

    if activation not in (
        "equipped",
        "owned",
    ):
        return False

    if activation == "equipped":
        if not (
            equipped
            and historical_equipped
        ):
            return False

        start = first_activated_at

    else:
        start = obtained_at

    if start is None:
        return False

    start = parse_time(start)

    if now < start:
        return False

    duration = discount.get(
        "duration_hours",
        DEFAULT_DURATION_HOURS,
    )

    if (
        duration is None
        or duration == "permanent"
    ):
        return True

    try:
        hours = int(duration)
    except (TypeError, ValueError):
        return False

    if hours <= 0:
        return False

    expires = start + timedelta(
        hours=hours
    )

    return now < expires


# ==========================================
# 取得角色最佳商城折扣
# ==========================================

def get_shop_discount_detail(
    guild_id,
    character_id,
    *,
    now=None,
    shop_type="general",
    category_id=None,
    brand_id=None,
    shop_id=None,
    faction_id=None,
    event_id=None,
):
    """折扣只取最高優惠，不疊加。"""

    init_db()

    moment = parse_time(
        now or utc_now()
    )

    gid = str(guild_id)
    cid = int(character_id)

    best = {
        "rate": Decimal("1"),
        "percent_off": Decimal("0"),
        "title": None,
        "code": None,
    }

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT
                d.id AS achievement_id,
                d.code,
                d.name,
                d.effect_json,
                a.obtained_at,
                COALESCE(e.equipped, 0)
                    AS equipped,
                e.first_activated_at

            FROM rp_character_achievements a

            JOIN rp_achievement_definitions d
              ON d.id=a.achievement_id
             AND d.guild_id=a.guild_id

            LEFT JOIN rp_title_equipment e
              ON e.guild_id=a.guild_id
             AND e.character_id=a.character_id
             AND e.achievement_id=a.achievement_id

            WHERE a.guild_id=?
              AND a.character_id=?
              AND d.enabled=1
            """,
            (gid, cid),
        ).fetchall()

        for row in rows:
            try:
                effect = json.loads(
                    row["effect_json"] or "{}"
                )
            except (TypeError, ValueError):
                continue

            discounts = extract_discounts(
                effect
            )

            if not discounts:
                continue

            equipped = bool(
                row["equipped"]
            )

            historical = False

            if equipped:
                historical = worn_at(
                    db,
                    gid,
                    cid,
                    row["achievement_id"],
                    moment,
                )

            for discount in discounts:
                if not discount_matches(
                    discount,
                    shop_type=shop_type,
                    category_id=category_id,
                    brand_id=brand_id,
                    shop_id=shop_id,
                    faction_id=faction_id,
                    event_id=event_id,
                ):
                    continue

                if not discount_is_active(
                    discount,
                    now=moment,
                    obtained_at=row["obtained_at"],
                    first_activated_at=(
                        row["first_activated_at"]
                    ),
                    equipped=equipped,
                    historical_equipped=historical,
                ):
                    continue

                rate = discount_multiplier(
                    discount
                )

                if rate is None:
                    continue

                if rate < best["rate"]:
                    best = {
                        "rate": rate,
                        "percent_off": (
                            Decimal("1") - rate
                        ) * Decimal("100"),
                        "title": row["name"],
                        "code": row["code"],
                    }

    return best


# ==========================================
# 舊商城倍率接口
# ==========================================

def get_shop_discount(
    guild_id,
    character_id,
    *,
    now=None,
    shop_type="general",
    category_id=None,
    brand_id=None,
    shop_id=None,
    faction_id=None,
    event_id=None,
):
    detail = get_shop_discount_detail(
        guild_id,
        character_id,
        now=now,
        shop_type=shop_type,
        category_id=category_id,
        brand_id=brand_id,
        shop_id=shop_id,
        faction_id=faction_id,
        event_id=event_id,
    )

    return float(
        detail["rate"]
    )


# ==========================================
# 計算商城實付價格
# ==========================================

def calculate_discounted_price(
    guild_id,
    character_id,
    original_price,
    *,
    now=None,
    shop_type="general",
    category_id=None,
    brand_id=None,
    shop_id=None,
    faction_id=None,
    event_id=None,
):
    if (
        type(original_price) is not int
        or original_price < 0
    ):
        raise ValueError(
            "商品原價必須是非負整數。"
        )

    detail = get_shop_discount_detail(
        guild_id,
        character_id,
        now=now,
        shop_type=shop_type,
        category_id=category_id,
        brand_id=brand_id,
        shop_id=shop_id,
        faction_id=faction_id,
        event_id=event_id,
    )

    total = (
        Decimal(original_price)
        * detail["rate"]
    )

    return int(
        total.to_integral_value(
            rounding=ROUND_FLOOR
        )
    )


# ==========================================
# Discord 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    LOG.info(
        "稱號每日效果 V3 與通用折扣"
        "系統已載入。"
    )
