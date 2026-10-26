
"""Discord RP：互動式角色建立系統。

指令：/創角

功能：
- 建立新角色或選擇既有草稿
- 中文分頁式資料填寫
- 所有資料保存至資料庫
- 十三項屬性、雙配點池
- 主副賽道及額外加成
- 生日與感情設定
- 逐頁確認
- 完整預覽
- 雙重審核送件

依賴：
core.database
core.permissions
core.attribute_validation
modules.attributes
modules.profile
"""

from __future__ import annotations

import json
import logging
from datetime import date

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
    load_schema,
    calculate_initial,
    allocate_initial,
)
from modules.profile import (
    FIELDS,
    REQUIRED,
    read_settings,
    validate_field,
    missing_fields,
    RELATIONSHIP_STATUSES,
)

LOG = logging.getLogger(__name__)

STEPS = (
    "個人資料",
    "屬性配點",
    "感情與補充資料",
    "預覽及送審",
)

DEFAULT_GENERAL = [
    "流量熱度",
    "商業價值",
    "路人緣",
    "公關",
    "影響指數",
]

DEFAULT_TALENT = [
    "容貌",
    "才智",
    "歌唱",
    "舞蹈",
    "演繹",
    "主持",
    "綜藝",
    "個人才藝",
]

EXTRA_FIELDS = {
    "生日（月／日）": "birthday",
    "感情狀態": "relationship_status",
    "伴侶姓名": "partner_name",
}

PERSONAL = {
    label: key
    for label, key in FIELDS.items()
    if key not in (
        "name",
        "age",
        "occupation",
        "main_track",
        "secondary_track",
    )
}

GROUPS = [
    (
        "基本資訊",
        [
            "性別",
            "國籍或出身",
            "身高cm",
            "體重kg",
            "學歷",
        ],
    ),
    (
        "經歷與立繪",
        [
            "立繪網址",
            "立繪來源",
            "語言",
            "資歷年數",
            "生平簡述",
        ],
    ),
    (
        "公開介紹",
        [
            "藝名或稱號",
            "代表色碼",
            "公開人設",
            "私下性格",
            "特殊設定",
        ],
    ),
    (
        "其他資料",
        ["其他備註"],
    ),
]


def safe_json(raw):
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def owned(actor, character_id, *, draft=True):
    """確認角色屬於本人。"""

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
                character_id,
            ),
        ).fetchone()

    if (
        row is None
        or str(row["owner_id"]) != actor.user_id
        or row["character_type"] != "player"
    ):
        raise PermissionDenied(
            "角色不存在，或不是你擁有的原創角色。"
        )

    if draft and row["status"] != "draft":
        raise InvalidState(
            "角色已送審或啟用，無法直接修改草稿。"
        )

    return dict(row)


def update_fields(
    actor,
    character_id,
    fields,
):
    """保存一批角色資料。"""

    with transaction() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        if (
            row is None
            or str(row["owner_id"]) != actor.user_id
            or row["character_type"] != "player"
        ):
            raise PermissionDenied(
                "你沒有操作這名角色的權限。"
            )

        if row["status"] != "draft":
            raise InvalidState(
                "只有草稿可以修改。"
            )

        settings = safe_json(
            row["settings_json"]
        )

        main_fields = {}

        for key, value in fields.items():
            if key in (
                "name",
                "age",
                "occupation",
            ):
                main_fields[key] = value
            else:
                settings[key] = value

        if "age" in main_fields:
            years = settings.get(
                "experience_years"
            )

            if (
                type(years) is int
                and years > max(
                    0,
                    main_fields["age"] - 5,
                )
            ):
                raise InvalidState(
                    "年齡與已有資歷不符。"
                )

        conn.execute(
            """
            UPDATE characters
            SET name = ?,
                age = ?,
                occupation = ?,
                settings_json = ?,
                version = version + 1
            WHERE guild_id = ?
              AND id = ?
              AND status = 'draft'
            """,
            (
                main_fields.get(
                    "name",
                    row["name"],
                ),
                main_fields.get(
                    "age",
                    row["age"],
                ),
                main_fields.get(
                    "occupation",
                    row["occupation"],
                ),
                json.dumps(
                    settings,
                    ensure_ascii=False,
                ),
                actor.guild_id,
                character_id,
            ),
        )


def ensure_schema(actor):
    """建立預設十三項屬性，不覆蓋已有設定。"""

    with transaction() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM module_records
            WHERE guild_id = ?
              AND module = 'attributes'
              AND record_type = 'official_schema'
            LIMIT 1
            """,
            (actor.guild_id,),
        ).fetchone()

        if row is None:
            schema = {
                "general": DEFAULT_GENERAL,
                "talent": DEFAULT_TALENT,
                "version": 1,
            }

            conn.execute(
                """
                INSERT OR IGNORE INTO guilds (
                    guild_id
                )
                VALUES (?)
                """,
                (actor.guild_id,),
            )

            conn.execute(
                """
                INSERT INTO module_records (
                    guild_id,
                    module,
                    owner_id,
                    record_type,
                    data_json
                )
                VALUES (
                    ?,
                    'attributes',
                    'system',
                    'official_schema',
                    ?
                )
                """,
                (
                    actor.guild_id,
                    json.dumps(
                        schema,
                        ensure_ascii=False,
                    ),
                ),
            )

        return load_schema(
            conn,
            actor.guild_id,
        )


def save_working_points(
    actor,
    character_id,
    values,
):
    row = owned(
        actor,
        character_id,
    )

    settings = safe_json(
        row["settings_json"]
    )

    points = settings.get(
        "wizard_points",
        {},
    )

    if not isinstance(points, dict):
        points = {}

    points.update(values)

    update_fields(
        actor,
        character_id,
        {"wizard_points": points},
    )


def commit_points(actor, character_id):
    """檢查並正式儲存初始配點。"""

    row = owned(
        actor,
        character_id,
    )

    settings = safe_json(
        row["settings_json"]
    )

    schema = ensure_schema(actor)

    main = settings.get(
        "wizard_main"
    )

    secondary = settings.get(
        "wizard_secondary"
    )

    if not main or not secondary:
        raise InvalidState(
            "請選擇主賽道與副賽道。"
        )

    points = settings.get(
        "wizard_points",
        {},
    )

    general = {
        name: points.get(name)
        for name in schema["general"]
    }

    talent = {
        name: points.get(name)
        for name in schema["talent"]
    }

    values = (
        list(general.values())
        + list(talent.values())
    )

    if any(
        type(value) is not int
        for value in values
    ):
        raise InvalidState(
            "十三項屬性都需要填寫數字，"
            "未分配可填0。"
        )

    calculate_initial(
        schema,
        main,
        secondary,
        general,
        talent,
    )

    result = allocate_initial(
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

    with transaction() as conn:
        validate_initial_attributes(
            conn,
            actor.guild_id,
            character_id,
        )

    return result


def progress_text(
    actor,
    character_id,
    stage,
):
    """產生玩家看到的中文創角畫面。"""

    row = owned(
        actor,
        character_id,
    )

    settings = safe_json(
        row["settings_json"]
    )

    lines = [
        f"🎭 **創角｜{STEPS[stage]}**",
        (
            f"角色：{row['name']}｜"
            f"{row['age']}歲｜"
            f"{row['occupation']}"
        ),
        (
            "資料會自動儲存。"
            "關閉畫面後可以重新使用"
            " /創角 繼續。"
        ),
    ]

    if stage == 0:
        missing = []

        for label, key in PERSONAL.items():
            if (
                key in REQUIRED
                and settings.get(key) in (
                    None,
                    "",
                )
            ):
                missing.append(label)

        lines.extend([
            (
                "必填尚缺："
                + (
                    "、".join(missing)
                    if missing
                    else "已完成"
                )
            ),
            "請選擇資料組別填寫。",
            "完成後點擊 ➡️ 下一頁。",
        ])

    elif stage == 1:
        points = settings.get(
            "wizard_points",
            {},
        )

        schema = ensure_schema(actor)

        for key, label in (
            ("general", "一般"),
            ("talent", "藝能"),
        ):
            names = schema[key]

            spent = sum(
                value
                for name, value in points.items()
                if (
                    name in names
                    and type(value) is int
                )
            )

            lines.append(
                f"{label}配點池："
                f"{spent}/500點"
            )

        lines.extend([
            (
                "主賽道："
                + str(
                    settings.get(
                        "wizard_main"
                    ) or "未選擇"
                )
            ),
            (
                "副賽道："
                + str(
                    settings.get(
                        "wizard_secondary"
                    ) or "未選擇"
                )
            ),
            (
                "十三項屬性都要輸入數值，"
                "沒有分配的填0。"
            ),
        ])

    elif stage == 2:
        lines.extend([
            (
                "生日："
                + str(
                    settings.get(
                        "birthday"
                    ) or "未填寫"
                )
            ),
            (
                "感情狀態："
                + str(
                    settings.get(
                        "relationship_status"
                    ) or "未填寫"
                )
            ),
            "此頁可返回修改或繼續預覽。",
        ])

    else:
        lines.append(
            "📋 **角色檔案預覽**"
        )

        for label, key in PERSONAL.items():
            value = settings.get(key)

            lines.append(
                f"{label}："
                f"{str(value or '未填寫')[:65]}"
            )

        lines.extend([
            (
                "生日："
                + str(
                    settings.get(
                        "birthday"
                    ) or "未填寫"
                )
            ),
            (
                "感情狀態："
                + str(
                    settings.get(
                        "relationship_status"
                    ) or "未填寫"
                )
            ),
            (
                "主賽道："
                + str(
                    settings.get(
                        "main_track"
                    ) or "未填寫"
                )
            ),
            (
                "副賽道："
                + str(
                    settings.get(
                        "secondary_track"
                    ) or "未填寫"
                )
            ),
            (
                "確認無誤後點擊"
                "『提交雙重審核』。"
            ),
        ])

    return "\n".join(lines)[:1900]


async def fail(
    interaction,
    exc,
):
    message = f"⚠️ {str(exc)[:1500]}"

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


class StartModal(
    discord.ui.Modal,
    title="創角｜建立原創角色",
):
    姓名 = discord.ui.TextInput(
        label="角色姓名",
        max_length=100,
    )

    年齡 = discord.ui.TextInput(
        label="角色年齡",
        placeholder="18",
        max_length=3,
    )

    職業 = discord.ui.TextInput(
        label="角色職業",
        max_length=100,
    )

    async def on_submit(self, interaction):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            age = validate_field(
                "age",
                str(self.年齡),
                None,
            )

            name = validate_field(
                "name",
                str(self.姓名),
                age,
            )

            occupation = validate_field(
                "occupation",
                str(self.職業),
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
                progress_text(
                    actor,
                    character_id,
                    0,
                ),
                view=Wizard(
                    actor,
                    character_id,
                    0,
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await fail(interaction, exc)


class GroupModal(discord.ui.Modal):
    """分組填寫個人資料及關係設定。"""

    def __init__(
        self,
        actor,
        character_id,
        labels,
        *,
        category="personal",
    ):
        super().__init__(
            title=(
                "創角｜資料填寫"
                if category == "personal"
                else "創角｜關係資料"
            )
        )

        self.actor = actor
        self.character_id = character_id

        row = owned(
            actor,
            character_id,
        )

        settings = safe_json(
            row["settings_json"]
        )

        self.inputs = []

        for label in labels:
            key = (
                PERSONAL.get(label)
                or EXTRA_FIELDS.get(label)
            )

            existing = settings.get(
                key,
                "",
            )

            maximum = (
                2000
                if key in (
                    "biography",
                    "notes",
                    "special_notes",
                )
                else 500
            )

            field = discord.ui.TextInput(
                label=label,
                default=str(
                    existing or ""
                )[:maximum],
                required=key in REQUIRED,
                max_length=maximum,
                style=(
                    discord.TextStyle.paragraph
                    if key in (
                        "biography",
                        "notes",
                        "special_notes",
                        "public_persona",
                        "private_personality",
                    )
                    else discord.TextStyle.short
                ),
            )

            self.add_item(field)
            self.inputs.append(
                (key, field)
            )

    async def on_submit(self, interaction):
        try:
            row = owned(
                self.actor,
                self.character_id,
            )

            values = {}

            for key, field in self.inputs:
                raw = str(
                    field.value
                ).strip()

                if not raw:
                    if key in REQUIRED:
                        raise ValueError(
                            "必填欄位不可留空。"
                        )

                    values[key] = ""
                    continue

                if key == "birthday":
                    try:
                        month, day = [
                            int(x)
                            for x in raw.replace(
                                "-",
                                "/",
                            ).split("/")
                        ]

                        date(
                            2000,
                            month,
                            day,
                        )

                        values[key] = (
                            f"{month:02d}/"
                            f"{day:02d}"
                        )

                    except (
                        ValueError,
                        TypeError,
                    ) as exc:
                        raise ValueError(
                            "生日格式請填 MM/DD，"
                            "例如06/15。"
                        ) from exc

                elif key == "relationship_status":
                    if raw not in RELATIONSHIP_STATUSES:
                        raise ValueError(
                            "感情狀態請使用官方名稱。"
                        )

                    values[key] = raw

                elif key == "partner_name":
                    if len(raw) > 100:
                        raise ValueError(
                            "伴侶姓名最多100字。"
                        )

                    values[key] = raw

                else:
                    values[key] = validate_field(
                        key,
                        raw,
                        row["age"],
                    )

            update_fields(
                self.actor,
                self.character_id,
                values,
            )

            stage = (
                2
                if any(
                    key in EXTRA_FIELDS.values()
                    for key in values
                )
                else 0
            )

            await interaction.response.send_message(
                progress_text(
                    self.actor,
                    self.character_id,
                    stage,
                ),
                view=Wizard(
                    self.actor,
                    self.character_id,
                    stage,
                ),
                ephemeral=True,
            )

        except (
            InvalidState,
            ValueError,
            PermissionDenied,
        ) as exc:
            await fail(
                interaction,
                exc,
            )


class PointsModal(discord.ui.Modal):
    """每次填寫最多五項屬性。"""

    def __init__(
        self,
        actor,
        character_id,
        names,
    ):
        super().__init__(
            title="創角｜屬性配點"
        )

        self.actor = actor
        self.character_id = character_id
        self.names = names

        row = owned(
            actor,
            character_id,
        )

        settings = safe_json(
            row["settings_json"]
        )

        points = settings.get(
            "wizard_points",
            {},
        )

        self.inputs = []

        for name in names:
            field = discord.ui.TextInput(
                label=name,
                default=str(
                    points.get(
                        name,
                        0,
                    )
                ),
                max_length=4,
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
                    str(
                        field.value
                    ).strip()
                )

                if not 0 <= value <= 150:
                    raise ValueError(
                        f"「{name}」配點"
                        "必須介於0～150。"
                    )

                values[name] = value

            save_working_points(
                self.actor,
                self.character_id,
                values,
            )

            await interaction.response.send_message(
                progress_text(
                    self.actor,
                    self.character_id,
                    1,
                ),
                view=Wizard(
                    self.actor,
                    self.character_id,
                    1,
                ),
                ephemeral=True,
            )

        except (
            InvalidState,
            ValueError,
            PermissionDenied,
        ) as exc:
            await fail(
                interaction,
                exc,
            )


class GroupSelector(discord.ui.Select):
    """資料組別與配點組別選單。"""

    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        self.actor = actor
        self.character_id = character_id
        self.stage = stage
        self.groups = {}

        if stage == 0:
            self.groups = {
                str(i): labels
                for i, (_, labels)
                in enumerate(GROUPS)
            }

            options = [
                discord.SelectOption(
                    label=title,
                    value=str(i),
                )
                for i, (title, _)
                in enumerate(GROUPS)
            ]

        elif stage == 1:
            schema = ensure_schema(actor)
            options = []

            for pool, caption in (
                ("general", "一般池"),
                ("talent", "藝能池"),
            ):
                for index in range(
                    0,
                    len(schema[pool]),
                    5,
                ):
                    key = (
                        f"{pool}:{index}"
                    )

                    self.groups[key] = (
                        schema[pool][
                            index:index + 5
                        ]
                    )

                    options.append(
                        discord.SelectOption(
                            label=(
                                f"{caption}｜"
                                f"第{index // 5 + 1}組"
                            ),
                            value=key,
                        )
                    )

        else:
            self.groups = {
                "relation": [
                    "生日（月／日）",
                    "感情狀態",
                    "伴侶姓名",
                ]
            }

            options = [
                discord.SelectOption(
                    label="生日與感情狀態",
                    value="relation",
                )
            ]

        super().__init__(
            placeholder="選擇要填寫的資料組別",
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        try:
            names = self.groups[
                self.values[0]
            ]

            if self.stage == 1:
                modal = PointsModal(
                    self.actor,
                    self.character_id,
                    names,
                )
            else:
                modal = GroupModal(
                    self.actor,
                    self.character_id,
                    names,
                    category=(
                        "relation"
                        if self.stage == 2
                        else "personal"
                    ),
                )

            await interaction.response.send_modal(
                modal
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await fail(
                interaction,
                exc,
            )


class TrackSelector(discord.ui.Select):
    """主、副賽道選擇。"""

    def __init__(
        self,
        actor,
        character_id,
        target,
        names,
        row,
    ):
        self.actor = actor
        self.character_id = character_id
        self.target = target

        super().__init__(
            placeholder=(
                "選擇主賽道對應屬性"
                if target == "wizard_main"
                else "選擇副賽道對應屬性"
            ),
            options=[
                discord.SelectOption(
                    label=name,
                    value=name,
                )
                for name in names
            ],
            row=row,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        try:
            update_fields(
                self.actor,
                self.character_id,
                {
                    self.target: self.values[0]
                },
            )

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    self.character_id,
                    1,
                ),
                view=Wizard(
                    self.actor,
                    self.character_id,
                    1,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
        ) as exc:
            await fail(
                interaction,
                exc,
            )


class Confirm(discord.ui.View):
    """切換頁面前二次確認。"""

    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        super().__init__(
            timeout=900
        )

        self.actor = actor
        self.character_id = character_id
        self.stage = stage

    async def interaction_check(
        self,
        interaction,
    ):
        valid = (
            str(interaction.user.id)
            == self.actor.user_id
            and str(interaction.guild_id)
            == self.actor.guild_id
        )

        if not valid:
            await fail(
                interaction,
                "這不是你的創角表單。",
            )

        return valid

    @discord.ui.button(
        label="確認，進入下一頁 ➡️",
        style=discord.ButtonStyle.success,
    )
    async def approve(
        self,
        interaction,
        button,
    ):
        try:
            row = owned(
                self.actor,
                self.character_id,
            )

            if self.stage == 0:
                settings = safe_json(
                    row["settings_json"]
                )

                missing = [
                    label
                    for label, key
                    in PERSONAL.items()
                    if (
                        key in REQUIRED
                        and settings.get(key)
                        in (None, "")
                    )
                ]

                if missing:
                    raise InvalidState(
                        "必填資料尚缺："
                        + "、".join(missing)
                    )

            elif self.stage == 1:
                commit_points(
                    self.actor,
                    self.character_id,
                )

            next_stage = self.stage + 1

            update_fields(
                self.actor,
                self.character_id,
                {
                    "wizard_step":
                    next_stage
                },
            )

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    self.character_id,
                    next_stage,
                ),
                view=Wizard(
                    self.actor,
                    self.character_id,
                    next_stage,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await fail(
                interaction,
                exc,
            )

    @discord.ui.button(
        label="返回修改",
        style=discord.ButtonStyle.secondary,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        await interaction.response.edit_message(
            content=progress_text(
                self.actor,
                self.character_id,
                self.stage,
            ),
            view=Wizard(
                self.actor,
                self.character_id,
                self.stage,
            ),
        )


class Wizard(discord.ui.View):
    """完整創角流程控制器。"""

    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        super().__init__(
            timeout=900
        )

        self.actor = actor
        self.character_id = character_id
        self.stage = stage

        if stage in (0, 1, 2):
            self.add_item(
                GroupSelector(
                    actor,
                    character_id,
                    stage,
                )
            )

        if stage == 1:
            schema = ensure_schema(
                actor
            )

            names = (
                schema["general"]
                + schema["talent"]
            )

            self.add_item(
                TrackSelector(
                    actor,
                    character_id,
                    "wizard_main",
                    names,
                    row=1,
                )
            )

            self.add_item(
                TrackSelector(
                    actor,
                    character_id,
                    "wizard_secondary",
                    names,
                    row=2,
                )
            )

        self.previous.disabled = (
            stage == 0
        )

        self.next.label = (
            "📨 提交雙重審核"
            if stage == 3
            else "➡️ 下一頁"
        )

    async def interaction_check(
        self,
        interaction,
    ):
        valid = (
            str(interaction.user.id)
            == self.actor.user_id
            and str(interaction.guild_id)
            == self.actor.guild_id
        )

        if not valid:
            await fail(
                interaction,
                "這不是你的創角表單。",
            )

        return valid

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=4,
    )
    async def previous(
        self,
        interaction,
        button,
    ):
        try:
            previous_stage = (
                self.stage - 1
            )

            update_fields(
                self.actor,
                self.character_id,
                {
                    "wizard_step":
                    previous_stage
                },
            )

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    self.character_id,
                    previous_stage,
                ),
                view=Wizard(
                    self.actor,
                    self.character_id,
                    previous_stage,
                ),
            )

        except (
            InvalidState,
            PermissionDenied,
        ) as exc:
            await fail(
                interaction,
                exc,
            )

    @discord.ui.button(
        label="➡️ 下一頁",
        style=discord.ButtonStyle.primary,
        row=4,
    )
    async def next(
        self,
        interaction,
        button,
    ):
        try:
            row = owned(
                self.actor,
                self.character_id,
            )

            if self.stage == 3:
                missing = missing_fields(
                    row
                )

                if missing:
                    raise InvalidState(
                        "必填資料尚缺："
                        + "、".join(missing)
                    )

                with transaction() as conn:
                    validate_initial_attributes(
                        conn,
                        self.actor.guild_id,
                        self.character_id,
                    )

                status = submit_character(
                    self.actor,
                    self.character_id,
                )

                await interaction.response.edit_message(
                    content=(
                        "✅ **角色已成功送審**\n\n"
                        f"角色：{row['name']}\n"
                        f"狀態：{status}\n\n"
                        "請等待官方完成雙重審核。"
                    ),
                    view=None,
                )

                return

            await interaction.response.edit_message(
                content=(
                    f"❓ **確認"
                    f"「{STEPS[self.stage]}」"
                    "已填寫完成？**\n\n"
                    "點擊確認後進入下一頁。\n"
                    "正式送審前仍可返回修改。"
                ),
                view=Confirm(
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
            await fail(
                interaction,
                exc,
            )


class DraftSelect(discord.ui.Select):
    """選擇既有草稿，不需手動輸入編號。"""

    def __init__(
        self,
        actor,
        rows,
    ):
        self.actor = actor

        super().__init__(
            placeholder="選擇要繼續填寫的草稿",
            options=[
                discord.SelectOption(
                    label=str(
                        row["name"]
                    )[:95],
                    description=(
                        f"草稿 #{row['id']}"
                    ),
                    value=str(
                        row["id"]
                    ),
                )
                for row in rows
            ],
        )

    async def callback(
        self,
        interaction,
    ):
        if (
            str(interaction.user.id)
            != self.actor.user_id
            or str(interaction.guild_id)
            != self.actor.guild_id
        ):
            await fail(
                interaction,
                "這不是你的草稿清單。",
            )
            return

        try:
            character_id = int(
                self.values[0]
            )

            row = owned(
                self.actor,
                character_id,
            )

            settings = safe_json(
                row["settings_json"]
            )

            stage = settings.get(
                "wizard_step",
                0,
            )

            if (
                type(stage) is not int
                or not 0 <= stage <= 3
            ):
                stage = 0

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    character_id,
                    stage,
                ),
                view=Wizard(
                    self.actor,
                    character_id,
                    stage,
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await fail(
                interaction,
                exc,
            )


class DraftPicker(discord.ui.View):
    """繼續草稿或開始建立新角色。"""

    def __init__(
        self,
        actor,
        rows,
    ):
        super().__init__(
            timeout=900
        )

        self.actor = actor

        self.add_item(
            DraftSelect(
                actor,
                rows,
            )
        )

    @discord.ui.button(
        label="➕ 建立新角色",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def new(
        self,
        interaction,
        button,
    ):
        if (
            str(interaction.user.id)
            != self.actor.user_id
            or str(interaction.guild_id)
            != self.actor.guild_id
        ):
            await fail(
                interaction,
                "這不是你的表單。",
            )
            return

        await interaction.response.send_modal(
            StartModal()
        )


class CreationWizard(commands.Cog):
    """註冊唯一玩家入口：/創角。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="創角",
        description="建立原創角色或繼續填寫草稿",
    )
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            with get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, name
                    FROM characters
                    WHERE guild_id = ?
                      AND owner_id = ?
                      AND character_type = 'player'
                      AND status = 'draft'
                    ORDER BY id DESC
                    LIMIT 25
                    """,
                    (
                        actor.guild_id,
                        actor.user_id,
                    ),
                ).fetchall()

            if not rows:
                await interaction.response.send_modal(
                    StartModal()
                )

            else:
                await interaction.response.send_message(
                    (
                        "🎭 **角色建立中心**\n\n"
                        "請選擇要繼續填寫的草稿，"
                        "或建立一名新的原創角色。"
                    ),
                    view=DraftPicker(
                        actor,
                        rows,
                    ),
                    ephemeral=True,
                )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await fail(
                interaction,
                exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CreationWizard(bot)
    )
