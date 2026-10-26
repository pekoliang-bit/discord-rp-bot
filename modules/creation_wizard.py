
"""Discord RP：互動式分頁創角精靈。

/創角
  1. 基本資料及個人檔案
  2. 主副賽道、十三項屬性配點
  3. 完整預覽與送審

沿用現有資料庫、屬性計算及雙重審核。
"""

from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    create_character,
    get_connection,
    transaction,
)
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
    submit_character,
)
from core.attribute_validation import (
    validate_initial_attributes,
)
from modules.attributes import (
    allocate_initial,
    calculate_initial,
    load_schema,
)
from modules.profile import (
    FIELDS,
    REQUIRED,
    missing_fields,
    validate_field,
    read_settings,
)

LOG = logging.getLogger(__name__)

PERSONAL_FIELDS = {
    label: key
    for label, key in FIELDS.items()
    if key not in {
        "name",
        "age",
        "occupation",
        "main_track",
        "secondary_track",
    }
}


def load_character(actor, character_id):
    with get_connection() as conn:
        character = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (actor.guild_id, character_id),
        ).fetchone()

    if character is None:
        raise InvalidState("找不到這名角色。")

    if (
        str(character["owner_id"]) != actor.user_id
        or character["character_type"] != "player"
    ):
        raise PermissionDenied(
            "只能編輯自己的原創角色。"
        )

    if character["status"] != "draft":
        raise InvalidState(
            "只有草稿能使用創角精靈。"
        )

    return character


def save_setting(actor, character_id, key, value):
    with transaction() as conn:
        character = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (actor.guild_id, character_id),
        ).fetchone()

        if (
            character is None
            or str(character["owner_id"]) != actor.user_id
            or character["character_type"] != "player"
        ):
            raise PermissionDenied(
                "只能修改自己的原創角色。"
            )

        if character["status"] != "draft":
            raise InvalidState(
                "角色已送審，不能直接修改。"
            )

        settings = read_settings(character)
        settings[key] = value

        conn.execute(
            """
            UPDATE characters
            SET settings_json = ?,
                version = version + 1
            WHERE guild_id = ?
              AND id = ?
              AND status = 'draft'
            """,
            (
                json.dumps(
                    settings,
                    ensure_ascii=False,
                ),
                actor.guild_id,
                character_id,
            ),
        )


def personal_missing(character):
    settings = read_settings(character)
    missing = []

    for label, key in FIELDS.items():
        if key not in REQUIRED:
            continue

        if key in {
            "main_track",
            "secondary_track",
        }:
            continue

        if settings.get(key) in ("", None):
            missing.append(label)

    experience = settings.get("experience_years")

    if (
        type(experience) is int
        and experience > max(
            0,
            character["age"] - 5,
        )
    ):
        missing.append("資歷與年齡不符")

    return missing


def save_point_group(
    actor,
    character_id,
    pool,
    values,
):
    character = load_character(
        actor,
        character_id,
    )
    settings = read_settings(character)

    points = settings.get("wizard_points", {})

    if not isinstance(points, dict):
        points = {}

    current = points.get(pool, {})

    if not isinstance(current, dict):
        current = {}

    current.update(values)
    points[pool] = current

    save_setting(
        actor,
        character_id,
        "wizard_points",
        points,
    )


def save_allocation(actor, character_id):
    character = load_character(
        actor,
        character_id,
    )
    settings = read_settings(character)

    main = settings.get("wizard_main")
    secondary = settings.get("wizard_secondary")

    if not main or not secondary:
        raise InvalidState(
            "請先選擇主賽道與副賽道。"
        )

    with get_connection() as conn:
        schema = load_schema(
            conn,
            actor.guild_id,
        )

    points = settings.get("wizard_points", {})

    general_saved = points.get("general", {})
    talent_saved = points.get("talent", {})

    general = {
        name: int(general_saved.get(name, 0))
        for name in schema["general"]
    }

    talent = {
        name: int(talent_saved.get(name, 0))
        for name in schema["talent"]
    }

    # 先進行完整驗證，再寫入正式初始配點。
    calculate_initial(
        schema,
        main,
        secondary,
        general,
        talent,
    )

    return allocate_initial(
        actor,
        character_id,
        main,
        secondary,
        json.dumps(
            general,
            ensure_ascii=False,
        ),
        json.dumps(
            talent,
            ensure_ascii=False,
        ),
    )


async def show_error(interaction, exc):
    message = f"⚠️ {exc}"

    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


class BasicModal(
    discord.ui.Modal,
    title="創角｜基本資料",
):
    姓名 = discord.ui.TextInput(
        label="角色姓名",
        max_length=100,
        required=True,
    )
    年齡 = discord.ui.TextInput(
        label="角色年齡",
        placeholder="例如：20",
        max_length=3,
        required=True,
    )
    職業 = discord.ui.TextInput(
        label="角色職業",
        placeholder="例如：演員",
        max_length=100,
        required=True,
    )

    async def on_submit(self, interaction):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            age = int(str(self.年齡.value))

            name = validate_field(
                "name",
                str(self.姓名.value),
                age,
            )

            age = validate_field(
                "age",
                str(age),
                age,
            )

            occupation = validate_field(
                "occupation",
                str(self.職業.value),
                age,
            )

            character_id = create_character(
                actor.guild_id,
                actor.user_id,
                name,
                age,
                occupation,
            )

            await interaction.response.send_message(
                (
                    f"✅ 已建立角色草稿 #{character_id}\n"
                    "所有資料會在填寫後保存。\n\n"
                    "第一階段：個人資料"
                ),
                view=WizardView(
                    actor,
                    character_id,
                    0,
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
        ) as exc:
            await show_error(interaction, exc)


class FieldModal(discord.ui.Modal):
    def __init__(
        self,
        actor,
        character_id,
        field_label,
        field_key,
    ):
        super().__init__(
            title=f"填寫｜{field_label}"[:45]
        )

        self.actor = actor
        self.character_id = character_id
        self.field_key = field_key
        self.field_label = field_label

        character = load_character(
            actor,
            character_id,
        )

        existing = read_settings(
            character
        ).get(field_key, "")

        self.input = discord.ui.TextInput(
            label=field_label[:45],
            default=str(existing)[:4000],
            style=(
                discord.TextStyle.paragraph
                if field_key in {
                    "biography",
                    "special_notes",
                    "notes",
                    "public_persona",
                    "private_personality",
                }
                else discord.TextStyle.short
            ),
            max_length=(
                2000
                if field_key in {
                    "biography",
                    "special_notes",
                    "notes",
                }
                else 500
            ),
            required=True,
        )

        self.add_item(self.input)

    async def on_submit(self, interaction):
        try:
            character = load_character(
                self.actor,
                self.character_id,
            )

            value = validate_field(
                self.field_key,
                str(self.input.value),
                character["age"],
            )

            save_setting(
                self.actor,
                self.character_id,
                self.field_key,
                value,
            )

            await interaction.response.send_message(
                f"✅ 已儲存「{self.field_label}」。",
                view=WizardView(
                    self.actor,
                    self.character_id,
                    0,
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await show_error(interaction, exc)


class PointGroupModal(discord.ui.Modal):
    def __init__(
        self,
        actor,
        character_id,
        pool,
        names,
    ):
        super().__init__(
            title=(
                "一般屬性配點"
                if pool == "general"
                else "藝能屬性配點"
            )
        )

        self.actor = actor
        self.character_id = character_id
        self.pool = pool
        self.names = names

        character = load_character(
            actor,
            character_id,
        )
        settings = read_settings(character)
        saved = settings.get(
            "wizard_points", {}
        ).get(pool, {})

        self.inputs = []

        for name in names:
            field = discord.ui.TextInput(
                label=name[:45],
                default=str(
                    saved.get(name, 0)
                ),
                max_length=3,
                required=True,
                placeholder="輸入分配點數",
            )

            self.add_item(field)
            self.inputs.append(field)

    async def on_submit(self, interaction):
        try:
            values = {}

            for name, field in zip(
                self.names,
                self.inputs,
            ):
                value = int(
                    str(field.value).strip()
                )

                if value < 0 or value > 150:
                    raise ValueError(
                        f"「{name}」配點須介於0至150；"
                        "仍會另外檢查基礎值上限。"
                    )

                values[name] = value

            save_point_group(
                self.actor,
                self.character_id,
                self.pool,
                values,
            )

            await interaction.response.send_message(
                "✅ 這組配點已暫存。",
                view=WizardView(
                    self.actor,
                    self.character_id,
                    1,
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await show_error(interaction, exc)


class FieldSelect(discord.ui.Select):
    def __init__(self, actor, character_id):
        self.actor = actor
        self.character_id = character_id

        super().__init__(
            placeholder="✏️ 選擇要填寫的個人資料",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=label,
                    value=key,
                )
                for label, key
                in PERSONAL_FIELDS.items()
            ],
            row=0,
        )

    async def callback(self, interaction):
        try:
            key = self.values[0]

            label = next(
                label
                for label, value
                in PERSONAL_FIELDS.items()
                if value == key
            )

            await interaction.response.send_modal(
                FieldModal(
                    self.view.actor,
                    self.character_id,
                    label,
                    key,
                )
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await show_error(interaction, exc)


class TrackSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
        kind,
        names,
    ):
        self.actor = actor
        self.character_id = character_id
        self.kind = kind

        super().__init__(
            placeholder=(
                "選擇主賽道對應屬性"
                if kind == "main"
                else "選擇副賽道對應屬性"
            ),
            options=[
                discord.SelectOption(
                    label=name,
                    value=name,
                )
                for name in names
            ],
            row=(
                0 if kind == "main" else 1
            ),
        )

    async def callback(self, interaction):
        try:
            key = (
                "wizard_main"
                if self.kind == "main"
                else "wizard_secondary"
            )

            save_setting(
                self.actor,
                self.character_id,
                key,
                self.values[0],
            )

            await interaction.response.edit_message(
                content=(
                    f"✅ 已選擇：{self.values[0]}\n"
                    "請繼續配點。"
                ),
                view=WizardView(
                    self.actor,
                    self.character_id,
                    1,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
        ) as exc:
            await show_error(interaction, exc)


class PointGroupSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
        schema,
    ):
        self.actor = actor
        self.character_id = character_id
        self.groups = {}

        options = []

        for pool, label in (
            ("general", "一般池"),
            ("talent", "藝能池"),
        ):
            names = schema[pool]

            for index in range(
                0,
                len(names),
                5,
            ):
                group = names[
                    index:index + 5
                ]
                key = f"{pool}:{index}"

                self.groups[key] = (
                    pool,
                    group,
                )

                options.append(
                    discord.SelectOption(
                        label=(
                            f"{label}｜"
                            f"第{index // 5 + 1}組"
                        ),
                        description="、".join(
                            group
                        )[:100],
                        value=key,
                    )
                )

        super().__init__(
            placeholder="🧮 選擇配點組別（每次最多5項）",
            options=options,
            row=2,
        )

    async def callback(self, interaction):
        try:
            pool, names = self.groups[
                self.values[0]
            ]

            await interaction.response.send_modal(
                PointGroupModal(
                    self.actor,
                    self.character_id,
                    pool,
                    names,
                )
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


class ConfirmView(discord.ui.View):
    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        super().__init__(timeout=900)
        self.actor = actor
        self.character_id = character_id
        self.stage = stage

    async def interaction_check(
        self,
        interaction,
    ):
        return (
            str(interaction.user.id)
            == self.actor.user_id
            and str(interaction.guild_id)
            == self.actor.guild_id
        )

    @discord.ui.button(
        label="✅ 確認，進入下一頁",
        style=discord.ButtonStyle.success,
    )
    async def yes(self, interaction, button):
        try:
            load_character(
                self.actor,
                self.character_id,
            )

            if self.stage == 0:
                character = load_character(
                    self.actor,
                    self.character_id,
                )
                missing = personal_missing(
                    character
                )

                if missing:
                    raise InvalidState(
                        "個人資料尚未完成："
                        + "、".join(missing)
                    )

            if self.stage == 1:
                save_allocation(
                    self.actor,
                    self.character_id,
                )

            await interaction.response.edit_message(
                content=(
                    "✅ 本頁已確認，"
                    "進入下一階段。"
                ),
                view=WizardView(
                    self.actor,
                    self.character_id,
                    self.stage + 1,
                ),
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await show_error(interaction, exc)

    @discord.ui.button(
        label="返回修改",
        style=discord.ButtonStyle.secondary,
    )
    async def no(self, interaction, button):
        await interaction.response.edit_message(
            content="已返回原頁面。",
            view=WizardView(
                self.actor,
                self.character_id,
                self.stage,
            ),
        )


class WizardView(discord.ui.View):
    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        super().__init__(timeout=900)

        self.actor = actor
        self.character_id = character_id
        self.stage = stage

        if stage == 0:
            self.add_item(
                FieldSelect(
                    actor,
                    character_id,
                )
            )

        elif stage == 1:
            with get_connection() as conn:
                schema = load_schema(
                    conn,
                    actor.guild_id,
                )

            names = (
                schema["general"]
                + schema["talent"]
            )

            self.add_item(
                TrackSelect(
                    actor,
                    character_id,
                    "main",
                    names,
                )
            )

            self.add_item(
                TrackSelect(
                    actor,
                    character_id,
                    "secondary",
                    names,
                )
            )

            self.add_item(
                PointGroupSelect(
                    actor,
                    character_id,
                    schema,
                )
            )

        if stage == 2:
            self.next_button.label = (
                "📨 確認完成並提交審核"
            )
        else:
            self.next_button.label = (
                "➡️ 下一頁"
            )

        if stage == 0:
            self.previous_button.disabled = True

    async def interaction_check(
        self,
        interaction,
    ):
        if (
            str(interaction.user.id)
            != self.actor.user_id
            or str(interaction.guild_id)
            != self.actor.guild_id
        ):
            await interaction.response.send_message(
                "⛔ 這不是你的創角表單。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def previous_button(
        self,
        interaction,
        button,
    ):
        previous = max(
            0,
            self.stage - 1,
        )

        try:
            await interaction.response.edit_message(
                content="已返回上一頁。",
                view=WizardView(
                    self.actor,
                    self.character_id,
                    previous,
                ),
            )
        except (
            InvalidState,
            PermissionDenied,
        ) as exc:
            await show_error(interaction, exc)

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.primary,
        row=4,
    )
    async def next_button(
        self,
        interaction,
        button,
    ):
        try:
            character = load_character(
                self.actor,
                self.character_id,
            )

            if self.stage == 2:
                missing = missing_fields(
                    character
                )

                if missing:
                    raise InvalidState(
                        "尚有必填資料："
                        + "、".join(missing)
                    )

                with transaction() as conn:
                    validate_initial_attributes(
                        conn,
                        self.actor.guild_id,
                        self.character_id,
                    )

                result = submit_character(
                    self.actor,
                    self.character_id,
                )

                await interaction.response.edit_message(
                    content=(
                        f"🎉 角色 #{self.character_id} "
                        f"提交成功！\n"
                        f"目前狀態：{result}\n"
                        "請等待官方審核。"
                    ),
                    view=None,
                )
                return

            if self.stage == 0:
                missing = personal_missing(
                    character
                )

                if missing:
                    raise InvalidState(
                        "請先補完："
                        + "、".join(missing)
                    )

            if self.stage == 1:
                settings = read_settings(
                    character
                )
                if not (
                    settings.get("wizard_main")
                    and settings.get(
                        "wizard_secondary"
                    )
                ):
                    raise InvalidState(
                        "請先選擇主、副賽道。"
                    )

            await interaction.response.edit_message(
                content=(
                    "❓ 是否確認這一頁的資料？\n"
                    "選擇確認後會進入下一頁。\n"
                    "送審前仍可返回修改。"
                ),
                view=ConfirmView(
                    self.actor,
                    self.character_id,
                    self.stage,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


class CreationWizard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="創角",
        description="建立角色或繼續填寫既有草稿",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        角色編號=(
            "留空建立新角色；"
            "輸入草稿編號則繼續編輯"
        ),
    )
    async def create_wizard(
        self,
        interaction: discord.Interaction,
        角色編號: int | None = None,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            if 角色編號 is None:
                await interaction.response.send_modal(
                    BasicModal()
                )
                return

            character = load_character(
                actor,
                角色編號,
            )

            settings = read_settings(
                character
            )

            with get_connection() as conn:
                schema = None
                try:
                    schema = load_schema(
                        conn,
                        actor.guild_id,
                    )
                except InvalidState:
                    pass

            if not schema:
                stage = 0
            elif (
                settings.get("initial_allocation")
                and not personal_missing(
                    character
                )
            ):
                stage = 2
            elif not personal_missing(character):
                stage = 1
            else:
                stage = 0

            await interaction.response.send_message(
                (
                    f"📝 繼續編輯角色 "
                    f"#{角色編號}\n"
                    "已儲存的資料不會清除。"
                ),
                view=WizardView(
                    actor,
                    角色編號,
                    stage,
                ),
                ephemeral=True,
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CreationWizard(bot)
    )
