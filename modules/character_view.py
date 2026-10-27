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

    # 車輛、房產、珠寶、公司；不存在的來源顯示尚未登記，不虛構資產。
    for group_name, group_lines in _panel_asset_groups(actor, character_id):
        embed.add_field(
            name=group_name,
            value=shorten("\n".join(group_lines) if group_lines else "尚無登記", 1024),
            inline=False,
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

    # 十三項屬性全部公開，包括目前沒有紀錄的 0 點屬性。
    general = [f"{name}：{attributes.get(name, 0)}" for name in ATTRIBUTE_GENERAL]
    talents = [f"{name}：{attributes.get(name, 0)}" for name in ATTRIBUTE_TALENT]
    add_field_chunks(embed, "📈 商業與名氣（5 項）", general)
    add_field_chunks(embed, "🎤 藝能與實力（8 項）", talents)
    known = set(ATTRIBUTE_GENERAL + ATTRIBUTE_TALENT)
    others = [f"{name}：{value}" for name, value in attributes.items() if name not in known]
    if others:
        add_field_chunks(embed, "其他已登記屬性", others)

    embed.add_field(
        name="🏳️ 所屬陣營",
        value=_panel_faction(actor, character_id),
        inline=False,
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
            "💕 正式 CP："
            f"{_panel_cp(actor, character_id)}"
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

        await interaction.response.edit_message(
            content=category_text(self.category, result, keyword),
            embed=None,
            view=CharacterListView(actor, self.category, keyword, result["page"]),
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


class PanelItemModal(discord.ui.Modal):
    """背包操作：成功後編輯來源訊息，不額外發送結果訊息。"""

    def __init__(self, parent, kind, page=0):
        super().__init__(title={"use":"使用物品", "batch":"批量使用物品", "gift":"贈送物品"}[kind])
        self.parent = parent
        self.kind = kind
        self.page = page
        self.product = discord.ui.TextInput(label="商品編號", placeholder="例如：12", max_length=12)
        self.add_item(self.product)
        if kind in ("batch", "gift"):
            self.quantity = discord.ui.TextInput(label="使用次數（1～100）" if kind == "batch" else "贈送數量（1～10000）", max_length=5, default="1")
            self.add_item(self.quantity)
        if kind == "gift":
            self.recipient = discord.ui.TextInput(label="收禮角色編號", max_length=12)
            self.add_item(self.recipient)

    async def on_submit(self, interaction):
        actor = Actor.from_interaction(interaction)
        if (str(actor.user_id) != str(self.parent.actor.user_id) or
                str(actor.guild_id) != str(self.parent.actor.guild_id)):
            await interaction.response.send_message("⛔ 這不是你的背包。", ephemeral=True)
            return
        try:
            require_inventory_owner(actor, self.parent.character_id)
            pid = int(str(self.product.value))
            qty = int(str(self.quantity.value)) if self.kind in ("batch", "gift") else 1
            if pid <= 0 or qty < 1 or qty > (100 if self.kind == "batch" else 10000):
                raise ValueError("商品編號或數量不正確。")
            if self.kind == "gift":
                target = int(str(self.recipient.value))
                if target <= 0:
                    raise ValueError("收禮角色編號不正確。")
            if self.kind == "batch":
                with get_connection() as db:
                    item = db.execute("SELECT effect_kind FROM shop_products WHERE guild_id=? AND id=?", (str(actor.guild_id), pid)).fetchone()
                    if item is None:
                        raise ValueError("商品不存在。")
                    # 多次使用商品仍由現有商城使用橋接器處理；
                    # 批量呼叫同一個 use_item 並確保每次使用都有唯一交易鍵。
                    if item["effect_kind"] != "instant":
                        raise InvalidState("批量使用僅支援消耗型商品。")
            from modules import shop
            completed = 0
            result = ""
            if self.kind == "gift":
                result = await asyncio.to_thread(shop.gift, actor, self.parent.character_id, target, pid, qty, str(interaction.id))
            else:
                for i in range(qty):
                    try:
                        result = await asyncio.to_thread(shop.use_item, actor, self.parent.character_id, pid, f"{interaction.id}:{i}")
                        completed += 1
                    except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as err:
                        if not completed:
                            raise
                        result = f"成功 {completed}/{qty} 次；停止原因：{err}"
                        break
            info, last = _panel_inventory(actor, self.parent.character_id, self.page)
            view = PanelSubView(self.parent, "bag", min(self.page, last))
            await interaction.response.edit_message(
                content=None,
                embed=_panel_embed("🎒 背包", f"✅ {result}\n\n{info}"),
                view=view,
            )
        except (ValueError, PermissionDenied, InvalidState, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        except Exception:
            LOG.exception("背包操作失敗")
            await interaction.response.send_message("⚠️ 背包操作失敗，請查看 Railway 日誌。", ephemeral=True)


def _panel_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _panel_embed(title, text):
    return discord.Embed(title=title, description=str(text)[:3900] or "尚無紀錄。", color=discord.Color.blurple())


def _panel_cp(actor, cid):
    with get_connection() as db:
        if not _panel_table(db, 'rp_cp_pairs'):
            return "尚未建立正式 CP。"
        rows=db.execute("""SELECT c.id,c.name FROM rp_cp_pairs p JOIN characters c
          ON c.guild_id=p.guild_id AND c.id=(CASE WHEN p.a=? THEN p.b ELSE p.a END)
          WHERE p.guild_id=? AND (p.a=? OR p.b=?)""",
          (cid,str(actor.guild_id),cid,cid)).fetchall()
    return "、".join(f"{r['name']}（#{r['id']}）" for r in rows) or "尚未建立正式 CP。"


def _panel_relationships(actor,cid,page=0):
    with get_connection() as db:
        if not _panel_table(db,'rp_relationship_scores'):
            return '尚無好感度紀錄。',0
        rows=db.execute("""SELECT r.a,r.b,r.kind,r.points,c.name,
         COALESCE(t.name,'') AS relation_name
         FROM rp_relationship_scores r JOIN characters c
         ON c.guild_id=r.guild_id AND c.id=CASE WHEN r.a=? THEN r.b ELSE r.a END
         LEFT JOIN rp_relation_labels t ON t.guild_id=r.guild_id AND t.a=r.a AND t.b=r.b
         WHERE r.guild_id=? AND (r.a=? OR r.b=?)
         ORDER BY c.name,r.kind""", (cid,str(actor.guild_id),cid,cid)).fetchall() if _panel_table(db,'rp_relation_labels') else db.execute("""SELECT r.a,r.b,r.kind,r.points,c.name,'' AS relation_name
         FROM rp_relationship_scores r JOIN characters c
         ON c.guild_id=r.guild_id AND c.id=CASE WHEN r.a=? THEN r.b ELSE r.a END
         WHERE r.guild_id=? AND (r.a=? OR r.b=?) ORDER BY c.name,r.kind""",
         (cid,str(actor.guild_id),cid,cid)).fetchall()
        cp=db.execute('SELECT a,b FROM rp_cp_pairs WHERE guild_id=? AND (a=? OR b=?)',(str(actor.guild_id),cid,cid)).fetchall() if _panel_table(db,'rp_cp_pairs') else []
        labels=db.execute("SELECT a,b,name FROM rp_relation_labels WHERE guild_id=? AND (a=? OR b=?)",(str(actor.guild_id),cid,cid)).fetchall() if _panel_table(db,'rp_relation_labels') else []
        names={}
        for r in rows:
            other=r['b'] if r['a']==cid else r['a']
            x=names.setdefault(other,{'name':r['name'],'labels':'','scores':[]})
            x['labels']=r['relation_name'] or x['labels']
            x['scores'].append(f"{'CP' if r['kind']=='cp' else '贈禮' if r['kind']=='gift' else r['kind']} {r['points']:,}")
        for r in labels:
            other=r['b'] if r['a']==cid else r['a']
            if other not in names:
                name=db.execute('SELECT name FROM characters WHERE guild_id=? AND id=?',(str(actor.guild_id),other)).fetchone()
                names[other]={'name':name['name'] if name else f'#{other}','labels':'','scores':[]}
            names[other]['labels']=r['name']
        for r in cp:
            other=r['b'] if r['a']==cid else r['a']
            if other not in names:
                name=db.execute('SELECT name FROM characters WHERE guild_id=? AND id=?',(str(actor.guild_id),other)).fetchone()
                names[other]={'name':name['name'] if name else f'#{other}','labels':'','scores':[]}
            names[other]['scores'].append('💕 正式 CP')
    entries=[f"**{v['name']}（#{k}）**｜關係：{v['labels'] or '未設定'}\n好感度：{'／'.join(v['scores']) or '尚無分數'}" for k,v in sorted(names.items())]
    last=max(0,(len(entries)-1)//10);page=max(0,min(page,last))
    return ('\n\n'.join(entries[page*10:page*10+10]) or '目前尚無角色關係紀錄。')+f'\n\n第 {page+1}/{last+1} 頁',last


def _panel_inventory(actor,cid,page=0):
    with get_connection() as db:
        rows=db.execute("""SELECT i.product_id,i.quantity,p.name,p.effect_kind,p.effect_stat,p.effect_value,
             p.effect_context FROM shop_inventory i JOIN shop_products p
             ON p.guild_id=i.guild_id AND p.id=i.product_id
             WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0 ORDER BY p.id""",
             (str(actor.guild_id),cid)).fetchall()
        rules={}
        if _panel_table(db,'item_use_settings'):
            rules={int(r['product_id']):int(r['max_uses']) for r in db.execute('SELECT product_id,max_uses FROM item_use_settings WHERE guild_id=?',(str(actor.guild_id),))}
        remains={}
        if _panel_table(db,'item_instances'):
            for r in db.execute('SELECT product_id,remaining_uses FROM item_instances WHERE guild_id=? AND character_id=? ORDER BY remaining_uses,id',(str(actor.guild_id),cid)):
                remains.setdefault(int(r['product_id']),[]).append(int(r['remaining_uses']))
        catalog={}
        for table in ('b02_catalog','b04_catalog'):
            if _panel_table(db,table):
                for r in db.execute(f"SELECT product_id,bonus_stat,bonus_value,item_type FROM {table} WHERE guild_id=?",(str(actor.guild_id),)):
                    if r['item_type']=='持有': catalog[int(r['product_id'])]=(r['bonus_stat'],int(r['bonus_value']))
    lines=[]
    for r in rows:
        pid=int(r['product_id']);qty=int(r['quantity']);stat=r['effect_stat'];value=int(r['effect_value'] or 0)
        if pid in rules:
            effect=f"每次：{stat or '未設定'} +{value}｜每件 {rules[pid]} 次"
            if pid in remains:effect+=f"｜剩餘次數：{'、'.join(map(str,remains[pid][:12]))}"+("…" if len(remains[pid])>12 else '')
        elif pid in catalog:
            st,v=catalog[pid];effect=f'預定持有：{st} +{v}（尚未接入生效）'
        elif stat and value:
            effect=f"{'使用後永久' if r['effect_kind']=='instant' else '條件加成' if r['effect_kind']=='context' else '效果'}：{stat} +{value}"
            if r['effect_kind']=='context':effect+=f"｜{r['effect_context'] or '條件觸發'}"
        else:effect='無直接屬性加成'
        lines.append(f"**#{pid} {r['name']} ×{qty}**\n✨ {effect}")
    # 玩家商店物品同樣列入背包；目前該類商品尚無統一的加成欄位。
    with get_connection() as db:
        if _panel_table(db,'personal_store_inventory') and _panel_table(db,'personal_store_products'):
            for r in db.execute('''SELECT p.id,p.name,i.quantity FROM personal_store_inventory i
                 JOIN personal_store_products p ON p.guild_id=i.guild_id AND p.id=i.product_id
                 WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0 ORDER BY p.id''',
                 (str(actor.guild_id),cid)).fetchall():
                lines.append(f"**玩家商店 #{r['id']} {r['name']} ×{r['quantity']}**\n✨ 尚未設定統一的數值／次數資料")
    last=max(0,(len(lines)-1)//7);page=max(0,min(page,last))
    return ('\n\n'.join(lines[page*7:page*7+7]) or '目前背包沒有官方商城物品。')+f'\n\n第 {page+1}/{last+1} 頁',last


def _panel_awards(actor,cid):
    with get_connection() as db:
        if not _panel_table(db,'rp_award_grants'):
            return '目前尚無稱號與獎項。'
        rows=db.execute("""SELECT d.kind,d.name,d.rewards_json FROM rp_award_grants g
           JOIN rp_award_definitions d ON d.guild_id=g.guild_id AND d.id=g.definition_id
           WHERE g.guild_id=? AND g.character_id=? AND g.revoked=0 ORDER BY g.granted_at DESC LIMIT 30""",
           (str(actor.guild_id),cid)).fetchall()
    titles=[];honors=[]
    for r in rows:
        data=safe_json(r['rewards_json']);title=data.get('title') or (r['name'] if r['kind']=='title' else '')
        if title:titles.append(str(title))
        if r['kind']=='honor':honors.append(r['name'])
    return f"🌟 **獲得稱號**\n{', '.join(titles) or '尚無'}\n\n🏆 **獲得獎項**\n"+('\n'.join(honors[:20]) or '尚無')


def _panel_career(actor,cid,mode,page=0):
    with get_connection() as db:
        if not _panel_table(db,'career_registrations'):
            return '尚無登記作品與代言。',0
        types=('代言',) if mode=='endorsement' else ('電影','電視劇','網路劇','音樂作品','MV','綜藝','舞台劇','其他','商業活動')
        placeholders=','.join('?' for _ in types)
        rows=db.execute(f"""SELECT r.title,r.status,t.name AS category FROM career_registrations r
          JOIN registration_types t ON t.id=r.type_id AND t.guild_id=r.guild_id
          WHERE r.guild_id=? AND r.character_id=? AND t.name IN ({placeholders})
            AND r.status='approved' ORDER BY r.id DESC""",
          [str(actor.guild_id),cid,*types]).fetchall()
    last=max(0,(len(rows)-1)//10);page=max(0,min(page,last))
    lines=[f"{page*10+i+1}. **{r['title']}**｜{r['category']}｜已核准" for i,r in enumerate(rows[page*10:page*10+10])]
    return ('\n'.join(lines) or '目前尚無已核准的'+('代言' if mode=='endorsement' else '作品')+'紀錄。')+f'\n\n第 {page+1}/{last+1} 頁',last


def _panel_faction(actor, cid):
    """目前沒有正式陣營資料結構時，不臆測角色歸屬。"""
    with get_connection() as db:
        # 若日後陣營模組建立此關聯表，公開面板可立即顯示。
        if _panel_table(db, "faction_members") and _panel_table(db, "factions"):
            try:
                row = db.execute(
                    """SELECT f.name FROM faction_members m
                       JOIN factions f ON f.guild_id=m.guild_id AND f.id=m.faction_id
                       WHERE m.guild_id=? AND m.character_id=? LIMIT 1""",
                    (str(actor.guild_id), cid),
                ).fetchone()
                if row:
                    return str(row["name"])
            except sqlite3.OperationalError:
                pass
    return "尚未加入／尚未建立陣營資料"


def _panel_asset_groups(actor, cid):
    """只讀取現有的資產來源；沒有正式權屬資料的類別不杜撰。"""
    groups = [("🚗 車輛", []), ("🏠 房產", []), ("💎 珠寶", []), ("🏢 公司", [])]
    with get_connection() as db:
        if _panel_table(db, "shop_inventory") and _panel_table(db, "shop_products"):
            rows = db.execute(
                """SELECT p.name, p.category, i.quantity
                   FROM shop_inventory i
                   JOIN shop_products p ON p.guild_id=i.guild_id AND p.id=i.product_id
                   WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0""",
                (str(actor.guild_id), cid),
            ).fetchall()
            for row in rows:
                name = str(row["name"])
                category = str(row["category"] or "")
                qty = int(row["quantity"])
                if "珠寶" in category or "鐘錶" in category or "B03" in category.upper():
                    groups[2][1].append(f"{name} ×{qty}")
                elif "車輛" in category or "汽車" in category:
                    groups[0][1].append(f"{name} ×{qty}")
                elif "房產" in category or "不動產" in category:
                    groups[1][1].append(f"{name} ×{qty}")
    # 公司、車、房的獨立所有權須等正式登記系統串接。
    return groups


def _panel_routes(actor,cid):
    # 劇情需由未來「內容建立」功能正式建立；此處只讀取，不建立故事。
    return '目前尚未建立官方角色攻略劇情。\n\n未觸發的劇情將只顯示前置任務；完成前置任務後才公開劇情名稱、順序及未完成條件。'


class PanelRelationModal(discord.ui.Modal, title="設定角色關係"):
    """儲存自訂關係，成功後直接刷新原本的關係面板。"""

    def __init__(self, parent, page=0):
        super().__init__()
        self.parent = parent
        self.cid = parent.character_id
        self.page = page
        self.other = discord.ui.TextInput(
            label="對方角色編號", required=True, max_length=12
        )
        self.relation = discord.ui.TextInput(
            label="自訂關係（例如：曖昧、好奇、水火不容）",
            required=True,
            max_length=40,
        )
        self.add_item(self.other)
        self.add_item(self.relation)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            actor = Actor.from_interaction(interaction)
            require_inventory_owner(actor, self.cid)
            other = int(str(self.other.value))
            name = str(self.relation.value).strip()
            if other <= 0 or other == self.cid or not name:
                raise ValueError("請填寫有效的對方角色編號及關係名稱。")

            a, b = sorted((self.cid, other))
            with get_connection() as db:
                exists = db.execute(
                    "SELECT 1 FROM characters WHERE guild_id=? AND id=? "
                    "AND status!='deleted'",
                    (str(actor.guild_id), other),
                ).fetchone()
                if not exists:
                    raise ValueError("找不到對方角色。")

            from core.database import transaction
            with transaction() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS rp_relation_labels ("
                    "guild_id TEXT NOT NULL, a INTEGER NOT NULL, "
                    "b INTEGER NOT NULL, name TEXT NOT NULL, "
                    "PRIMARY KEY(guild_id,a,b))"
                )
                db.execute(
                    "INSERT INTO rp_relation_labels(guild_id,a,b,name) "
                    "VALUES(?,?,?,?) ON CONFLICT(guild_id,a,b) "
                    "DO UPDATE SET name=excluded.name",
                    (str(actor.guild_id), a, b, name),
                )

            # Modal 由原角色面板的按鈕開啟，直接編輯來源訊息。
            text, last = _panel_relationships(actor, self.cid, self.page)
            page = max(0, min(self.page, last))
            view = PanelSubView(self.parent, "relations", page)
            await interaction.response.edit_message(
                content=None,
                embed=_panel_embed("💕 關係", text),
                view=view,
            )
        except (ValueError, PermissionDenied, InvalidState, sqlite3.Error) as exc:
            # Modal 失敗時不能保證來源訊息可編輯，使用短暫的私密錯誤通知。
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


class PanelSubView(discord.ui.View):
    def __init__(self,parent,mode,page=0):
        super().__init__(timeout=900);self.parent=parent;self.mode=mode;self.page=page
        if mode=='bag':
            for label,kind in [('使用','use'),('批量使用','batch'),('贈送','gift')]:
                btn=discord.ui.Button(label=label,style=discord.ButtonStyle.primary if kind=='use' else discord.ButtonStyle.secondary,row=0)
                async def action(interaction,k=kind):
                    await interaction.response.send_modal(PanelItemModal(self.parent,k,self.page))
                btn.callback=action;self.add_item(btn)
        if mode=='awards':
            for label,kind in [('成就','works'),('榮譽','endorsement')]:
                btn=discord.ui.Button(label=label,style=discord.ButtonStyle.secondary,row=0)
                async def action(interaction,k=kind):await self.show(interaction,k,0)
                btn.callback=action;self.add_item(btn)
        if mode=='relations' and self.parent.is_own_character:
            btn=discord.ui.Button(label='自訂關係',style=discord.ButtonStyle.secondary,row=0)
            async def relation(interaction):await interaction.response.send_modal(PanelRelationModal(self.parent, self.page))
            btn.callback=relation;self.add_item(btn)
        if mode in ('bag','relations','works','endorsement'):
            previous=discord.ui.Button(label='上一頁',style=discord.ButtonStyle.secondary,row=1)
            next_button=discord.ui.Button(label='下一頁',style=discord.ButtonStyle.secondary,row=1)
            async def prev(interaction):await self.show(interaction,self.mode,max(0,self.page-1))
            async def nxt(interaction):await self.show(interaction,self.mode,self.page+1)
            previous.callback=prev;next_button.callback=nxt;self.add_item(previous);self.add_item(next_button)
        back=discord.ui.Button(label='返回原畫面',style=discord.ButtonStyle.secondary,row=2)
        async def go_back(interaction):
            await interaction.response.edit_message(content=None,embed=make_character_embed(Actor.from_interaction(interaction),self.parent.character_id),view=self.parent)
        back.callback=go_back;self.add_item(back)
    async def interaction_check(self,interaction):return await self.parent.interaction_check(interaction)
    async def show(self,interaction,mode,page=0):
        actor=Actor.from_interaction(interaction);cid=self.parent.character_id
        if mode=='bag':text,last=_panel_inventory(actor,cid,page);title='🎒 背包'
        elif mode=='relations':text,last=_panel_relationships(actor,cid,page);title='💕 關係'
        elif mode=='awards':text=_panel_awards(actor,cid);last=0;title='🏆 成就與榮譽'
        elif mode in ('works','endorsement'):text,last=_panel_career(actor,cid,mode,page);title='🎬 已完成作品' if mode=='works' else '📣 代言紀錄'
        else:text=_panel_routes(actor,cid);last=0;title='📖 官方角色攻略'
        page=max(0,min(page,last));view=PanelSubView(self.parent,mode,page)
        await interaction.response.edit_message(content=None,embed=_panel_embed(title,text),view=view)


class PublicPanelView(discord.ui.View):
    """公開資料的次級頁面，關係已在主頁第二排；此類僅供舊版本相容。"""
    def __init__(self,parent):
        super().__init__(timeout=900);self.parent=parent
    async def interaction_check(self,interaction):
        return await self.parent.interaction_check(interaction)
    @discord.ui.button(label='💕 關係',style=discord.ButtonStyle.primary,row=0)
    async def relationships(self,interaction,button):
        await PanelSubView(self.parent,'relations').show(interaction,'relations')
    @discord.ui.button(label='⬅️ 返回原畫面',style=discord.ButtonStyle.secondary,row=0)
    async def back(self,interaction,button):
        await interaction.response.edit_message(content=None,embed=make_character_embed(Actor.from_interaction(interaction),self.parent.character_id),view=self.parent)


class CharacterDetailView(discord.ui.View):
    def __init__(self,actor,character_id,category,keyword,page):
        super().__init__(timeout=900)
        self.actor=actor;self.character_id=int(character_id);self.category=category;self.keyword=keyword;self.page=page
        character=get_character(actor,character_id)
        self.is_own_character=is_owner(actor,character) and character['status']=='active'
        self.private_button.disabled=not can_see_private(actor,character)
        self.asset_detail_button.disabled=not can_see_private(actor,character)
        if not self.is_own_character:self.inventory_button.disabled=True
    async def interaction_check(self,interaction):
        actor=Actor.from_interaction(interaction)
        if actor.user_id!=self.actor.user_id or actor.guild_id!=self.actor.guild_id:
            await interaction.response.send_message('⛔ 這不是你的角色面板。',ephemeral=True);return False
        self.actor=actor;return True
    @discord.ui.button(label='🌐 公開',style=discord.ButtonStyle.secondary,row=0)
    async def public_button(self,interaction,button):
        await interaction.response.edit_message(content=None,embed=make_character_embed(self.actor,self.character_id),view=self)
    @discord.ui.button(label='🔒 私人',style=discord.ButtonStyle.primary,row=0)
    async def private_button(self,interaction,button):
        await interaction.response.edit_message(content=None,embed=make_character_embed(self.actor,self.character_id,private=True),view=self)
    @discord.ui.button(label='💰 資產',style=discord.ButtonStyle.secondary,row=0)
    async def asset_detail_button(self,interaction,button):
        await interaction.response.edit_message(content=None,embed=make_asset_detail_embed(self.actor,self.character_id),view=self)
    @discord.ui.button(label='🎒 背包',style=discord.ButtonStyle.primary,row=0)
    async def inventory_button(self,interaction,button):
        require_inventory_owner(self.actor,self.character_id)
        await PanelSubView(self,'bag').show(interaction,'bag')
    @discord.ui.button(label='📖 攻略',style=discord.ButtonStyle.secondary,row=1)
    async def guides_button(self,interaction,button):
        await PanelSubView(self,'routes').show(interaction,'routes')
    @discord.ui.button(label='💕 關係',style=discord.ButtonStyle.secondary,row=1)
    async def relationship_button(self,interaction,button):
        await PanelSubView(self,'relations').show(interaction,'relations')
    @discord.ui.button(label='🏆 成就榮譽',style=discord.ButtonStyle.secondary,row=1)
    async def awards_button(self,interaction,button):
        await PanelSubView(self,'awards').show(interaction,'awards')
    @discord.ui.button(label='⬅️ 返回',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,interaction,button):
        result=find_characters(self.actor,self.category,self.keyword,self.page)
        await interaction.response.edit_message(content=category_text(self.category,result,self.keyword),embed=None,view=CharacterListView(self.actor,self.category,self.keyword,result['page']))

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
