"""品牌熱度、營業額及自動升級核心。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

import sqlite3

from discord.ext import commands, tasks

from core.database import get_connection, transaction
from modules.company_business import _maybe_auto_group
from modules.world_terms import term


TAIPEI = ZoneInfo("Asia/Taipei")

BRAND_LEVELS = [
    "平價",
    "小資",
    "輕奢",
    "奢侈",
    "高奢",
]

UPGRADE_REQUIREMENTS = {
    "小資": {
        "heat": 10_000,
        "revenue": 200_000,
        "stats": (1_000, 1_000, 500),
    },
    "輕奢": {
        "heat": 20_000,
        "revenue": 1_000_000,
        "stats": (2_000, 2_000, 1_000),
    },
    "奢侈": {
        "heat": 100_000,
        "revenue": 5_000_000,
        "stats": (10_000, 10_000, 5_000),
    },
    "高奢": {
        "heat": 1_000_000,
        "revenue": 25_000_000,
        "stats": (30_000, 30_000, 15_000),
    },
}

HEAT_SOURCES = {
    "consumption",
    "promotion",
    "cooperation",
    "representative",
}

DESIGN_KEYWORDS = (
    "設計",
    "服裝",
    "服飾",
    "珠寶",
    "飾品",
    "皮具",
    "皮革",
    "鞋履",
    "鞋類",
    "家具",
    "家居設計",
    "香水",
    "美妝",
    "彩妝",
)

RESTAURANT_KEYWORDS = (
    "餐廳",
    "餐飲",
    "料理",
    "咖啡廳",
    "甜點店",
    "食堂",
)

ATTRIBUTE_NAMES = (
    "流量熱度",
    "商業價值",
    "影響指數",
)


def utc_now():
    return datetime.now(timezone.utc)


def utc_text(value):
    return value.strftime("%Y-%m-%d %H:%M:%S")


def initialize():

    statements = [
        """
        CREATE TABLE IF NOT EXISTS rp_brand_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            brand_id TEXT NOT NULL,
            event_key TEXT NOT NULL,
            kind TEXT NOT NULL
                CHECK(kind IN ('sale', 'refund')),
            amount INTEGER NOT NULL CHECK(amount > 0),
            reference_key TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, event_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rp_brand_heat_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            brand_id TEXT NOT NULL,
            event_key TEXT NOT NULL,
            source TEXT NOT NULL,
            points INTEGER NOT NULL CHECK(points >= 0),
            created_at TEXT NOT NULL,
            UNIQUE(guild_id, event_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rp_brand_upgrade_runs (
            guild_id TEXT NOT NULL,
            brand_id TEXT NOT NULL,
            check_date TEXT NOT NULL,
            result TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(guild_id, brand_id, check_date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS rp_brand_notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            brand_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            delivered INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_brand_sales_window
        ON rp_brand_sales(
            guild_id, brand_id, created_at
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_brand_heat_window
        ON rp_brand_heat_events(
            guild_id, brand_id, source, created_at
        )
        """,
    ]

    with transaction() as db:
        for statement in statements:
            db.execute(statement)


def require_brand(db, guild_id, brand_id):

    row = db.execute(
        """
        SELECT *
        FROM rp_business_entities
        WHERE guild_id = ?
          AND business_id = ?
          AND kind = 'brand'
          AND status = 'active'
        """,
        (str(guild_id), str(brand_id)),
    ).fetchone()

    if row is None:
        raise ValueError("找不到已成立的品牌。")

    return row


def record_sale(
    guild_id,
    brand_id,
    event_key,
    amount,
    *,
    kind="sale",
    reference_key=None,
):
    """供正式商城結帳與退款流程呼叫。

    此函式只記錄品牌業績，不會再次扣款。
    """

    if kind not in ("sale", "refund"):
        raise ValueError("交易類型只能是 sale 或 refund。")

    amount = int(amount)

    if amount <= 0:
        raise ValueError("交易金額必須大於零。")

    with transaction() as db:
        require_brand(db, guild_id, brand_id)

        if kind == "refund":
            if not reference_key:
                raise ValueError("退款必須提供原交易編號。")

            original = db.execute(
                """
                SELECT amount
                FROM rp_brand_sales
                WHERE guild_id = ?
                  AND brand_id = ?
                  AND event_key = ?
                  AND kind = 'sale'
                """,
                (
                    str(guild_id),
                    str(brand_id),
                    str(reference_key),
                ),
            ).fetchone()

            if not original:
                raise ValueError("找不到對應的原始銷售紀錄。")

            refunded = db.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM rp_brand_sales
                WHERE guild_id = ?
                  AND brand_id = ?
                  AND kind = 'refund'
                  AND reference_key = ?
                """,
                (
                    str(guild_id),
                    str(brand_id),
                    str(reference_key),
                ),
            ).fetchone()[0]

            if refunded + amount > original["amount"]:
                raise ValueError("退款總額不可超過原始銷售金額。")

        db.execute(
            """
            INSERT INTO rp_brand_sales (
                guild_id,
                brand_id,
                event_key,
                kind,
                amount,
                reference_key,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                str(brand_id),
                str(event_key),
                kind,
                amount,
                reference_key,
                utc_text(utc_now()),
            ),
        )


def record_heat(
    guild_id,
    brand_id,
    event_key,
    source,
    points,
):
    """記錄宣傳、合作或代表曝光的正式熱度事件。"""

    if source not in HEAT_SOURCES - {"consumption"}:
        raise ValueError("不支援的熱度來源。")

    points = int(points)

    if points < 0:
        raise ValueError("熱度不可小於零。")

    with transaction() as db:
        require_brand(db, guild_id, brand_id)

        db.execute(
            """
            INSERT INTO rp_brand_heat_events (
                guild_id,
                brand_id,
                event_key,
                source,
                points,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                str(brand_id),
                str(event_key),
                source,
                points,
                utc_text(utc_now()),
            ),
        )


def brand_statistics(db, guild_id, brand_id, target_heat):

    cutoff = utc_text(utc_now() - timedelta(days=30))

    rows = db.execute(
        """
        SELECT kind, COALESCE(SUM(amount), 0) AS amount
        FROM rp_brand_sales
        WHERE guild_id = ?
          AND brand_id = ?
          AND created_at >= ?
        GROUP BY kind
        """,
        (
            str(guild_id),
            str(brand_id),
            cutoff,
        ),
    ).fetchall()

    amounts = {
        row["kind"]: row["amount"]
        for row in rows
    }

    gross = amounts.get("sale", 0)
    refunds = amounts.get("refund", 0)
    net_revenue = max(0, gross - refunds)

    # 每累積淨消費 1,000 金幣提供 1 點熱度。
    consumption_heat = net_revenue // 1000

    heat_rows = db.execute(
        """
        SELECT source, COALESCE(SUM(points), 0) AS points
        FROM rp_brand_heat_events
        WHERE guild_id = ?
          AND brand_id = ?
          AND created_at >= ?
        GROUP BY source
        """,
        (
            str(guild_id),
            str(brand_id),
            cutoff,
        ),
    ).fetchall()

    sources = {
        row["source"]: row["points"]
        for row in heat_rows
    }

    sources["consumption"] = consumption_heat

    # 各來源最多占下一級目標的35%。
    source_limit = target_heat * 35 // 100

    effective = {
        source: min(
            max(0, sources.get(source, 0)),
            source_limit,
        )
        for source in HEAT_SOURCES
    }

    total_heat = sum(effective.values())

    return {
        "gross": gross,
        "refunds": refunds,
        "revenue": net_revenue,
        "raw_heat": sources,
        "effective_heat": effective,
        "total_heat": total_heat,
    }


def character_stats(db, guild_id, character_id):

    rows = db.execute(
        """
        SELECT name, value
        FROM attributes
        WHERE guild_id = ?
          AND character_id = ?
          AND name IN (
              '流量熱度',
              '商業價值',
              '影響指數'
          )
        """,
        (
            str(guild_id),
            int(character_id),
        ),
    ).fetchall()

    return {
        row["name"]: row["value"]
        for row in rows
    }


def eligible_industry(industry, target_level):

    industry = str(industry)

    if target_level == "高奢":
        return any(
            keyword in industry
            for keyword in DESIGN_KEYWORDS
        )

    if target_level == "奢侈":
        return True

    return True


def check_upgrade(guild_id, brand_id, *, check_date=None):
    """每日每品牌最多處理一次，並且最多升一級。"""

    now = datetime.now(TAIPEI)

    if check_date is None:
        check_date = now.date().isoformat()

    with transaction() as db:
        brand = require_brand(db, guild_id, brand_id)

        previous_run = db.execute(
            """
            SELECT result
            FROM rp_brand_upgrade_runs
            WHERE guild_id = ?
              AND brand_id = ?
              AND check_date = ?
            """,
            (
                str(guild_id),
                str(brand_id),
                check_date,
            ),
        ).fetchone()

        if previous_run:
            return previous_run["result"]

        current_level = brand["brand_tier"]

        if current_level not in BRAND_LEVELS:
            result = "invalid_tier"

        elif current_level == "高奢":
            result = "maximum"

        else:
            next_level = BRAND_LEVELS[
                BRAND_LEVELS.index(current_level) + 1
            ]

            requirements = UPGRADE_REQUIREMENTS[next_level]

            founded_at = datetime.fromisoformat(
                brand["founded_at"]
            ).replace(tzinfo=timezone.utc)

            age = utc_now() - founded_at

            stats = brand_statistics(
                db,
                guild_id,
                brand_id,
                requirements["heat"],
            )

            qualified = True
            reasons = []

            if age < timedelta(days=14):
                qualified = False
                reasons.append("成立未滿14天")

            if not eligible_industry(
                brand["industry"],
                next_level,
            ):
                qualified = False
                reasons.append("產業不符合升級資格")

            if (
                stats["total_heat"]
                < requirements["heat"]
            ):
                qualified = False
                reasons.append("品牌熱度不足")

            if (
                stats["revenue"]
                < requirements["revenue"]
            ):
                qualified = False
                reasons.append("30天淨營業額不足")

            # 所有等級皆檢查持有人；
            # 已指定代表時也要檢查代表。
            characters = [
                brand["owner_character_id"]
            ]

            representative = brand[
                "representative_character_id"
            ]

            if representative is not None:
                characters.append(representative)

            if (
                next_level in ("奢侈", "高奢")
                and representative is None
            ):
                qualified = False
                reasons.append("尚未指定品牌代表")

            for character_id in characters:
                values = character_stats(
                    db,
                    guild_id,
                    character_id,
                )

                for name, minimum in zip(
                    ATTRIBUTE_NAMES,
                    requirements["stats"],
                ):
                    if values.get(name, 0) < minimum:
                        qualified = False
                        reasons.append(
                            f"角色#{character_id}的{name}不足"
                        )

            if qualified:
                db.execute(
                    """
                    UPDATE rp_business_entities
                    SET brand_tier = ?,
                        category = ?
                    WHERE guild_id = ?
                      AND business_id = ?
                    """,
                    (
                        next_level,
                        next_level,
                        str(guild_id),
                        str(brand_id),
                    ),
                )

                db.execute(
                    """
                    INSERT INTO rp_business_audit (
                        guild_id,
                        business_id,
                        action,
                        actor_id,
                        details
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(guild_id),
                        str(brand_id),
                        "brand_upgrade",
                        "system",
                        f"{current_level} → {next_level}",
                    ),
                )

                db.execute(
                    """
                    INSERT INTO rp_brand_notices (
                        guild_id,
                        brand_id,
                        event_type,
                        content
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(guild_id),
                        str(brand_id),
                        "upgrade",
                        (
                            f"🎉 {term(guild_id, 'brand')}「{brand['name']}」"
                            f"已從{current_level}升級為{next_level}！"
                        ),
                    ),
                )

                if next_level == "高奢":
                    _maybe_auto_group(
                        db,
                        str(guild_id),
                        str(brand["owner_id"]),
                        brand["owner_character_id"],
                    )

                result = f"upgraded:{next_level}"

            else:
                result = "pending:" + "、".join(reasons)

        db.execute(
            """
            INSERT INTO rp_brand_upgrade_runs (
                guild_id,
                brand_id,
                check_date,
                result
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                str(guild_id),
                str(brand_id),
                check_date,
                result,
            ),
        )

        return result


def check_all_brands():

    with get_connection() as db:
        brands = db.execute(
            """
            SELECT guild_id, business_id
            FROM rp_business_entities
            WHERE kind = 'brand'
              AND status = 'active'
            """
        ).fetchall()

        targets = [
            (
                row["guild_id"],
                row["business_id"],
            )
            for row in brands
        ]

    results = {}

    for guild_id, brand_id in targets:
        try:
            results[(guild_id, brand_id)] = check_upgrade(
                guild_id,
                brand_id,
            )
        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            results[(guild_id, brand_id)] = f"error:{exc}"

    return results


class BrandProgression(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.daily_upgrade.start()

    def cog_unload(self):
        self.daily_upgrade.cancel()

    @tasks.loop(
        time=time(
            hour=0,
            minute=0,
            tzinfo=TAIPEI,
        )
    )
    async def daily_upgrade(self):

        import asyncio

        await asyncio.to_thread(check_all_brands)

    @daily_upgrade.before_loop
    async def before_daily_upgrade(self):
        await self.bot.wait_until_ready()


async def setup(bot):

    initialize()

    await bot.add_cog(
        BrandProgression(bot)
    )


def upgrade_history(guild_id, brand_id, limit=10):
    """向品牌介面提供升級審核紀錄。"""
    from modules.company_brand_reporting import recent_upgrade_checks
    return recent_upgrade_checks(guild_id, brand_id, limit)
