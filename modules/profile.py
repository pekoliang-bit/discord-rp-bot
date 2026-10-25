
"""Discord RP 角色檔案、草稿、審核、啟用與感情設定。"""

from __future__ import annotations

import json
import logging
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

LOG = logging.getLogger(__name__)


# 可透過 /角色填寫 修改的欄位
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


# 官方提供的情感狀態清單
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


# 角色送審必填欄位
REQUIRED = (
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
)


def _data(character):
    """讀取角色的擴充資料。"""
    try:
        payload = json.loads(
            character["settings_json"] or "{}"
        )
    except (ValueError, TypeError):
        payload = {}

    if isinstance(payload, dict):
        return payload

    return {}


def _relationship_display(info: dict) -> str:
    """將感情資料轉換為角色檔案顯示文字。"""

    status = str(
        info.get("relationship_status") or ""
    ).strip()

    partner = str(
        info.get("partner_name") or ""
    ).strip()

    if not status and not partner:
        return "未填寫"

    if not partner:
        return status

    # 自由填寫伴侶姓名不代表雙方已建立正式關係
    return (
        f"{partner}（{status or '未設定狀態'}）"
        "〔伴侶身分待確認〕"
    )


def _validate_value(
    field: str,
    raw: str,
    age: int | None,
):
    """驗證角色填寫內容。"""

    value = raw.strip()

    if not value:
        raise ValueError("欄位內容不可空白。")

    if field == "age":
        try:
            result = int(value)
        except ValueError as exc:
            raise ValueError(
                "年齡必須是整數。"
            ) from exc

        if not 0 <= result <= 200:
            raise ValueError(
                "年齡須介於 0～200 歲。"
            )

        return result

    if field == "experience_years":
        try:
            result = int(value)
        except ValueError as exc:
            raise ValueError(
                "資歷年數必須是整數。"
            ) from exc

        if result < 0:
            raise ValueError(
                "資歷年數不可小於零。"
            )

        if (
            age is not None
            and result > max(0, age - 5)
        ):
            raise ValueError(
                "資歷不得超過角色年齡減五年。"
            )

        return result

    if field in ("height_cm", "weight_kg"):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(
                "身高或體重必須是數字。"
            ) from exc

        if (
            not result.is_finite()
            or result <= 0
            or result > 500
        ):
            raise ValueError(
                "身高或體重必須是合理的正數。"
            )

        return str(result)

    if field == "name":
        if len(value) > 100:
            raise ValueError(
                "角色姓名不得超過 100 字。"
            )

    if field == "color_hex":
        if not re.fullmatch(
            r"#[0-9A-Fa-f]{6}",
            value
        ):
            raise ValueError(
                "代表色碼格式應為 #FFEBC5。"
            )

    if field == "portrait_url":
        if not value.startswith(
            ("https://", "http://")
        ):
            raise ValueError(
                "立繪網址必須以 http:// "
                "或 https:// 開頭。"
            )

    large_fields = {
        "biography",
        "special_notes",
        "notes",
    }

    limit = (
        2000 if field in large_fields else 500
    )

    if len(value) > limit:
        raise ValueError(
            f"欄位內容不可超過 {limit} 字。"
        )

    return value


def _submission_missing(character):
    """檢查角色送審前缺少的資料。"""

    data = _data(character)
    missing = []

    for label, key in FIELDS.items():
        if key not in REQUIRED:
            continue

        if data.get(key) in (None, ""):
            missing.append(label)

    if (
        not character["name"]
        or character["age"] is None
        or not character["occupation"]
    ):
        missing.append("姓名／年齡／職業")

    return missing


def _actor(interaction: discord.Interaction):
    return Actor.from_interaction(interaction)


async def _reply(
    interaction: discord.Interaction,
    message: str,
):
    """所有角色資料預設私人回覆。"""

    if interaction.response.is_done():
        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            message[:1900],
            ephemeral=True,
        )


def _audit(
    conn,
    actor: Actor,
    action: str,
    target_id: int,
    payload: dict,
):
    """記錄角色資料變更。"""

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
            f"character:{target_id}",
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )


class Profile(commands.Cog):

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    # ==========================
    # 建立角色
    # ==========================

    @app_commands.command(
        name="建立角色",
        description="建立新的角色草稿",
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
            _actor(interaction)

            if not name.strip():
                raise ValueError(
                    "角色姓名不可空白。"
                )

            if len(name.strip()) > 100:
                raise ValueError(
                    "角色姓名過長。"
                )

            if not occupation.strip():
                raise ValueError(
                    "角色職業不可空白。"
                )

            if len(occupation.strip()) > 100:
                raise ValueError(
                    "職業名稱過長。"
                )

            character_id = create_character(
                interaction.guild_id,
                interaction.user.id,
                name.strip(),
                age,
                occupation.strip(),
            )

            await _reply(
                interaction,
                (
                    f"✅ 角色草稿 #{character_id} "
                    f"已建立：{name}\n"
                    "請使用 /角色填寫 "
                    "補齊角色資料。\n"
                    "情感狀態可使用 /感情設定。\n"
                    "草稿必須經過雙重審核，"
                    "再由玩家確認啟用。"
                ),
            )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
        ) as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )

    # ==========================
    # 我的角色
    # ==========================

    @app_commands.command(
        name="角色",
        description="查看自己持有的角色",
    )
    async def list_profiles(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = _actor(interaction)

            rows = get_characters(
                actor.guild_id,
                actor.user_id,
            )

            rows = [
                row
                for row in rows
                if row["status"] != "deleted"
            ]

            if not rows:
                await _reply(
                    interaction,
                    "你目前沒有角色，"
                    "請使用 /建立角色。",
                )
                return

            lines = []

            for row in rows[:25]:
                lines.append(
                    f"#{row['id']}｜"
                    f"{row['name']}｜"
                    f"{row['occupation']}｜"
                    f"{row['status']}｜"
                    f"體力：{row['stamina']}/"
                    f"{row['max_stamina']}"
                )

            await _reply(
                interaction,
                "📋 **我的角色**\n"
                + "\n".join(lines),
            )

        except PermissionDenied as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )

    # ==========================
    # 填寫角色
    # ==========================

    @app_commands.command(
        name="角色填寫",
        description="修改角色草稿的一項資料",
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
            actor = _actor(interaction)

            key = FIELDS.get(
                field.value
            )

            if key is None:
                raise ValueError(
                    "不支援的角色欄位。"
                )

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
                    or row["owner_id"] != actor.user_id
                    or row["character_type"] != "player"
                ):
                    raise PermissionDenied(
                        "只能修改自己的玩家角色。"
                    )

                if row["status"] != "draft":
                    raise InvalidState(
                        "目前只能直接修改草稿。"
                        "送審後的重大修改"
                        "須遵循審核流程。"
                    )

                parsed = _validate_value(
                    key,
                    value,
                    row["age"],
                )

                if key in {
                    "name",
                    "age",
                    "occupation",
                }:
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
                    info = _data(row)

                    info[key] = parsed

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
                                info,
                                ensure_ascii=False,
                            ),
                            actor.guild_id,
                            character_id,
                        ),
                    )

                _audit(
                    conn,
                    actor,
                    "character.edit",
                    character_id,
                    {"field": key},
                )

            await _reply(
                interaction,
                (
                    f"✅ 角色 #{character_id} "
                    f"的「{field.value}」"
                    "已儲存。"
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )

    # ==========================
    # 感情設定
    # ==========================

    @app_commands.command(
        name="感情設定",
        description="設定角色的情感狀態與伴侶姓名",
    )
    @app_commands.describe(
        character_id="自己的角色編號",
        status="選擇官方情感狀態",
        partner="伴侶姓名，沒有可以留空",
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
            actor = _actor(interaction)

            selected = status.value

            if (
                selected != "__clear__"
                and selected not in RELATIONSHIP_STATUSES
            ):
                raise ValueError(
                    "請從官方選單選擇情感狀態。"
                )

            partner_name = (
                partner or ""
            ).strip()

            if len(partner_name) > 100:
                raise ValueError(
                    "伴侶姓名不得超過100字。"
                )

            if selected == "__clear__":
                if partner_name:
                    raise ValueError(
                        "清空狀態時，"
                        "請不要同時輸入伴侶姓名。"
                    )

                partner_name = ""

            if (
                partner_name
                and selected in NO_PARTNER_STATUSES
            ):
                raise ValueError(
                    "目前的情感狀態"
                    "不適合填寫現任伴侶姓名。"
                )

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
                    or row["owner_id"] != actor.user_id
                    or row["character_type"] != "player"
                ):
                    raise PermissionDenied(
                        "只能設定自己的玩家角色。"
                    )

                if row["status"] != "draft":
                    raise InvalidState(
                        "目前只能編輯草稿感情資料。"
                        "正式角色的公開關係變更"
                        "需要遵循審核流程。"
                    )

                info = _data(row)

                info["relationship_status"] = (
                    ""
                    if selected == "__clear__"
                    else selected
                )

                info["partner_name"] = partner_name

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
                            info,
                            ensure_ascii=False,
                        ),
                        actor.guild_id,
                        character_id,
                    ),
                )

                _audit(
                    conn,
                    actor,
                    "character.relationship.edit",
                    character_id,
                    {
                        "status": info[
                            "relationship_status"
                        ],
                        "partner_proposed": bool(
                            partner_name
                        ),
                    },
                )

            await _reply(
                interaction,
                (
                    f"✅ 角色 #{character_id} "
                    "感情資料已儲存。\n"
                    "情感狀態："
                    f"{_relationship_display(info)}"
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )

    # ==========================
    # 完整角色檔案
    # ==========================

    @app_commands.command(
        name="角色檔案",
        description="查看角色詳細資料",
    )
    @app_commands.describe(
        character_id="角色編號"
    )
    async def profile_detail(
        self,
        interaction: discord.Interaction,
        character_id: int,
    ):
        try:
            actor = _actor(interaction)

            row = require_character_visible(
                actor,
                character_id,
            )

            data = _data(row)

            lines = [
                f"📄 **#{row['id']} "
                f"{row['name']}**",
                (
                    f"狀態：{row['status']}\n"
                    f"年齡：{row['age']}\n"
                    f"職業：{row['occupation']}"
                ),
            ]

            for label, key in FIELDS.items():
                if key in {
                    "name",
                    "age",
                    "occupation",
                }:
                    continue

                value = data.get(
                    key,
                    "未填",
                )

                lines.append(
                    f"{label}："
                    f"{str(value)[:120]}"
                )

            lines.append(
                "情感狀態："
                + _relationship_display(data)
            )

            missing = _submission_missing(row)

            if missing:
                lines.append(
                    "尚缺必填："
                    + "、".join(missing)
                )

            await _reply(
                interaction,
                "\n".join(lines),
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )

    # ==========================
    # 角色送審
    # ==========================

    @app_commands.command(
        name="角色送審",
        description="將完整角色草稿送交第一審",
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
            actor = _actor(interaction)

            row = require_character_owner(
                actor,
                character_id,
            )

            missing = _submission_missing(row)

            if missing:
                raise InvalidState(
                    "以下必填資料尚未完成："
                    + "、".join(missing)
                )

            new_status = submit_character(
                actor,
                character_id,
            )

            await _reply(
                interaction,
                (
                    f"✅ 角色 #{character_id} "
                    f"已送審。\n"
                    f"目前狀態：{new_status}"
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )

    # ==========================
    # 雙重審核
    # ==========================

    @app_commands.command(
        name="角色審核",
        description="管理員審核角色",
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
            actor = _actor(interaction)

            status = review_character(
                actor,
                character_id,
                approve=approve,
                reason=reason,
            )

            await _reply(
                interaction,
                (
                    f"✅ 角色 #{character_id} "
                    "審核處理完成。\n"
                    f"目前狀態：{status}"
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )

    # ==========================
    # 確認正式啟用
    # ==========================

    @app_commands.command(
        name="角色啟用",
        description="二審通過後確認啟用角色",
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
            actor = _actor(interaction)

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
                    or row["owner_id"] != actor.user_id
                    or row["character_type"] != "player"
                ):
                    raise PermissionDenied(
                        "只有角色擁有者"
                        "可以確認啟用。"
                    )

                if row["status"] != "approved":
                    raise InvalidState(
                        "只有通過二審的角色"
                        "可以正式啟用。"
                    )

                count = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM attributes
                    WHERE guild_id = ?
                      AND character_id = ?
                    """,
                    (
                        actor.guild_id,
                        character_id,
                    ),
                ).fetchone()["n"]

                if count < 13:
                    raise InvalidState(
                        "角色尚未完成十三項"
                        "初始屬性建檔，"
                        "目前不能正式啟用。"
                    )

                # 正式配點池與賽道加成合法性，
                # 仍須由獨立 attributes 模組驗證。

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
                        "角色狀態已變更，"
                        "請重新確認。"
                    )

                _audit(
                    conn,
                    actor,
                    "character.activate",
                    character_id,
                    {
                        "from": "approved",
                        "to": "active",
                    },
                )

            await _reply(
                interaction,
                (
                    f"🎉 角色 #{character_id} "
                    "已正式啟用！"
                ),
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await _reply(
                interaction,
                f"⚠️ {exc}",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
