
"""Discord RP｜玩家成就稱號管理介面。

檔案：modules/achievement_player_ui.py

指令：
    /稱號
    /稱號 角色編號

功能：
- 自動選擇自己的角色或指定角色
- 查看已取得及尚未取得的稱號
- 穿戴／卸下稱號
- 將穿戴中的稱號加入角色面板展示
- 最多展示 3 個稱號
- 穿戴數量不設上限
- 顯示首次穿戴時間與效果剩餘時間
- 群主可管理任何角色
- 分頁支援超過 25 個稱號

重要：
首次穿戴時間由 achievement_core.py 保存。
卸下及重新穿戴均不會重置 168 小時計時。
本模組不會直接發放稱號獎勵。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection

from modules.achievement_core import (
    seed_fixed_titles,
    list_title_definitions,
    list_character_titles,
    equip_title,
    set_display_titles,
    title_remaining_seconds,
)


LOG = logging.getLogger(__name__)

PAGE_SIZE = 20


# ==========================================
# 基礎工具
# ==========================================

def is_owner(interaction):
    return bool(
        interaction.guild is not None
        and interaction.user.id
        == interaction.guild.owner_id
    )


async def respond(interaction, content):
    """回覆互動，不公開玩家私人操作。"""

    content = str(content)

    if len(content) > 1900:
        content = content[:1890] + "……"

    if interaction.response.is_done():
        await interaction.followup.send(
            content,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    else:
        await interaction.response.send_message(
            content,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def escape(value):
    value = str(value)

    return discord.utils.escape_markdown(
        discord.utils.escape_mentions(value)
    )


def display_time(value):
    if not value:
        return "尚未啟動"

    dt = datetime.fromisoformat(str(value))

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return (
        f"<t:{int(dt.timestamp())}:f>"
    )


def remaining_text(seconds):
    if seconds is None:
        return "無 168 小時效果"

    if seconds <= 0:
        return "已結束"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts = []

    if days:
        parts.append(f"{days} 天")

    if hours:
        parts.append(f"{hours} 小時")

    if minutes and not days:
        parts.append(f"{minutes} 分鐘")

    return " ".join(parts) or "不足 1 分鐘"


# ==========================================
# 角色資料與權限
# ==========================================

def get_character(guild_id, character_id):
    with get_connection() as db:
        row = db.execute(
            """
            SELECT
                id,
                owner_id,
                name,
                character_type,
                status
            FROM characters
            WHERE guild_id=?
              AND id=?
            """,
            (
                str(guild_id),
                int(character_id),
            ),
        ).fetchone()

    if row is None:
        raise ValueError("找不到這個角色。")

    return dict(row)


def accessible_characters(
    interaction,
    limit=25,
):
    """列出玩家可管理的角色。"""

    if interaction.guild is None:
        return []

    gid = str(interaction.guild_id)
    uid = str(interaction.user.id)

    with get_connection() as db:
        if is_owner(interaction):
            rows = db.execute(
                """
                SELECT
                    id,
                    name,
                    owner_id,
                    status
                FROM characters
                WHERE guild_id=?
                  AND status IN ('approved', 'active')
                ORDER BY id DESC
                LIMIT ?
                """,
                (gid, int(limit)),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT
                    id,
                    name,
                    owner_id,
                    status
                FROM characters
                WHERE guild_id=?
                  AND owner_id=?
                  AND status IN ('approved', 'active')
                ORDER BY id DESC
                LIMIT ?
                """,
                (gid, uid, int(limit)),
            ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def ensure_character_access(
    interaction,
    character_id,
):
    if interaction.guild is None:
        raise PermissionError(
            "請在 Discord 伺服器內使用。"
        )

    character = get_character(
        interaction.guild_id,
        character_id,
    )

    if character["status"] not in (
        "approved",
        "active",
    ):
        raise ValueError(
            "只有已審核或啟用的角色能管理稱號。"
        )

    if (
        not is_owner(interaction)
        and str(character["owner_id"])
        != str(interaction.user.id)
    ):
        raise PermissionError(
            "你只能管理自己的角色稱號。"
        )

    return character


# ==========================================
# 角色稱號狀態
# ==========================================

def get_owned_mapping(guild_id, character_id):
    owned = list_character_titles(
        guild_id,
        character_id,
    )

    return {
        item["code"]: item
        for item in owned
    }


def active_display_codes(guild_id, character_id):
    owned = list_character_titles(
        guild_id,
        character_id,
    )

    displayed = [
        item
        for item in owned
        if item["display_order"] is not None
        and item["equipped"]
    ]

    displayed.sort(
        key=lambda item: int(
            item["display_order"]
        )
    )

    return [
        item["code"]
        for item in displayed
    ]


def format_title_detail(
    guild_id,
    character_id,
    definition,
    owned,
):
    code = definition["code"]
    name = escape(definition["name"])

    if owned is None:
        return (
            f"🔒 **{name}**\n"
            "尚未取得\n"
            f"代碼：`{code}`"
        )

    equipped = bool(
        owned["equipped"]
    )

    state = (
        "✅ 已穿戴"
        if equipped
        else "📦 已取得・未穿戴"
    )

    lines = [
        f"🎖️ **{name}**",
        state,
        f"取得時間：{display_time(owned['obtained_at'])}",
    ]

    if owned["display_order"] is not None:
        lines.append(
            f"角色面板展示：第 "
            f"{owned['display_order']} 格"
        )

    effect = definition.get(
        "effect",
        {},
    )

    effect_type = effect.get(
        "type",
        "none",
    )

    # 部分活動稱號的折扣以 discounts 保存。
    has_effect = (
        effect_type != "none"
        or bool(effect.get("discounts"))
    )

    if not has_effect:
        lines.append(
            "限時效果：無"
        )

    elif owned["first_activated_at"]:
        seconds = title_remaining_seconds(
            guild_id,
            character_id,
            code,
        )

        lines.extend([
            "首次穿戴："
            + display_time(
                owned["first_activated_at"]
            ),
            "效果截止："
            + display_time(
                owned["expires_at"]
            ),
            "剩餘時間："
            + remaining_text(seconds),
        ])

    else:
        lines.append(
            "168 小時效果："
            "首次穿戴時開始計時"
        )

    lines.append(
        "一次性獎勵："
        + (
            "已全部處理"
            if owned["reward_state"] == "done"
            else "尚有待發獎項"
        )
    )

    lines.append(
        f"代碼：`{code}`"
    )

    return "\n".join(lines)


def build_overview(
    guild_id,
    character_id,
    *,
    page=0,
    selected_code=None,
):
    definitions = list_title_definitions(
        guild_id,
        include_disabled=True,
    )

    owned = get_owned_mapping(
        guild_id,
        character_id,
    )

    total = len(definitions)
    page_count = max(
        1,
        (total + PAGE_SIZE - 1) // PAGE_SIZE,
    )

    page = max(
        0,
        min(page, page_count - 1),
    )

    start = page * PAGE_SIZE
    portion = definitions[
        start:start + PAGE_SIZE
    ]

    obtained_count = len(owned)

    equipped_count = sum(
        1
        for item in owned.values()
        if item["equipped"]
    )

    display_codes = active_display_codes(
        guild_id,
        character_id,
    )

    lines = [
        "🎖️ **角色成就稱號**",
        "",
        f"已取得：**{obtained_count}**",
        f"目前穿戴：**{equipped_count}**（無上限）",
        f"面板展示：**{len(display_codes)}/3**",
        "",
        f"**稱號列表｜第 {page + 1}/{page_count} 頁**",
    ]

    for definition in portion:
        code = definition["code"]

        item = owned.get(code)

        if item is None:
            marker = "🔒"

        elif item["equipped"]:
            marker = "✅"

        else:
            marker = "📦"

        display_mark = (
            " ⭐"
            if item and item["display_order"] is not None
            else ""
        )

        lines.append(
            f"{marker} {escape(definition['name'])}"
            f"{display_mark}"
        )

    if selected_code:
        selected = next(
            (
                item
                for item in definitions
                if item["code"] == selected_code
            ),
            None,
        )

        if selected is not None:
            lines.extend([
                "",
                "━━━━━━━━━━━━",
                format_title_detail(
                    guild_id,
                    character_id,
                    selected,
                    owned.get(selected_code),
                ),
            ])

    return "\n".join(lines)[:1950]


# ==========================================
# 角色選擇
# ==========================================

class CharacterSelect(discord.ui.Select):

    def __init__(self, characters):
        options = []

        for character in characters:
            options.append(
                discord.SelectOption(
                    label=str(
                        character["name"]
                    )[:95],
                    value=str(
                        character["id"]
                    ),
                    description=(
                        f"角色 #{character['id']}"
                    ),
                )
            )

        super().__init__(
            placeholder="選擇要管理稱號的角色",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction):
        cid = int(self.values[0])

        try:
            character = ensure_character_access(
                interaction,
                cid,
            )

            view = AchievementPlayerView(
                interaction,
                cid,
            )

            await interaction.response.edit_message(
                content=(
                    f"角色：**{escape(character['name'])}**\n\n"
                    + view.render()
                ),
                view=view,
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


class CharacterChooseView(discord.ui.View):

    def __init__(
        self,
        interaction,
        characters,
    ):
        super().__init__(
            timeout=600
        )

        self.guild_id = interaction.guild_id
        self.user_id = interaction.user.id

        self.add_item(
            CharacterSelect(characters)
        )

    async def interaction_check(
        self,
        interaction,
    ):
        if (
            interaction.guild_id != self.guild_id
            or interaction.user.id != self.user_id
        ):
            await respond(
                interaction,
                "這不是你的角色選擇面板。",
            )
            return False

        return True


# ==========================================
# 稱號下拉選單
# ==========================================

class TitleSelect(discord.ui.Select):

    def __init__(self, parent_view, definitions):
        self.parent_view = parent_view

        options = []

        owned = get_owned_mapping(
            parent_view.guild_id,
            parent_view.character_id,
        )

        for definition in definitions:
            code = definition["code"]
            item = owned.get(code)

            if item is None:
                description = "尚未取得"
            elif item["equipped"]:
                description = "已穿戴"
            else:
                description = "已取得"

            options.append(
                discord.SelectOption(
                    label=str(
                        definition["name"]
                    )[:100],
                    value=code,
                    description=description,
                    default=(
                        code == parent_view.selected_code
                    ),
                )
            )

        super().__init__(
            placeholder="選擇要查看或操作的稱號",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction):
        self.parent_view.selected_code = (
            self.values[0]
        )

        self.parent_view.rebuild()

        await interaction.response.edit_message(
            content=self.parent_view.render(),
            view=self.parent_view,
        )


# ==========================================
# 玩家稱號操作介面
# ==========================================

class AchievementPlayerView(discord.ui.View):

    def __init__(
        self,
        interaction,
        character_id,
        *,
        page=0,
        selected_code=None,
    ):
        super().__init__(
            timeout=600
        )

        self.guild_id = interaction.guild_id
        self.user_id = interaction.user.id
        self.character_id = int(character_id)

        self.page = int(page)
        self.selected_code = selected_code

        seed_fixed_titles(
            self.guild_id
        )

        self.rebuild()

    async def interaction_check(
        self,
        interaction,
    ):
        if (
            interaction.guild_id != self.guild_id
            or interaction.user.id != self.user_id
        ):
            await respond(
                interaction,
                "這不是你的稱號管理面板。",
            )
            return False

        try:
            ensure_character_access(
                interaction,
                self.character_id,
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
            return False

        return True

    def definitions(self):
        return list_title_definitions(
            self.guild_id,
            include_disabled=True,
        )

    def rebuild(self):
        """重新產生選單和按鈕狀態。"""

        self.clear_items()

        definitions = self.definitions()

        max_page = max(
            0,
            (len(definitions) - 1) // PAGE_SIZE,
        )

        self.page = max(
            0,
            min(self.page, max_page),
        )

        start = self.page * PAGE_SIZE
        portion = definitions[
            start:start + PAGE_SIZE
        ]

        if portion:
            self.add_item(
                TitleSelect(self, portion)
            )

        previous = discord.ui.Button(
            label="◀ 上一頁",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=self.page <= 0,
        )

        next_page = discord.ui.Button(
            label="下一頁 ▶",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=self.page >= max_page,
        )

        previous.callback = self.go_previous
        next_page.callback = self.go_next

        self.add_item(previous)
        self.add_item(next_page)

        self.add_action_button(
            label="🎖️ 穿戴",
            callback=self.do_equip,
            row=2,
            style=discord.ButtonStyle.success,
        )

        self.add_action_button(
            label="卸下",
            callback=self.do_unequip,
            row=2,
            style=discord.ButtonStyle.secondary,
        )

        self.add_action_button(
            label="⭐ 加入面板",
            callback=self.do_display,
            row=3,
            style=discord.ButtonStyle.primary,
        )

        self.add_action_button(
            label="取消面板展示",
            callback=self.do_remove_display,
            row=3,
            style=discord.ButtonStyle.secondary,
        )

        self.add_action_button(
            label="📋 查看面板稱號",
            callback=self.show_display,
            row=4,
            style=discord.ButtonStyle.secondary,
        )

        self.add_action_button(
            label="🔄 更新",
            callback=self.refresh,
            row=4,
            style=discord.ButtonStyle.secondary,
        )

    def add_action_button(
        self,
        *,
        label,
        callback,
        row,
        style,
    ):
        button = discord.ui.Button(
            label=label,
            style=style,
            row=row,
        )

        button.callback = callback
        self.add_item(button)

    def render(self):
        return build_overview(
            self.guild_id,
            self.character_id,
            page=self.page,
            selected_code=self.selected_code,
        )

    def get_selected(self):
        if not self.selected_code:
            raise ValueError(
                "請先從下拉選單選擇稱號。"
            )

        return self.selected_code

    def ensure_owned(self, *, must_equip=False):
        code = self.get_selected()

        mapping = get_owned_mapping(
            self.guild_id,
            self.character_id,
        )

        item = mapping.get(code)

        if item is None:
            raise ValueError(
                "角色尚未取得這個稱號。"
            )

        if must_equip and not item["equipped"]:
            raise ValueError(
                "必須先穿戴稱號，"
                "才能加入角色面板展示。"
            )

        return code, item

    async def redraw(self, interaction):
        self.rebuild()

        await interaction.response.edit_message(
            content=self.render(),
            view=self,
        )

    # --------------------------------------
    # 上一頁
    # --------------------------------------

    async def go_previous(self, interaction):
        self.page -= 1
        self.selected_code = None

        await self.redraw(interaction)

    # --------------------------------------
    # 下一頁
    # --------------------------------------

    async def go_next(self, interaction):
        self.page += 1
        self.selected_code = None

        await self.redraw(interaction)

    # --------------------------------------
    # 穿戴稱號
    # --------------------------------------

    async def do_equip(self, interaction):
        try:
            code, item = self.ensure_owned()

            if item["equipped"]:
                raise ValueError(
                    "這個稱號已經穿戴。"
                )

            equip_title(
                self.guild_id,
                self.character_id,
                code,
                equip=True,
            )

            await self.redraw(interaction)

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 卸下稱號
    # --------------------------------------

    async def do_unequip(self, interaction):
        try:
            code, item = self.ensure_owned()

            if not item["equipped"]:
                raise ValueError(
                    "這個稱號目前沒有穿戴。"
                )

            equip_title(
                self.guild_id,
                self.character_id,
                code,
                equip=False,
            )

            await self.redraw(interaction)

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 加入面板展示
    # --------------------------------------

    async def do_display(self, interaction):
        try:
            code, item = self.ensure_owned(
                must_equip=True
            )

            displayed = active_display_codes(
                self.guild_id,
                self.character_id,
            )

            if code in displayed:
                raise ValueError(
                    "這個稱號已經在角色面板上。"
                )

            if len(displayed) >= 3:
                raise ValueError(
                    "角色面板最多展示三個稱號，"
                    "請先移除其中一個。"
                )

            displayed.append(code)

            set_display_titles(
                self.guild_id,
                self.character_id,
                displayed,
            )

            await self.redraw(interaction)

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 取消面板展示
    # --------------------------------------

    async def do_remove_display(
        self,
        interaction,
    ):
        try:
            code, item = self.ensure_owned()

            displayed = active_display_codes(
                self.guild_id,
                self.character_id,
            )

            if code not in displayed:
                raise ValueError(
                    "這個稱號不在面板展示中。"
                )

            displayed.remove(code)

            set_display_titles(
                self.guild_id,
                self.character_id,
                displayed,
            )

            await self.redraw(interaction)

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                interaction,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 查看展示中的三個稱號
    # --------------------------------------

    async def show_display(self, interaction):
        displayed = active_display_codes(
            self.guild_id,
            self.character_id,
        )

        if not displayed:
            return await respond(
                interaction,
                "目前沒有設定角色面板展示稱號。",
            )

        definitions = {
            item["code"]: item["name"]
            for item in self.definitions()
        }

        lines = [
            "⭐ **角色面板稱號展示**",
            "",
        ]

        for index, code in enumerate(
            displayed,
            start=1,
        ):
            lines.append(
                f"{index}. "
                f"{escape(definitions.get(code, code))}"
            )

        await respond(
            interaction,
            "\n".join(lines),
        )

    # --------------------------------------
    # 重新整理
    # --------------------------------------

    async def refresh(self, interaction):
        await self.redraw(interaction)


# ==========================================
# 玩家 /稱號 指令
# ==========================================

class AchievementPlayerCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="稱號",
        description="查看、穿戴、卸下及展示角色稱號",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        角色編號="留空可選擇自己的角色",
    )
    async def achievement(
        self,
        interaction: discord.Interaction,
        角色編號: int | None = None,
    ):
        try:
            if interaction.guild is None:
                raise ValueError(
                    "請在伺服器內使用此功能。"
                )

            seed_fixed_titles(
                interaction.guild_id
            )

            # 指定角色編號時直接開啟。
            if 角色編號 is not None:
                character = ensure_character_access(
                    interaction,
                    角色編號,
                )

                view = AchievementPlayerView(
                    interaction,
                    角色編號,
                )

                await interaction.response.send_message(
                    (
                        f"角色：**{escape(character['name'])}**\n\n"
                        + view.render()
                    ),
                    view=view,
                    ephemeral=True,
                )

                return

            # 未指定角色時列出可以操作的角色。
            characters = accessible_characters(
                interaction,
                limit=25,
            )

            if not characters:
                raise ValueError(
                    "目前沒有可管理的已審核角色。"
                )

            if len(characters) == 1:
                character = characters[0]

                view = AchievementPlayerView(
                    interaction,
                    character["id"],
                )

                await interaction.response.send_message(
                    (
                        f"角色：**{escape(character['name'])}**\n\n"
                        + view.render()
                    ),
                    view=view,
                    ephemeral=True,
                )

                return

            await interaction.response.send_message(
                "🎭 **請先選擇要管理稱號的角色**",
                view=CharacterChooseView(
                    interaction,
                    characters,
                ),
                ephemeral=True,
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
# 載入模組
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        AchievementPlayerCog(bot)
    )

    LOG.info(
        "玩家稱號查看與穿戴介面已載入。"
    )
