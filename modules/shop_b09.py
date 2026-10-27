
"""Discord RP｜B09 特殊道具店。

正式指令：
/b09 上架
/b09 查詢

功能：
- 20 件特殊功能憑證
- 群主批量上架
- 重複執行不重複建立商品
- 保留原有商品、角色、銀行與背包
- 避免未開發的特殊效果被誤觸發
- 為後續特殊憑證兌換系統保留資料

重要：
本模組只負責商品登記與上架。
改名、配點重置、活動權限等實際兌換功能
需要後續獨立開發。

不建立第二個 /商城 指令。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction


LOG = logging.getLogger(__name__)

SHOP_CODE = "B09"
SHOP_CATEGORY = "B09-特殊道具"
EXPECTED_COUNT = 20


# ==========================================
# B09 商品清單
# ==========================================

# 格式：
# 代碼|名稱|價格|功能類別|功能代碼|用途
#
# 這些商品不增加十三項屬性。
# 購買後先保存在背包。
# 必須等正式兌換系統完成才能使用。

CATALOG_TEXT = """
SP-001|角色改名卡|10000|角色資料|rename|申請變更角色名稱
SP-002|角色藝名變更卡|6000|角色資料|stage_name|申請變更角色藝名
SP-003|角色公開資料修訂券|8000|角色資料|public_edit|申請修訂公開角色資料
SP-004|角色形象重塑券|12000|角色資料|image_edit|申請更新角色形象設定
SP-005|角色職業轉換券|20000|角色資料|occupation|申請變更角色職業
SP-006|初始配點重置券|30000|角色成長|reset_initial|申請重置可分配的初始屬性點
SP-007|個人才藝專精調整券|18000|角色成長|talent_specialty|申請調整個人才藝專精設定
SP-008|演藝發展方向調整券|16000|角色成長|career_track|申請變更角色發展賽道
SP-009|角色培養方案重訂券|24000|角色成長|growth_plan|申請修改角色培養方案
SP-010|進階角色設定修訂券|25000|角色成長|advanced_edit|申請修訂需要特殊審核的角色設定
SP-011|特別試鏡入場券|12000|演藝活動|audition|兌換指定活動的特別試鏡資格
SP-012|優先活動報名券|15000|演藝活動|event_priority|兌換指定活動的優先報名資格
SP-013|專屬宣傳企劃券|20000|演藝活動|promotion|申請指定宣傳企劃
SP-014|特殊合作申請券|25000|演藝活動|collaboration|申請符合規定的特殊合作
SP-015|限定活動參與券|30000|演藝活動|limited_event|兌換指定限定活動參與資格
SP-016|特殊紀念收藏證書|5000|特殊收藏|certificate|兌換官方特殊紀念證書
SP-017|專屬紀念徽章券|8000|特殊收藏|badge|兌換指定紀念徽章
SP-018|限定個人名片券|10000|特殊收藏|namecard|兌換限定角色名片樣式
SP-019|個人展示主題券|15000|特殊收藏|profile_theme|兌換角色展示主題
SP-020|特別紀念稱號申請券|20000|特殊收藏|special_title|申請符合規則的限定稱號
""".strip()


# ==========================================
# 商品資料驗證
# ==========================================

def catalog():
    products = []
    seen_skus = set()

    for index, raw in enumerate(
        CATALOG_TEXT.splitlines(),
        start=1,
    ):
        line = raw.strip()

        if not line:
            continue

        parts = [
            part.strip()
            for part in line.split("|")
        ]

        if len(parts) != 6:
            raise ValueError(
                f"第 {index} 行商品資料格式錯誤。"
            )

        (
            sku,
            name,
            price_raw,
            category,
            action,
            description,
        ) = parts

        if (
            not sku
            or not name
            or not category
            or not action
            or not description
        ):
            raise ValueError(
                f"第 {index} 行存在空白欄位。"
            )

        if sku in seen_skus:
            raise ValueError(
                f"商品代碼重複：{sku}"
            )

        seen_skus.add(sku)

        price = int(price_raw)

        if price <= 0:
            raise ValueError(
                f"{sku} 售價必須大於零。"
            )

        products.append((
            sku,
            name,
            price,
            category,
            action,
            description,
        ))

    if len(products) != EXPECTED_COUNT:
        raise ValueError(
            "B09 商品數量必須為 20 件。"
        )

    expected_skus = {
        f"SP-{number:03d}"
        for number in range(1, EXPECTED_COUNT + 1)
    }

    if seen_skus != expected_skus:
        raise ValueError(
            "商品代碼必須是 SP-001～SP-020。"
        )

    return products


# ==========================================
# B09 獨立商品索引
# ==========================================

def init_db():
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS b09_catalog (
                guild_id TEXT NOT NULL,
                sku TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                action_code TEXT NOT NULL,
                action_category TEXT NOT NULL,

                PRIMARY KEY(guild_id, sku),
                UNIQUE(guild_id, product_id)
            )
            """
        )


# ==========================================
# 商品描述
# ==========================================

def product_description(
    sku,
    category,
    action,
    description,
):
    return (
        f"B09 特殊道具｜{sku}｜"
        f"功能分類：{category}｜"
        f"用途：{description}｜"
        f"功能代碼：{action}｜"
        "特殊憑證；兌換功能尚未開放。"
    )


# ==========================================
# 商品上架
# ==========================================

def import_catalog(guild_id):
    gid = str(guild_id)

    init_db()
    products = catalog()

    created = 0
    skipped = 0

    with transaction() as conn:
        existing = {
            str(row["sku"])
            for row in conn.execute(
                """
                SELECT sku
                FROM b09_catalog
                WHERE guild_id=?
                """,
                (gid,),
            ).fetchall()
        }

        for (
            sku,
            name,
            price,
            category,
            action,
            description,
        ) in products:

            if sku in existing:
                skipped += 1
                continue

            # 只匹配本模組的正式代碼標記。
            # 不會直接修改舊版商品。
            legacy = conn.execute(
                """
                SELECT id
                FROM shop_products
                WHERE guild_id=?
                  AND category=?
                  AND description LIKE ?
                ORDER BY id
                """,
                (
                    gid,
                    SHOP_CATEGORY,
                    f"B09 特殊道具｜{sku}｜%",
                ),
            ).fetchall()

            if len(legacy) > 1:
                raise ValueError(
                    f"{sku} 找到多筆既有商品，"
                    "請先由管理員確認，避免錯誤合併。"
                )

            if legacy:
                conn.execute(
                    """
                    INSERT INTO b09_catalog (
                        guild_id,
                        sku,
                        product_id,
                        action_code,
                        action_category
                    )
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        gid,
                        sku,
                        int(legacy[0]["id"]),
                        action,
                        category,
                    ),
                )

                skipped += 1
                continue

            now = datetime.now(
                timezone.utc
            ).isoformat()

            result = conn.execute(
                """
                INSERT INTO shop_products (
                    guild_id,
                    name,
                    category,
                    description,
                    price,
                    stock,
                    scope,
                    effect_kind,
                    effect_stat,
                    effect_value,
                    effect_context,
                    active,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    1, ?, ?
                )
                """,
                (
                    gid,
                    name,
                    SHOP_CATEGORY,
                    product_description(
                        sku,
                        category,
                        action,
                        description,
                    ),
                    price,
                    None,
                    "public",
                    "none",
                    None,
                    0,
                    "特殊憑證｜兌換功能尚未開放",
                    now,
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO b09_catalog (
                    guild_id,
                    sku,
                    product_id,
                    action_code,
                    action_category
                )
                VALUES (?,?,?,?,?)
                """,
                (
                    gid,
                    sku,
                    int(result.lastrowid),
                    action,
                    category,
                ),
            )

            created += 1

    return (
        "✅ **B09 特殊道具上架完成**\n\n"
        f"新增：{created} 件\n"
        f"已有紀錄：{skipped} 件\n"
        f"預設商品：{EXPECTED_COUNT} 件\n\n"
        "⚠️ 特殊功能尚未開放兌換。"
    )


# ==========================================
# 商品查詢
# ==========================================

def query_catalog(guild_id):
    gid = str(guild_id)

    init_db()

    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                b.sku,
                b.action_category,
                b.action_code,
                p.name,
                p.price,
                p.active
            FROM b09_catalog AS b
            JOIN shop_products AS p
              ON p.guild_id=b.guild_id
             AND p.id=b.product_id
            WHERE b.guild_id=?
            ORDER BY b.sku
            """,
            (gid,),
        ).fetchall()

    total = len(rows)

    active = sum(
        1
        for row in rows
        if int(row["active"]) == 1
    )

    groups = {}

    for row in rows:
        category = str(
            row["action_category"]
        )

        groups[category] = (
            groups.get(category, 0) + 1
        )

    group_lines = [
        f"{name}：{count} 件"
        for name, count in groups.items()
    ]

    return (
        "🎁 **B09 特殊道具店**\n\n"
        f"已登記：{total}/{EXPECTED_COUNT} 件\n"
        f"已上架：{active} 件\n\n"
        + (
            "\n".join(group_lines)
            if group_lines
            else "目前尚無商品。"
        )
        + "\n\n兌換系統：尚未開放"
    )


# ==========================================
# 管理權限
# ==========================================

def require_owner(
    interaction: discord.Interaction,
):
    if (
        interaction.guild is None
        or interaction.user.id
        != interaction.guild.owner_id
    ):
        raise ValueError(
            "僅限伺服器群主管理 B09 商品。"
        )


# ==========================================
# Discord 指令
# ==========================================

class B09Shop(commands.Cog):

    group = app_commands.Group(
        name="b09",
        description="B09 特殊道具店管理",
        guild_only=True,
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(
            init_db
        )

    @group.command(
        name="上架",
        description="批量上架 20 件特殊道具",
    )
    async def publish(
        self,
        interaction: discord.Interaction,
    ):
        try:
            require_owner(interaction)

        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                import_catalog,
                interaction.guild_id,
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            result = (
                f"⚠️ 上架失敗：{exc}"
            )

        except Exception:
            LOG.exception(
                "B09 商品上架失敗"
            )

            result = (
                "⚠️ 商品上架失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )

    @group.command(
        name="查詢",
        description="查詢 B09 商品登記進度",
    )
    async def query(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                query_catalog,
                interaction.guild_id,
            )

        except Exception:
            LOG.exception(
                "B09 商品查詢失敗"
            )

            result = (
                "⚠️ 商品查詢失敗，"
                "請查看 Railway 日誌。"
            )

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        B09Shop(bot)
    )
