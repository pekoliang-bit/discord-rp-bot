
"""Discord RP：完整互動式創角中心。

指令：/創角

功能：
- 先顯示角色建立中心
- 建立不限數量的原創角色
- 繼續已保存的角色草稿
- 草稿清單分頁
- 基本資料可重新修改
- 個人資料分組填寫
- 十三項屬性雙配點池
- 主副賽道設定
- 生日與感情狀態
- 每頁確認及返回修改
- 完整預覽
- 雙重審核送件
- 嚴格驗證 Discord 使用者及角色擁有權
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
    ensure_default_schema,
    calculate_initial,
    allocate_initial,
)
from modules.profile import (
    FIELDS,
    REQUIRED,
    RELATIONSHIP_STATUSES,
    validate_field,
    missing_fields,
)

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25
TIMEOUT = 900

STEPS = [
    "個人資料",
    "屬性配點",
    "生日與感情設定",
    "預覽及送審",
]

PERSONAL_GROUPS = [
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
        "人物介紹",
        [
            "藝名或稱號",
            "代表色碼",
            "公開人設",
            "私下性格",
            "特殊設定",
        ],
    ),
    (
        "其他設定",
        ["其他備註"],
    ),
]

EXTRA_FIELDS = {
    "生日（月／日）": "birthday",
    "感情狀態": "relationship_status",
    "伴侶姓名": "partner_name",
}

LONG_FIELDS = {
    "biography",
    "notes",
    "special_notes",
    "public_persona",
    "private_personality",
}

GENERAL_MAIN_SKILLS = (
    "歌唱",
    "舞蹈",
    "演繹",
    "主持",
    "綜藝",
)


def safe_json(raw):
    try:
        result = json.loads(raw or "{}")
        return (
            result
            if isinstance(result, dict)
            else {}
        )
    except (ValueError, TypeError):
        return {}


def get_character(actor, character_id):
    """讀取本人草稿，禁止跨伺服器及跨玩家操作。"""

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
        raise InvalidState("找不到角色。")

    if str(row["owner_id"]) != actor.user_id:
        raise PermissionDenied(
            "你不能操作其他玩家的角色。"
        )

    if row["character_type"] != "player":
        raise PermissionDenied(
            "這個創角入口僅限原創角色。"
        )

    if row["status"] != "draft":
        raise InvalidState(
            "只有草稿可以修改，"
            "已送審或啟用的角色不可直接修改。"
        )

    return dict(row)


def list_drafts(actor):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, age, occupation,
                   settings_json
            FROM characters
            WHERE guild_id = ?
              AND owner_id = ?
              AND character_type = 'player'
              AND status = 'draft'
            ORDER BY id DESC
            """,
            (
                actor.guild_id,
                actor.user_id,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def save_fields(actor, character_id, values):
    """以交易保存資料，並再次檢查角色擁有權。"""

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
                "無權修改這名角色。"
            )

        if row["status"] != "draft":
            raise InvalidState(
                "角色已不在草稿狀態。"
            )

        settings = safe_json(
            row["settings_json"]
        )

        basic = {
            "name": row["name"],
            "age": row["age"],
            "occupation": row["occupation"],
        }

        for key, value in values.items():
            if key in basic:
                basic[key] = value
            else:
                settings[key] = value

        years = settings.get(
            "experience_years"
        )

        if (
            type(years) is int
            and type(basic["age"]) is int
            and years > max(
                0,
                basic["age"] - 5,
            )
        ):
            raise ValueError(
                "現有資歷超過角色年齡允許的上限。"
            )

        changed = conn.execute(
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
                basic["name"],
                basic["age"],
                basic["occupation"],
                json.dumps(
                    settings,
                    ensure_ascii=False,
                ),
                actor.guild_id,
                character_id,
            ),
        )

        if changed.rowcount != 1:
            raise InvalidState(
                "角色資料狀態已變更。"
            )


def get_schema(actor):
    """確保官方十三項屬性存在。"""

    with transaction() as conn:
        ensure_default_schema(
            conn,
            actor.guild_id,
        )
        return load_schema(
            conn,
            actor.guild_id,
        )


def stage_of(actor, character_id):
    row = get_character(
        actor,
        character_id,
    )

    settings = safe_json(
        row["settings_json"]
    )

    stage = settings.get(
        "wizard_step",
        0,
    )

    if type(stage) is not int:
        return 0

    return max(0, min(stage, 3))


def missing_personal(row):
    settings = safe_json(
        row["settings_json"]
    )

    return [
        label
        for label, key in FIELDS.items()
        if (
            key in REQUIRED
            and key not in (
                "main_track",
                "secondary_track",
            )
            and settings.get(key) in (
                None,
                "",
            )
        )
    ]


def save_points(actor, character_id, values):
    row = get_character(
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

    save_fields(
        actor,
        character_id,
        {"wizard_points": points},
    )


def finalize_points(actor, character_id):
    """完成十三項配點並寫入正式屬性表。"""

    row = get_character(
        actor,
        character_id,
    )

    settings = safe_json(
        row["settings_json"]
    )

    schema = get_schema(actor)

    main = settings.get(
        "wizard_main"
    )

    secondary = settings.get(
        "wizard_secondary"
    )

    if not main or not secondary:
        raise InvalidState(
            "請先設定主賽道及副賽道。"
        )

    if main == secondary:
        raise InvalidState(
            "主副賽道不可相同。"
        )

    raw_points = settings.get(
        "wizard_points",
        {},
    )

    if not isinstance(raw_points, dict):
        raise InvalidState(
            "配點資料格式不正確。"
        )

    general = {
        name: raw_points.get(name)
        for name in schema["general"]
    }

    talent = {
        name: raw_points.get(name)
        for name in schema["talent"]
    }

    all_points = (
        list(general.values())
        + list(talent.values())
    )

    if any(
        type(value) is not int
        for value in all_points
    ):
        raise InvalidState(
            "十三項屬性均須填寫點數，"
            "沒有分配的請填0。"
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


def progress_text(actor, character_id, stage):
    row = get_character(
        actor,
        character_id,
    )

    settings = safe_json(
        row["settings_json"]
    )

    lines = [
        f"🎭 **創角｜{STEPS[stage]}**",
        f"角色：{row['name']}",
        f"年齡：{row['age']}",
        f"職業：{row['occupation']}",
        "",
        "📁 已填寫的資料會自動保存。",
        "",
    ]

    if stage == 0:
        missing = missing_personal(row)

        lines.extend([
            "**第一階段：個人資料**",
            (
                "尚未填寫："
                + (
                    "、".join(missing)
                    if missing
                    else "必填項目已完成"
                )
            ),
            "",
            "可使用下方選單分組填寫。",
            "需要修改姓名、年齡或職業時，"
            "點擊「修改基本資料」。",
        ])

    elif stage == 1:
        schema = get_schema(actor)
        points = settings.get(
            "wizard_points",
            {},
        )

        if not isinstance(points, dict):
            points = {}

        lines.append(
            "**第二階段：十三項屬性**"
        )

        for key, label in (
            ("general", "一般池"),
            ("talent", "藝能池"),
        ):
            spent = sum(
                points.get(name, 0)
                for name in schema[key]
                if type(
                    points.get(name, 0)
                ) is int
            )

            lines.append(
                f"{label}：{spent}/500點"
            )

        lines.extend([
            (
                "主賽道："
                + str(
                    settings.get("wizard_main")
                    or "未選擇"
                )
            ),
            (
                "副賽道："
                + str(
                    settings.get("wizard_secondary")
                    or "未選擇"
                )
            ),
            "",
            "請先選擇主、副賽道，"
            "再逐組填寫十三項配點。",
        ])

    elif stage == 2:
        lines.extend([
            "**第三階段：生日與感情設定**",
            (
                "生日："
                + str(
                    settings.get("birthday")
                    or "未填寫"
                )
            ),
            (
                "感情狀態："
                + str(
                    settings.get(
                        "relationship_status"
                    )
                    or "未填寫"
                )
            ),
            (
                "伴侶："
                + str(
                    settings.get("partner_name")
                    or "未填寫"
                )
            ),
        ])

    else:
        lines.append(
            "**第四階段：角色完整預覽**"
        )

        for label, key in FIELDS.items():
            if key in (
                "name",
                "age",
                "occupation",
            ):
                continue

            value = settings.get(key)

            lines.append(
                f"{label}："
                f"{str(value)[:45] if value not in (None, '') else '未填寫'}"
            )

        lines.extend([
            "生日："
            + str(
                settings.get("birthday")
                or "未填寫"
            ),
            "感情狀態："
            + str(
                settings.get(
                    "relationship_status"
                )
                or "未填寫"
            ),
            "",
            "請確認完整資料後送審。",
        ])

    return "\n".join(lines)[:1900]


async def show_error(interaction, exc):
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


class OwnedView(discord.ui.View):
    """所有創角按鈕共用權限驗證。"""

    def __init__(self, actor, *, timeout=TIMEOUT):
        super().__init__(timeout=timeout)
        self.actor = actor

    async def interaction_check(self, interaction):
        valid = (
            str(interaction.user.id)
            == self.actor.user_id
            and str(interaction.guild_id)
            == self.actor.guild_id
        )

        if not valid:
            await interaction.response.send_message(
                "⛔ 這不是你的創角介面。",
                ephemeral=True,
            )

        return valid


class BasicModal(discord.ui.Modal):
    """建立角色或修改已有草稿的基本資料。"""

    def __init__(self, actor, character_id=None):
        super().__init__(
            title=(
                "創角｜建立新角色"
                if character_id is None
                else "創角｜修改基本資料"
            )
        )

        self.actor = actor
        self.character_id = character_id

        old = (
            get_character(actor, character_id)
            if character_id is not None
            else None
        )

        self.name_field = discord.ui.TextInput(
            label="角色姓名",
            default=(
                str(old["name"])
                if old else None
            ),
            max_length=100,
        )

        self.age_field = discord.ui.TextInput(
            label="角色年齡",
            default=(
                str(old["age"])
                if old else None
            ),
            placeholder="18",
            max_length=3,
        )

        self.job_field = discord.ui.TextInput(
            label="角色職業",
            default=(
                str(old["occupation"])
                if old else None
            ),
            max_length=100,
        )

        self.add_item(self.name_field)
        self.add_item(self.age_field)
        self.add_item(self.job_field)

    async def on_submit(self, interaction):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            if (
                actor.guild_id
                != self.actor.guild_id
                or actor.user_id
                != self.actor.user_id
            ):
                raise PermissionDenied(
                    "無權使用這份表單。"
                )

            age = validate_field(
                "age",
                str(self.age_field.value),
                None,
            )

            name = validate_field(
                "name",
                str(self.name_field.value),
                age,
            )

            occupation = validate_field(
                "occupation",
                str(self.job_field.value),
                age,
            )

            if self.character_id is None:
                character_id = create_character(
                    actor.guild_id,
                    actor.user_id,
                    name,
                    age,
                    occupation,
                )
            else:
                character_id = self.character_id

                save_fields(
                    actor,
                    character_id,
                    {
                        "name": name,
                        "age": age,
                        "occupation": occupation,
                    },
                )

            await interaction.response.send_message(
                progress_text(
                    actor,
                    character_id,
                    0,
                ),
                view=WizardView(
                    actor,
                    character_id,
                    0,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


class DataModal(discord.ui.Modal):
    """每次最多五個個人資料欄位。"""

    def __init__(
        self,
        actor,
        character_id,
        labels,
        stage,
    ):
        super().__init__(
            title="創角｜資料填寫"
        )

        self.actor = actor
        self.character_id = character_id
        self.stage = stage

        row = get_character(
            actor,
            character_id,
        )

        settings = safe_json(
            row["settings_json"]
        )

        self.fields = []

        for label in labels:
            key = (
                FIELDS.get(label)
                or EXTRA_FIELDS.get(label)
            )

            if not key:
                raise ValueError(
                    f"未知的欄位：{label}"
                )

            maximum = (
                2000
                if key in LONG_FIELDS
                else 500
            )

            if key == "partner_name":
                maximum = 100

            if key == "birthday":
                maximum = 5

            field = discord.ui.TextInput(
                label=label[:45],
                default=str(
                    settings.get(key)
                    or ""
                )[:maximum],
                required=key in REQUIRED,
                max_length=maximum,
                style=(
                    discord.TextStyle.paragraph
                    if key in LONG_FIELDS
                    else discord.TextStyle.short
                ),
            )

            self.add_item(field)

            self.fields.append(
                (key, field)
            )

    async def on_submit(self, interaction):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            if (
                actor.user_id != self.actor.user_id
                or actor.guild_id != self.actor.guild_id
            ):
                raise PermissionDenied(
                    "無權使用這份表單。"
                )

            row = get_character(
                actor,
                self.character_id,
            )

            values = {}

            for key, field in self.fields:
                raw = str(field.value).strip()

                if not raw:
                    if key in REQUIRED:
                        raise ValueError(
                            "必填欄位不能空白。"
                        )

                    values[key] = ""
                    continue

                if key == "birthday":
                    try:
                        month, day = map(
                            int,
                            raw.replace(
                                "-",
                                "/",
                            ).split("/"),
                        )

                        date(2000, month, day)

                    except ValueError as exc:
                        raise ValueError(
                            "生日請填 MM/DD，"
                            "例如 06/15。"
                        ) from exc

                    values[key] = (
                        f"{month:02d}/"
                        f"{day:02d}"
                    )

                elif key == "relationship_status":
                    if raw not in RELATIONSHIP_STATUSES:
                        raise ValueError(
                            "感情狀態不符合官方清單："
                            + "、".join(
                                RELATIONSHIP_STATUSES
                            )
                        )

                    values[key] = raw

                else:
                    values[key] = validate_field(
                        key,
                        raw,
                        row["age"],
                    )

            save_fields(
                actor,
                self.character_id,
                values,
            )

            await interaction.response.send_message(
                progress_text(
                    actor,
                    self.character_id,
                    self.stage,
                ),
                view=WizardView(
                    actor,
                    self.character_id,
                    self.stage,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


class PointsModal(discord.ui.Modal):
    def __init__(
        self,
        actor,
        character_id,
        names,
    ):
        super().__init__(
            title="創角｜十三項屬性配點"
        )

        self.actor = actor
        self.character_id = character_id
        self.names = names

        row = get_character(
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

        self.inputs = []

        for name in names:
            field = discord.ui.TextInput(
                label=name,
                default=str(
                    points.get(name, 0)
                ),
                max_length=3,
            )

            self.add_item(field)
            self.inputs.append(field)

    async def on_submit(self, interaction):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            if (
                actor.user_id != self.actor.user_id
                or actor.guild_id != self.actor.guild_id
            ):
                raise PermissionDenied(
                    "無權修改這份配點。"
                )

            schema = get_schema(actor)

            row = get_character(
                actor,
                self.character_id,
            )

            settings = safe_json(
                row["settings_json"]
            )

            main = settings.get(
                "wizard_main"
            )

            secondary = settings.get(
                "wizard_secondary"
            )

            values = {}

            for name, field in zip(
                self.names,
                self.inputs,
            ):
                value = int(
                    str(field.value).strip()
                )

                base = (
                    35 if name == main
                    else 25 if name == secondary
                    else 20
                )

                limit = 150 - base

                if not 0 <= value <= limit:
                    raise ValueError(
                        f"{name}最多可分配"
                        f"{limit}點。"
                    )

                values[name] = value

            saved = settings.get(
                "wizard_points",
                {},
            )

            if not isinstance(saved, dict):
                saved = {}

            combined = {
                **saved,
                **values,
            }

            for pool, label in (
                ("general", "一般"),
                ("talent", "藝能"),
            ):
                spent = sum(
                    combined.get(name, 0)
                    for name in schema[pool]
                )

                if spent > 500:
                    raise ValueError(
                        f"{label}池合計"
                        f"{spent}點，超過500點。"
                    )

            save_points(
                actor,
                self.character_id,
                values,
            )

            await interaction.response.send_message(
                progress_text(
                    actor,
                    self.character_id,
                    1,
                ),
                view=WizardView(
                    actor,
                    self.character_id,
                    1,
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


class DataSelect(discord.ui.Select):
    """個人資料與生日資料選擇。"""

    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        self.actor = actor
        self.character_id = character_id
        self.stage = stage

        if stage == 0:
            groups = {
                str(i): labels
                for i, (_, labels)
                in enumerate(PERSONAL_GROUPS)
            }

            options = [
                discord.SelectOption(
                    label=title,
                    value=str(i),
                )
                for i, (title, _)
                in enumerate(PERSONAL_GROUPS)
            ]

        else:
            groups = {
                "extra": list(
                    EXTRA_FIELDS.keys()
                )
            }

            options = [
                discord.SelectOption(
                    label="生日與感情狀態",
                    value="extra",
                )
            ]

        self.groups = groups

        super().__init__(
            placeholder="選擇要填寫的資料",
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        names = self.groups[
            self.values[0]
        ]

        await interaction.response.send_modal(
            DataModal(
                self.actor,
                self.character_id,
                names,
                self.stage,
            )
        )


class PointSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
    ):
        self.actor = actor
        self.character_id = character_id

        schema = get_schema(actor)

        self.groups = {}
        options = []

        for pool, label in (
            ("general", "一般池"),
            ("talent", "藝能池"),
        ):
            for index in range(
                0,
                len(schema[pool]),
                5,
            ):
                key = f"{pool}:{index}"

                self.groups[key] = (
                    schema[pool][
                        index:index + 5
                    ]
                )

                options.append(
                    discord.SelectOption(
                        label=(
                            f"{label}｜"
                            f"第{index // 5 + 1}組"
                        ),
                        value=key,
                    )
                )

        super().__init__(
            placeholder="選擇屬性配點組別",
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        await interaction.response.send_modal(
            PointsModal(
                self.actor,
                self.character_id,
                self.groups[
                    self.values[0]
                ],
            )
        )


class TrackSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
        target,
        row_number,
    ):
        self.actor = actor
        self.character_id = character_id
        self.target = target

        schema = get_schema(actor)

        names = (
            schema["general"]
            + schema["talent"]
        )

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
            row=row_number,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        try:
            choice = self.values[0]

            character = get_character(
                self.actor,
                self.character_id,
            )

            settings = safe_json(
                character["settings_json"]
            )

            other = (
                "wizard_secondary"
                if self.target == "wizard_main"
                else "wizard_main"
            )

            if settings.get(other) == choice:
                raise ValueError(
                    "主、副賽道不可選相同屬性。"
                )

            save_fields(
                self.actor,
                self.character_id,
                {
                    self.target: choice
                },
            )

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    self.character_id,
                    1,
                ),
                view=WizardView(
                    self.actor,
                    self.character_id,
                    1,
                ),
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await show_error(interaction, exc)


class ConfirmationView(OwnedView):
    """切換階段前確認，避免誤觸。"""

    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        super().__init__(actor)
        self.character_id = character_id
        self.stage = stage

    @discord.ui.button(
        label="✅ 確認，進入下一頁",
        style=discord.ButtonStyle.success,
    )
    async def confirm(
        self,
        interaction,
        button,
    ):
        try:
            row = get_character(
                self.actor,
                self.character_id,
            )

            if self.stage == 0:
                missing = missing_personal(
                    row
                )

                if missing:
                    raise InvalidState(
                        "還缺少："
                        + "、".join(missing)
                    )

            elif self.stage == 1:
                finalize_points(
                    self.actor,
                    self.character_id,
                )

            next_stage = self.stage + 1

            save_fields(
                self.actor,
                self.character_id,
                {
                    "wizard_step": next_stage
                },
            )

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    self.character_id,
                    next_stage,
                ),
                view=WizardView(
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
            await show_error(interaction, exc)

    @discord.ui.button(
        label="✏️ 返回修改",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
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
            view=WizardView(
                self.actor,
                self.character_id,
                self.stage,
            ),
        )


class WizardView(OwnedView):
    """創角主畫面，四階段共用。"""

    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        super().__init__(actor)

        self.character_id = character_id
        self.stage = stage

        if stage == 0:
            self.add_item(
                DataSelect(
                    actor,
                    character_id,
                    0,
                )
            )

        elif stage == 1:
            self.add_item(
                PointSelect(
                    actor,
                    character_id,
                )
            )

            self.add_item(
                TrackSelect(
                    actor,
                    character_id,
                    "wizard_main",
                    1,
                )
            )

            self.add_item(
                TrackSelect(
                    actor,
                    character_id,
                    "wizard_secondary",
                    2,
                )
            )

        elif stage == 2:
            self.add_item(
                DataSelect(
                    actor,
                    character_id,
                    2,
                )
            )

        self.previous.disabled = stage == 0

        self.edit_basic.disabled = stage != 0

        self.next.label = (
            "📨 提交雙重審核"
            if stage == 3
            else "➡️ 下一頁"
        )

    @discord.ui.button(
        label="✏️ 修改基本資料",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def edit_basic(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            BasicModal(
                self.actor,
                self.character_id,
            )
        )

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
            next_stage = max(
                0,
                self.stage - 1,
            )

            save_fields(
                self.actor,
                self.character_id,
                {
                    "wizard_step": next_stage
                },
            )

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    self.character_id,
                    next_stage,
                ),
                view=WizardView(
                    self.actor,
                    self.character_id,
                    next_stage,
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await show_error(interaction, exc)

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
            row = get_character(
                self.actor,
                self.character_id,
            )

            if self.stage == 3:
                missing = missing_fields(row)

                if missing:
                    raise InvalidState(
                        "尚缺必填資料："
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
                        "✅ **角色已送審成功！**\n\n"
                        f"角色：{row['name']}\n"
                        f"目前狀態：{status}\n\n"
                        "請等待兩階段審核。\n"
                        "審核完成後再由玩家確認啟用。"
                    ),
                    view=None,
                )

                return

            await interaction.response.edit_message(
                content=(
                    f"❓ **確定完成"
                    f"「{STEPS[self.stage]}」嗎？**\n\n"
                    "確認後將進入下一頁。\n"
                    "正式送審前仍然可以返回修改。"
                ),
                view=ConfirmationView(
                    self.actor,
                    self.character_id,
                    self.stage,
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


class DraftSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        drafts,
        page,
    ):
        self.actor = actor

        start = page * PAGE_SIZE

        items = drafts[
            start:start + PAGE_SIZE
        ]

        super().__init__(
            placeholder="選擇要繼續填寫的草稿",
            options=[
                discord.SelectOption(
                    label=str(
                        row["name"]
                    )[:100],
                    value=str(row["id"]),
                    description=(
                        f"草稿 #{row['id']}｜"
                        f"{row['occupation'] or '未設定職業'}"
                    )[:100],
                )
                for row in items
            ],
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        try:
            character_id = int(
                self.values[0]
            )

            get_character(
                self.actor,
                character_id,
            )

            stage = stage_of(
                self.actor,
                character_id,
            )

            await interaction.response.edit_message(
                content=progress_text(
                    self.actor,
                    character_id,
                    stage,
                ),
                view=WizardView(
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
            await show_error(interaction, exc)


class DraftListView(OwnedView):
    def __init__(
        self,
        actor,
        drafts,
        page=0,
    ):
        super().__init__(actor)

        self.drafts = drafts

        self.max_page = max(
            0,
            (len(drafts) - 1) // PAGE_SIZE,
        )

        self.page = max(
            0,
            min(page, self.max_page),
        )

        if drafts:
            self.add_item(
                DraftSelect(
                    actor,
                    drafts,
                    self.page,
                )
            )

        self.previous.disabled = (
            self.page == 0
        )

        self.next.disabled = (
            self.page == self.max_page
        )

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(
        self,
        interaction,
        button,
    ):
        page = self.page - 1

        await interaction.response.edit_message(
            content=(
                "📁 **角色草稿清單**\n"
                f"第 {page + 1}/"
                f"{self.max_page + 1} 頁"
            ),
            view=DraftListView(
                self.actor,
                self.drafts,
                page,
            ),
        )

    @discord.ui.button(
        label="下一頁 ➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(
        self,
        interaction,
        button,
    ):
        page = self.page + 1

        await interaction.response.edit_message(
            content=(
                "📁 **角色草稿清單**\n"
                f"第 {page + 1}/"
                f"{self.max_page + 1} 頁"
            ),
            view=DraftListView(
                self.actor,
                self.drafts,
                page,
            ),
        )

    @discord.ui.button(
        label="🏠 返回創角中心",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def home(
        self,
        interaction,
        button,
    ):
        drafts = list_drafts(self.actor)

        await interaction.response.edit_message(
            content=home_text(len(drafts)),
            view=HomeView(self.actor),
        )


def home_text(draft_count):
    return (
        "🎭 **角色建立中心**\n\n"
        "歡迎來到演藝圈角色建立系統！\n\n"
        "**建立流程**\n"
        "① 建立角色基本資料\n"
        "② 填寫完整個人檔案\n"
        "③ 選擇主副賽道與十三項屬性\n"
        "④ 設定生日、感情資料\n"
        "⑤ 預覽並提交雙重審核\n\n"
        f"📁 尚未送審的草稿：{draft_count}份\n\n"
        "資料會自動保存，"
        "可以隨時繼續填寫。\n\n"
        "請選擇下方操作。"
    )


class HomeView(OwnedView):
    """執行 /創角 後的第一個畫面。"""

    @discord.ui.button(
        label="➕ 建立新角色",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def create_new(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            BasicModal(self.actor)
        )

    @discord.ui.button(
        label="📁 繼續角色草稿",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def continue_draft(
        self,
        interaction,
        button,
    ):
        drafts = list_drafts(
            self.actor
        )

        if not drafts:
            await interaction.response.send_message(
                "目前沒有未完成的角色草稿。",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                "📁 **角色草稿清單**\n\n"
                f"共有 {len(drafts)} 份草稿。\n"
                "請選擇要繼續填寫的角色。"
            ),
            view=DraftListView(
                self.actor,
                drafts,
            ),
        )

    @discord.ui.button(
        label="📖 創角規則",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def rules(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_message(
            (
                "📖 **角色建立規則**\n\n"
                "• 每名玩家可建立多名角色。\n"
                "• 所有草稿自動保存。\n"
                "• 一般配點池500點。\n"
                "• 藝能配點池500點。\n"
                "• 共十三項初始屬性。\n"
                "• 一般屬性基礎值20。\n"
                "• 主賽道基礎值35、額外+30。\n"
                "• 副賽道基礎值25、額外+15。\n"
                "• 配點後單項基礎加配點"
                "不可超過150。\n"
                "• 正式角色不能重配初始點數。\n"
                "• 送審需經過兩階段審核。\n"
                "• 審核通過後由玩家確認啟用。"
            ),
            ephemeral=True,
        )


class CreationWizard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="創角",
        description="開啟完整角色建立中心",
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

            drafts = list_drafts(
                actor
            )

            await interaction.response.send_message(
                home_text(len(drafts)),
                view=HomeView(actor),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await show_error(interaction, exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CreationWizard(bot)
    )
