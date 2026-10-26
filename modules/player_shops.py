
"""個人商店：七級規模、申請審核、保證金與營業額考核。"""
from __future__ import annotations

import asyncio
import calendar
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import (
    transaction,
    get_connection,
    get_or_create_account,
    account_balance,
)
from core.economy import _payment
from core.permissions import (
    Actor,
    require_admin,
    InvalidState,
    PermissionDenied,
)

DEFAULT_TIERS = [
    (1, 10000, 2),
    (2, 50000, 7),
    (3, 100000, 10),
    (4, 200000, 15),
    (5, 300000, 20),
    (6, 400000, 30),
    (7, 500000, 50),
]


def stamp(dt=None):
    return (
        dt or datetime.now(timezone.utc)
    ).isoformat(timespec="seconds")


def add_month(dt):
    y = dt.year + (dt.month // 12)
    m = dt.month % 12 + 1
    d = min(
        dt.day,
        calendar.monthrange(y, m)[1],
    )
    return dt.replace(
        year=y,
        month=m,
        day=d,
    )


def initialize():
    with transaction() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS personal_shop_tiers (
            guild_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            deposit INTEGER NOT NULL
                CHECK(deposit >= 0),
            employee_limit INTEGER NOT NULL
                CHECK(employee_limit >= 0),
            PRIMARY KEY(guild_id, level)
        );

        CREATE TABLE IF NOT EXISTS personal_shop_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            level INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            applied_at TEXT NOT NULL,
            reviewed_by TEXT,
            reviewed_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            UNIQUE(guild_id, id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS
            personal_shop_pending
        ON personal_shop_applications(
            guild_id, character_id, name
        )
        WHERE status = 'pending';

        CREATE TABLE IF NOT EXISTS personal_shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            application_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            level INTEGER NOT NULL,
            employee_limit INTEGER NOT NULL,
            deposit INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            opened_at TEXT NOT NULL,
            first_month_end TEXT NOT NULL,
            sales_total INTEGER NOT NULL DEFAULT 0,
            first_month_sales INTEGER NOT NULL DEFAULT 0,
            first_month_bonus_qualified INTEGER
                NOT NULL DEFAULT 0,
            deposit_returned_at TEXT,
            supervision_started_at TEXT,
            supervision_ended_at TEXT,
            CHECK(sales_total >= 0),
            CHECK(first_month_sales >= 0),
            UNIQUE(guild_id, id)
        );

        CREATE TABLE IF NOT EXISTS personal_shop_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            order_key TEXT NOT NULL,
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL
                CHECK(kind IN ('sale','refund')),
            occurred_at TEXT NOT NULL,
            UNIQUE(guild_id, order_key)
        );

        CREATE TABLE IF NOT EXISTS personal_shop_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """)


def owner_character(db, actor, cid):
    c = db.execute(
        """
        SELECT id, owner_id, status
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            actor.guild_id,
            cid,
        ),
    ).fetchone()

    if not c or c["status"] != "active":
        raise InvalidState(
            "角色不存在或尚未啟用。"
        )

    if (
        c["owner_id"] != actor.user_id
        and not actor.is_admin
    ):
        raise PermissionDenied(
            "你無權操作這名角色。"
        )

    return c


def tier(db, gid, level):
    row = db.execute(
        """
        SELECT deposit, employee_limit
        FROM personal_shop_tiers
        WHERE guild_id = ?
          AND level = ?
        """,
        (gid, level),
    ).fetchone()

    if row:
        return (
            row["deposit"],
            row["employee_limit"],
        )

    for lv, dep, count in DEFAULT_TIERS:
        if lv == level:
            return dep, count

    raise ValueError(
        "店鋪等級必須為1至7。"
    )


def request_store(
    actor,
    cid,
    name,
    category,
    level,
    description,
):
    name = name.strip()
    category = category.strip()

    if not (
        1 <= len(name) <= 80
        and 1 <= len(category) <= 80
    ):
        raise ValueError(
            "店名與類型需為1至80字。"
        )

    if len(description) > 500:
        raise ValueError(
            "商店介紹最多500字。"
        )

    with transaction() as db:
        owner_character(
            db,
            actor,
            cid,
        )

        deposit, limit = tier(
            db,
            actor.guild_id,
            level,
        )

        exists = db.execute(
            """
            SELECT 1
            FROM personal_shops
            WHERE guild_id = ?
              AND character_id = ?
              AND name = ?
              AND status IN (
                  'open', 'supervised'
              )
            """,
            (
                actor.guild_id,
                cid,
                name,
            ),
        ).fetchone()

        if exists:
            raise InvalidState(
                "你已經有同名營業中的商店。"
            )

        cursor = db.execute(
            """
            INSERT INTO personal_shop_applications (
                guild_id,
                character_id,
                name,
                category,
                description,
                level,
                applied_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                cid,
                name,
                category,
                description,
                level,
                stamp(),
            ),
        )

        return (
            f"✅ 開店申請 "
            f"#{cursor.lastrowid} 已送出；"
            f"預設保證金 {deposit:,}，"
            f"員工上限 {limit} 人。"
        )


def approve_store(
    actor,
    application_id,
    approve,
    note,
):
    require_admin(actor)

    with transaction() as db:
        a = db.execute(
            """
            SELECT
                a.*,
                c.owner_id,
                c.status AS character_status
            FROM personal_shop_applications a
            JOIN characters c
              ON c.guild_id = a.guild_id
             AND c.id = a.character_id
            WHERE a.guild_id = ?
              AND a.id = ?
            """,
            (
                actor.guild_id,
                application_id,
            ),
        ).fetchone()

        if (
            not a
            or a["status"] != "pending"
        ):
            raise InvalidState(
                "申請不存在或已審核。"
            )

        if a["owner_id"] == actor.user_id:
            raise PermissionDenied(
                "不能審核自己的角色。"
            )

        if len(note) > 500:
            raise ValueError(
                "審核意見不能超過500字。"
            )

        if not approve:
            db.execute(
                """
                UPDATE personal_shop_applications
                SET status = 'rejected',
                    reviewed_by = ?,
                    reviewed_at = ?,
                    note = ?
                WHERE id = ?
                  AND guild_id = ?
                """,
                (
                    actor.user_id,
                    stamp(),
                    note,
                    application_id,
                    actor.guild_id,
                ),
            )
            return (
                "申請已拒絕；"
                "沒有扣除保證金。"
            )

        if a["character_status"] != "active":
            raise InvalidState(
                "角色已停用，不能核准開店。"
            )

        deposit, limit = tier(
            db,
            actor.guild_id,
            a["level"],
        )

        wallet = get_or_create_account(
            db,
            actor.guild_id,
            "character",
            str(a["character_id"]),
            "wallet",
        )

        escrow = get_or_create_account(
            db,
            actor.guild_id,
            "system",
            f"personal_shop_deposit:{application_id}",
            "treasury",
        )

        if account_balance(
            db,
            actor.guild_id,
            wallet,
        ) < deposit:
            raise InvalidState(
                "角色錢包不足，"
                f"需有 {deposit:,} 金幣"
                "才能繳保證金開店。"
            )

        if deposit:
            _payment(
                db,
                actor.guild_id,
                f"shop:deposit:{application_id}",
                wallet,
                escrow,
                deposit,
                "玩家個人商店開業保證金",
            )

        start = datetime.now(
            timezone.utc
        )
        finish = add_month(start)

        cur = db.execute(
            """
            INSERT INTO personal_shops (
                guild_id,
                character_id,
                application_id,
                name,
                category,
                level,
                employee_limit,
                deposit,
                opened_at,
                first_month_end
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                a["character_id"],
                application_id,
                a["name"],
                a["category"],
                a["level"],
                limit,
                deposit,
                stamp(start),
                stamp(finish),
            ),
        )

        db.execute(
            """
            UPDATE personal_shop_applications
            SET status = 'approved',
                reviewed_by = ?,
                reviewed_at = ?,
                note = ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                actor.user_id,
                stamp(start),
                note,
                actor.guild_id,
                application_id,
            ),
        )

        return (
            f"✅ 店鋪 #{cur.lastrowid}"
            f"「{a['name']}」開業！\n"
            f"已託管保證金 {deposit:,} 金幣；"
            f"首月考核至 "
            f"{stamp(finish)[:10]}。"
        )


def release_deposit(db, s):
    if s["deposit_returned_at"] is not None:
        return False

    if s["sales_total"] < 2 * s["deposit"]:
        return False

    escrow = get_or_create_account(
        db,
        s["guild_id"],
        "system",
        (
            "personal_shop_deposit:"
            f"{s['application_id']}"
        ),
        "treasury",
    )

    wallet = get_or_create_account(
        db,
        s["guild_id"],
        "character",
        str(s["character_id"]),
        "wallet",
    )

    if s["deposit"]:
        _payment(
            db,
            s["guild_id"],
            f"shop:deposit:return:{s['id']}",
            escrow,
            wallet,
            s["deposit"],
            "商店營業額達標，返還保證金",
        )

    db.execute(
        """
        UPDATE personal_shops
        SET deposit_returned_at = ?,
            status = CASE
                WHEN status = 'supervised'
                THEN 'open'
                ELSE status
            END,
            supervision_ended_at = CASE
                WHEN supervision_started_at
                     IS NOT NULL
                THEN ?
                ELSE supervision_ended_at
            END
        WHERE guild_id = ?
          AND id = ?
          AND deposit_returned_at IS NULL
        """,
        (
            stamp(),
            stamp(),
            s["guild_id"],
            s["id"],
        ),
    )

    return True


def record_completed_sale(
    db,
    guild_id,
    shop_id,
    amount,
    order_key,
    kind="sale",
    original_sale_key=None,
):
    """由已驗證的訂單服務呼叫。

    呼叫端必須先確認商品交付與實際收款，
    並將此函式放在同一個資料庫交易裡。

    退款編號格式：
    refund:原始訂單編號:唯一識別
    """

    guild_id = str(guild_id)

    if (
        type(amount) is not int
        or amount <= 0
        or amount > 10**15
    ):
        raise ValueError(
            "營業額必須為正整數。"
        )

    if (
        kind not in ("sale", "refund")
        or not order_key
        or len(order_key) > 160
    ):
        raise ValueError(
            "訂單參數不合法。"
        )

    existing = db.execute(
        """
        SELECT shop_id, amount, kind
        FROM personal_shop_sales
        WHERE guild_id = ?
          AND order_key = ?
        """,
        (
            guild_id,
            order_key,
        ),
    ).fetchone()

    if existing:
        if (
            existing["shop_id"],
            existing["amount"],
            existing["kind"],
        ) != (
            shop_id,
            amount,
            kind,
        ):
            raise InvalidState(
                "訂單編號已被不同交易使用。"
            )

        return False

    s = db.execute(
        """
        SELECT *
        FROM personal_shops
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            guild_id,
            shop_id,
        ),
    ).fetchone()

    if (
        not s
        or s["status"] not in (
            "open",
            "supervised",
        )
    ):
        raise InvalidState(
            "店鋪不存在或無法營業。"
        )

    if kind == "refund":
        if not original_sale_key:
            raise ValueError(
                "退款必須指定原銷售編號。"
            )

        original = db.execute(
            """
            SELECT amount, occurred_at
            FROM personal_shop_sales
            WHERE guild_id = ?
              AND shop_id = ?
              AND order_key = ?
              AND kind = 'sale'
            """,
            (
                guild_id,
                shop_id,
                original_sale_key,
            ),
        ).fetchone()

        if not original:
            raise InvalidState(
                "找不到原始銷售。"
            )

        if not order_key.startswith(
            f"refund:{original_sale_key}:"
        ):
            raise ValueError(
                "退款編號格式不正確。"
            )

        refunds = db.execute(
            """
            SELECT COALESCE(
                SUM(amount), 0
            )
            FROM personal_shop_sales
            WHERE guild_id = ?
              AND shop_id = ?
              AND kind = 'refund'
              AND order_key LIKE ?
            """,
            (
                guild_id,
                shop_id,
                f"refund:{original_sale_key}:%",
            ),
        ).fetchone()[0]

        if (
            refunds + amount
            > original["amount"]
        ):
            raise InvalidState(
                "退款超過原始銷售金額。"
            )

        sign = -1

        first_month_delta = (
            -amount
            if original["occurred_at"]
               < s["first_month_end"]
            else 0
        )

    else:
        sign = 1

        first_month_delta = (
            amount
            if stamp()
               < s["first_month_end"]
            else 0
        )

    if (
        s["sales_total"] + sign * amount < 0
        or s["first_month_sales"]
           + first_month_delta < 0
    ):
        raise InvalidState(
            "退款造成營業額不合法。"
        )

    db.execute(
        """
        INSERT INTO personal_shop_sales (
            guild_id,
            shop_id,
            order_key,
            amount,
            kind,
            occurred_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            shop_id,
            order_key,
            amount,
            kind,
            stamp(),
        ),
    )

    db.execute(
        """
        UPDATE personal_shops
        SET sales_total = sales_total + ?,
            first_month_sales =
                first_month_sales + ?
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            sign * amount,
            first_month_delta,
            guild_id,
            shop_id,
        ),
    )

    current = db.execute(
        """
        SELECT *
        FROM personal_shops
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            guild_id,
            shop_id,
        ),
    ).fetchone()

    if (
        first_month_delta > 0
        and current["first_month_sales"]
            >= 3 * current["deposit"]
    ):
        db.execute(
            """
            UPDATE personal_shops
            SET first_month_bonus_qualified = 1
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                guild_id,
                shop_id,
            ),
        )

    release_deposit(
        db,
        current,
    )

    return True


def check_supervision():
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT guild_id, id
            FROM personal_shops
            WHERE status = 'open'
              AND deposit_returned_at IS NULL
              AND first_month_end <= ?
            LIMIT 500
            """,
            (stamp(),),
        ).fetchall()

    for r in rows:
        with transaction() as db:
            s = db.execute(
                """
                SELECT *
                FROM personal_shops
                WHERE guild_id = ?
                  AND id = ?
                """,
                (
                    r["guild_id"],
                    r["id"],
                ),
            ).fetchone()

            if (
                not s
                or s["status"] != "open"
                or s["deposit_returned_at"]
            ):
                continue

            if not release_deposit(db, s):
                db.execute(
                    """
                    UPDATE personal_shops
                    SET status = 'supervised',
                        supervision_started_at = ?
                    WHERE guild_id = ?
                      AND id = ?
                      AND status = 'open'
                    """,
                    (
                        stamp(),
                        s["guild_id"],
                        s["id"],
                    ),
                )

                db.execute(
                    """
                    INSERT INTO personal_shop_audit (
                        guild_id,
                        shop_id,
                        action,
                        details,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        s["guild_id"],
                        s["id"],
                        "supervision",
                        (
                            "首月未達兩倍營業額；"
                            "官方監督並提供協助，"
                            "不罰款、不接管。"
                        ),
                        stamp(),
                    ),
                )


def describe(actor, cid):
    with get_connection() as db:
        owner_character(
            db,
            actor,
            cid,
        )

        shops = db.execute(
            """
            SELECT *
            FROM personal_shops
            WHERE guild_id = ?
              AND character_id = ?
            ORDER BY id DESC
            LIMIT 15
            """,
            (
                actor.guild_id,
                cid,
            ),
        ).fetchall()

        if not shops:
            return (
                "角色目前沒有已核准的"
                "個人商店。"
            )

        return "\n\n".join(
            (
                f"🏬 #{s['id']} "
                f"{s['name']}｜"
                f"LV.{s['level']}｜"
                f"{s['status']}\n"
                f"員工上限："
                f"{s['employee_limit']}｜"
                f"保證金："
                f"{s['deposit']:,}\n"
                f"累計實際營業額："
                f"{s['sales_total']:,}\n"
                f"首月營業額："
                f"{s['first_month_sales']:,}\n"
                "保證金："
                f"{'已返還' if s['deposit_returned_at'] else '尚未返還'}\n"
                "首月三倍獎勵資格："
                f"{'已取得' if s['first_month_bonus_qualified'] else '尚未取得'}"
            )
            for s in shops
        )


class PlayerShops(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            initialize
        )
        self.monitor.start()

    async def cog_unload(self):
        self.monitor.cancel()

    @tasks.loop(hours=1)
    async def monitor(self):
        await asyncio.to_thread(
            check_supervision
        )

    @monitor.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

    async def respond(
        self,
        interaction,
        work,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "請在伺服器內操作。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                work
            )
        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as ex:
            result = f"⚠️ {ex}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )

    @app_commands.command(
        name="店鋪",
        description="查看角色經營的個人商店",
    )
    async def view_shops(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        await self.respond(
            interaction,
            lambda: describe(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
            ),
        )

    @app_commands.command(
        name="店鋪申請",
        description="申請開設個人商店",
    )
    async def apply(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        店名: str,
        商品類型: str,
        等級: app_commands.Range[int, 1, 7],
        介紹: str = "",
    ):
        await self.respond(
            interaction,
            lambda: request_store(
                Actor.from_interaction(
                    interaction
                ),
                角色編號,
                店名,
                商品類型,
                等級,
                介紹,
            ),
        )

    async def review_command(
        self,
        interaction: discord.Interaction,
        申請編號: int,
        通過: bool,
        意見: str = "",
    ):
        await self.respond(
            interaction,
            lambda: approve_store(
                Actor.from_interaction(
                    interaction
                ),
                申請編號,
                通過,
                意見,
            ),
        )

    async def waiting_command(
        self,
        interaction: discord.Interaction,
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )
            require_admin(actor)

            with get_connection() as db:
                rows = db.execute(
                    """
                    SELECT
                        id,
                        character_id,
                        name,
                        level
                    FROM personal_shop_applications
                    WHERE guild_id = ?
                      AND status = 'pending'
                    ORDER BY id
                    LIMIT 25
                    """,
                    (actor.guild_id,),
                ).fetchall()

                return "\n".join(
                    (
                        f"#{r['id']}｜"
                        f"角色#{r['character_id']}｜"
                        f"{r['name']}｜"
                        f"LV.{r['level']}"
                    )
                    for r in rows
                ) or "沒有待審開店申請。"

        await self.respond(
            interaction,
            work,
        )

    async def tier_command(
        self,
        interaction: discord.Interaction,
        等級: app_commands.Range[int, 1, 7],
        保證金: app_commands.Range[
            int, 0, 1000000000
        ],
        員工上限: app_commands.Range[
            int, 0, 10000
        ],
    ):
        def work():
            actor = Actor.from_interaction(
                interaction
            )
            require_admin(actor)

            with transaction() as db:
                db.execute(
                    """
                    INSERT INTO personal_shop_tiers (
                        guild_id,
                        level,
                        deposit,
                        employee_limit
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, level)
                    DO UPDATE SET
                        deposit = excluded.deposit,
                        employee_limit =
                            excluded.employee_limit
                    """,
                    (
                        actor.guild_id,
                        等級,
                        保證金,
                        員工上限,
                    ),
                )

            return (
                f"已修改 LV.{等級} "
                f"預設保證金 {保證金:,}、"
                f"員工上限 {員工上限}。"
                "只影響後續核准的新店。"
            )

        await self.respond(
            interaction,
            work,
        )


async def setup(bot: commands.Bot):
    """管理指令加入既有 /管理 群組。"""

    management = bot.tree.get_command(
        "管理"
    )

    if not isinstance(
        management,
        app_commands.Group,
    ):
        raise RuntimeError(
            "請先載入 modules/shop.py，"
            "建立 /管理 群組後"
            "再載入本模組。"
        )

    cog = PlayerShops(bot)

    management.add_command(
        app_commands.Command(
            name="店鋪待審",
            description="查看待審個人商店申請",
            callback=cog.waiting_command,
        )
    )

    management.add_command(
        app_commands.Command(
            name="店鋪審核",
            description="核准或拒絕個人商店申請",
            callback=cog.review_command,
        )
    )

    management.add_command(
        app_commands.Command(
            name="店鋪等級",
            description="修改本群組店鋪預設規模",
            callback=cog.tier_command,
        )
    )

    await bot.add_cog(cog)
