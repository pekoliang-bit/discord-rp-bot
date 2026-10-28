
"""Discord RP｜稱號效果系統 V2。

功能：
1. 首次穿戴後每滿 24 小時結算，最多七次。
2. 卸下不暫停計時，不重新計算期限。
3. 結算時沒有穿戴，該輪永久錯過。
4. 相同日期、相同數值類型只保留最高加成。
5. 隨機加成排除影響指數和追蹤數據。
6. 支援稱號自訂商城折扣。
7. 折扣範圍包括全商城、分類、品牌、
   指定商店、陣營商店與活動商品。
8. 多種折扣同時適用時只取最高優惠。
9. 支援穿戴生效及取得即生效。
10. 支援限時或永久折扣。

重要：
此模組提供折扣計算函式。
商城必須在扣款前呼叫該函式，
折扣才會真正生效。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal, ROUND_FLOOR

from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)

from modules.achievement_core import (
    init_db as init_core,
)


LOG = logging.getLogger(__name__)

MAX_CYCLES = 7
DEFAULT_DURATION_HOURS = 168


# ==========================================
# 正式屬性
# ==========================================

ATTRIBUTE_NAMES = {
    "heat": "流量熱度",
    "business_value": "商業價值",
    "favor": "路人緣",
    "pr": "公關",
    "appearance": "容貌",
    "intelligence": "才智",
}

# 隨機加成不包含影響指數、
# 粉絲或大眾認知度等追蹤數據。
RANDOM_POOL = (
    "heat",
    "business_value",
    "favor",
    "pr",
    "appearance",
    "intelligence",
)

EXTERNAL_STATS = {
    "fans",
    "recognition",
}

SUPPORTED_DAILY_STATS = (
    set(ATTRIBUTE_NAMES)
    | EXTERNAL_STATS
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
        result = datetime.fromisoformat(
            str(value)
        )

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(
        timezone.utc
    )


def iso(value):
    return parse_time(value).isoformat(
        timespec="seconds"
    )


# ==========================================
# 資料庫
# ==========================================

def init_db():
    init_core()

    with transaction() as db:

        # 每個稱號各自七輪的紀錄。
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

        # 每個日期、每個數值類型
        # 實際已入帳的最高總量。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_title_daily_paid (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                cycle_key TEXT NOT NULL,
                stat_key TEXT NOT NULL,
                paid_amount INTEGER NOT NULL
                    DEFAULT 0,

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    cycle_key,
                    stat_key
                )
            )
        """)


# ==========================================
# 指定時刻穿戴判定
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
# 固定隨機結果
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

    return options[
        number % len(options)
    ]


# ==========================================
# 計算單次每日效果
# ==========================================

def calculate_daily_effect(
    guild_id,
    character_id,
    achievement_id,
    cycle_number,
    definition,
):
    effect_type = definition.get(
        "type",
        "none",
    )

    if effect_type == "daily":
        values = definition.get(
            "stats",
            {},
        )

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
        amount = definition.get(
            "amount",
            0,
        )

        if (
            type(amount) is not int
            or amount <= 0
        ):
            return {}

        requested = definition.get(
            "pool",
            RANDOM_POOL,
        )

        if not isinstance(
            requested,
            (list, tuple),
        ):
            return {}

        excluded = set(
            definition.get(
                "exclude",
                [],
            )
        )

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

        return {
            selected: amount
        }

    # 折扣稱號不產生每日數值獎勵。
    return {}


# ==========================================
# 同日只取最高數值
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
    row = db.execute(
        """
        SELECT granted_amount
        FROM rp_title_daily_claims

        WHERE guild_id=?
          AND character_id=?
          AND cycle_key=?
          AND stat_key=?
        """,
        (
            str(guild_id),
            int(character_id),
            date_key,
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
                date_key,
                stat_key,
                int(amount),
                int(achievement_id),
            ),
        )
        return

    if int(row["granted_amount"]) >= amount:
        return

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
            date_key,
            stat_key,
        ),
    )


# ==========================================
# 建立已到期的每日輪次
# ==========================================

def queue_due_cycles(
    guild_id,
    *,
    character_id=None,
    now=None,
):
    init_db()

    moment = parse_time(
        now or utc_now()
    )

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
            query += (
                " AND e.character_id=?"
            )
            params.append(
                int(character_id)
            )

        rows = db.execute(
            query,
            params,
        ).fetchall()

        for row in rows:
            cid = int(
                row["character_id"]
            )

            aid = int(
                row["achievement_id"]
            )

            first = parse_time(
                row["first_activated_at"]
            )

            effect = json.loads(
                row["effect_json"]
            )

            if effect.get("type") not in (
                "daily",
                "random_daily",
            ):
                continue

            for cycle in range(
                1,
                MAX_CYCLES + 1,
            ):
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
# 發放待處理每日加成
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
        query += (
            " AND character_id=?"
        )
        params.append(
            int(character_id)
        )

    with get_connection() as db:
        rows = db.execute(
            query,
            params,
        ).fetchall()

    for item in rows:
        cid = int(
            item["character_id"]
        )

        date_key = item[
            "cycle_key"
        ]

        stat_key = item[
            "stat_key"
        ]

        # 粉絲與大眾認知度留給
        # 追蹤數據適配器處理。
        if stat_key not in ATTRIBUTE_NAMES:
            result["pending"].append(
                (
                    cid,
                    date_key,
                    stat_key,
                )
            )
            continue

        try:
            with transaction() as db:

                claim = db.execute(
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
                        gid,
                        cid,
                        date_key,
                        stat_key,
                    ),
                ).fetchone()

                if (
                    claim is None
                    or claim["state"] != "pending"
                ):
                    continue

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
                        gid,
                        cid,
                        date_key,
                        stat_key,
                    ),
                ).fetchone()

                previous = (
                    int(paid["paid_amount"])
                    if paid
                    else 0
                )

                target = int(
                    claim["granted_amount"]
                )

                delta = max(
                    0,
                    target - previous,
                )

                if delta > 0:
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
                            ATTRIBUTE_NAMES[
                                stat_key
                            ],
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
                        date_key,
                        stat_key,
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
                        date_key,
                        stat_key,
                    ),
                )

            result["done"].append(
                (
                    cid,
                    date_key,
                    stat_key,
                )
            )

        except sqlite3.Error as exc:
            LOG.exception(
                "每日稱號加成發放失敗"
            )

            result["errors"].append(
                str(exc)
            )

    return result


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
# 通用折扣資料解析
# ==========================================

def extract_discounts(effect):
    """兼容兩種折扣資料格式。

    舊版：
        {"type":"shop_discount","rate":0.9}

    新版：
        {
            "type":"daily",
            "stats":{"heat":10},
            "discounts":[
                {
                    "type":"shop_discount",
                    "percent_off":20,
                    "scope":"category",
                    "target":"clothing",
                    "duration_hours":168,
                    "activation":"equipped"
                }
            ]
        }

    一個稱號可以同時有每日效果與折扣。
    """

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
    """把折扣設定轉為商品付款倍率。"""

    if "percent_off" in discount:
        try:
            percent = Decimal(
                str(discount["percent_off"])
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

    if "discount_percent" in discount:
        try:
            percent = Decimal(
                str(discount["discount_percent"])
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
# 折扣期限與生效條件
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
    """判斷折扣在指定時間是否有效。

    equipped：
        必須穿戴。
        限時效果從首次穿戴起算。

    owned：
        取得後即生效。
        限時效果從取得時起算。

    permanent：
        不設定到期時間。
    """

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

    # None 或 permanent 代表永久。
    if duration is None or duration == "permanent":
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
# 計算角色適用折扣
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
    """取得符合條件的最大折扣。

    返回：
        {
            "rate": Decimal("0.8"),
            "percent_off": 20,
            "title": "購物狂歡",
            "code": "event:shopping_festival"
        }

    注意：
    折扣取最大優惠，不連乘疊加。
    """

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
            (
                gid,
                cid,
            ),
        ).fetchall()

        for row in rows:

            try:
                effect = json.loads(
                    row["effect_json"]
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

                # 折扣倍率越小，優惠越高。
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
# 與舊商城相容的倍率函式
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
    """回傳折扣付款倍率，例如 0.8。"""

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
# 計算實際付款金額
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
    """原價乘上最優惠倍率後，無條件捨去小數。

    此函式只計算價格。
    不執行扣款、發貨或修改訂單。
    """

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
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    LOG.info(
        "稱號每日效果與通用折扣系統已載入。"
    )
