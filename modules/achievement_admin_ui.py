
"""Discord RP｜官方成就稱號管理中心。

檔案：
    modules/achievement_admin_ui.py

依賴：
    achievement_core.py
    achievement_rewards.py
    achievement_effects.py
    achievement_triggers.py

功能：
- 查看固定／活動稱號
- 建立活動專屬稱號
- 自訂一次性獎勵
- 設定每日加成或商城折扣
- 啟用及停用活動稱號
- 設定正式開服日期
- 建立傳奇見證者官方活動
- 登記活動參與及任務完成
- 指定主辦人確認
- 群主最終確認
- 群主特別覆核

注意：
本模組是官方管理介面。
不會讓玩家自行取得稱號。

通用折扣的適用範圍與自訂期限會保存，
但必須升級 achievement_effects.py 及商城結帳模組，
才能完整執行。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)

from modules.achievement_core import (
    init_db as init_core,
    seed_fixed_titles,
    create_event_title,
    list_title_definitions,
)

from modules.achievement_triggers import (
    init_db as init_triggers,
    set_server_opening,
    create_legend_event,
    record_legend_progress,
    approve_legend,
)


LOG = logging.getLogger(__name__)


# ==========================================
# 基本工具
# ==========================================

def is_owner(interaction: discord.Interaction):
    return bool(
        interaction.guild
        and interaction.user.id
        == interaction.guild.owner_id
    )


def owner_id(interaction: discord.Interaction):
    if interaction.guild is None:
        raise ValueError("只能在伺服器使用。")

    return interaction.guild.owner_id


async def respond(
    interaction: discord.Interaction,
    message: str,
):
    """確保回覆不會超過 Discord 長度限制。"""

    message = str(message)

    if len(message) > 1900:
        message = message[:1890] + "……"

    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True,
            allowed_mentions=(
                discord.AllowedMentions.none()
            ),
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True,
            allowed_mentions=(
                discord.AllowedMentions.none()
            ),
        )


def escape(value):
    return discord.utils.escape_markdown(
        str(value)
    )


def positive_int(value, label):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"{label}必須是正整數。"
        )

    if number <= 0:
        raise ValueError(
            f"{label}必須大於零。"
        )

    return number


def nonnegative_int(value, label):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"{label}必須是非負整數。"
        )

    if number < 0:
        raise ValueError(
            f"{label}不能小於零。"
        )

    return number


def load_json_dict(raw, label):
    try:
        value = json.loads(
            str(raw).strip()
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label}不是有效 JSON：{exc.msg}"
        )

    if not isinstance(value, dict):
        raise ValueError(
            f"{label}必須是 JSON 物件。"
        )

    return value


def normalize_event_code(value):
    code = str(value).strip()

    if not code:
        raise ValueError(
            "活動稱號代碼不可空白。"
        )

    if not code.startswith("event:"):
        code = "event:" + code

    if not re.fullmatch(
        r"event:[A-Za-z0-9_-]{1,70}",
        code,
    ):
        raise ValueError(
            "代碼只能使用英文字母、數字、"
            "底線及減號。"
        )

    return code


def ensure_initialized(guild_id):
    init_core()
    init_triggers()
    seed_fixed_titles(guild_id)


# ==========================================
# 取得稱號資料
# ==========================================

def get_title(guild_id, code):
    ensure_initialized(guild_id)

    with get_connection() as db:
        row = db.execute(
            """
            SELECT *
            FROM rp_achievement_definitions
            WHERE guild_id=?
              AND code=?
            """,
            (
                str(guild_id),
                str(code),
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "找不到指定稱號。"
        )

    result = dict(row)

    result["condition"] = json.loads(
        result["condition_json"]
    )

    result["reward"] = json.loads(
        result["reward_json"]
    )

    result["effect"] = json.loads(
        result["effect_json"]
    )

    return result


def set_title_enabled(
    guild_id,
    code,
    enabled,
):
    """固定稱號不可在一般介面停用。

    停用活動稱號不刪除既有取得紀錄。
    """

    title = get_title(
        guild_id,
        code,
    )

    if title["category"] != "event":
        raise PermissionError(
            "固定稱號不能在此停用，"
            "請使用專用規則調整功能。"
        )

    with transaction() as db:
        db.execute(
            """
            UPDATE rp_achievement_definitions

            SET enabled=?

            WHERE guild_id=?
              AND code=?
              AND category='event'
            """,
            (
                int(bool(enabled)),
                str(guild_id),
                code,
            ),
        )


# ==========================================
# 權限限制 View
# ==========================================

class OwnerView(discord.ui.View):

    def __init__(
        self,
        interaction: discord.Interaction,
        *,
        timeout=600,
    ):
        super().__init__(
            timeout=timeout
        )

        self.owner_user_id = (
            interaction.user.id
        )

        self.guild_id = (
            interaction.guild_id
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if (
            interaction.guild_id
            != self.guild_id
            or interaction.user.id
            != self.owner_user_id
            or not is_owner(interaction)
        ):
            await respond(
                interaction,
                "⛔ 只有開啟此面板的群主可以操作。",
            )
            return False

        return True


# ==========================================
# 活動稱號：新增
# ==========================================

class CreateEventTitleModal(
    discord.ui.Modal,
    title="建立活動專屬稱號",
):

    title_code = discord.ui.TextInput(
        label="稱號代碼（英文）",
        placeholder="anniversary_2026",
        max_length=70,
    )

    title_name = discord.ui.TextInput(
        label="稱號名稱",
        placeholder="週年慶典見證者",
        max_length=100,
    )

    condition_field = discord.ui.TextInput(
        label="取得條件 JSON",
        style=discord.TextStyle.paragraph,
        placeholder=(
            '{"type":"official_event",'
            '"event_id":"anniversary_2026"}'
        ),
        default=(
            '{"type":"official_event",'
            '"event_id":"anniversary_2026"}'
        ),
        max_length=1000,
    )

    reward_field = discord.ui.TextInput(
        label="一次性獎勵 JSON",
        style=discord.TextStyle.paragraph,
        placeholder=(
            '{"free_points":100,'
            '"heat":20,"fans":50}'
        ),
        default='{"free_points":100}',
        max_length=1000,
    )

    effect_field = discord.ui.TextInput(
        label="168小時效果 JSON（可填 none）",
        style=discord.TextStyle.paragraph,
        placeholder=(
            '{"type":"daily",'
            '"stats":{"heat":10}}'
        ),
        default='{"type":"none"}',
        max_length=1500,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if not is_owner(interaction):
            return await respond(
                interaction,
                "⛔ 僅群主能建立稱號。",
            )

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            code = normalize_event_code(
                self.title_code.value
            )

            name = str(
                self.title_name.value
            ).strip()

            condition = load_json_dict(
                self.condition_field.value,
                "取得條件",
            )

            reward = load_json_dict(
                self.reward_field.value,
                "一次性獎勵",
            )

            effect = load_json_dict(
                self.effect_field.value,
                "稱號效果",
            )

            # 基本結構檢查，防止寫入不合法資料。
            validate_reward(reward)
            validate_effect(effect)

            achievement_id = create_event_title(
                interaction.guild_id,
                code,
                name,
                condition,
                reward,
                effect,
            )

            await respond(
                interaction,
                (
                    "✅ 活動專屬稱號建立成功！\n"
                    f"名稱：**{escape(name)}**\n"
                    f"代碼：`{code}`\n"
                    f"編號：`{achievement_id}`\n\n"
                    "稱號已登記，但不會自動授予玩家。"
                ),
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ 建立失敗：{exc}",
            )


# ==========================================
# 獎勵資料驗證
# ==========================================

KNOWN_REWARD_KEYS = {
    "free_points",
    "heat",
    "business_value",
    "favor",
    "pr",
    "influence",
    "appearance",
    "intelligence",
    "fans",
    "recognition",
    "all_performance_stats",
    "money",
    "action_item",
    "ordinary_apartment_room",
}


def validate_reward(reward):
    """驗證目前系統支援的獎勵名稱。"""

    for key, amount in reward.items():

        if key not in KNOWN_REWARD_KEYS:
            raise ValueError(
                f"尚未支援的獎勵類型：{key}"
            )

        if (
            type(amount) is not int
            or amount < 0
        ):
            raise ValueError(
                f"{key} 必須是非負整數。"
            )


def validate_effect(effect):
    effect_type = effect.get(
        "type",
        "none",
    )

    valid_types = {
        "none",
        "daily",
        "random_daily",
        "shop_discount",
    }

    if effect_type not in valid_types:
        raise ValueError(
            "不支援的稱號效果類型。"
        )

    if effect_type == "daily":

        stats = effect.get("stats")

        if not isinstance(stats, dict):
            raise ValueError(
                "每日效果需要 stats 字典。"
            )

        allowed = {
            "heat",
            "business_value",
            "favor",
            "pr",
            "appearance",
            "intelligence",
            "fans",
            "recognition",
        }

        for key, amount in stats.items():
            if key not in allowed:
                raise ValueError(
                    f"未支援的每日加成：{key}"
                )

            if (
                type(amount) is not int
                or amount <= 0
            ):
                raise ValueError(
                    "每日加成必須是正整數。"
                )

    elif effect_type == "random_daily":

        amount = effect.get(
            "amount"
        )

        if (
            type(amount) is not int
            or amount <= 0
        ):
            raise ValueError(
                "隨機加成必須是正整數。"
            )

    elif effect_type == "shop_discount":

        rate = effect.get(
            "rate"
        )

        if (
            type(rate) not in (int, float)
            or not 0 < rate <= 1
        ):
            raise ValueError(
                "折扣倍率必須大於 0 且小於等於 1。"
            )


# ==========================================
# 商城折扣快速建立
# ==========================================

DISCOUNT_SCOPES = {
    "all": "全商城",
    "category": "指定商品分類",
    "brand": "指定品牌或商店",
    "faction": "陣營商店",
    "event": "活動限定商品",
}


class DiscountTitleModal(
    discord.ui.Modal,
    title="建立折扣類活動稱號",
):

    code_field = discord.ui.TextInput(
        label="稱號英文代碼",
        placeholder="shopping_festival",
        max_length=70,
    )

    name_field = discord.ui.TextInput(
        label="稱號名稱",
        placeholder="購物狂歡",
        max_length=100,
    )

    discount_field = discord.ui.TextInput(
        label="折扣百分比（5～90）",
        placeholder="20（代表八折）",
        max_length=2,
    )

    scope_field = discord.ui.TextInput(
        label="範圍 all/category/brand/faction/event",
        default="all",
        max_length=20,
    )

    details_field = discord.ui.TextInput(
        label="指定範圍ID（全商城留空）",
        required=False,
        placeholder="例如 clothing",
        max_length=100,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if not is_owner(interaction):
            return await respond(
                interaction,
                "⛔ 只有群主能建立折扣稱號。",
            )

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            code = normalize_event_code(
                self.code_field.value
            )

            name = str(
                self.name_field.value
            ).strip()

            percent = positive_int(
                self.discount_field.value,
                "折扣百分比",
            )

            if percent > 90:
                raise ValueError(
                    "折扣百分比不能超過 90%。"
                )

            scope = str(
                self.scope_field.value
            ).strip().lower()

            if scope not in DISCOUNT_SCOPES:
                raise ValueError(
                    "範圍只能使用 all、category、"
                    "brand、faction、event。"
                )

            target = str(
                self.details_field.value or ""
            ).strip()

            if scope != "all" and not target:
                raise ValueError(
                    "指定範圍必須填寫目標 ID。"
                )

            # 20% OFF = 0.8 倍原價。
            rate = round(
                (100 - percent) / 100,
                4,
            )

            effect = {
                "type": "shop_discount",
                "rate": rate,
                "discount_percent": percent,
                "scope": scope,
                "target_id": target or None,
                "duration_hours": 168,
                "activation": "equipped",
                "stacking": "highest",
            }

            # 活動條件只能由官方後續核准。
            # 建立稱號不等於達成條件。
            condition = {
                "type": "manual_official",
                "description": (
                    "需由官方確認取得資格"
                ),
            }

            achievement_id = create_event_title(
                interaction.guild_id,
                code,
                name,
                condition,
                {},
                effect,
            )

            explanation = (
                "全商城"
                if scope == "all"
                else (
                    f"{DISCOUNT_SCOPES[scope]}"
                    f"（{target}）"
                )
            )

            await respond(
                interaction,
                (
                    "✅ 折扣稱號建立成功！\n"
                    f"名稱：**{escape(name)}**\n"
                    f"代碼：`{code}`\n"
                    f"稱號 ID：`{achievement_id}`\n"
                    f"折扣：**{percent}% OFF**\n"
                    f"適用範圍：{escape(explanation)}\n"
                    "有效期限：首次穿戴起 168 小時\n"
                    "生效條件：必須穿戴\n"
                    "多稱號折扣：取最大優惠\n\n"
                    "⚠️ 指定分類、品牌、陣營及活動"
                    "折扣尚需升級效果模組和商城"
                    "結帳流程，才能正式生效。"
                ),
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )


# ==========================================
# 稱號啟用／停用
# ==========================================

class ToggleTitleModal(
    discord.ui.Modal,
    title="啟用或停用活動稱號",
):

    code_field = discord.ui.TextInput(
        label="活動稱號代碼",
        placeholder="event:shopping_festival",
        max_length=100,
    )

    operation_field = discord.ui.TextInput(
        label="操作：啟用 或 停用",
        placeholder="停用",
        max_length=2,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if not is_owner(interaction):
            return await respond(
                interaction,
                "⛔ 只有群主能修改稱號。",
            )

        try:
            code = normalize_event_code(
                self.code_field.value
            )

            operation = str(
                self.operation_field.value
            ).strip()

            if operation not in {
                "啟用",
                "停用",
            }:
                raise ValueError(
                    "只能輸入「啟用」或「停用」。"
                )

            enabled = (
                operation == "啟用"
            )

            set_title_enabled(
                interaction.guild_id,
                code,
                enabled,
            )

            await respond(
                interaction,
                (
                    f"✅ 已{operation}稱號 "
                    f"`{code}`。\n"
                    "既有取得紀錄不會刪除。"
                ),
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )


# ==========================================
# 正式開服日期
# ==========================================

class OpeningDateModal(
    discord.ui.Modal,
    title="設定伺服器正式開服日期",
):

    date_field = discord.ui.TextInput(
        label="開服日期（台灣時間）",
        placeholder="2026-11-01 12:00",
        max_length=16,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if not is_owner(interaction):
            return await respond(
                interaction,
                "⛔ 僅限群主。",
            )

        try:
            from zoneinfo import ZoneInfo

            local_time = datetime.strptime(
                str(self.date_field.value).strip(),
                "%Y-%m-%d %H:%M",
            ).replace(
                tzinfo=ZoneInfo("Asia/Taipei")
            )

            opened_at = local_time.astimezone(
                timezone.utc
            )

            set_server_opening(
                interaction.guild_id,
                interaction.user.id,
                owner_id(interaction),
                opened_at,
            )

            await respond(
                interaction,
                (
                    "✅ 正式開服日期已設定為：\n"
                    f"**{local_time:%Y-%m-%d %H:%M}**"
                    "（台灣時間）\n\n"
                    "「開城元老」將依這個日期"
                    "判定建立與啟用資格。"
                ),
            )

        except ValueError as exc:
            await respond(
                interaction,
                f"⚠️ 日期格式錯誤：{exc}",
            )


# ==========================================
# 傳奇見證者：建立活動
# ==========================================

class CreateLegendEventModal(
    discord.ui.Modal,
    title="建立傳奇見證者指定活動",
):

    event_id_field = discord.ui.TextInput(
        label="活動英文代碼",
        placeholder="main_story_001",
        max_length=100,
    )

    event_name_field = discord.ui.TextInput(
        label="活動名稱",
        placeholder="世界主線：終焉之夜",
        max_length=150,
    )

    organizer_field = discord.ui.TextInput(
        label="指定主辦人 Discord ID",
        placeholder="123456789012345678",
        max_length=22,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        if not is_owner(interaction):
            return await respond(
                interaction,
                "⛔ 只有群主能建立指定活動。",
            )

        try:
            organizer = positive_int(
                self.organizer_field.value,
                "主辦人 ID",
            )

            # 確認指定的是伺服器內的真實成員。
            member = interaction.guild.get_member(
                organizer
            )

            if member is None:
                member = (
                    await interaction.guild.fetch_member(
                        organizer
                    )
                )

            create_legend_event(
                interaction.guild_id,
                interaction.user.id,
                owner_id(interaction),
                str(
                    self.event_id_field.value
                ).strip(),
                str(
                    self.event_name_field.value
                ).strip(),
                member.id,
            )

            await respond(
                interaction,
                (
                    "✅ 傳奇見證者指定活動已建立！\n"
                    f"活動：{escape(self.event_name_field.value)}\n"
                    f"代碼：`{self.event_id_field.value}`\n"
                    f"主辦人：`{member.id}`\n\n"
                    "完成指定任務後，仍需要"
                    "主辦人及群主分別確認。"
                ),
            )

        except (
            ValueError,
            discord.HTTPException,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ 建立活動失敗：{exc}",
            )


# ==========================================
# 傳奇見證者：參與及任務進度
# ==========================================

class LegendProgressModal(
    discord.ui.Modal,
    title="登記傳奇見證者活動進度",
):

    event_id_field = discord.ui.TextInput(
        label="活動代碼",
        max_length=100,
    )

    character_field = discord.ui.TextInput(
        label="角色編號",
        max_length=20,
    )

    progress_field = discord.ui.TextInput(
        label="進度：參與 或 完成",
        placeholder="完成",
        max_length=2,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        try:
            progress = str(
                self.progress_field.value
            ).strip()

            if progress not in {
                "參與",
                "完成",
            }:
                raise ValueError(
                    "進度只能輸入「參與」或「完成」。"
                )

            cid = positive_int(
                self.character_field.value,
                "角色編號",
            )

            record_legend_progress(
                interaction.guild_id,
                str(
                    self.event_id_field.value
                ).strip(),
                cid,
                interaction.user.id,
                owner_id(interaction),
                participated=True,
                task_completed=(
                    progress == "完成"
                ),
            )

            await respond(
                interaction,
                (
                    f"✅ 角色 #{cid} "
                    f"已登記活動進度：{progress}。\n"
                    "只有完成任務的角色"
                    "才能進入核准程序。"
                ),
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )


# ==========================================
# 傳奇見證者：確認
# ==========================================

class LegendApprovalModal(
    discord.ui.Modal,
    title="傳奇見證者資格確認",
):

    event_field = discord.ui.TextInput(
        label="活動代碼",
        max_length=100,
    )

    character_field = discord.ui.TextInput(
        label="角色編號",
        max_length=20,
    )

    action_field = discord.ui.TextInput(
        label="操作：主辦／群主／特別覆核",
        placeholder="群主",
        max_length=4,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        try:
            action = str(
                self.action_field.value
            ).strip()

            mapping = {
                "主辦": "organizer",
                "群主": "owner",
                "特別覆核": "owner",
            }

            role = mapping.get(
                action
            )

            if role is None:
                raise ValueError(
                    "請輸入主辦、群主或特別覆核。"
                )

            cid = positive_int(
                self.character_field.value,
                "角色編號",
            )

            result = approve_legend(
                interaction.guild_id,
                str(
                    self.event_field.value
                ).strip(),
                cid,
                interaction.user.id,
                owner_id(interaction),
                role=role,
                owner_override=(
                    action == "特別覆核"
                ),
            )

            if result["completed"]:
                message = (
                    f"✅ 角色 #{cid} 已完成"
                    "「傳奇見證者」資格確認！\n"
                    "稱號已解鎖或先前已取得，"
                    "一次性獎勵已提交發放流程。"
                )
            else:
                message = (
                    f"✅ 角色 #{cid} 的"
                    f"「{action}」確認已記錄。\n"
                    "尚待另一位指定審核者確認。"
                )

            await respond(
                interaction,
                message,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )


# ==========================================
# 主辦人專用面板
# ==========================================

class LegendOrganizerView(
    discord.ui.View
):

    def __init__(
        self,
        interaction: discord.Interaction,
    ):
        super().__init__(
            timeout=600
        )

        self.user_id = interaction.user.id
        self.guild_id = interaction.guild_id

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.user_id
            or interaction.guild_id != self.guild_id
        ):
            await respond(
                interaction,
                "這不是你的活動管理介面。",
            )
            return False

        return True

    @discord.ui.button(
        label="📋 登記活動進度",
        style=discord.ButtonStyle.primary,
    )
    async def progress(self, interaction, button):
        await interaction.response.send_modal(
            LegendProgressModal()
        )

    @discord.ui.button(
        label="✅ 主辦人確認",
        style=discord.ButtonStyle.success,
    )
    async def organizer_approve(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            LegendApprovalModal()
        )


# ==========================================
# 稱號清單文字
# ==========================================

def format_titles(guild_id, category=None):
    titles = list_title_definitions(
        guild_id,
        include_disabled=True,
    )

    if category:
        titles = [
            item
            for item in titles
            if item["category"] == category
        ]

    if not titles:
        return "目前沒有符合條件的稱號。"

    lines = []

    for item in titles:
        status = (
            "✅"
            if item["enabled"]
            else "⛔"
        )

        category_name = (
            "固定"
            if item["category"] == "fixed"
            else "活動"
        )

        lines.append(
            f"{status} **{escape(item['name'])}**"
            f"｜{category_name}\n"
            f"代碼：`{item['code']}`"
        )

    message = "\n\n".join(lines)

    if len(message) > 1800:
        return (
            message[:1750]
            + "\n\n……清單較長，"
            "請使用分類查看。"
        )

    return message


# ==========================================
# 官方主面板
# ==========================================

class AchievementAdminView(
    OwnerView
):

    def __init__(
        self,
        interaction: discord.Interaction,
    ):
        super().__init__(interaction)

        ensure_initialized(
            interaction.guild_id
        )

    @discord.ui.button(
        label="📋 固定稱號",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def fixed_list(
        self,
        interaction,
        button,
    ):
        await respond(
            interaction,
            "🎖️ **固定稱號列表**\n\n"
            + format_titles(
                interaction.guild_id,
                "fixed",
            ),
        )

    @discord.ui.button(
        label="🏆 活動稱號",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def event_list(
        self,
        interaction,
        button,
    ):
        await respond(
            interaction,
            "🏆 **活動專屬稱號列表**\n\n"
            + format_titles(
                interaction.guild_id,
                "event",
            ),
        )

    @discord.ui.button(
        label="➕ 新增活動稱號",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def new_event_title(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            CreateEventTitleModal()
        )

    @discord.ui.button(
        label="🏷️ 新增折扣稱號",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def new_discount_title(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            DiscountTitleModal()
        )

    @discord.ui.button(
        label="⚙️ 啟用／停用",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def toggle_title(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            ToggleTitleModal()
        )

    @discord.ui.button(
        label="📅 設定開服日期",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def opening_date(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            OpeningDateModal()
        )

    @discord.ui.button(
        label="🌍 建立重大活動",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def create_major_event(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            CreateLegendEventModal()
        )

    @discord.ui.button(
        label="📜 活動任務進度",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def legend_progress(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            LegendProgressModal()
        )

    @discord.ui.button(
        label="✅ 傳奇稱號審核",
        style=discord.ButtonStyle.success,
        row=3,
    )
    async def legend_approve(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            LegendApprovalModal()
        )

    @discord.ui.button(
        label="↩️ 返回內容建立",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        from modules.content_builder import (
            ContentView,
        )

        await interaction.response.edit_message(
            content="✍️ **內容建立中心**",
            view=ContentView(interaction),
        )


# ==========================================
# 官方入口按鈕
# ==========================================

class AchievementAdminButton(
    discord.ui.Button
):

    def __init__(self, row=2):
        super().__init__(
            label="🎖️ 成就稱號",
            style=discord.ButtonStyle.primary,
            row=row,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if not is_owner(interaction):
            return await respond(
                interaction,
                "⛔ 只有群主可以進入稱號管理。",
            )

        await interaction.response.edit_message(
            content=(
                "🎖️ **成就稱號管理中心**\n"
                "可建立活動稱號、設定折扣、"
                "管理重大活動及審核資格。"
            ),
            view=AchievementAdminView(interaction),
        )


# ==========================================
# Discord 模組入口
# ==========================================

async def setup(bot: commands.Bot):
    init_core()
    init_triggers()

    LOG.info(
        "官方成就稱號管理介面已載入。"
    )
