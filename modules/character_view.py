
"""Discord RP｜角色查看及背包整合系統。

正式指令：/查看

功能：
- 原創角色、玩家 NPC、官方角色、路人角色
- 分類搜尋與無總數上限分頁
- 公開、私人角色資料
- 十三項屬性
- 系統動態追蹤
- 公開貨幣資產、私人帳戶明細
- 角色立繪
- 背包查看
- 物品贈送
- 單次使用
- 批量使用
- 停用物品效果

批量使用：
- 每次最多 100 次
- 只允許角色擁有者操作
- 每次呼叫原商城使用函式
- 每次有獨立交易識別碼
- 數量不足時停止，不產生額外加成

不重建或清空角色及銀行資料。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from types import SimpleNamespace

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25

CATEGORIES = {
    "original": "原創角色",
    "player_npc": "玩家 NPC",
    "official_npc": "官方角色",
    "extra": "路人角色",
}

PUBLIC_FIELDS = {
    "stage_name": "藝名／稱號",
    "gender": "性別",
    "nationality": "國籍／出身",
    "height_cm": "身高",
    "weight_kg": "體重",
    "birthday": "生日",
    "main_track": "主賽道",
    "secondary_track": "副賽道",
    "education": "學歷",
    "languages": "語言",
    "experience_years": "資歷",
    "public_persona": "公開人設",
}

PRIVATE_FIELDS = {
    "private_personality": "私下性格",
    "special_notes": "特殊設定",
    "notes": "其他備註",
    "biography": "完整生平",
    "relationship_status": "感情狀態",
    "partner_name": "伴侶姓名",
    "initial_allocation": "初始配點紀錄",
}

ATTRIBUTE_GENERAL = (
    "流量熱度",
    "商業價值",
    "路人緣",
    "公關",
    "影響指數",
)

ATTRIBUTE_TALENT = (
    "容貌",
    "才智",
    "歌唱",
    "舞蹈",
    "演繹",
    "主持",
    "綜藝",
    "個人才藝",
)


def safe_json(raw):
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}

    return data if isinstance(data, dict) else {}


def display_value(value, default="未公開"):
    if value is None or value == "":
        return default

    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
        )

    return str(value)


def shorten(value, limit=1000):
    value = str(value)

    if len(value) > limit:
        return value[:limit - 1] + "…"

    return value


def is_extra(settings):
    return (
        settings.get("character_category")
        in ("extra", "路人")
        or settings.get("public_category")
        in ("extra", "路人")
        or settings.get("is_extra") is True
        or settings.get("is_bystander") is True
    )


def get_category(character):
    settings = safe_json(
        character["settings_json"]
    )

    if is_extra(settings):
        return "extra"

    kind = character["character_type"]

    if kind == "player_npc":
        return "player_npc"

    if kind == "official_npc":
        return "official_npc"

    return "original"


def is_owner(actor, character):
    return (
        str(character["owner_id"])
        == str(actor.user_id)
    )


def can_see_private(actor, character):
    return (
        is_owner(actor, character)
        or actor.is_admin
    )


def can_see_character(actor, character):
    if character["status"] == "deleted":
        return False

    if can_see_private(actor, character):
        return True

    return character["status"] in (
        "active",
        "retired",
    )


def get_character(actor, character_id):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                actor.guild_id,
                int(character_id),
            ),
        ).fetchone()

    if row is None:
        raise InvalidState(
            "找不到這名角色。"
        )

    character = dict(row)

    if not can_see_character(
        actor,
        character,
    ):
        raise PermissionDenied(
            "角色不存在或目前不可公開查看。"
        )

    return character


def require_inventory_owner(actor, character_id):
    character = get_character(
        actor,
        character_id,
    )

    if not is_owner(actor, character):
        raise PermissionDenied(
            "只能操作自己擁有的角色背包。"
        )

    if character["status"] != "active":
        raise InvalidState(
            "角色尚未啟用，無法操作背包。"
        )

    return character


def find_characters(
    actor,
    category,
    keyword="",
    page=0,
):
    if category not in CATEGORIES:
        raise ValueError(
            "角色分類不正確。"
        )

    keyword = str(keyword).strip()[:100]
    page = max(0, int(page))

    with get_connection() as conn:
        if actor.is_admin:
            rows = conn.execute(
                """
                SELECT *
                FROM characters
                WHERE guild_id = ?
                  AND status != 'deleted'
                ORDER BY id
                """,
                (actor.guild_id,),
            ).fetchall()

        else:
            rows = conn.execute(
                """
                SELECT *
                FROM characters
                WHERE guild_id = ?
                  AND (
                    status IN ('active', 'retired')
                    OR (
                        owner_id = ?
                        AND status != 'deleted'
                    )
                  )
                ORDER BY id
                """,
                (
                    actor.guild_id,
                    actor.user_id,
                ),
            ).fetchall()

    matches = []

    for row in rows:
        character = dict(row)

        if get_category(character) != category:
            continue

        if not can_see_character(
            actor,
            character,
        ):
            continue

        settings = safe_json(
            character["settings_json"]
        )

        searchable = " ".join([
            str(character["name"]),
            str(settings.get("stage_name") or ""),
            str(character.get("public_code") or ""),
        ]).casefold()

        if (
            keyword
            and keyword.casefold() not in searchable
        ):
            continue

        matches.append({
            "id": character["id"],
            "name": character["name"],
            "occupation": character["occupation"],
            "status": character["status"],
        })

    total = len(matches)
    maximum = max(
        0,
        (total - 1) // PAGE_SIZE,
    )

    page = min(page, maximum)
    start = page * PAGE_SIZE

    return {
        "items": matches[
            start:start + PAGE_SIZE
        ],
        "total": total,
        "page": page,
        "max_page": maximum,
    }


def add_field_chunks(embed, name, lines):
    chunk = ""
    index = 1

    for line in lines:
        line = shorten(line, 1000)

        if len(chunk) + len(line) + 1 > 1024:
            if chunk:
                embed.add_field(
                    name=(
                        name if index == 1
                        else f"{name}（續{index}）"
                    ),
                    value=chunk,
                    inline=False,
                )

                index += 1
                chunk = ""

        chunk += line + "\n"

    if chunk.strip():
        embed.add_field(
            name=(
                name if index == 1
                else f"{name}（續{index}）"
            ),
            value=chunk,
            inline=False,
        )


def read_attributes(actor, character_id):
    get_character(actor, character_id)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT name, value
            FROM attributes
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchall()

    return {
        row["name"]: row["value"]
        for row in rows
    }


def read_tracking(actor, character_id):
    get_character(actor, character_id)

    empty = {
        "loyal_fans": 0,
        "active_antis": 0,
        "public_awareness": 0,
    }

    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    loyal_fans,
                    active_antis,
                    public_awareness
                FROM character_system_tracking
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.guild_id,
                    character_id,
                ),
            ).fetchone()

    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc):
            raise

        return empty

    return dict(row) if row else empty


def read_money_assets(actor, character_id):
    get_character(actor, character_id)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                a.account_type,
                COALESCE(SUM(e.amount), 0) AS balance
            FROM accounts AS a
            LEFT JOIN ledger_entries AS e
              ON e.guild_id = a.guild_id
             AND e.account_id = a.id
            WHERE a.guild_id = ?
              AND a.owner_type = 'character'
              AND a.owner_id = ?
              AND a.account_type IN ('wallet', 'bank')
            GROUP BY a.id, a.account_type
            """,
            (
                actor.guild_id,
                str(character_id),
            ),
        ).fetchall()

    money = {
        "wallet": 0,
        "bank": 0,
    }

    for row in rows:
        kind = row["account_type"]
        money[kind] += int(row["balance"])

    money["total"] = (
        money["wallet"] + money["bank"]
    )

    return money


def make_asset_detail_embed(actor, character_id):
    character = get_character(
        actor,
        character_id,
    )

    if not can_see_private(actor, character):
        raise PermissionDenied(
            "只有本人及授權管理員可以查看詳細帳目。"
        )

    money = read_money_assets(
        actor,
        character_id,
    )

    embed = discord.Embed(
        title=(
            f"💰 {shorten(character['name'], 180)}"
            "｜資產明細"
        ),
        description=(
            f"錢包：{money['wallet']:,} 金幣\n"
            f"銀行：{money['bank']:,} 金幣\n"
            f"貨幣合計：{money['total']:,} 金幣"
        ),
        color=discord.Color.gold(),
    )

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                t.id,
                t.created_at,
                a.account_type,
                e.amount
            FROM ledger_entries AS e
            JOIN accounts AS a
              ON a.id = e.account_id
             AND a.guild_id = e.guild_id
            JOIN ledger_transactions AS t
              ON t.id = e.transaction_id
             AND t.guild_id = e.guild_id
            WHERE a.guild_id = ?
              AND a.owner_type = 'character'
              AND a.owner_id = ?
              AND a.account_type IN ('wallet', 'bank')
            ORDER BY t.id DESC, e.account_id DESC
            LIMIT 10
            """,
            (
                actor.guild_id,
                str(character_id),
            ),
        ).fetchall()

    lines = []

    for row in rows:
        account = (
            "錢包"
            if row["account_type"] == "wallet"
            else "銀行"
        )

        lines.append(
            f"#{row['id']}｜{row['created_at']}｜"
            f"{account}｜"
            f"{int(row['amount']):+,} 金幣"
        )

    embed.add_field(
        name="最近 10 筆帳戶分錄",
        value=(
            shorten("\n".join(lines), 1024)
            if lines else "尚無紀錄"
        ),
        inline=False,
    )

    embed.set_footer(
        text="不包含商品、房產及其他未估值資產。"
    )

    return embed


def make_character_embed(
    actor,
    character_id,
    *,
    private=False,
):
    character = get_character(
        actor,
        character_id,
    )

    if (
        private
        and not can_see_private(actor, character)
    ):
        raise PermissionDenied(
            "你沒有查看私人資料的權限。"
        )

    settings = safe_json(
        character["settings_json"]
    )

    category = get_category(character)

    embed = discord.Embed(
        title=shorten(character["name"], 250),
        description=(
            f"🎭 {CATEGORIES[category]}\n"
            f"角色編號：#{character['id']}\n"
            f"公開代碼："
            f"{character.get('public_code') or '未設定'}\n"
            f"狀態：{character['status']}"
        ),
        color=discord.Color.blurple(),
    )

    color_code = settings.get("color_hex")

    if (
        isinstance(color_code, str)
        and len(color_code) == 7
        and color_code.startswith("#")
    ):
        try:
            embed.color = discord.Color(
                int(color_code[1:], 16)
            )
        except ValueError:
            pass

    embed.add_field(
        name="📋 基本資料",
        value=(
            f"年齡：{display_value(character['age'])}\n"
            f"職業：{display_value(character['occupation'])}"
        ),
        inline=False,
    )

    public_lines = []

    for key, label in PUBLIC_FIELDS.items():
        value = settings.get(key)

        if value is None or value == "":
            continue

        suffix = ""

        if key == "height_cm":
            suffix = " cm"

        elif key == "weight_kg":
            suffix = " kg"

        elif key == "experience_years":
            suffix = " 年"

        public_lines.append(
            f"**{label}**："
            f"{display_value(value)}{suffix}"
        )

    if public_lines:
        add_field_chunks(
            embed,
            "🌐 公開人物設定",
            public_lines,
        )

    public_bio = settings.get(
        "public_biography"
    )

    if public_bio:
        add_field_chunks(
            embed,
            "📖 公開生平",
            [str(public_bio)],
        )

    attributes = read_attributes(
        actor,
        character_id,
    )

    if attributes:
        general = [
            f"{name}：{attributes[name]}"
            for name in ATTRIBUTE_GENERAL
            if name in attributes
        ]

        talents = [
            f"{name}：{attributes[name]}"
            for name in ATTRIBUTE_TALENT
            if name in attributes
        ]

        known = set(
            ATTRIBUTE_GENERAL + ATTRIBUTE_TALENT
        )

        others = [
            f"{name}：{value}"
            for name, value in attributes.items()
            if name not in known
        ]

        if general:
            add_field_chunks(
                embed,
                "📈 商業與名氣",
                general,
            )

        if talents:
            add_field_chunks(
                embed,
                "🎤 藝能與實力",
                talents,
            )

        if others:
            add_field_chunks(
                embed,
                "其他屬性",
                others,
            )

    tracking = read_tracking(
        actor,
        character_id,
    )

    embed.add_field(
        name="📊 系統動態追蹤",
        value=(
            "❤️ 死忠粉絲數："
            f"{int(tracking['loyal_fans']):,}\n"
            "🌪️ 活躍黑粉數："
            f"{int(tracking['active_antis']):,}\n"
            "🌍 大眾認知度："
            f"{int(tracking['public_awareness']):,}\n"
            "💕 CP 好感度："
            "由正式關係系統記錄"
        ),
        inline=False,
    )

    money = read_money_assets(
        actor,
        character_id,
    )

    embed.add_field(
        name="💰 公開貨幣資產",
        value=(
            f"**{money['total']:,} 金幣**\n"
            "包含錢包與銀行存款。\n"
            "不公開帳戶餘額與交易明細。"
        ),
        inline=False,
    )

    if private:
        private_lines = [
            f"**{label}**："
            f"{display_value(settings.get(key), '未填寫')}"
            for key, label in PRIVATE_FIELDS.items()
        ]

        add_field_chunks(
            embed,
            "🔒 私人角色資料",
            private_lines,
        )

    portrait = settings.get(
        "portrait_url"
    )

    if (
        isinstance(portrait, str)
        and portrait.startswith(
            ("https://", "http://")
        )
    ):
        embed.set_image(
            url=portrait
        )

    source = settings.get(
        "portrait_source"
    )

    if source:
        embed.set_footer(
            text=(
                "立繪來源："
                f"{shorten(source, 180)}"
            )
        )

    return embed


def category_text(category, result, keyword=""):
    return (
        f"🎭 **{CATEGORIES[category]}**\n"
        f"搜尋關鍵字：{keyword or '全部'}\n"
        f"符合角色：{result['total']} 名\n"
        f"第 {result['page'] + 1}/"
        f"{result['max_page'] + 1} 頁\n\n"
        "請從下拉選單選擇角色，"
        "或使用搜尋功能。"
    )


class SearchModal(discord.ui.Modal):
    def __init__(self, actor, category):
        super().__init__(
            title="搜尋角色"
        )

        self.actor = actor
        self.category = category

        self.keyword = discord.ui.TextInput(
            label="姓名、藝名或公開代碼",
            required=False,
            max_length=100,
            placeholder="留空代表全部角色",
        )

        self.add_item(self.keyword)

    async def on_submit(self, interaction):
        actor = Actor.from_interaction(
            interaction
        )

        if (
            actor.user_id != self.actor.user_id
            or actor.guild_id != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的搜尋表單。",
                ephemeral=True,
            )
            return

        keyword = str(
            self.keyword.value
        ).strip()

        result = find_characters(
            actor,
            self.category,
            keyword,
        )

        await interaction.response.send_message(
            category_text(
                self.category,
                result,
                keyword,
            ),
            view=CharacterListView(
                actor,
                self.category,
                keyword,
                result["page"],
            ),
            ephemeral=True,
        )


class CharacterCategorySelect(discord.ui.Select):
    def __init__(self, actor):
        self.actor = actor

        options = [
            discord.SelectOption(
                label=label,
                value=code,
                emoji=emoji,
            )
            for code, label, emoji in (
                ("original", "原創角色", "🎭"),
                ("player_npc", "玩家 NPC", "👤"),
                ("official_npc", "官方角色", "🏛️"),
                ("extra", "路人角色", "🚶"),
            )
        ]

        super().__init__(
            placeholder="選擇角色分類",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        actor = Actor.from_interaction(
            interaction
        )

        if (
            actor.user_id != self.actor.user_id
            or actor.guild_id != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的選單。",
                ephemeral=True,
            )
            return

        category = self.values[0]

        result = find_characters(
            actor,
            category,
        )

        await interaction.response.edit_message(
            content=category_text(
                category,
                result,
            ),
            embed=None,
            view=CharacterListView(
                actor,
                category,
            ),
        )


class CategoryView(discord.ui.View):
    def __init__(self, actor):
        super().__init__(timeout=900)

        self.actor = actor

        self.add_item(
            CharacterCategorySelect(actor)
        )


class CharacterItemSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        category,
        keyword,
        page,
        items,
    ):
        self.actor = actor
        self.category = category
        self.keyword = keyword
        self.page = page

        options = []

        for character in items:
            options.append(
                discord.SelectOption(
                    label=shorten(
                        character["name"],
                        100,
                    ),
                    value=str(character["id"]),
                    description=shorten(
                        f"#{character['id']}｜"
                        f"{character['occupation'] or '未設定職業'}",
                        100,
                    ),
                )
            )

        if not options:
            options = [
                discord.SelectOption(
                    label="沒有符合條件的角色",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇要查看的角色",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not bool(items),
            row=0,
        )

    async def callback(self, interaction):
        actor = Actor.from_interaction(
            interaction
        )

        if (
            actor.user_id != self.actor.user_id
            or actor.guild_id != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的角色選單。",
                ephemeral=True,
            )
            return

        try:
            character_id = int(
                self.values[0]
            )

            character = get_character(
                actor,
                character_id,
            )

            if get_category(character) != self.category:
                raise InvalidState(
                    "角色分類已更改，請重新查詢。"
                )

            embed = make_character_embed(
                actor,
                character_id,
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=CharacterDetailView(
                    actor,
                    character_id,
                    self.category,
                    self.keyword,
                    self.page,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


class CharacterListView(discord.ui.View):
    def __init__(
        self,
        actor,
        category,
        keyword="",
        page=0,
    ):
        super().__init__(timeout=900)

        self.actor = actor
        self.category = category
        self.keyword = keyword

        result = find_characters(
            actor,
            category,
            keyword,
            page,
        )

        self.page = result["page"]
        self.max_page = result["max_page"]

        self.add_item(
            CharacterItemSelect(
                actor,
                category,
                keyword,
                self.page,
                result["items"],
            )
        )

        self.previous.disabled = (
            self.page <= 0
        )

        self.next.disabled = (
            self.page >= self.max_page
        )

    async def interaction_check(self, interaction):
        actor = Actor.from_interaction(
            interaction
        )

        if (
            actor.user_id != self.actor.user_id
            or actor.guild_id != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的角色面板。",
                ephemeral=True,
            )
            return False

        self.actor = actor
        return True

    async def move(self, interaction, page):
        result = find_characters(
            self.actor,
            self.category,
            self.keyword,
            page,
        )

        await interaction.response.edit_message(
            content=category_text(
                self.category,
                result,
                self.keyword,
            ),
            embed=None,
            view=CharacterListView(
                self.actor,
                self.category,
                self.keyword,
                result["page"],
            ),
        )

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(self, interaction, button):
        await self.move(
            interaction,
            self.page - 1,
        )

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(self, interaction, button):
        await self.move(
            interaction,
            self.page + 1,
        )

    @discord.ui.button(
        label="🔎 搜尋",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def search(self, interaction, button):
        await interaction.response.send_modal(
            SearchModal(
                self.actor,
                self.category,
            )
        )

    @discord.ui.button(
        label="📂 返回分類",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def categories(self, interaction, button):
        await interaction.response.edit_message(
            content=(
                "🎭 **角色資料中心**\n\n"
                "請選擇要查看的角色分類。"
            ),
            embed=None,
            view=CategoryView(self.actor),
        )


class BatchUseModal(discord.ui.Modal):
    """批量使用物品。

    單次最多 100 次。
    每次獨立結算。
    """

    def __init__(self, actor, character_id):
        super().__init__(
            title="📦 批量使用物品"
        )

        self.user_id = str(actor.user_id)
        self.guild_id = str(actor.guild_id)
        self.character_id = int(character_id)

        self.product = discord.ui.TextInput(
            label="商品編號",
            placeholder="例如：12",
            max_length=12,
            required=True,
        )

        self.count = discord.ui.TextInput(
            label="使用次數（1～100）",
            placeholder="例如：5",
            max_length=3,
            required=True,
        )

        self.add_item(self.product)
        self.add_item(self.count)

    async def on_submit(self, interaction):
        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的背包。",
                ephemeral=True,
            )
            return

        try:
            product_id = int(
                str(self.product.value)
            )

            quantity = int(
                str(self.count.value)
            )

            if product_id <= 0:
                raise ValueError(
                    "商品編號必須大於零。"
                )

            if not 1 <= quantity <= 100:
                raise ValueError(
                    "單次使用次數必須介於 1～100。"
                )

        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ 輸入錯誤：{exc}",
                ephemeral=True,
            )
            return

        actor = Actor.from_interaction(
            interaction
        )

        try:
            require_inventory_owner(
                actor,
                self.character_id,
            )

        except (
            InvalidState,
            PermissionDenied,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        # 只允許一次性道具批量使用。
        # 非消耗品不能透過重複啟用刷取屬性。
        with get_connection() as conn:
            item = conn.execute(
                """
                SELECT effect_kind
                FROM shop_products
                WHERE guild_id = ?
                  AND id = ?
                """,
                (
                    actor.guild_id,
                    product_id,
                ),
            ).fetchone()

        if item is None:
            await interaction.response.send_message(
                "⚠️ 商品不存在。",
                ephemeral=True,
            )
            return

        if item["effect_kind"] != "instant":
            await interaction.response.send_message(
                "⚠️ 這件商品不是一次性效果商品，"
                "請使用單次啟用或穿戴功能。",
                ephemeral=True,
            )
            return

        from modules.shop import use_item

        await interaction.response.defer(
            ephemeral=True
        )

        completed = 0
        error = ""

        for index in range(quantity):
            try:
                await asyncio.to_thread(
                    use_item,
                    actor,
                    self.character_id,
                    product_id,
                    f"{interaction.id}:{index}",
                )

                completed += 1

            except (
                ValueError,
                PermissionDenied,
                InvalidState,
                sqlite3.Error,
            ) as exc:
                error = str(exc)
                break

            except Exception:
                LOG.exception(
                    "背包批量使用失敗"
                )
                error = "系統處理失敗，請聯絡管理員。"
                break

        message = (
            f"📦 **批量使用結果**\n"
            f"商品編號：#{product_id}\n"
            f"預計使用：{quantity} 次\n"
            f"成功使用：{completed} 次"
        )

        if error:
            message += f"\n⚠️ 停止原因：{error}"

        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )


class CharacterDetailView(discord.ui.View):
    def __init__(
        self,
        actor,
        character_id,
        category,
        keyword,
        page,
    ):
        super().__init__(timeout=900)

        self.actor = actor
        self.character_id = character_id
        self.category = category
        self.keyword = keyword
        self.page = page

        character = get_character(
            actor,
            character_id,
        )

        self.is_own_character = is_owner(
            actor,
            character,
        ) and character["status"] == "active"

        self.private_button.disabled = (
            not can_see_private(actor, character)
        )

        self.asset_detail_button.disabled = (
            not can_see_private(actor, character)
        )

        if not self.is_own_character:
            for control in (
                self.inventory_button,
                self.gift_item_button,
                self.use_item_button,
                self.bulk_use_button,
                self.stop_effect_button,
            ):
                self.remove_item(control)

    async def interaction_check(self, interaction):
        actor = Actor.from_interaction(
            interaction
        )

        if (
            actor.user_id != self.actor.user_id
            or actor.guild_id != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的角色面板。",
                ephemeral=True,
            )
            return False

        self.actor = actor
        return True

    @discord.ui.button(
        label="🌐 公開面板",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def public_button(self, interaction, button):
        try:
            embed = make_character_embed(
                self.actor,
                self.character_id,
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=self,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @discord.ui.button(
        label="🔒 私人資料",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def private_button(self, interaction, button):
        try:
            embed = make_character_embed(
                self.actor,
                self.character_id,
                private=True,
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=self,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @discord.ui.button(
        label="💰 資產明細",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def asset_detail_button(self, interaction, button):
        try:
            embed = make_asset_detail_embed(
                self.actor,
                self.character_id,
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

    @discord.ui.button(
        label="🎒 我的背包",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def inventory_button(self, interaction, button):
        actor = Actor.from_interaction(
            interaction
        )

        try:
            require_inventory_owner(
                actor,
                self.character_id,
            )

            from modules.shop_integration import (
                combined_inventory,
            )

            await interaction.response.defer(
                ephemeral=True
            )

            result = await asyncio.to_thread(
                combined_inventory,
                actor,
                self.character_id,
            )

            await interaction.followup.send(
                str(result)[:1900],
                ephemeral=True,
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
            sqlite3.Error,
        ) as exc:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"⚠️ {exc}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"⚠️ {exc}",
                    ephemeral=True,
                )

    async def open_item_action(
        self,
        interaction,
        kind,
    ):
        actor = Actor.from_interaction(
            interaction
        )

        try:
            require_inventory_owner(
                actor,
                self.character_id,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        from modules.shop import ShopActionModal

        menu = SimpleNamespace(
            owner_id=int(actor.user_id),
            character_id=self.character_id,
            guild_id=interaction.guild_id,
        )

        await interaction.response.send_modal(
            ShopActionModal(
                menu,
                kind,
            )
        )

    @discord.ui.button(
        label="🎁 贈送物品",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def gift_item_button(self, interaction, button):
        await self.open_item_action(
            interaction,
            "gift",
        )

    @discord.ui.button(
        label="✨ 使用物品",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def use_item_button(self, interaction, button):
        await self.open_item_action(
            interaction,
            "use",
        )

    @discord.ui.button(
        label="📦 批量使用",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def bulk_use_button(self, interaction, button):
        actor = Actor.from_interaction(
            interaction
        )

        try:
            require_inventory_owner(
                actor,
                self.character_id,
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            BatchUseModal(
                actor,
                self.character_id,
            )
        )

    @discord.ui.button(
        label="⏹️ 停用效果",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def stop_effect_button(self, interaction, button):
        await self.open_item_action(
            interaction,
            "stop",
        )

    @discord.ui.button(
        label="⬅️ 返回角色清單",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(self, interaction, button):
        result = find_characters(
            self.actor,
            self.category,
            self.keyword,
            self.page,
        )

        await interaction.response.edit_message(
            content=category_text(
                self.category,
                result,
                self.keyword,
            ),
            embed=None,
            view=CharacterListView(
                self.actor,
                self.category,
                self.keyword,
                result["page"],
            ),
        )


class CharacterView(commands.Cog):
    """角色與 NPC 資料查看中心。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="查看",
        description="查看角色、屬性、資產及背包",
    )
    @app_commands.guild_only()
    async def view_character(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            await interaction.response.send_message(
                (
                    "🎭 **角色資料中心**\n\n"
                    "請先選擇角色分類，"
                    "再選擇要查看的角色。\n\n"
                    "📋 角色基本資料\n"
                    "📈 十三項角色屬性\n"
                    "📊 系統動態追蹤\n"
                    "💰 公開貨幣資產\n"
                    "🔒 私人資料與帳戶明細\n"
                    "🎒 個人背包\n"
                    "🎁 贈送物品\n"
                    "✨ 使用與停用效果\n"
                    "📦 批量使用物品\n\n"
                    "背包操作僅限角色擁有者。"
                ),
                view=CategoryView(actor),
                ephemeral=True,
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        except sqlite3.Error:
            LOG.exception(
                "角色查看系統發生資料庫錯誤"
            )

            await interaction.response.send_message(
                "⚠️ 角色資料讀取失敗，請聯絡管理員。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CharacterView(bot)
    )
