"""Discord RP｜十間商店首頁、商品數值顯示與付費行動值。

檔案：modules/zzz_shop_home.py

功能：
- B01～B10 官方商店分類
- 每頁 10 件商品
- 所有已設定商品效果公開顯示
- B01 穿戴型屬性加成顯示
- B02／B04 消耗品每次加成與使用次數
- 持有型商品預定加成顯示
- 商品批量購買入口
- 黑卡隱藏商店權限
- 行動值快捷購買
- 每點行動值 500 金幣
- 不限制累計購買數量
- 每筆購買獨立有效 30 天
- 付費行動值獨立帳本
- 重複交易防護

注意：
1. 本模組不建立 /商城 指令。
2. 唯一 /商城 仍由 modules.shop 註冊。
3. 商品顯示不代表未接入的效果已生效。
4. 正式 /行動 消耗功能仍須另外整合。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
    get_or_create_account,
)

from core.economy import _payment

from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)

from modules import shop


LOG = logging.getLogger(__name__)

ACTION_PRICE = 500
PERIOD_DAYS = 30

PAGE_SIZE = 10
MAX_MESSAGE_LENGTH = 1900


# ==========================================
# 官方商店分類
# ==========================================

STORES = (
    ("B01", "👗 服飾精品店"),
    ("B02", "💄 美妝保養店"),
    ("B03", "💎 珠寶鐘錶店"),
    ("B04", "📱 數位科技店"),
    ("B05", "🎭 演藝用品店"),
    ("B06", "📚 書籍文具店"),
    ("B07", "🏠 家居生活店"),
    ("B08", "🎮 娛樂收藏店"),
    ("B09", "🎁 特殊道具店"),
    ("B10", "🖤 黑卡隱藏商店"),
)

STORE_NAMES = dict(STORES)


# ==========================================
# 付費行動值資料庫
# ==========================================

def init_action_db():
    """建立付費行動值帳本，不清除既有資料。"""

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            paid_action_packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                remaining INTEGER NOT NULL,
                purchased_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                request_key TEXT NOT NULL,
                UNIQUE(guild_id, request_key)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            paid_action_expiry
            ON paid_action_packs (
                guild_id,
                character_id,
                expires_at
            )
            """
        )


# ==========================================
# 購買行動值
# ==========================================

def buy_actions(
    actor,
    character_id,
    quantity,
    request_key,
):
    """購買付費行動值。

    每點 500 金幣。
    每筆購買獨立計算 30 天。
    不限制累計購買數量。
    """

    if not isinstance(quantity, int):
        raise ValueError(
            "購買數量必須是整數。"
        )

    if not 1 <= quantity <= 1_000_000:
        raise ValueError(
            "單次最多購買 1,000,000 點，"
            "可分次購買，累計沒有上限。"
        )

    with transaction() as conn:
        shop.require_character(
            conn,
            actor,
            character_id,
        )

        now = datetime.now(timezone.utc)
        now_text = now.isoformat()

        # 過期行動值歸零。
        conn.execute(
            """
            UPDATE paid_action_packs
            SET remaining=0
            WHERE guild_id=?
              AND character_id=?
              AND expires_at<=?
              AND remaining>0
            """,
            (
                str(actor.guild_id),
                character_id,
                now_text,
            ),
        )

        # 防止同一筆請求重複扣款。
        existing = conn.execute(
            """
            SELECT id
            FROM paid_action_packs
            WHERE guild_id=?
              AND request_key=?
            """,
            (
                str(actor.guild_id),
                request_key,
            ),
        ).fetchone()

        if existing:
            return (
                "✅ 這筆行動值購買已經完成，"
                "不會再次扣款。"
            )

        cost = quantity * ACTION_PRICE

        wallet = get_or_create_account(
            conn,
            actor.guild_id,
            "character",
            str(character_id),
            "wallet",
        )

        treasury = get_or_create_account(
            conn,
            actor.guild_id,
            "system",
            "action_store",
            "treasury",
        )

        _payment(
            conn,
            actor.guild_id,
            "action:" + request_key,
            wallet,
            treasury,
            cost,
            f"購買行動值 ×{quantity}",
        )

        expires = now + timedelta(
            days=PERIOD_DAYS
        )

        conn.execute(
            """
            INSERT INTO paid_action_packs (
                guild_id,
                character_id,
                quantity,
                remaining,
                purchased_at,
                expires_at,
                request_key
            )
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                str(actor.guild_id),
                character_id,
                quantity,
                quantity,
                now_text,
                expires.isoformat(),
                request_key,
            ),
        )

        return (
            "⚡ **行動值購買成功！**\n\n"
            f"購買數量：{quantity:,} 點\n"
            f"支付金額：{cost:,} 金幣\n"
            f"有效期限："
            f"{expires:%Y-%m-%d %H:%M} UTC\n\n"
            "未使用的行動值將在 30 天後失效。\n"
            "正式 /行動 消耗功能尚待接入。"
        )


# ==========================================
# 查詢付費行動值
# ==========================================

def action_balance(
    actor,
    character_id,
):
    """查詢角色尚未過期的付費行動值。"""

    with get_connection() as conn:
        shop.require_character(
            conn,
            actor,
            character_id,
        )

        now = datetime.now(
            timezone.utc
        ).isoformat()

        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(remaining),0)
                AS total
            FROM paid_action_packs
            WHERE guild_id=?
              AND character_id=?
              AND expires_at>?
            """,
            (
                str(actor.guild_id),
                character_id,
                now,
            ),
        ).fetchone()

    return int(row["total"])


# ==========================================
# 扣除付費行動值
# ==========================================

def spend_paid_actions(
    conn,
    guild_id,
    character_id,
    quantity,
):
    """供正式 /行動 系統呼叫。

    優先使用最早到期的行動值。
    必須在呼叫者自己的資料庫交易中執行。
    """

    if (
        not isinstance(quantity, int)
        or quantity < 0
    ):
        raise ValueError(
            "扣除數量不合法。"
        )

    if quantity == 0:
        return

    rows = conn.execute(
        """
        SELECT id, remaining
        FROM paid_action_packs
        WHERE guild_id=?
          AND character_id=?
          AND expires_at>?
          AND remaining>0
        ORDER BY expires_at ASC, id ASC
        """,
        (
            str(guild_id),
            character_id,
            datetime.now(
                timezone.utc
            ).isoformat(),
        ),
    ).fetchall()

    available = sum(
        int(row["remaining"])
        for row in rows
    )

    if available < quantity:
        raise InvalidState(
            "付費行動值不足。"
        )

    left = quantity

    for row in rows:
        used = min(
            int(row["remaining"]),
            left,
        )

        if used <= 0:
            continue

        conn.execute(
            """
            UPDATE paid_action_packs
            SET remaining=remaining-?
            WHERE id=?
            """,
            (
                used,
                row["id"],
            ),
        )

        left -= used

        if left == 0:
            break


# ==========================================
# 商城首頁文字
# ==========================================

def home_text(character_id, guild_id=None, black_allowed=False):
    """商城首頁只列出目前在商城內的商店，外部商店顯示連結。"""
    from modules.shop_location import get_location

    mall = []
    external = []
    for code, name in STORES:
        if code == "B10" and not black_allowed:
            continue
        placement, url = (
            get_location(guild_id, code)
            if guild_id is not None
            else ("mall", "")
        )
        if placement == "channel":
            if url:
                external.append(f"{name}：{url}")
        else:
            mall.append(f"{name}")

    available = "\n".join(mall) or "目前沒有商城內商店。"
    outside = (
        "\n\n📍 **其他頻道商店**\n" + "\n".join(external)
        if external else ""
    )
    return (
        f"🛍️ **官方商城｜角色 #{character_id}**\n\n"
        "歡迎來到 Discord RP 官方商城！\n"
        "請從下方選單選擇商店。\n\n"
        + available + outside +
        "\n\n✨ 商品清單顯示已登記的數值效果。"
        "\n⚡ 行動值每點 500 金幣，有效 30 天。"
        "\n🎒 背包、使用及贈送請前往 `/查看`。"
    )[:1900]


# ==========================================
# 商店權限
# ==========================================

def require_access(
    actor,
    character_id,
    code="",
):
    """檢查角色及黑卡商店存取權限。"""

    with get_connection() as conn:
        shop.require_character(
            conn,
            actor,
            character_id,
        )

        if code == "B10":
            allowed = shop.has_black_access(
                conn,
                actor.guild_id,
                character_id,
            )

            if not allowed:
                raise PermissionDenied(
                    "只有有效黑卡會員可以進入此商店。"
                )


# ==========================================
# 商品資料讀取工具
# ==========================================

def get_existing_tables(conn):
    """取得資料庫現有資料表名稱。"""

    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        """
    ).fetchall()

    return {
        str(row[0])
        for row in rows
    }


def get_product_rules(
    conn,
    guild_id,
    product_ids,
    tables,
):
    """讀取 B02、B04 消耗品使用次數。"""

    rules = {}

    if not product_ids:
        return rules

    if "item_use_settings" not in tables:
        return rules

    placeholders = ",".join(
        "?" for _ in product_ids
    )

    rows = conn.execute(
        f"""
        SELECT product_id, max_uses
        FROM item_use_settings
        WHERE guild_id=?
          AND product_id IN ({placeholders})
        """,
        [
            str(guild_id),
            *product_ids,
        ],
    ).fetchall()

    for row in rows:
        rules[int(row["product_id"])] = int(
            row["max_uses"]
        )

    return rules


def get_held_bonuses(
    conn,
    guild_id,
    product_ids,
    tables,
):
    """讀取 B02、B04 尚未啟用的持有型加成。"""

    result = {}

    if not product_ids:
        return result

    placeholders = ",".join(
        "?" for _ in product_ids
    )

    for table in (
        "b02_catalog",
        "b04_catalog",
    ):
        if table not in tables:
            continue

        # 資料表名稱來自程式固定清單，
        # 不是玩家輸入。
        rows = conn.execute(
            f"""
            SELECT
                product_id,
                item_type,
                bonus_stat,
                bonus_value
            FROM {table}
            WHERE guild_id=?
              AND product_id IN ({placeholders})
            """,
            [
                str(guild_id),
                *product_ids,
            ],
        ).fetchall()

        for row in rows:
            if row["item_type"] != "持有":
                continue

            result[int(row["product_id"])] = (
                str(row["bonus_stat"]),
                int(row["bonus_value"]),
            )

    return result


def get_product_effect(
    product,
    uses,
    held,
):
    """將商品效果轉成玩家可理解的中文。"""

    product_id = int(
        product["id"]
    )

    kind = str(
        product["effect_kind"] or "none"
    ).strip().lower()

    stat = str(
        product["effect_stat"] or ""
    ).strip()

    value = int(
        product["effect_value"] or 0
    )

    context = str(
        product["effect_context"] or ""
    ).strip()

    # --------------------------------------
    # 多次使用消耗品
    # --------------------------------------

    if product_id in uses:
        count = uses[product_id]

        if stat and value > 0:
            total = value * count

            return (
                f"✨ 每次使用：{stat} +{value}\n"
                f"🔋 每件可使用：{count} 次\n"
                f"📈 全部用完：{stat} +{total}\n"
                "🗑️ 次數歸零後商品消失"
            )

        return (
            f"🔋 可使用 {count} 次\n"
            "⚠️ 尚未設定有效的屬性加成"
        )

    # --------------------------------------
    # B02／B04 持有型商品
    # --------------------------------------

    if product_id in held:
        held_stat, held_value = (
            held[product_id]
        )

        return (
            f"✨ 預定持有加成："
            f"{held_stat} +{held_value}\n"
            "⚠️ 此效果尚未接入角色屬性系統"
        )

    # --------------------------------------
    # B01 服裝及其他條件型商品
    # --------------------------------------

    if kind == "context":
        if not stat or value == 0:
            return (
                "⚠️ 條件型商品，"
                "尚未設定完整數值"
            )

        if context:
            condition = context
        elif str(
            product["category"] or ""
        ).upper().startswith("B01"):
            condition = "穿戴期間"
        else:
            condition = "符合指定條件時"

        return (
            f"✨ {stat} +{value}\n"
            f"📌 生效條件：{condition}"
        )

    # --------------------------------------
    # 一次性永久加成
    # --------------------------------------

    if kind == "instant":
        if stat and value != 0:
            return (
                f"✨ 使用後永久增加："
                f"{stat} +{value}"
            )

        return "目前沒有設定有效的使用加成"

    # --------------------------------------
    # 週期型效果
    # --------------------------------------

    if kind == "weekly":
        if stat and value != 0:
            message = (
                f"✨ 週期加成："
                f"{stat} +{value}"
            )

            if context:
                message += (
                    f"\n📌 生效條件：{context}"
                )

            return message

        return "目前沒有設定有效的週期加成"

    # --------------------------------------
    # 特殊效果與其他已設定數值
    # --------------------------------------

    if stat and value != 0:
        if context:
            return (
                f"✨ {stat} +{value}\n"
                f"📌 生效條件：{context}"
            )

        return (
            f"✨ {stat} +{value}\n"
            "📌 效果觸發方式待確認"
        )

    return "✨ 無直接屬性加成"


def get_compact_effect(
    product,
    uses,
    held,
):
    """商品清單較長時使用的精簡效果文字。"""

    pid = int(product["id"])

    stat = str(
        product["effect_stat"] or ""
    ).strip()

    value = int(
        product["effect_value"] or 0
    )

    kind = str(
        product["effect_kind"] or "none"
    ).lower()

    if pid in uses:
        count = uses[pid]

        if stat and value > 0:
            return (
                f"{stat}+{value}/次"
                f"×{count}｜總+{value * count}"
            )

        return f"可用{count}次｜效果未設定"

    if pid in held:
        hstat, hvalue = held[pid]

        return (
            f"預定{hstat}+{hvalue}"
            "（未啟用）"
        )

    if stat and value:
        if kind == "context":
            condition = str(
                product["effect_context"] or ""
            ).strip()

            if not condition:
                category = str(
                    product["category"] or ""
                ).upper()

                condition = (
                    "穿戴期間"
                    if category.startswith("B01")
                    else "條件生效"
                )

            return (
                f"{stat}+{value}"
                f"（{condition}）"
            )

        if kind == "instant":
            return (
                f"{stat}+{value}"
                "（永久）"
            )

        if kind == "weekly":
            return (
                f"{stat}+{value}"
                "（週期）"
            )

        return (
            f"{stat}+{value}"
            "（條件待確認）"
        )

    return "無直接加成"


# ==========================================
# 商品文字格式化
# ==========================================

def format_product(
    product,
    uses,
    held,
):
    """輸出單件商品完整資料。"""

    pid = int(
        product["id"]
    )

    name = discord.utils.escape_mentions(
        str(product["name"])
    )

    price = int(
        product["price"]
    )

    stock = (
        "不限"
        if product["stock"] is None
        else str(product["stock"])
    )

    effect = get_product_effect(
        product,
        uses,
        held,
    )

    return (
        f"**#{pid}｜{name}**\n"
        f"💰 售價：{price:,} 金幣"
        f"｜庫存：{stock}\n"
        f"{effect}"
    )


def format_compact_product(
    product,
    uses,
    held,
):
    """精簡商品資訊，仍保留商品的加成數值。"""

    pid = int(
        product["id"]
    )

    name = discord.utils.escape_mentions(
        str(product["name"])
    )

    price = int(
        product["price"]
    )

    effect = get_compact_effect(
        product,
        uses,
        held,
    )

    return (
        f"**#{pid} {name}**"
        f"｜💰{price:,}\n"
        f"✨ {effect}"
    )


# ==========================================
# 商店商品畫面
# ==========================================

def make_store_screen(
    actor,
    character_id,
    code,
    page=0,
):
    """購買前顯示全部已登記的商品加成。"""

    if code not in STORE_NAMES:
        raise ValueError(
            "商店編號錯誤。"
        )

    from modules.shop_location import get_location
    placement, url = get_location(actor.guild_id, code)
    if placement == "channel":
        raise ValueError(
            "此商店已改至其他頻道販售：" + (url or "請洽管理員")
        )

    require_access(
        actor,
        character_id,
        code,
    )

    store_name = STORE_NAMES[code]

    scope = (
        "black"
        if code == "B10"
        else "public"
    )

    conditions = [
        "guild_id=?",
        "scope=?",
        "active=1",
    ]

    args = [
        str(actor.guild_id),
        scope,
    ]

    # 黑卡專屬商店依 scope 查詢。
    if code != "B10":
        short_name = (
            store_name.split(" ", 1)[-1]
        )

        conditions.append(
            "(category LIKE ? OR category LIKE ?)"
        )

        args.extend([
            f"%{code}%",
            f"%{short_name}%",
        ])

    where_sql = " AND ".join(
        conditions
    )

    with get_connection() as conn:
        total = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM shop_products
                WHERE {where_sql}
                """,
                args,
            ).fetchone()[0]
        )

        last = max(
            0,
            (total - 1) // PAGE_SIZE,
        )

        page = max(
            0,
            min(int(page), last),
        )

        # 特別讀取分類及效果欄位，
        # 不能只讀商品編號、名稱及價格。
        products = conn.execute(
            f"""
            SELECT
                id,
                name,
                category,
                description,
                price,
                stock,
                effect_kind,
                effect_stat,
                effect_value,
                effect_context
            FROM shop_products
            WHERE {where_sql}
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            [
                *args,
                PAGE_SIZE,
                page * PAGE_SIZE,
            ],
        ).fetchall()

        product_ids = [
            int(product["id"])
            for product in products
        ]

        tables = get_existing_tables(
            conn
        )

        uses = get_product_rules(
            conn,
            actor.guild_id,
            product_ids,
            tables,
        )

        held = get_held_bonuses(
            conn,
            actor.guild_id,
            product_ids,
            tables,
        )

    header = (
        f"**{store_name}**\n"
        f"📦 商品總數：{total} 件\n"
        f"📄 第 {page + 1}/{last + 1} 頁"
    )

    footer = (
        "🛒 購買請輸入商品編號與數量。"
    )

    # --------------------------------------
    # 優先顯示完整效果
    # --------------------------------------

    detailed_blocks = [
        format_product(
            product,
            uses,
            held,
        )
        for product in products
    ]

    if not products:
        content = (
            header
            + "\n\n目前尚未上架商品。"
        )

    else:
        content = (
            header
            + "\n\n"
            + "\n\n".join(detailed_blocks)
            + "\n\n"
            + footer
        )

    # --------------------------------------
    # 內容過長：改為精簡商品列表
    # --------------------------------------

    if len(content) > MAX_MESSAGE_LENGTH:
        compact_blocks = [
            format_compact_product(
                product,
                uses,
                held,
            )
            for product in products
        ]

        content = (
            header
            + "\n\n"
            + "\n".join(compact_blocks)
            + "\n\n"
            + footer
        )

    # --------------------------------------
    # 商品名稱極長時，仍需避免訊息超長
    # --------------------------------------

    if len(content) > MAX_MESSAGE_LENGTH:
        short_lines = [
            header,
            "",
        ]

        for product in products:
            pid = int(
                product["id"]
            )

            name = discord.utils.escape_mentions(
                str(product["name"])[:22]
            )

            price = int(
                product["price"]
            )

            effect = get_compact_effect(
                product,
                uses,
                held,
            )

            block = (
                f"#{pid} {name}\n"
                f"💰{price:,}｜✨{effect}"
            )

            # 不截斷已經加入的商品區塊。
            projected = (
                "\n".join(short_lines)
                + "\n"
                + block
            )

            if len(projected) > 1800:
                short_lines.append(
                    "其餘商品請查看下一頁或"
                    "由管理員調整過長的名稱。"
                )
                break

            short_lines.append(
                block
            )

        content = "\n".join(
            short_lines
        )

    view = ShopHome(
        int(actor.user_id),
        character_id,
        code == "B10",
        code,
        int(actor.guild_id),
        page=page,
        last=last,
    )

    return content, view


# ==========================================
# 付費行動值購買表單
# ==========================================

class ActionBuyModal(
    discord.ui.Modal,
    title="⚡ 購買行動值",
):
    quantity = discord.ui.TextInput(
        label="購買點數（每點 500 金幣）",
        placeholder="例如：10",
        required=True,
        max_length=7,
    )

    def __init__(self, menu):
        super().__init__()

        self.menu = menu

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if (
            interaction.user.id
            != self.menu.owner_id
            or interaction.guild_id
            != self.menu.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的商城。",
                ephemeral=True,
            )
            return

        try:
            quantity = int(
                str(self.quantity.value)
            )

            if not 1 <= quantity <= 1_000_000:
                raise ValueError(
                    "購買數量必須介於 "
                    "1～1,000,000。"
                )

        except ValueError:
            await interaction.response.send_message(
                "⚠️ 請輸入有效的購買點數。",
                ephemeral=True,
            )
            return

        actor = Actor.from_interaction(
            interaction
        )

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                buy_actions,
                actor,
                self.menu.character_id,
                quantity,
                str(interaction.id),
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        except Exception:
            LOG.exception(
                "行動值購買失敗"
            )

            result = (
                "⚠️ 行動值購買發生系統錯誤，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


# ==========================================
# 商店選擇器
# ==========================================

class StorePicker(discord.ui.Select):
    def __init__(
        self,
        menu,
        black_allowed,
    ):
        self.menu = menu

        from modules.shop_location import get_location
        options = []
        for code, label in STORES:
            if code == "B10" and not black_allowed:
                continue
            if get_location(self.menu.guild_id, code)[0] == "channel":
                continue
            options.append(discord.SelectOption(label=label, value=code))
        if not options:
            options.append(discord.SelectOption(
                label="目前沒有商城內商店", value="none"
            ))

        super().__init__(
            placeholder="選擇要進入的商店",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "目前沒有商城內商店。", ephemeral=True
            )
            return
        actor = Actor.from_interaction(
            interaction
        )

        try:
            content, view = make_store_screen(
                actor,
                self.menu.character_id,
                self.values[0],
                0,
            )

            await interaction.response.edit_message(
                content=content,
                view=view,
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 商城互動面板
# ==========================================

class ShopHome(discord.ui.View):
    def __init__(
        self,
        owner_id,
        character_id,
        black=False,
        category="",
        guild_id=None,
        *,
        page=0,
        last=0,
    ):
        super().__init__(
            timeout=900
        )

        self.owner_id = int(
            owner_id
        )

        self.character_id = int(
            character_id
        )

        self.black = bool(
            black
        )

        self.category = str(
            category or ""
        )

        self.guild_id = (
            int(guild_id)
            if guild_id is not None
            else 0
        )

        self.page = int(
            page
        )

        self.last = int(
            last
        )

        if self.category not in STORE_NAMES:
            self.category = ""
            self.black = False

        # ----------------------------------
        # 商城首頁
        # ----------------------------------

        if not self.category:
            with get_connection() as conn:
                black_allowed = (
                    shop.has_black_access(
                        conn,
                        str(self.guild_id),
                        self.character_id,
                    )
                )

            self.add_item(
                StorePicker(
                    self,
                    black_allowed,
                )
            )

            self.buy_button.disabled = True
            self.previous.disabled = True
            self.next.disabled = True

        # ----------------------------------
        # 進入指定商店
        # ----------------------------------

        else:
            self.previous.disabled = (
                self.page <= 0
            )

            self.next.disabled = (
                self.page >= self.last
            )

    def _black_allowed(self):
        with get_connection() as conn:
            return bool(shop.has_black_access(
                conn, str(self.guild_id), self.character_id
            ))

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if (
            interaction.user.id
            != self.owner_id
            or interaction.guild_id
            != self.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的商城面板。",
                ephemeral=True,
            )
            return False

        return True

    # ======================================
    # 批量購買
    # ======================================

    @discord.ui.button(
        label="🛒 批量購買商品",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def buy_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        actor = Actor.from_interaction(
            interaction
        )

        try:
            require_access(
                actor,
                self.character_id,
                self.category,
            )

            await interaction.response.send_modal(
                shop.ShopActionModal(
                    self,
                    "buy",
                )
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    # ======================================
    # 商品分頁
    # ======================================

    async def move(
        self,
        interaction,
        page,
    ):
        actor = Actor.from_interaction(
            interaction
        )

        try:
            content, view = make_store_screen(
                actor,
                self.character_id,
                self.category,
                page,
            )

            await interaction.response.edit_message(
                content=content,
                view=view,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def previous(
        self,
        interaction,
        button,
    ):
        await self.move(
            interaction,
            self.page - 1,
        )

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def next(
        self,
        interaction,
        button,
    ):
        await self.move(
            interaction,
            self.page + 1,
        )

    # ======================================
    # 返回商城首頁
    # ======================================

    @discord.ui.button(
        label="🏠 商城首頁",
        style=discord.ButtonStyle.primary,
        row=3,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        actor = Actor.from_interaction(
            interaction
        )

        try:
            require_access(
                actor,
                self.character_id,
            )

            await interaction.response.edit_message(
                content=home_text(
                    self.character_id,
                    self.guild_id,
                    self._black_allowed(),
                ),
                view=ShopHome(
                    self.owner_id,
                    self.character_id,
                    False,
                    "",
                    self.guild_id,
                ),
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
            sqlite3.Error,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    # ======================================
    # 行動值快捷購買
    # ======================================

    @discord.ui.button(label='🏬 批發市場',row=4)
    async def wholesale_market(self,interaction,button):
        from core import wholesale_market_v1 as market
        from modules.wholesale_market_ui_v1 import MarketView,describe_offers
        await interaction.response.defer(ephemeral=True)
        try:
            rows=await asyncio.to_thread(market.list_offers,interaction.guild.id)
            await interaction.followup.send(describe_offers(rows),
                view=MarketView(interaction.user.id,self.character_id,interaction.guild.id,rows),ephemeral=True)
        except Exception:
            LOG.exception('批發市場讀取失敗')
            await interaction.followup.send('⚠️ 批發市場暫時無法載入，請查看日誌。',ephemeral=True)

    @discord.ui.button(
        label="⚡ 購買行動值",
        style=discord.ButtonStyle.primary,
        row=3,
    )
    async def action_info(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            ActionBuyModal(self)
        )


# ==========================================
# 模組初始化
# ==========================================

async def setup(
    bot: commands.Bot,
):
    await asyncio.to_thread(
        init_action_db
    )

    # 不註冊第二個 /商城。
    # 正式指令由 modules.shop 提供。

    LOG.info(
        "十間商店首頁、商品數值顯示"
        "與付費行動值系統已載入；"
        "/商城 仍由 modules.shop 提供。"
    )
