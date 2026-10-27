"""Discord RP｜十間商店首頁與付費行動值系統。

檔案：modules/zzz_shop_home.py

功能：
- B01～B10 官方商店分類
- 每頁 10 件商品
- 商品批量購買入口
- 黑卡隱藏商店權限
- 行動值快捷購買
- 每點行動值 500 金幣
- 行動值購買不設累計上限
- 每筆購買有效 30 天，逾期不保留
- 獨立付費行動值帳本
- 防止重複交易與重複扣款

注意：
本模組不建立第二個 /商城 指令。
唯一 /商城 指令仍由 modules.shop 註冊。
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


# ==========================================
# 付費行動值資料庫
# ==========================================

def init_action_db():
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paid_action_packs (
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
            CREATE INDEX IF NOT EXISTS paid_action_expiry
            ON paid_action_packs(
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
    每筆購買獨立計算 30 天期限。
    沒有累計購買上限。

    使用交易唯一鍵防止重複扣款。
    """

    if not isinstance(quantity, int):
        raise ValueError(
            "購買數量必須是整數。"
        )

    if not 1 <= quantity <= 1_000_000:
        raise ValueError(
            "單次最多購買 1,000,000 點，"
            "可以分次購買，沒有累計上限。"
        )

    with transaction() as conn:
        shop.require_character(
            conn,
            actor,
            character_id,
        )

        now = datetime.now(timezone.utc)

        # 將已過期的點數歸零。
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
                actor.guild_id,
                character_id,
                now.isoformat(),
            ),
        )

        # 相同請求只執行一次。
        existing = conn.execute(
            """
            SELECT id
            FROM paid_action_packs
            WHERE guild_id=?
              AND request_key=?
            """,
            (
                actor.guild_id,
                request_key,
            ),
        ).fetchone()

        if existing:
            return (
                "這筆行動值購買已經處理，"
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

        # 扣款沿用銀行交易核心。
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
                actor.guild_id,
                character_id,
                quantity,
                quantity,
                now.isoformat(),
                expires.isoformat(),
                request_key,
            ),
        )

        return (
            f"⚡ 行動值購買成功！\n\n"
            f"購買數量：{quantity:,} 點\n"
            f"支付金額：{cost:,} 金幣\n"
            f"有效期限：{expires:%Y-%m-%d %H:%M} UTC\n\n"
            "30 天後未使用的行動值不保留。\n"
            "正式 /行動 消耗功能尚待接入。"
        )


# ==========================================
# 查詢付費行動值
# ==========================================

def action_balance(
    actor,
    character_id,
):
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
                COALESCE(SUM(remaining),0) AS total
            FROM paid_action_packs
            WHERE guild_id=?
              AND character_id=?
              AND expires_at>?
            """,
            (
                actor.guild_id,
                character_id,
                now,
            ),
        ).fetchone()

    return int(row["total"])


# ==========================================
# 行動值消耗接口
# ==========================================

def spend_paid_actions(
    conn,
    guild_id,
    character_id,
    quantity,
):
    """預留給正式 /行動 系統使用。

    優先扣除最早到期的點數。
    必須在呼叫端的資料庫交易中執行。
    """

    if quantity < 0:
        raise ValueError(
            "扣除數量不合法。"
        )

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
            guild_id,
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

        if used > 0:
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

def home_text(character_id):
    return (
        f"🛍️ **官方商城｜角色 #{character_id}**\n\n"
        "歡迎來到 Discord RP 官方商城！\n"
        "請從下方選單選擇商店。\n\n"
        "👗 B01｜服飾精品店\n"
        "💄 B02｜美妝保養店\n"
        "💎 B03｜珠寶鐘錶店\n"
        "📱 B04｜數位科技店\n"
        "🎭 B05｜演藝用品店\n"
        "📚 B06｜書籍文具店\n"
        "🏠 B07｜家居生活店\n"
        "🎮 B08｜娛樂收藏店\n"
        "🎁 B09｜特殊道具店\n"
        "🖤 B10｜黑卡隱藏商店\n\n"
        "⚡ **行動值補給**\n"
        "每點 500 金幣，30 天後失效。\n\n"
        "🎒 背包、物品使用及贈送："
        "請前往 `/查看`。"
    )


# ==========================================
# 商店權限
# ==========================================

def require_access(
    actor,
    character_id,
    code="",
):
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
# 商店商品查詢
# ==========================================

def make_store_screen(actor, character_id, code, page=0):
    """商品清單：購買前直接顯示效果、價格及使用次數。"""
    if code not in dict(STORES):
        raise ValueError("商店編號錯誤。")

    require_access(actor, character_id, code)
    scope = "black" if code == "B10" else "public"
    where = "guild_id=? AND scope=? AND active=1"
    args = [str(actor.guild_id), scope]
    if code != "B10":
        name = dict(STORES)[code].split(" ", 1)[-1]
        where += " AND (category LIKE ? OR category LIKE ?)"
        args.extend([f"%{code}%", f"%{name}%"])

    with get_connection() as conn:
        total = int(conn.execute(
            "SELECT COUNT(*) FROM shop_products WHERE " + where,
            args,
        ).fetchone()[0])
        last = max(0, (total - 1) // PAGE_SIZE)
        page = max(0, min(int(page), last))
        rows = conn.execute(
            "SELECT id,name,price,stock,effect_kind,effect_stat,"
            "effect_value,effect_context FROM shop_products WHERE "
            + where + " ORDER BY id LIMIT ? OFFSET ?",
            [*args, PAGE_SIZE, page * PAGE_SIZE],
        ).fetchall()

        ids = [int(r["id"]) for r in rows]
        uses = {}
        held = {}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "item_use_settings" in tables:
                for r in conn.execute(
                    "SELECT product_id,max_uses FROM item_use_settings "
                    f"WHERE guild_id=? AND product_id IN ({placeholders})",
                    [str(actor.guild_id), *ids],
                ):
                    uses[int(r["product_id"])] = int(r["max_uses"])
            for table in ("b02_catalog", "b04_catalog"):
                if table not in tables:
                    continue
                for r in conn.execute(
                    "SELECT product_id,bonus_stat,bonus_value,item_type "
                    f"FROM {table} WHERE guild_id=? "
                    f"AND product_id IN ({placeholders})",
                    [str(actor.guild_id), *ids],
                ):
                    if r["item_type"] == "持有":
                        held[int(r["product_id"])] = (
                            str(r["bonus_stat"]), int(r["bonus_value"])
                        )

    lines = [
        f"**{dict(STORES)[code]}**｜共 {total} 件",
        f"第 {page+1}/{last+1} 頁", "",
    ]
    for r in rows:
        pid = int(r["id"])
        stat = str(r["effect_stat"] or "").strip()
        value = int(r["effect_value"] or 0)
        kind = str(r["effect_kind"] or "none").lower()
        condition = str(r["effect_context"] or "").strip()
        if pid in uses:
            n = uses[pid]
            effect = (f"{stat} +{value}/次 ×{n} 次（總 +{value*n}）"
                      if stat and value > 0 else f"可用 {n} 次｜效果未設定")
        elif kind == "context" and stat and value:
            effect = f"{stat} +{value}（{condition or '條件生效'}）"
        elif kind == "instant" and stat and value:
            effect = f"{stat} +{value}（使用後永久）"
        elif kind == "weekly" and stat and value:
            effect = f"{stat} +{value}（週期效果）"
        elif pid in held:
            hstat, hvalue = held[pid]
            effect = f"{hstat} +{hvalue}（預定持有加成，尚未啟用）"
        elif stat and value:
            effect = f"{stat} +{value}（{condition or '效果待確認'}）"
        else:
            effect = "無直接屬性加成"
        stock = "不限" if r["stock"] is None else str(r["stock"])
        lines.append(f"**#{pid} {r['name']}**｜💰{int(r['price']):,}｜庫存 {stock}")
        lines.append(f"✨ {effect}")

    if not rows:
        lines.append("目前尚未上架商品。")
    lines.extend(["", "🛒 購買請使用商品編號；支援批量購買。"])
    content = "\n".join(lines)
    if len(content) > 1900:
        # 顯示完整商品優先：縮短文字，不以字元切割商品。
        compact = [f"**{dict(STORES)[code]}**｜第 {page+1}/{last+1} 頁"]
        for r in rows:
            pid = int(r["id"])
            stat = str(r["effect_stat"] or "")
            value = int(r["effect_value"] or 0)
            if pid in uses:
                effect = f"{stat}+{value}/次×{uses[pid]}"
            elif pid in held:
                s, v = held[pid]
                effect = f"預定{s}+{v}（未啟用）"
            elif stat and value:
                effect = f"{stat}+{value}"
            else:
                effect = "無加成"
            compact.append(
                f"#{pid} {str(r['name'])[:40]}｜{int(r['price']):,}｜{effect}"
            )
        content = "\n".join(compact)
        if len(content) > 1900:
            content = content[:1890] + "…"

    view = ShopHome(
        int(actor.user_id), character_id, code == "B10", code,
        int(actor.guild_id), page=page, last=last,
    )
    return content, view


# ==========================================
# 行動值購買表單
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
            interaction.user.id != self.menu.owner_id
            or interaction.guild_id != self.menu.guild_id
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
                    "購買數量必須介於 1～1,000,000。"
                )

        except ValueError:
            await interaction.response.send_message(
                "⚠️ 請輸入有效的行動值數量。",
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

        options = [
            discord.SelectOption(
                label=label,
                value=code,
            )
            for code, label in STORES
            if code != "B10" or black_allowed
        ]

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
        super().__init__(timeout=900)

        self.owner_id = int(owner_id)
        self.character_id = int(character_id)
        self.black = bool(black)
        self.category = str(category or "")

        self.guild_id = (
            int(guild_id)
            if guild_id is not None
            else 0
        )

        self.page = page
        self.last = last

        if self.category not in dict(STORES):
            self.category = ""
            self.black = False

        # 商城首頁
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

        # 已進入指定商店
        else:
            self.previous.disabled = page <= 0
            self.next.disabled = page >= last

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
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
    # 返回商店首頁
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
                    self.character_id
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

async def setup(bot: commands.Bot):
    # 初始化行動值資料庫。
    await asyncio.to_thread(init_action_db)

    # 不註冊第二個 /商城。
    # 正式指令由 modules.shop 負責。
    LOG.info(
        "十間商店首頁與付費行動值系統已載入；"
        "/商城 唯一入口由 modules.shop 提供。"
    )
