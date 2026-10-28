
"""Discord RP：正式創角中心。

/創角
- 多角色與永久草稿
- 個人資料分組填寫
- 五選一主攻，沒有副賽道
- 十三項屬性、雙500點配點池
- 生日、感情、完整預覽
- 雙階段審核送件
- 完整錯誤日誌
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
    ensure_default_schema,
    load_schema,
    calculate_initial,
    allocate_initial,
)
from modules import profile as profile_module

LOG = logging.getLogger(__name__)

TIMEOUT = 900
PAGE_SIZE = 25

# 特殊管制數值：不可由玩家自行配點
RESTRICTED_INITIAL_STATS = frozenset({
    "影響指數",
})


def ensure_no_restricted_points(points):
    """防止舊草稿或其他流程寫入禁止配點的數值。"""
    for name in RESTRICTED_INITIAL_STATS:
        value = points.get(name, 0)

        if type(value) is not int or value != 0:
            raise InvalidState(
                f"「{name}」只能透過系統或指定獎勵取得，"
                "創角時不可自行配點。"
            )

MAIN_SKILLS = (
    "演繹",
    "舞蹈",
    "歌唱",
    "綜藝",
    "主持",
)

STEPS = (
    "個人資料",
    "主攻與十三項配點",
    "生日與感情",
    "預覽與送審",
)

FIELDS = dict(profile_module.FIELDS)

REQUIRED = set(
    profile_module.REQUIRED
) - {
    "secondary_track",
    "main_track",
}

RELATIONSHIP_STATUSES = tuple(
    profile_module.RELATIONSHIP_STATUSES
)

VALIDATE_FIELD = (
    getattr(
        profile_module,
        "validate_field",
        None,
    )
    or getattr(
        profile_module,
        "_validate_value",
    )
)

GROUPS = (
    (
        "基本資訊",
        (
            "性別",
            "國籍或出身",
            "身高cm",
            "體重kg",
            "學歷",
        ),
    ),
    (
        "經歷與立繪",
        (
            "立繪網址",
            "立繪來源",
            "語言",
            "資歷年數",
            "生平簡述",
        ),
    ),
    (
        "人物介紹",
        (
            "藝名或稱號",
            "代表色碼",
            "公開人設",
            "私下性格",
            "特殊設定",
        ),
    ),
    (
        "其他設定",
        ("其他備註",),
    ),
)

EXTRA = {
    "生日（月／日）": "birthday",
    "感情狀態": "relationship_status",
    "伴侶姓名": "partner_name",
}

LONG = {
    "biography",
    "notes",
    "special_notes",
    "public_persona",
    "private_personality",
}


def unpack(raw):
    try:
        obj = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}

    return (
        obj
        if isinstance(obj, dict)
        else {}
    )


def actor_from(interaction):
    return Actor.from_interaction(
        interaction
    )


def check_owner(interaction, actor):
    actual = actor_from(interaction)

    if (
        actual.guild_id != actor.guild_id
        or actual.user_id != actor.user_id
    ):
        raise PermissionDenied(
            "這不是你的創角表單。"
        )

    return actual


def draft(actor, character_id):
    with get_connection() as db:
        row = db.execute(
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
            "找不到角色草稿。"
        )

    row = dict(row)

    if (
        str(row["owner_id"])
        != actor.user_id
        or row["character_type"]
        != "player"
    ):
        raise PermissionDenied(
            "不能操作其他玩家的角色。"
        )

    if row["status"] != "draft":
        raise InvalidState(
            "角色已送審或啟用，"
            "不能修改草稿。"
        )

    return row


def drafts(actor):
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT id, name, age,
                   occupation, settings_json
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

    return [
        dict(row)
        for row in rows
    ]


def save(actor, character_id, values):
    with transaction() as db:
        row = db.execute(
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

        if (
            row is None
            or str(row["owner_id"])
            != actor.user_id
            or row["character_type"]
            != "player"
        ):
            raise PermissionDenied(
                "無權修改這名角色。"
            )

        if row["status"] != "draft":
            raise InvalidState(
                "角色已不在草稿狀態。"
            )

        settings = unpack(
            row["settings_json"]
        )

        basics = {
            key: row[key]
            for key in (
                "name",
                "age",
                "occupation",
            )
        }

        for key, value in values.items():
            if key in basics:
                basics[key] = value
            else:
                settings[key] = value

        years = settings.get(
            "experience_years"
        )

        if (
            type(years) is int
            and type(basics["age"]) is int
            and years > max(
                0,
                basics["age"] - 5,
            )
        ):
            raise ValueError(
                "資歷不得超過年齡減五年。"
            )

        changed = db.execute(
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
                basics["name"],
                basics["age"],
                basics["occupation"],
                json.dumps(
                    settings,
                    ensure_ascii=False,
                ),
                actor.guild_id,
                int(character_id),
            ),
        )

        if changed.rowcount != 1:
            raise InvalidState(
                "草稿狀態已變更。"
            )


def schema_for(actor):
    with transaction() as db:
        ensure_default_schema(
            db,
            actor.guild_id,
        )

        return load_schema(
            db,
            actor.guild_id,
        )


def missing_personal(row):
    settings = unpack(
        row["settings_json"]
    )

    return [
        label
        for label, key in FIELDS.items()
        if (
            key in REQUIRED
            and settings.get(key)
            in (None, "")
        )
    ]


def points_of(settings):
    points = settings.get(
        "wizard_points"
    )

    return (
        points
        if isinstance(points, dict)
        else {}
    )


def finish_points(actor, character_id):
    row = draft(
        actor,
        character_id,
    )

    settings = unpack(
        row["settings_json"]
    )

    main = settings.get(
        "wizard_main"
    )

    if main not in MAIN_SKILLS:
        raise InvalidState(
            "請先選擇演繹、舞蹈、"
            "歌唱、綜藝、主持"
            "其中一項主攻。"
        )

    schema = schema_for(actor)
    points = points_of(settings)

    names = (
        schema["general"]
        + schema["talent"]
    )

    if any(
        type(points.get(name)) is not int
        for name in names
    ):
        raise InvalidState(
            "十三項都必須填寫，"
            "未分配的請填 0。"
        )

    general = {
        name: points[name]
        for name in schema["general"]
    }

    talent = {
        name: points[name]
        for name in schema["talent"]
    }

    calculate_initial(
        schema,
        main,
        general,
        talent,
    )

    result = allocate_initial(
        actor,
        character_id,
        main,
        json.dumps(
            general,
            ensure_ascii=False,
        ),
        json.dumps(
            talent,
            ensure_ascii=False,
        ),
    )

    with transaction() as db:
        validate_initial_attributes(
            db,
            actor.guild_id,
            character_id,
        )

    return result


def screen(actor, character_id, stage):
    row = draft(
        actor,
        character_id,
    )

    settings = unpack(
        row["settings_json"]
    )

    lines = [
        f"🎭 **創角｜{STEPS[stage]}**",
        f"角色：{row['name']}",
        f"年齡：{row['age']}",
        f"職業：{row['occupation']}",
        "📁 所有填寫資料自動保存。",
        "",
    ]

    if stage == 0:
        missing = missing_personal(row)

        lines += [
            "**第一階段：個人資料**",
            "必填尚缺："
            + (
                "、".join(missing)
                if missing
                else "已完成"
            ),
            "從選單選擇一組資料填寫。",
        ]

    elif stage == 1:
        schema = schema_for(actor)
        points = points_of(settings)

        lines += [
            "**第二階段：主攻與十三項配點**",
            "🎯 主攻："
            + str(
                settings.get("wizard_main")
                or "尚未選擇"
            ),
        ]

        for pool, title in (
            ("general", "一般池"),
            ("talent", "藝能池"),
        ):
            spent = sum(
                points.get(name, 0)
                for name in schema[pool]
                if type(
                    points.get(name, 0)
                ) is int
            )

            lines.append(
                f"{title}：{spent}/500點"
            )

        lines += [
            "主攻五選一：演繹、舞蹈、"
            "歌唱、綜藝、主持。",
            "主攻基礎35、額外加成30；"
            "其餘基礎20。",
            "沒有副賽道。",
            "每項都要填寫數字，"
            "沒有配點請填0。",
        ]

    elif stage == 2:
        lines += [
            "**第三階段：生日與感情設定（選填）**",
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
            "伴侶："
            + str(
                settings.get("partner_name")
                or "未填寫"
            ),
        ]

    else:
        lines += [
            "**第四階段：完整預覽**",
            "姓名：" + row["name"],
            "年齡：" + str(row["age"]),
            "職業：" + str(
                row["occupation"]
            ),
        ]

        for label, key in FIELDS.items():
            if key in (
                "name",
                "age",
                "occupation",
                "secondary_track",
            ):
                continue

            value = (
                settings.get("main_track")
                if key == "main_track"
                else settings.get(key)
            )

            shown = (
                str(value)[:48]
                if value not in (None, "")
                else "未填寫"
            )

            lines.append(
                (
                    "主攻技能"
                    if key == "main_track"
                    else label
                )
                + "："
                + shown
            )

        lines += [
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
            "確認所有資料後再送審。",
        ]

    return "\n".join(lines)[:1900]


async def report(
    interaction,
    exc,
    place="創角",
):
    LOG.error(
        "%s 操作失敗",
        place,
        exc_info=(
            type(exc),
            exc,
            exc.__traceback__,
        ),
    )

    if isinstance(
        exc,
        (
            InvalidState,
            PermissionDenied,
            ValueError,
        ),
    ):
        message = (
            f"⚠️ {str(exc)[:1400]}"
        )
    else:
        message = (
            "⚠️ 系統發生 "
            f"{type(exc).__name__}，"
            "詳細原因已記錄在 "
            "Railway Deploy Logs。"
        )

    try:
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

    except discord.HTTPException:
        LOG.exception(
            "創角錯誤回覆失敗"
        )


class SafeModal(discord.ui.Modal):
    async def on_error(
        self,
        interaction,
        error,
    ):
        await report(
            interaction,
            error,
            self.title,
        )


class OwnedView(discord.ui.View):
    def __init__(self, actor):
        super().__init__(
            timeout=TIMEOUT
        )
        self.actor = actor

    async def interaction_check(
        self,
        interaction,
    ):
        try:
            check_owner(
                interaction,
                self.actor,
            )
            return True

        except Exception as exc:
            await report(
                interaction,
                exc,
                "權限驗證",
            )
            return False

    async def on_error(
        self,
        interaction,
        error,
        item,
    ):
        await report(
            interaction,
            error,
            "創角互動",
        )


class BasicModal(SafeModal):
    def __init__(
        self,
        actor,
        character_id=None,
    ):
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
            draft(actor, character_id)
            if character_id is not None
            else None
        )

        self.name = discord.ui.TextInput(
            label="角色姓名",
            default=(
                str(old["name"])
                if old
                else None
            ),
            max_length=100,
        )

        self.age = discord.ui.TextInput(
            label="角色年齡",
            default=(
                str(old["age"])
                if old
                else None
            ),
            max_length=3,
        )

        self.job = discord.ui.TextInput(
            label="角色職業",
            default=(
                str(old["occupation"])
                if old
                else None
            ),
            max_length=100,
        )

        for item in (
            self.name,
            self.age,
            self.job,
        ):
            self.add_item(item)

    async def on_submit(
        self,
        interaction,
    ):
        try:
            actor = check_owner(
                interaction,
                self.actor,
            )

            age = VALIDATE_FIELD(
                "age",
                str(self.age.value),
                None,
            )

            name = VALIDATE_FIELD(
                "name",
                str(self.name.value),
                age,
            )

            occupation = VALIDATE_FIELD(
                "occupation",
                str(self.job.value),
                age,
            )

            # 優先回應 Discord，
            # 避免資料庫操作造成 3 秒逾時。
            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
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
                character_id = (
                    self.character_id
                )

                save(
                    actor,
                    character_id,
                    {
                        "name": name,
                        "age": age,
                        "occupation": occupation,
                    },
                )

            await interaction.followup.send(
                screen(
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

        except Exception as exc:
            await report(
                interaction,
                exc,
                "建立角色基本資料",
            )


class DataModal(SafeModal):
    def __init__(
        self,
        actor,
        character_id,
        labels,
        stage,
    ):
        super().__init__(
            title="創角｜填寫資料"
        )

        self.actor = actor
        self.character_id = character_id
        self.stage = stage

        settings = unpack(
            draft(
                actor,
                character_id,
            )["settings_json"]
        )

        self.entries = []

        for label in labels:
            key = (
                FIELDS.get(label)
                or EXTRA.get(label)
            )

            if not key:
                raise ValueError(
                    f"未知欄位：{label}"
                )

            limit = (
                5
                if key == "birthday"
                else 100
                if key == "partner_name"
                else 2000
                if key in LONG
                else 500
            )

            item = discord.ui.TextInput(
                label=label[:45],
                default=str(
                    settings.get(key) or ""
                )[:limit],
                required=key in REQUIRED,
                max_length=limit,
                style=(
                    discord.TextStyle.paragraph
                    if key in LONG
                    else discord.TextStyle.short
                ),
            )

            self.add_item(item)
            self.entries.append(
                (key, item)
            )

    async def on_submit(
        self,
        interaction,
    ):
        try:
            actor = check_owner(
                interaction,
                self.actor,
            )

            row = draft(
                actor,
                self.character_id,
            )

            values = {}

            for key, item in self.entries:
                raw = str(
                    item.value
                ).strip()

                if not raw:
                    if key in REQUIRED:
                        raise ValueError(
                            "必填欄位不可留空。"
                        )

                    values[key] = ""

                elif key == "birthday":
                    try:
                        month, day = map(
                            int,
                            raw.replace(
                                "-",
                                "/",
                            ).split("/"),
                        )

                        date(
                            2000,
                            month,
                            day,
                        )

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
                    if (
                        raw
                        not in RELATIONSHIP_STATUSES
                    ):
                        raise ValueError(
                            "感情狀態請填："
                            + "、".join(
                                RELATIONSHIP_STATUSES
                            )
                        )

                    values[key] = raw

                else:
                    values[key] = VALIDATE_FIELD(
                        key,
                        raw,
                        row["age"],
                    )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            save(
                actor,
                self.character_id,
                values,
            )

            await interaction.followup.send(
                screen(
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

        except Exception as exc:
            await report(
                interaction,
                exc,
                "個人資料保存",
            )


class PointsModal(SafeModal):
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

        settings = unpack(
            draft(
                actor,
                character_id,
            )["settings_json"]
        )

        points = points_of(settings)
        self.entries = []

        for name in names:
            item = discord.ui.TextInput(
                label=name,
                default=str(
                    points.get(name, 0)
                ),
                max_length=3,
            )

            self.add_item(item)
            self.entries.append(item)

    async def on_submit(
        self,
        interaction,
    ):
        try:
            actor = check_owner(
                interaction,
                self.actor,
            )

            settings = unpack(
                draft(
                    actor,
                    self.character_id,
                )["settings_json"]
            )

            main = settings.get(
                "wizard_main"
            )

            if main not in MAIN_SKILLS:
                raise InvalidState(
                    "請先選擇主攻技能。"
                )

            new = {}

            for name, item in zip(
                self.names,
                self.entries,
            ):
                value = int(
                    str(item.value).strip()
                )

                limit = (
                    115
                    if name == main
                    else 130
                )

                if (
                    value < 0
                    or value > limit
                ):
                    raise ValueError(
                        f"「{name}」最多"
                        f"可配 {limit} 點。"
                    )

                new[name] = value

            combined = {
                **points_of(settings),
                **new,
            }

            schema = schema_for(actor)

            for pool, label in (
                ("general", "一般"),
                ("talent", "藝能"),
            ):
                total = sum(
                    combined.get(name, 0)
                    for name in schema[pool]
                )

                if total > 500:
                    raise ValueError(
                        f"{label}池"
                        "超過 500 點。"
                    )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            save(
                actor,
                self.character_id,
                {
                    "wizard_points": combined
                },
            )

            await interaction.followup.send(
                screen(
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

        except Exception as exc:
            await report(
                interaction,
                exc,
                "屬性配點保存",
            )


class DataSelect(discord.ui.Select):
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

        else:
            groups = {
                "extra": tuple(EXTRA)
            }

            options = [
                discord.SelectOption(
                    label="生日與感情資料",
                    value="extra",
                )
            ]

        self.groups = groups

        super().__init__(
            placeholder="選擇要填寫的資料",
            options=options,
            row=0,
        )

    async def callback(
        self,
        interaction,
    ):
        await interaction.response.send_modal(
            DataModal(
                self.actor,
                self.character_id,
                self.groups[
                    self.values[0]
                ],
                self.stage,
            )
        )


class MainSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
    ):
        self.actor = actor
        self.character_id = character_id

        super().__init__(
            placeholder=(
                "🎯 選擇唯一主攻技能"
                "（五選一）"
            ),
            options=[
                discord.SelectOption(
                    label=name,
                    value=name,
                )
                for name in MAIN_SKILLS
            ],
            row=1,
        )

    async def callback(
        self,
        interaction,
    ):
        try:
            row = draft(
                self.actor,
                self.character_id,
            )

            settings = unpack(
                row["settings_json"]
            )

            main = self.values[0]

            values = {
                "wizard_main": main,
                "wizard_secondary": "",
            }

            # 改選主攻時，舊配點可能
            # 超出新主攻的 115 點上限。
            if (
                settings.get("wizard_main")
                != main
            ):
                values["wizard_points"] = {}

            save(
                self.actor,
                self.character_id,
                values,
            )

            await interaction.response.edit_message(
                content=screen(
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

        except Exception as exc:
            await report(
                interaction,
                exc,
                "選擇主攻",
            )


class PointSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        character_id,
    ):
        self.actor = actor
        self.character_id = character_id

        schema = schema_for(actor)
        self.groups = {}
        options = []

        for pool, title in (
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
                            f"{title}｜"
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

    async def callback(
        self,
        interaction,
    ):
        settings = unpack(
            draft(
                self.actor,
                self.character_id,
            )["settings_json"]
        )

        if (
            settings.get("wizard_main")
            not in MAIN_SKILLS
        ):
            raise InvalidState(
                "請先選擇主攻技能。"
            )

        await interaction.response.send_modal(
            PointsModal(
                self.actor,
                self.character_id,
                self.groups[
                    self.values[0]
                ],
            )
        )


class ConfirmView(OwnedView):
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
        label="✅ 確認進入下一頁",
        style=discord.ButtonStyle.success,
    )
    async def confirm(
        self,
        interaction,
        button,
    ):
        try:
            row = draft(
                self.actor,
                self.character_id,
            )

            if self.stage == 0:
                missing = missing_personal(
                    row
                )

                if missing:
                    raise InvalidState(
                        "必填尚缺："
                        + "、".join(missing)
                    )

            if self.stage == 1:
                await interaction.response.defer(
                    ephemeral=True,
                    thinking=True,
                )

                finish_points(
                    self.actor,
                    self.character_id,
                )

                stage = 2

                save(
                    self.actor,
                    self.character_id,
                    {
                        "wizard_step": stage
                    },
                )

                await interaction.followup.send(
                    screen(
                        self.actor,
                        self.character_id,
                        stage,
                    ),
                    view=WizardView(
                        self.actor,
                        self.character_id,
                        stage,
                    ),
                    ephemeral=True,
                )

                return

            stage = self.stage + 1

            save(
                self.actor,
                self.character_id,
                {
                    "wizard_step": stage
                },
            )

            await interaction.response.edit_message(
                content=screen(
                    self.actor,
                    self.character_id,
                    stage,
                ),
                view=WizardView(
                    self.actor,
                    self.character_id,
                    stage,
                ),
            )

        except Exception as exc:
            await report(
                interaction,
                exc,
                "確認頁面",
            )

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
            content=screen(
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
    def __init__(
        self,
        actor,
        character_id,
        stage,
    ):
        super().__init__(actor)

        self.character_id = character_id
        self.stage = stage

        if stage in (0, 2):
            self.add_item(
                DataSelect(
                    actor,
                    character_id,
                    stage,
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
                MainSelect(
                    actor,
                    character_id,
                )
            )

        self.previous.disabled = (
            stage == 0
        )

        self.edit_basic.disabled = (
            stage != 0
        )

        if stage == 3:
            self.next.label = (
                "📨 提交雙重審核"
            )

    @discord.ui.button(
        label="🏷️ 選擇初始標籤",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def choose_tags(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        try:
            # 確認正在操作的是自己的角色草稿
            row = draft(
                self.actor,
                self.character_id,
            )

            # 從正式標籤庫開啟角色標籤選單
            from modules.tag_selection_ui import (
                show_tag_picker,
            )

            await show_tag_picker(
                interaction,
                self.character_id,
                row["character_type"],
                row["owner_id"],
            )

        except Exception as exc:
            await report(
                interaction,
                exc,
                "選擇角色初始標籤",
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
            stage = max(
                0,
                self.stage - 1,
            )

            save(
                self.actor,
                self.character_id,
                {
                    "wizard_step": stage
                },
            )

            await interaction.response.edit_message(
                content=screen(
                    self.actor,
                    self.character_id,
                    stage,
                ),
                view=WizardView(
                    self.actor,
                    self.character_id,
                    stage,
                ),
            )

        except Exception as exc:
            await report(
                interaction,
                exc,
                "返回上一頁",
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
            row = draft(
                self.actor,
                self.character_id,
            )

            if self.stage == 3:
                missing = missing_personal(
                    row
                )

                if missing:
                    raise InvalidState(
                        "仍缺少必填欄位："
                        + "、".join(missing)
                    )

                settings = unpack(
                    row["settings_json"]
                )

                if (
                    settings.get("main_track")
                    not in MAIN_SKILLS
                ):
                    raise InvalidState(
                        "請完成唯一主攻配點。"
                    )

                await interaction.response.defer(
                    ephemeral=True,
                    thinking=True,
                )

                with transaction() as db:
                    validate_initial_attributes(
                        db,
                        self.actor.guild_id,
                        self.character_id,
                    )

                status = submit_character(
                    self.actor,
                    self.character_id,
                )

                await interaction.followup.send(
                    (
                        f"✅ 角色「{row['name']}」"
                        "已送審！\n"
                        f"目前狀態：{status}\n"
                        "請等待雙階段審核。"
                    ),
                    ephemeral=True,
                )

                return

            await interaction.response.edit_message(
                content=(
                    "❓ 確定完成"
                    f"「{STEPS[self.stage]}」嗎？\n"
                    "確認後進入下一頁，"
                    "送審前仍可修改。"
                ),
                view=ConfirmView(
                    self.actor,
                    self.character_id,
                    self.stage,
                ),
            )

        except Exception as exc:
            await report(
                interaction,
                exc,
                "送審或換頁",
            )


class DraftSelect(discord.ui.Select):
    def __init__(
        self,
        actor,
        rows,
        page,
    ):
        self.actor = actor

        subset = rows[
            page * PAGE_SIZE:
            (page + 1) * PAGE_SIZE
        ]

        super().__init__(
            placeholder="選擇要繼續的草稿",
            row=0,
            options=[
                discord.SelectOption(
                    label=str(
                        row["name"]
                    )[:100],
                    value=str(row["id"]),
                    description=(
                        f"草稿 #{row['id']}"
                    ),
                )
                for row in subset
            ],
        )

    async def callback(
        self,
        interaction,
    ):
        character_id = int(
            self.values[0]
        )

        row = draft(
            self.actor,
            character_id,
        )

        stage = unpack(
            row["settings_json"]
        ).get(
            "wizard_step",
            0,
        )

        stage = (
            max(0, min(3, stage))
            if type(stage) is int
            else 0
        )

        await interaction.response.edit_message(
            content=screen(
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


class DraftView(OwnedView):
    def __init__(
        self,
        actor,
        page=0,
    ):
        super().__init__(actor)

        rows = drafts(actor)

        self.max_page = max(
            0,
            (len(rows) - 1) // PAGE_SIZE,
        )

        self.page = max(
            0,
            min(
                page,
                self.max_page,
            ),
        )

        if rows:
            self.add_item(
                DraftSelect(
                    actor,
                    rows,
                    self.page,
                )
            )

        self.previous.disabled = (
            self.page == 0
        )

        self.next.disabled = (
            self.page >= self.max_page
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
        view = DraftView(
            self.actor,
            self.page - 1,
        )

        await interaction.response.edit_message(
            content=(
                "📁 草稿清單｜"
                f"第{view.page + 1}/"
                f"{view.max_page + 1}頁"
            ),
            view=view,
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
        view = DraftView(
            self.actor,
            self.page + 1,
        )

        await interaction.response.edit_message(
            content=(
                "📁 草稿清單｜"
                f"第{view.page + 1}/"
                f"{view.max_page + 1}頁"
            ),
            view=view,
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
        await interaction.response.edit_message(
            content=home_text(
                len(drafts(self.actor))
            ),
            view=HomeView(self.actor),
        )


def home_text(count):
    return (
        "🎭 **角色建立中心**\n\n"
        "① 建立基本資料\n"
        "② 填寫個人檔案\n"
        "③ 選擇唯一主攻及十三項配點\n"
        "④ 生日與感情設定\n"
        "⑤ 預覽及雙審送件\n\n"
        "主攻五選一：演繹、舞蹈、"
        "歌唱、綜藝、主持。\n"
        "**沒有副賽道。**\n"
        f"📁 未送審草稿：{count}份\n"
        "所有草稿自動保存。"
    )


class HomeView(OwnedView):
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
        rows = drafts(
            self.actor
        )

        if not rows:
            await interaction.response.send_message(
                "目前沒有尚未送審的草稿。",
                ephemeral=True,
            )
            return

        view = DraftView(
            self.actor
        )

        await interaction.response.edit_message(
            content=(
                "📁 草稿清單｜"
                f"共{len(rows)}份"
            ),
            view=view,
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
                "📖 **創角規則**\n"
                "13項屬性，一般池500點、"
                "藝能池500點。\n"
                "主攻技能五選一，"
                "基礎35、額外+30；"
                "其他基礎20。\n"
                "單項基礎加配點上限150，"
                "額外加成可突破。\n"
                "未分配點數作廢；"
                "啟用後不得重新分配"
                "初始點數。\n"
                "送審需要兩階段審核。"
            ),
            ephemeral=True,
        )


class CreationWizard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="創角",
        description=(
            "建立角色或繼續已保存的草稿"
        ),
    )
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = actor_from(
                interaction
            )

            await interaction.response.send_message(
                home_text(
                    len(drafts(actor))
                ),
                view=HomeView(actor),
                ephemeral=True,
            )

        except Exception as exc:
            await report(
                interaction,
                exc,
                "開啟創角中心",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        CreationWizard(bot)
    )
