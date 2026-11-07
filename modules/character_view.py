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

from core.database import get_connection, transaction
from modules.character_view_privacy import visible_biography, update_visibility, visibility_state
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25

CATEGORIES = {
    "mine": "我的角色",
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
    "appearance_aura": "外貌氣質",
    "experience_years": "資歷",
    "public_persona": "公開人設",
}

PRIVATE_FIELDS = {
    "private_personality": "私下性格",
    "sexual_orientation": "性向",
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

        if category == 'mine':
            if not is_owner(actor, character):
                continue
        elif get_category(character) != category:
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
    """原追蹤數值 + 尚未搬入原表的稱號獎勵。

    rp_reward_metrics 目前只存統一獎勵核心發出的增量，
    不覆寫角色既有追蹤資料。
    """
    get_character(actor, character_id)
    result = {
        "loyal_fans": 0,
        "active_antis": 0,
        "public_awareness": 0,
    }
    with get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('character_system_tracking', 'rp_reward_metrics')"
            ).fetchall()
        }
        if "character_system_tracking" in tables:
            row = conn.execute(
                """SELECT loyal_fans, active_antis, public_awareness
                   FROM character_system_tracking
                   WHERE guild_id=? AND character_id=?""",
                (str(actor.guild_id), int(character_id)),
            ).fetchone()
            if row is not None:
                for key in result:
                    result[key] = int(row[key] or 0)

        if "rp_reward_metrics" in tables:
            rows = conn.execute(
                """SELECT metric, value FROM rp_reward_metrics
                   WHERE guild_id=? AND character_id=?
                     AND subject_character_id=0
                     AND metric IN ('粉絲', '大眾認知度')""",
                (str(actor.guild_id), int(character_id)),
            ).fetchall()
            for row in rows:
                if row["metric"] == "粉絲":
                    result["loyal_fans"] += int(row["value"] or 0)
                elif row["metric"] == "大眾認知度":
                    result["public_awareness"] += int(row["value"] or 0)
    return result


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


def _biography_pages(biography, page_chars=1600):
    """Split biography safely without truncating long text in Discord embeds."""
    text = str(biography or "").strip()
    if not text:
        return []
    pages = []
    while text:
        if len(text) <= page_chars:
            pages.append(text)
            break
        split = text.rfind("\n", 0, page_chars + 1)
        if split < page_chars // 2:
            split = text.rfind("。", 0, page_chars + 1)
            if split >= page_chars // 2:
                split += 1
        if split < page_chars // 2:
            split = page_chars
        pages.append(text[:split].strip())
        text = text[split:].strip()
    return pages


def _character_detail_max_page(actor, character_id, private=False):
    """固定前兩頁，最後一頁及可能的續頁均屬於生平。"""
    character = get_character(actor, character_id)
    settings = safe_json(character["settings_json"])
    personality, biography = visible_biography(settings, private and can_see_private(actor, character))
    combined = "\n\n".join(x for x in (personality, biography) if x)
    return 1 + max(1, len(_biography_pages(combined, page_chars=1500)))


def _add_asset_summary(embed, actor, cid):
    """公開僅顯示持有類型與商品名稱，不洩露銀行分錄。"""
    for name, lines in _panel_asset_groups(actor, cid):
        shown = lines[:12]
        summary = "\n".join(shown) or "尚無登記"
        if len(lines) > 12:
            summary += f"\n另有 {len(lines) - 12} 筆（詳見所屬功能）"
        embed.add_field(name=name, value=shorten(summary, 400), inline=False)
    with get_connection() as db:
        if _panel_table(db, "shop_inventory") and _panel_table(db, "shop_products"):
            row = db.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM shop_inventory WHERE guild_id=? AND character_id=?",
                (str(actor.guild_id), int(cid)),
            ).fetchone()
            embed.add_field(name="🎒 商城持有物品", value=f"目前持有 {int(row[0] or 0):,} 件（詳見背包）", inline=False)


def _character_status(actor, character_id, settings, category):
    from modules.character_status import effective_levels
    levels = effective_levels(actor.guild_id)
    lv = int(settings.get("status_level") or settings.get("level") or 1)
    if category != 'extra':
        with get_connection() as db:
            if _panel_table(db, "rp_character_status"):
                try:
                    row = db.execute("SELECT level FROM rp_character_status WHERE guild_id=? AND character_id=?",
                                     (str(actor.guild_id), int(character_id))).fetchone()
                    if row:
                        lv = int(row["level"])
                except sqlite3.OperationalError:
                    pass
    lv = max(1, min(len(levels), lv))
    return f"LV{lv} {levels[lv-1][1]}"


def make_character_embed(actor, character_id, *, private=False, detail_page=0):
    character = get_character(actor, character_id)
    authorized = can_see_private(actor, character)
    if private and not authorized:
        raise PermissionDenied("你沒有查看私人資料的權限。")
    settings = safe_json(character["settings_json"])
    category = get_category(character)
    maximum = _character_detail_max_page(actor, character_id, private=private)
    detail_page = max(0, min(int(detail_page), maximum))
    embed = discord.Embed(
        title=shorten(settings.get("stage_name") or character["name"], 250),
        description=(f"🎭 {CATEGORIES[category]}｜#{character['id']}\n"
                     f"公開代碼：{character.get('public_code') or '未設定'}"),
        color=discord.Color.blurple(),
    )
    color = settings.get("color_hex")
    if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
        try:
            embed.color = discord.Color(int(color[1:], 16))
        except ValueError:
            pass

    if detail_page == 0:
        embed.add_field(
            name="📋 基本資料",
            value=(f"名字：{character['name']}\n"
                   f"藝名：{display_value(settings.get('stage_name'), '未設定')}\n"
                   f"性別：{display_value(settings.get('gender'))}\n"
                   f"年齡：{display_value(character['age'])}\n"
                   f"職業：{display_value(character['occupation'])}"), inline=False,
        )
        try:
            from core.work_schedule import display_lines
            schedule = display_lines(actor.guild_id, character_id)
        except Exception:
            LOG.exception('讀取角色工作檔期失敗：%s', character_id)
            schedule = '檔期資料暫時無法讀取。'
        embed.add_field(name="📅 工作檔期／倒數", value=shorten(schedule, 1024), inline=False)
        embed.add_field(name="🏳️ 所屬陣營", value=_panel_faction(actor, character_id), inline=False)
        embed.add_field(name="💕 正式 CP", value=_panel_cp(actor, character_id), inline=False)
        embed.add_field(name="🏅 角色地位", value=_character_status(actor, character_id, settings, category), inline=False)
        money = read_money_assets(actor, character_id)
        embed.add_field(name="💰 公開貨幣資產", value=f"{money['total']:,} 金幣（錢包及銀行合計）", inline=False)
    elif detail_page == 1:
        attrs = read_attributes(actor, character_id)
        add_field_chunks(embed, "📈 商業與名氣（5 項）", [f"{k}：{attrs.get(k, 0)}" for k in ATTRIBUTE_GENERAL])
        add_field_chunks(embed, "🎤 藝能與實力（8 項）", [f"{k}：{attrs.get(k, 0)}" for k in ATTRIBUTE_TALENT])
        known = set(ATTRIBUTE_GENERAL + ATTRIBUTE_TALENT)
        extra_attrs = [f"{k}：{v}" for k, v in attrs.items() if k not in known]
        if extra_attrs:
            add_field_chunks(embed, "其他數值", extra_attrs)
        tracking = read_tracking(actor, character_id)
        embed.add_field(name="📊 系統動態追蹤", value=(
            f"死忠粉絲：{int(tracking['loyal_fans']):,}\n"
            f"活躍黑粉：{int(tracking['active_antis']):,}\n"
            f"大眾認知度：{int(tracking['public_awareness']):,}"), inline=False)
        _add_asset_summary(embed, actor, character_id)
        if private:
            public_details = [
                f"{label}：{shorten(display_value(settings.get(key), '未填寫'), 95)}"
                for key, label in PUBLIC_FIELDS.items()
                if key not in ('stage_name', 'gender') and settings.get(key) not in (None, '')
            ]
            if public_details:
                add_field_chunks(embed, "🔒 角色補充設定", public_details)
            # 本人／授權管理員能看私下補充設定；性格與身世集中於最後的生平頁。
            private_fields = [
                f"{label}：{shorten(display_value(settings.get(key), '未填寫'), 95)}"
                for key, label in PRIVATE_FIELDS.items()
                if key not in ('private_personality', 'biography')
            ]
            add_field_chunks(embed, "🔒 其他私人資料", private_fields)
            money = read_money_assets(actor, character_id)
            embed.add_field(name="🔒 私人貨幣明細", value=(
                f"錢包：{money['wallet']:,}\n銀行：{money['bank']:,}"), inline=False)
    else:
        personality, biography = visible_biography(settings, private and authorized)
        if not personality and not biography:
            embed.add_field(name="📖 生平", value="此角色尚未公開性格與身世背景。", inline=False)
        else:
            # 每一段獨立分頁；未獲授權者不會讀取隱藏的欄位內容。
            parts = []
            if personality:
                parts.append("🎭 角色性格\n" + personality)
            if biography:
                parts.append("📜 身世背景\n" + biography)
            pages = _biography_pages("\n\n".join(parts), page_chars=1500)
            index = detail_page - 2
            if index < len(pages):
                for i, chunk_start in enumerate(range(0, len(pages[index]), 900)):
                    embed.add_field(name="📖 生平" if i == 0 else "📖 生平（續）",
                                    value=pages[index][chunk_start:chunk_start + 900], inline=False)
    portrait = settings.get("portrait_url")
    if isinstance(portrait, str) and portrait.startswith(("https://", "http://")):
        embed.set_image(url=portrait)
    source = settings.get("portrait_source")
    if source:
        embed.set_footer(text="立繪來源：" + shorten(source, 180))
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
        self.my_characters = discord.ui.Button(
            label="👤 查看自己的角色", style=discord.ButtonStyle.primary, row=1,
        )
        async def open_my_characters(interaction):
            current = Actor.from_interaction(interaction)
            if current.user_id != self.actor.user_id or current.guild_id != self.actor.guild_id:
                await interaction.response.send_message("⛔ 這不是你的查看面板。", ephemeral=True)
                return
            result = find_characters(current, "mine")
            await interaction.response.edit_message(
                content=category_text("mine", result), embed=None,
                view=CharacterListView(current, "mine"),
            )
        self.my_characters.callback = open_my_characters
        self.add_item(self.my_characters)


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

            if (self.category == 'mine' and not is_owner(actor, character)) or (
                self.category != 'mine' and get_category(character) != self.category
            ):
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
        equipped = set()
        if _panel_table(db, 'shop_context_equipment'):
            equipped = {
                int(r['product_id']) for r in db.execute(
                    'SELECT product_id FROM shop_context_equipment WHERE guild_id=? AND character_id=?',
                    (str(actor.guild_id), cid),
                )
            }
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
            st,v=catalog[pid];effect=f'持有加成：{st} +{v}（同款僅計一次）'
        elif stat and value:
            effect=f"{'使用後永久' if r['effect_kind']=='instant' else '條件加成' if r['effect_kind']=='context' else '效果'}：{stat} +{value}"
            if r['effect_kind']=='context':effect+=f"｜{r['effect_context'] or '條件觸發'}"
        else:effect='無直接屬性加成'
        equipment_status = '｜✅ 已穿戴' if pid in equipped else ''
        lines.append(f"**#{pid} {r['name']} ×{qty}{equipment_status}**\n✨ {effect}")
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


def _panel_awards(actor, cid):
    """同時顯示舊版榮譽與新稱號系統的取得／穿戴紀錄。"""
    titles, honors = [], []
    with get_connection() as db:
        if _panel_table(db, 'rp_award_grants') and _panel_table(db, 'rp_award_definitions'):
            rows = db.execute(
                """SELECT d.kind, d.name, d.rewards_json
                   FROM rp_award_grants g
                   JOIN rp_award_definitions d
                     ON d.guild_id=g.guild_id AND d.id=g.definition_id
                   WHERE g.guild_id=? AND g.character_id=? AND g.revoked=0
                   ORDER BY g.granted_at DESC LIMIT 30""",
                (str(actor.guild_id), int(cid)),
            ).fetchall()
            for r in rows:
                data = safe_json(r['rewards_json'])
                title = data.get('title') or (r['name'] if r['kind'] == 'title' else '')
                if title:
                    titles.append(str(title))
                if r['kind'] == 'honor':
                    honors.append(str(r['name']))

        if _panel_table(db, 'rp_character_achievements') and _panel_table(db, 'rp_achievement_definitions'):
            rows = db.execute(
                """SELECT d.name, d.code,
                          COALESCE(e.equipped, 0) AS equipped,
                          a.reward_state
                   FROM rp_character_achievements a
                   JOIN rp_achievement_definitions d
                     ON d.id=a.achievement_id AND d.guild_id=a.guild_id
                   LEFT JOIN rp_title_equipment e
                     ON e.guild_id=a.guild_id
                    AND e.character_id=a.character_id
                    AND e.achievement_id=a.achievement_id
                   WHERE a.guild_id=? AND a.character_id=?
                   ORDER BY a.obtained_at DESC, d.id DESC LIMIT 50""",
                (str(actor.guild_id), int(cid)),
            ).fetchall()
            for r in rows:
                suffix = '（穿戴中）' if bool(r['equipped']) else ''
                if r['reward_state'] != 'done':
                    suffix += '（部分獎勵待發）'
                titles.append(str(r['name']) + suffix)

    # 相同名稱的稱號避免重複顯示。
    titles = list(dict.fromkeys(titles))
    title_lines = '\n'.join('• ' + t for t in titles[:40]) or '尚無'
    honor_lines = '\n'.join('• ' + h for h in honors[:20]) or '尚無'
    return (
        '🌟 **獲得稱號**\n' + title_lines
        + '\n\n🏆 **獲得獎項**\n' + honor_lines
    )


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
    """依照正式陣營表 faction_groups 讀取角色歸屬。"""
    with get_connection() as db:
        if not (_panel_table(db, 'faction_members') and _panel_table(db, 'faction_groups')):
            return '尚未加入陣營'
        row = db.execute(
            """SELECT f.name FROM faction_members m
               JOIN faction_groups f ON f.guild_id=m.guild_id AND f.id=m.faction_id
               WHERE m.guild_id=? AND m.character_id=? LIMIT 1""",
            (str(actor.guild_id), int(cid)),
        ).fetchone()
    return str(row['name']) if row else '尚未加入陣營'


def _panel_asset_groups(actor, cid):
    """分組顯示可核實的非貨幣持有資訊，絕不猜測權屬。"""
    groups = [
        ("🚗 車輛", []), ("🏠 房產", []), ("💎 珠寶／鐘錶", []),
        ("🏢 公司／品牌／集團", []), ("🏬 個人商店", []),
    ]
    with get_connection() as db:
        if _panel_table(db, "shop_inventory") and _panel_table(db, "shop_products"):
            rows = db.execute(
                """SELECT p.name, p.category, i.quantity FROM shop_inventory i
                   JOIN shop_products p ON p.guild_id=i.guild_id AND p.id=i.product_id
                   WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0""",
                (str(actor.guild_id), int(cid)),
            ).fetchall()
            for row in rows:
                category = str(row["category"] or "")
                entry = f"{row['name']} ×{int(row['quantity'])}"
                if "珠寶" in category or "鐘錶" in category or "B03" in category.upper():
                    groups[2][1].append(entry)
                elif "車輛" in category or "汽車" in category:
                    groups[0][1].append(entry)
                elif "房產" in category or "不動產" in category:
                    groups[1][1].append(entry)
        if _panel_table(db, "rp_business_entities"):
            rows = db.execute(
                """SELECT name, kind FROM rp_business_entities
                   WHERE guild_id=? AND owner_character_id=? AND status='active'
                   ORDER BY name LIMIT 40""",
                (str(actor.guild_id), int(cid)),
            ).fetchall()
            kind = {"company": "公司", "brand": "品牌", "group": "集團"}
            groups[3][1].extend(f"{row['name']}（{kind.get(row['kind'], '組織')}）" for row in rows)
        if _panel_table(db, "personal_shops"):
            rows = db.execute(
                """SELECT name, category FROM personal_shops
                   WHERE guild_id=? AND character_id=? AND status!='closed'
                   ORDER BY name LIMIT 30""",
                (str(actor.guild_id), int(cid)),
            ).fetchall()
            groups[4][1].extend(f"{row['name']}（{row['category']}）" for row in rows)
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


class PanelGearModal(discord.ui.Modal):
    """在背包內穿戴或卸下；完成後直接更新原訊息。"""

    def __init__(self, parent, action, page=0):
        super().__init__(title="👗 穿戴裝備" if action == "equip" else "🧥 卸下裝備")
        self.parent = parent
        self.action = action
        self.page = page
        self.product = discord.ui.TextInput(
            label="商品編號", placeholder="例如：12", required=True, max_length=12
        )
        self.add_item(self.product)

    async def on_submit(self, interaction: discord.Interaction):
        actor = Actor.from_interaction(interaction)
        if (str(actor.user_id) != str(self.parent.actor.user_id)
                or str(actor.guild_id) != str(self.parent.actor.guild_id)):
            await interaction.response.send_message("⛔ 這不是你的背包。", ephemeral=True)
            return
        try:
            require_inventory_owner(actor, self.parent.character_id)
            product_id = int(str(self.product.value).strip())
            if product_id <= 0:
                raise ValueError("商品編號必須大於零。")
            from modules import gear
            func = gear.equip if self.action == "equip" else gear.remove
            result = await asyncio.to_thread(
                func, actor, self.parent.character_id, product_id
            )
            info, last = _panel_inventory(actor, self.parent.character_id, self.page)
            await interaction.response.edit_message(
                content=None,
                embed=_panel_embed("🎒 背包", f"{result}\n\n{info}"),
                view=PanelSubView(self.parent, "bag", min(self.page, last)),
            )
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        except ImportError:
            await interaction.response.send_message(
                "⚠️ 找不到 modules/gear.py，請先安裝裝備模組。", ephemeral=True
            )
        except Exception:
            LOG.exception("背包裝備操作失敗")
            await interaction.response.send_message(
                "⚠️ 裝備操作失敗，請查看 Railway 日誌。", ephemeral=True
            )


class PanelGearStatusView(discord.ui.View):
    """只切換原訊息：裝備總覽與回背包。"""

    def __init__(self, parent, page=0):
        super().__init__(timeout=900)
        self.parent = parent
        self.page = page

    async def interaction_check(self, interaction):
        return await self.parent.interaction_check(interaction)

    @discord.ui.button(label="🎒 返回背包", style=discord.ButtonStyle.secondary)
    async def back_to_bag(self, interaction, button):
        await PanelSubView(self.parent, "bag", self.page).show(
            interaction, "bag", self.page
        )


class PanelSubView(discord.ui.View):
    def __init__(self,parent,mode,page=0):
        super().__init__(timeout=900);self.parent=parent;self.mode=mode;self.page=page
        if mode == 'bag':
            # 5 個操作按鈕同一排，符合 Discord 每排最多 5 個元件。
            for label, kind in [
                ('使用', 'use'), ('批量使用', 'batch'), ('贈送', 'gift'),
                ('穿戴', 'equip'), ('卸下', 'remove'),
            ]:
                btn = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.primary if kind in ('use', 'equip')
                    else discord.ButtonStyle.secondary,
                    row=0,
                )
                async def action(interaction, k=kind):
                    if k in ('equip', 'remove'):
                        await interaction.response.send_modal(
                            PanelGearModal(self.parent, k, self.page)
                        )
                    else:
                        await interaction.response.send_modal(
                            PanelItemModal(self.parent, k, self.page)
                        )
                btn.callback = action
                self.add_item(btn)
            status_btn = discord.ui.Button(
                label='裝備狀態', style=discord.ButtonStyle.secondary, row=1
            )
            async def show_gear(interaction):
                try:
                    from modules import gear
                    text = await asyncio.to_thread(
                        gear.current_equipment,
                        Actor.from_interaction(interaction),
                        self.parent.character_id,
                    )
                    await interaction.response.edit_message(
                        content=None,
                        embed=_panel_embed('👗 目前裝備與被動加成', text),
                        view=PanelGearStatusView(self.parent, self.page),
                    )
                except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
                    await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
                except ImportError:
                    await interaction.response.send_message(
                        '⚠️ 找不到 modules/gear.py。', ephemeral=True
                    )
            status_btn.callback = show_gear
            self.add_item(status_btn)
            redeem_btn = discord.ui.Button(
                label='🎁 特殊兌換', style=discord.ButtonStyle.secondary, row=1
            )
            async def open_redeem(interaction):
                from modules.redeem_ui import show_player
                await show_player(interaction, self.parent)
            redeem_btn.callback = open_redeem
            self.add_item(redeem_btn)
            title_btn = discord.ui.Button(
                label='🎖️ 稱號', style=discord.ButtonStyle.secondary, row=1
            )
            async def open_titles(interaction):
                try:
                    from modules.achievement_player_ui import (
                        AchievementPlayerView, ensure_character_access,
                        seed_fixed_titles, escape,
                    )
                    cid = self.parent.character_id
                    character = ensure_character_access(interaction, cid)
                    seed_fixed_titles(interaction.guild_id)
                    view = AchievementPlayerView(interaction, cid)
                    await interaction.response.edit_message(
                        content=(f"角色：**{escape(character['name'])}**\n\n"
                                 + view.render()),
                        embed=None,
                        view=view,
                    )
                except (ValueError, PermissionError, sqlite3.Error) as exc:
                    if interaction.response.is_done():
                        await interaction.followup.send(f'⚠️ {exc}', ephemeral=True)
                    else:
                        await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
                except Exception:
                    LOG.exception('背包開啟稱號管理失敗')
                    if interaction.response.is_done():
                        await interaction.followup.send('⚠️ 稱號介面無法開啟。', ephemeral=True)
                    else:
                        await interaction.response.send_message('⚠️ 稱號介面無法開啟。', ephemeral=True)
            title_btn.callback = open_titles
            self.add_item(title_btn)
        if mode == 'relations' and get_category(get_character(self.parent.actor, self.parent.character_id)) == 'extra':
            like = discord.ui.Button(label='🤍 喜歡／取消喜歡', style=discord.ButtonStyle.secondary, row=0)
            async def toggle_like(interaction):
                from modules.npc_likes import toggle
                actor = Actor.from_interaction(interaction)
                try:
                    toggle(actor.guild_id, self.parent.character_id, actor.user_id)
                    await self.show(interaction, 'relations', self.page)
                except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
                    await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
            like.callback = toggle_like
            self.add_item(like)
        if mode=='relations' and self.parent.is_own_character:
            btn=discord.ui.Button(label='自訂關係',style=discord.ButtonStyle.secondary,row=0)
            async def relation(interaction):await interaction.response.send_modal(PanelRelationModal(self.parent, self.page))
            btn.callback=relation;self.add_item(btn)
        if mode in ('bag','relations','works','endorsement'):
            previous=discord.ui.Button(label='◀️ 上一頁',style=discord.ButtonStyle.secondary,row=2 if mode=='bag' else 1)
            next_button=discord.ui.Button(label='▶️ 下一頁',style=discord.ButtonStyle.secondary,row=2 if mode=='bag' else 1)
            async def prev(interaction):await self.show(interaction,self.mode,max(0,self.page-1))
            async def nxt(interaction):await self.show(interaction,self.mode,self.page+1)
            previous.callback=prev;next_button.callback=nxt;self.add_item(previous);self.add_item(next_button)
        back=discord.ui.Button(label='⬅️ 返回',style=discord.ButtonStyle.secondary,row=3 if mode=='bag' else 2)
        async def go_back(interaction):
            await interaction.response.edit_message(content=None,embed=make_character_embed(Actor.from_interaction(interaction),self.parent.character_id,private=self.parent.display_private,detail_page=self.parent.detail_page),view=self.parent)
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
        await interaction.response.edit_message(content=None,embed=make_character_embed(Actor.from_interaction(interaction),self.parent.character_id,private=self.parent.display_private,detail_page=self.parent.detail_page),view=self.parent)


class CharacterEditStartView(discord.ui.View):
    def __init__(self, user_id, character_id, guild_id):
        super().__init__(timeout=600)
        self.user_id = str(user_id)
        self.character_id = int(character_id)
        self.guild_id = str(guild_id)

    async def interaction_check(self, interaction):
        if str(interaction.user.id) != self.user_id or str(interaction.guild_id) != self.guild_id:
            await interaction.response.send_message('這不是你的角色修改面板。', ephemeral=True)
            return False
        return True

    def refresh_buttons(self):
        with get_connection() as conn:
            row = conn.execute('SELECT settings_json FROM characters WHERE guild_id=? AND id=? AND owner_id=?',
                               (self.guild_id, self.character_id, self.user_id)).fetchone()
        flags = visibility_state(safe_json(row['settings_json'])) if row else {}
        self.personality_visibility.label = ('🌐 性格公開' if flags.get('personality') else '🔒 性格不公開')
        self.biography_visibility.label = ('🌐 身世公開' if flags.get('biography') else '🔒 身世不公開')

    @discord.ui.button(label='✏️ 一次修改並送出', style=discord.ButtonStyle.primary, row=0)
    async def open_editor(self, interaction, button):
        from modules.application_extras import ApplicationModal
        await interaction.response.send_modal(ApplicationModal(interaction, 'character_edit', self.character_id))

    async def _toggle(self, interaction, field):
        try:
            with get_connection() as db:
                row = db.execute('SELECT settings_json FROM characters WHERE guild_id=? AND id=?',
                                 (self.guild_id, self.character_id)).fetchone()
            if row is None:
                raise ValueError('角色已不存在。')
            current = visibility_state(safe_json(row['settings_json']))[field]
            update_visibility(self.guild_id, self.character_id, self.user_id, field, not current)
            self.refresh_buttons()
            await interaction.response.edit_message(view=self)
        except (PermissionError, ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)

    @discord.ui.button(label='🔒 性格不公開', style=discord.ButtonStyle.secondary, row=1)
    async def personality_visibility(self, interaction, button):
        await self._toggle(interaction, 'personality')

    @discord.ui.button(label='🔒 身世不公開', style=discord.ButtonStyle.secondary, row=1)
    async def biography_visibility(self, interaction, button):
        await self._toggle(interaction, 'biography')


class CharacterDetailView(discord.ui.View):
    def __init__(self,actor,character_id,category,keyword,page):
        super().__init__(timeout=900)
        self.actor=actor;self.character_id=int(character_id);self.category=category;self.keyword=keyword;self.page=page;self.detail_page=0;self.display_private=False
        character=get_character(actor,character_id)
        self.is_own_character=is_owner(actor,character) and character['status']=='active'
        self.private_button.disabled=not can_see_private(actor,character)
        self.asset_detail_button.disabled=not can_see_private(actor,character)
        if not self.is_own_character:self.inventory_button.disabled=True
        # 路人人氣功能移至「關係」子頁，不佔主面板規定的四排按鈕。
        self.remove_item(self.like_button)
        self.edit_request_button.disabled = not (self.is_own_character and get_category(character) in ('original','player_npc'))
        self.leaderboard_points_button.disabled = not self.is_own_character
        self._sync_paging()

    def _sync_paging(self):
        self.previous_detail_page.disabled = self.detail_page <= 0
        self.next_detail_page.disabled = self.detail_page >= _character_detail_max_page(self.actor, self.character_id, private=self.display_private)

    async def interaction_check(self,interaction):
        actor=Actor.from_interaction(interaction)
        if actor.user_id!=self.actor.user_id or actor.guild_id!=self.actor.guild_id:
            await interaction.response.send_message('⛔ 這不是你的角色面板。',ephemeral=True);return False
        self.actor=actor;return True
    @discord.ui.button(label='🌐 公開',style=discord.ButtonStyle.secondary,row=0)
    async def public_button(self,interaction,button):
        self.detail_page=0;self.display_private=False
        self._sync_paging()
        await interaction.response.edit_message(content=None,embed=make_character_embed(self.actor,self.character_id,detail_page=self.detail_page),view=self)
    @discord.ui.button(label='🔒 私人',style=discord.ButtonStyle.primary,row=0)
    async def private_button(self,interaction,button):
        self.detail_page=0;self.display_private=True
        self._sync_paging()
        await interaction.response.edit_message(content=None,embed=make_character_embed(self.actor,self.character_id,private=True,detail_page=self.detail_page),view=self)
    @discord.ui.button(label='💰 資產',style=discord.ButtonStyle.secondary,row=0)
    async def asset_detail_button(self,interaction,button):
        await interaction.response.edit_message(content=None,embed=make_asset_detail_embed(self.actor,self.character_id),view=self)
    @discord.ui.button(label='🎒 背包',style=discord.ButtonStyle.primary,row=0)
    async def inventory_button(self,interaction,button):
        require_inventory_owner(self.actor,self.character_id)
        await PanelSubView(self,'bag').show(interaction,'bag')
    @discord.ui.button(label='◀️',style=discord.ButtonStyle.secondary,row=3)
    async def previous_detail_page(self,interaction,button):
        self.detail_page = max(0, self.detail_page - 1)
        self._sync_paging()
        await interaction.response.edit_message(content=None,embed=make_character_embed(self.actor,self.character_id,private=self.display_private,detail_page=self.detail_page),view=self)

    @discord.ui.button(label='▶️',style=discord.ButtonStyle.secondary,row=3)
    async def next_detail_page(self,interaction,button):
        self.detail_page = min(_character_detail_max_page(self.actor, self.character_id, private=self.display_private), self.detail_page + 1)
        self._sync_paging()
        await interaction.response.edit_message(content=None,embed=make_character_embed(self.actor,self.character_id,private=self.display_private,detail_page=self.detail_page),view=self)

    @discord.ui.button(label='📖 攻略',style=discord.ButtonStyle.secondary,row=1)
    async def guides_button(self,interaction,button):
        await PanelSubView(self,'routes').show(interaction,'routes')
    @discord.ui.button(label='💕 關係',style=discord.ButtonStyle.secondary,row=1)
    async def relationship_button(self,interaction,button):
        await PanelSubView(self,'relations').show(interaction,'relations')
    @discord.ui.button(label='🏆 成就榮譽',style=discord.ButtonStyle.secondary,row=1)
    async def awards_button(self,interaction,button):
        await PanelSubView(self,'awards').show(interaction,'awards')
    def _edit_controls(self, interaction):
        view = CharacterEditStartView(interaction.user.id, self.character_id, interaction.guild_id)
        view.refresh_buttons()
        return view

    @discord.ui.button(label='📝 修改角色資料',style=discord.ButtonStyle.primary,row=2)
    async def edit_request_button(self,interaction,button):
        try:
            character = get_character(Actor.from_interaction(interaction), self.character_id)
            if not is_owner(self.actor, character) or get_category(character) not in ('original','player_npc'):
                raise PermissionDenied('只有原創角色或玩家 NPC 的持有人可以修改資料。')
            if character['status'] != 'active':
                raise InvalidState('只有已啟用角色可以修改資料。')
            from modules.application_extras import CHARACTER_EDIT_FIELDS, character_field_value
            settings = safe_json(character['settings_json'])
            embed = discord.Embed(
                title='📝 角色資料修改｜目前資料',
                description='檢查下列原始資料後按「一次修改並送出」。表單會帶入原值，只須改動想修改的項目。\n'
                            '一般文字直接生效；重大變更及玩家 NPC 性格送審。數值與陣營不在此修改。\n'
                            '下方兩個隱私開關可獨立切換，立即生效；不會更改角色文字或觸發重審。',
                color=discord.Color.blurple(),
            )
            for key, label in CHARACTER_EDIT_FIELDS.items():
                if key == 'biography' and get_category(character) != 'original':
                    continue
                original = str(character_field_value(character, settings, key) or '（未填寫）')
                embed.add_field(name=label, value=original[:900], inline=True)
            await interaction.response.send_message(
                embed=embed,
                view=self._edit_controls(interaction),
                ephemeral=True,
            )
        except (ValueError, sqlite3.Error, PermissionDenied, InvalidState) as exc:
            await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
    @discord.ui.button(label='🎯 點數分配',style=discord.ButtonStyle.primary,row=2)
    async def leaderboard_points_button(self,interaction,button):
        if not self.is_own_character:
            await interaction.response.send_message('只有已啟用角色的持有人可以分配點數。',ephemeral=True)
            return
        try:
            from modules.leaderboard_points_discord import open_points_panel
            await open_points_panel(interaction,self.character_id)
        except Exception as exc:
            if not interaction.response.is_done():
                await interaction.response.send_message(f'⚠️ 點數面板暫時無法開啟：{exc}',ephemeral=True)

    @discord.ui.button(label='⬅️ 返回',style=discord.ButtonStyle.secondary,row=3)
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
        角色: str | None = None,
    ):
        from modules.task_event_bridge import require_tutorial_channel, mark_tutorial_after_reply
        allowed_tutorial, training_character_id = await require_tutorial_channel(interaction, '查看')
        if not allowed_tutorial:
            return
        try:
            actor = Actor.from_interaction(
                interaction
            )

            # 直接指定編號或完整名稱；不修改目前角色狀態。
            if 角色 is not None and str(角色).strip():
                keyword = str(角色).strip()
                with get_connection() as conn:
                    if keyword.isdecimal():
                        rows = conn.execute(
                            "SELECT * FROM characters WHERE guild_id=? AND id=?",
                            (actor.guild_id, int(keyword)),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM characters WHERE guild_id=? AND name=? COLLATE NOCASE ORDER BY id LIMIT 26",
                            (actor.guild_id, keyword),
                        ).fetchall()

                allowed = [
                    dict(row) for row in rows
                    if can_see_character(actor, dict(row))
                ]
                if not allowed:
                    raise InvalidState("找不到可查看的角色，請確認編號或完整名稱。")
                if len(allowed) != 1:
                    raise InvalidState("有多位角色使用相同名稱，請改用角色編號。")

                character = allowed[0]
                cid = int(character["id"])
                await interaction.response.send_message(
                    embed=make_character_embed(actor, cid),
                    view=CharacterDetailView(
                        actor, cid, get_category(character), keyword, 0,
                    ),
                    ephemeral=True,
                )
                return

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
            mark_tutorial_after_reply(interaction, '查看', training_character_id)

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
