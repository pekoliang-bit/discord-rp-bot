
"""Discord RP：角色資料、感情設定、審核及正式啟用。"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    create_character,
    get_characters,
    transaction,
)
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
    require_character_owner,
    require_character_visible,
    review_character,
    submit_character,
)
from core.attribute_validation import (
    validate_initial_attributes,
)


# 角色欄位
FIELDS = {
    "姓名": "name",
    "年齡": "age",
    "職業": "occupation",
    "性別": "gender",
    "國籍或出身": "nationality",
    "身高cm": "height_cm",
    "體重kg": "weight_kg",
    "主賽道": "main_track",
    "副賽道": "secondary_track",
    "立繪網址": "portrait_url",
    "立繪來源": "portrait_source",
    "學歷": "education",
    "語言": "languages",
    "資歷年數": "experience_years",
    "生平簡述": "biography",
    "藝名或稱號": "stage_name",
    "代表色碼": "color_hex",
    "公開人設": "public_persona",
    "私下性格": "private_personality",
    "特殊設定": "special_notes",
    "其他備註": "notes",
}

REQUIRED = {
    "gender",
    "nationality",
    "height_cm",
    "weight_kg",
    "main_track",
    "portrait_url",
    "portrait_source",
    "education",
    "languages",
    "experience_years",
    "biography",
}

RELATIONSHIP_STATUSES = (
    "單身",
    "有好感對象",
    "曖昧中",
    "交往中",
    "穩定交往",
    "訂婚",
    "已婚",
    "遠距離戀愛",
    "分居",
    "分手",
    "離婚",
    "喪偶",
    "不公開",
    "其他",
)

NO_PARTNER_STATUSES = {
    "單身",
    "分手",
    "離婚",
    "喪偶",
}


def read_settings(character):
    try:
        data = json.loads(
            character["settings_json"] or "{}"
        )
    except (TypeError, ValueError):
        data = {}

    return data if isinstance(data, dict) else {}


def relationship_display(settings):
    status = str(
        settings.get("relationship_status") or ""
    ).strip()

    partner = str(
        settings.get("partner_name") or ""
    ).strip()

    if not status and not partner:
        return "未填寫"

    if not partner:
        return status or "未填寫"

    return (
        f"{partner}（{status or '未設定'}）"
        "〔伴侶身分待確認〕"
    )


def validate_field(field, raw, age):
    value = raw.strip()

    if not value:
        raise ValueError("欄位內容不可空白。")

    if field in {"age", "experience_years"}:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(
                "年齡與資歷必須是整數。"
            ) from exc

        if field == "age":
            if not 0 <= number <= 200:
                raise ValueError(
                    "年齡必須介於 0 至 200 歲。"
                )
        else:
            if number < 0:
                raise ValueError(
                    "資歷不能是負數。"
                )
            if age is not None:
                if number > max(0, age - 5):
                    raise ValueError(
                        "資歷不能超過年齡減五年。"
                    )

        return number

    if field in {"height_cm", "weight_kg"}:
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                "身高與體重必須是數字。"
            ) from exc

        if (
            not number.is_finite()
            or number <= 0
            or number > 500
        ):
            raise ValueError(
                "身高或體重數值不合法。"
            )

        return str(number)

    if field == "color_hex":
        if not re.fullmatch(
            r"#[0-9a-fA-F]{6}",
            value,
        ):
            raise ValueError(
                "代表色碼請使用 #FFEBC5 格式。"
            )

    if field == "portrait_url":
        if not value.startswith(
            ("https://", "http://")
        ):
            raise ValueError(
                "立繪必須提供有效網址格式。"
            )

    max_length = (
        2000
        if field in {
            "biography",
            "special_notes",
            "notes",
        }
        else 500
    )

    if field in {"name", "occupation"}:
        max_length = 100

    if len(value) > max_length:
        raise ValueError(
            f"這個欄位最多 {max_length} 字。"
        )

    return value


def missing_fields(character):
    settings = read_settings(character)
    missing = []

    if not character["name"]:
        missing.append("姓名")

    if character["age"] is None:
        missing.append("年齡")

    if not character["occupation"]:
        missing.append("職業")

    for label, field in FIELDS.items():
        if field in REQUIRED:
            if settings.get(field) in ("", None):
                missing.append(label)

    # 所有送審入口共用生日必填檢查，不只創角精靈。
    from modules.character_numbering import normalize_birthday
    if not normalize_birthday(settings.get("birthday")):
        missing.append("生日（月／日）")

    experience = settings.get("experience_years")

    if (
        type(experience) is int
        and character["age"] is not None
        and experience > max(
            0,
            character["age"] - 5,
        )
    ):
        missing.append("資歷與年齡不符")

    return missing


def audit(conn, actor, action, character_id, details):
    conn.execute(
        """
        INSERT INTO audit_events (
            guild_id,
            actor_id,
            action,
            target_key,
            details_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            actor.guild_id,
            actor.user_id,
            action,
            f"character:{character_id}",
            json.dumps(
                details,
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )


async def reply(interaction, message):
    text = str(message)[:1900]

    if interaction.response.is_done():
        await interaction.followup.send(
            text,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            text,
            ephemeral=True,
        )


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="建立角色",
        description="建立角色草稿",
    )
    @app_commands.describe(
        name="角色姓名",
        age="角色年齡",
        occupation="角色職業",
    )
    async def create_profile(
        self,
        interaction: discord.Interaction,
        name: str,
        age: app_commands.Range[int, 0, 200],
        occupation: str,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            name = validate_field(
                "name",
                name,
                age,
            )

            occupation = validate_field(
                "occupation",
                occupation,
                age,
            )

            character_id = create_character(
                actor.guild_id,
                actor.user_id,
                name,
                age,
                occupation,
            )

            await reply(
                interaction,
                (
                    f"✅ 已建立角色草稿 #{character_id}\n"
                    f"姓名：{name}\n"
                    f"年齡：{age}\n"
                    f"職業：{occupation}\n\n"
                    "接下來請填寫完整檔案，"
                    "並完成初始配點。"
                ),
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
        ) as exc:
            await reply(interaction, f"⚠️ {exc}")

    @app_commands.command(
        name="角色",
        description="查看自己擁有的角色",
    )
    async def list_profiles(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            characters = get_characters(
                actor.guild_id,
                actor.user_id,
            )

            characters = [
                character
                for character in characters
                if character["status"] != "deleted"
            ]

            if not characters:
                await reply(
                    interaction,
                    "尚未建立角色。",
                )
                return

            lines = []

            for character in characters[:25]:
                lines.append(
                    f"#{character['id']} "
                    f"{character['name']}\n"
                    f"職業：{character['occupation']}\n"
                    f"狀態：{character['status']}\n"
                    f"體力：{character['stamina']}/"
                    f"{character['max_stamina']}"
                )

            await reply(
                interaction,
                "📋 **我的角色**\n\n"
                + "\n\n".join(lines),
            )

        except PermissionDenied as exc:
            await reply(interaction, f"⚠️ {exc}")

    @app_commands.command(
        name="角色填寫",
        description="修改角色草稿資料",
    )
    @app_commands.describe(
        character_id="角色編號",
        field="需要填寫的欄位",
        value="欄位內容",
    )
    @app_commands.choices(
        field=[
            app_commands.Choice(
                name=label,
                value=label,
            )
            for label in FIELDS
        ]
    )
    async def fill_field(
        self,
        interaction: discord.Interaction,
        character_id: int,
        field: app_commands.Choice[str],
        value: str,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            key = FIELDS.get(field.value)

            if key is None:
                raise ValueError(
                    "不支援的欄位。"
                )

            # 主、副賽道必須由初始配點指令設定，
            # 避免與配點紀錄不一致。
            if key in {
                "main_track",
                "secondary_track",
            }:
                raise InvalidState(
                    "主、副賽道請使用 /初始配點 "
                    "一起設定，不能單獨修改。"
                )

            with transaction() as conn:
                character = conn.execute(
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
                    character is None
                    or character["owner_id"]
                    != actor.user_id
                    or character["character_type"]
                    != "player"
                ):
                    raise PermissionDenied(
                        "只能修改自己擁有的角色。"
                    )

                if character["status"] != "draft":
                    raise InvalidState(
                        "只有草稿可以直接編輯。"
                    )

                parsed = validate_field(
                    key,
                    value,
                    character["age"],
                )

                if key in {
                    "name",
                    "age",
                    "occupation",
                }:
                    if key == "age":
                        settings = read_settings(
                            character
                        )
                        experience = settings.get(
                            "experience_years"
                        )

                        if (
                            type(experience) is int
                            and experience
                            > max(0, parsed - 5)
                        ):
                            raise InvalidState(
                                "新年齡與已有資歷不符，"
                                "請先調整資歷。"
                            )

                    conn.execute(
                        f"""
                        UPDATE characters
                        SET {key} = ?,
                            version = version + 1
                        WHERE guild_id = ?
                          AND id = ?
                        """,
                        (
                            parsed,
                            actor.guild_id,
                            character_id,
                        ),
                    )

                else:
                    settings = read_settings(
                        character
                    )
                    settings[key] = parsed

                    conn.execute(
                        """
                        UPDATE characters
                        SET settings_json = ?,
                            version = version + 1
                        WHERE guild_id = ?
                          AND id = ?
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

                audit(
                    conn,
                    actor,
                    "character.edit",
                    character_id,
                    {"field": key},
                )

            await reply(
                interaction,
                f"✅ 已更新「{field.value}」。",
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
        ) as exc:
            await reply(interaction, f"⚠️ {exc}")

    @app_commands.command(
        name="感情設定",
        description="選擇情感狀態及伴侶姓名",
    )
    @app_commands.describe(
        character_id="自己的角色編號",
        status="官方情感狀態",
        partner="伴侶姓名，可留空",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(
                name="未填寫／清空",
                value="__clear__",
            ),
            *[
                app_commands.Choice(
                    name=name,
                    value=name,
                )
                for name in RELATIONSHIP_STATUSES
            ],
        ]
    )
    async def relationship_setting(
        self,
        interaction: discord.Interaction,
        character_id: int,
        status: app_commands.Choice[str],
        partner: str | None = None,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            selected = status.value
            partner_name = (partner or "").strip()

            if (
                selected != "__clear__"
                and selected not in RELATIONSHIP_STATUSES
            ):
                raise ValueError(
                    "請選擇官方情感狀態。"
                )

            if len(partner_name) > 100:
                raise ValueError(
                    "伴侶姓名不能超過100字。"
                )

            if selected == "__clear__":
                if partner_name:
                    raise ValueError(
                        "清空狀態時請勿填寫伴侶。"
                    )
                partner_name = ""

            if (
                partner_name
                and selected in NO_PARTNER_STATUSES
            ):
                raise ValueError(
                    "此狀態不適合填寫現任伴侶。"
                )

            with transaction() as conn:
                character = conn.execute(
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
                    character is None
                    or character["owner_id"]
                    != actor.user_id
                    or character["character_type"]
                    != "player"
                ):
                    raise PermissionDenied(
                        "只能設定自己的角色。"
                    )

                if character["status"] != "draft":
                    raise InvalidState(
                        "正式角色的公開關係變更"
                        "必須另外審核。"
                    )

                settings = read_settings(character)

                settings["relationship_status"] = (
                    ""
                    if selected == "__clear__"
                    else selected
                )
                settings["partner_name"] = partner_name

                conn.execute(
                    """
                    UPDATE characters
                    SET settings_json = ?,
                        version = version + 1
                    WHERE guild_id = ?
                      AND id = ?
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

                audit(
                    conn,
                    actor,
                    "character.relationship.edit",
                    character_id,
                    {
                        "status": settings[
                            "relationship_status"
                        ],
                        "partner_proposed": bool(
                            partner_name
                        ),
                    },
                )

            await reply(
                interaction,
                "✅ 感情資料已儲存。\n"
                "情感狀態："
                + relationship_display(settings),
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
        ) as exc:
            await reply(interaction, f"⚠️ {exc}")

    @app_commands.command(
        name="角色檔案",
        description="查看有權檢視的角色詳細資料",
    )
    @app_commands.describe(
        character_id="角色編號",
    )
    async def profile_detail(
        self,
        interaction: discord.Interaction,
        character_id: int,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            character = require_character_visible(
                actor,
                character_id,
            )

            settings = read_settings(character)

            lines = [
                f"📄 **角色 #{character['id']}**",
                f"姓名：{character['name']}",
                f"年齡：{character['age']}",
                f"職業：{character['occupation']}",
                f"狀態：{character['status']}",
            ]

            for label, field in FIELDS.items():
                if field in {
                    "name",
                    "age",
                    "occupation",
                }:
                    continue

                value = settings.get(field)

                if value in ("", None):
                    value = "未填寫"

                lines.append(
                    f"{label}：{str(value)[:100]}"
                )

            lines.append(
                "情感狀態："
                + relationship_display(settings)
            )

            missing = missing_fields(character)

            if missing:
                lines.append(
                    "尚缺必填："
                    + "、".join(missing)
                )

            await reply(
                interaction,
                "\n".join(lines),
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await reply(interaction, f"⚠️ {exc}")

    @app_commands.command(
        name="角色送審",
        description="提交角色進入第一階段審核",
    )
    @app_commands.describe(
        character_id="角色編號",
    )
    async def submit_profile(
        self,
        interaction: discord.Interaction,
        character_id: int,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            character = require_character_owner(
                actor,
                character_id,
            )

            missing = missing_fields(character)

            if missing:
                raise InvalidState(
                    "必填資料尚未完成："
                    + "、".join(missing)
                )

            # 送審前先驗證配點；啟用時還會
            # 在同一資料庫交易中再次驗證。
            with transaction() as conn:
                validate_initial_attributes(
                    conn,
                    actor.guild_id,
                    character_id,
                )

            status = submit_character(
                actor,
                character_id,
            )

            await reply(
                interaction,
                f"✅ 角色 #{character_id} "
                f"已送審。\n狀態：{status}",
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await reply(interaction, f"⚠️ {exc}")

    @app_commands.command(
        name="角色審核",
        description="管理員執行第一審、第二審或退件",
    )
    @app_commands.describe(
        character_id="角色編號",
        approve="是否通過",
        reason="審核意見或退件理由",
    )
    async def review_profile(
        self,
        interaction: discord.Interaction,
        character_id: int,
        approve: bool,
        reason: str = "",
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            status = review_character(
                actor,
                character_id,
                approve=approve,
                reason=reason,
                guild_owner_id=interaction.guild.owner_id if interaction.guild else None,
            )

            await reply(
                interaction,
                f"✅ 角色 #{character_id} "
                f"審核完成。\n目前狀態：{status}",
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await reply(interaction, f"⚠️ {exc}")

    @app_commands.command(
        name="角色啟用",
        description="二審通過後由玩家確認正式啟用",
    )
    @app_commands.describe(
        character_id="角色編號",
    )
    async def activate_profile(
        self,
        interaction: discord.Interaction,
        character_id: int,
    ):
        try:
            actor = Actor.from_interaction(
                interaction
            )

            with transaction() as conn:
                character = conn.execute(
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

                if character is None:
                    raise InvalidState(
                        "角色不存在。"
                    )

                if (
                    character["owner_id"]
                    != actor.user_id
                    or character["character_type"]
                    != "player"
                ):
                    raise PermissionDenied(
                        "只有角色擁有者可以啟用。"
                    )

                if character["status"] != "approved":
                    raise InvalidState(
                        "角色必須先通過兩階段審核。"
                    )

                missing = missing_fields(character)

                if missing:
                    raise InvalidState(
                        "角色必填資料不完整："
                        + "、".join(missing)
                    )

                # 關鍵：在同一筆交易中驗證屬性、
                # 更新狀態，避免檢查後被插隊修改。
                validation = validate_initial_attributes(
                    conn,
                    actor.guild_id,
                    character_id,
                )

                result = conn.execute(
                    """
                    UPDATE characters
                    SET status = 'active',
                        version = version + 1
                    WHERE guild_id = ?
                      AND id = ?
                      AND status = 'approved'
                    """,
                    (
                        actor.guild_id,
                        character_id,
                    ),
                )

                if result.rowcount != 1:
                    raise InvalidState(
                        "角色狀態已變更，請重試。"
                    )

                from core.character_creation_consent import persist_rumor_consent
                from core.character_proxy_identity import activate as activate_proxy
                persist_rumor_consent(conn,actor.guild_id,character_id,read_settings(character))
                activate_proxy(conn,actor.guild_id,character_id)

                # 正式開群前永不核發公開編號；開群後才核發。
                from modules.character_numbering import issue_in_transaction
                issue_in_transaction(conn, actor.guild_id, character_id)

                audit(
                    conn,
                    actor,
                    "character.activate",
                    character_id,
                    {
                        "from": "approved",
                        "to": "active",
                        "schema_version": validation[
                            "schema_version"
                        ],
                        "main_track": validation[
                            "main_track"
                        ],
                        "secondary_track": validation[
                            "secondary_track"
                        ],
                    },
                )

            # 僅在正式 approved→active 成功後處理歷史抽取；
            # 未匯入合法素材時記錄待補建，絕不捏造作品或提前加成。
            try:
                from core.history_v23 import attempt_lock
                attempt_lock(actor.guild_id, character_id)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("歷史履歷待補建：角色 %s",character_id)
            try:
                from modules.notifications import queue_notification
                queue_notification(actor.guild_id,'private','role',
                    f'🎉 角色 #{character_id} 已正式啟用。新人禮包請至原本領取入口辦理；歷史履歷在素材齊備後顯示。',
                    character_id=character_id,dedupe_key=f'v23:activation:{actor.guild_id}:{character_id}')
            except Exception:
                import logging
                logging.getLogger(__name__).exception("啟用私人通知排程失敗")

            await reply(
                interaction,
                (
                    f"🎉 角色 #{character_id} "
                    "已正式啟用！\n"
                    "初始屬性驗證：通過\n"
                    "雙重審核：通過\n"
                    "🎭 角色專屬代理發言身分已建立，編號沿用角色 ID；"
                    "機器人使用方式將於角色通知帖子說明。"
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await reply(interaction, f"⚠️ {exc}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
