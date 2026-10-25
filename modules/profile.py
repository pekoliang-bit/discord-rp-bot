"""Discord RP 角色檔案、草稿編修、送審、雙重審核及啟用。

配合 core/database.py 和 core/permissions.py 使用。
注意：本模組不取代獨立的角色屬性配點及立繪上傳審核模組。
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation

import discord
from discord import app_commands
from discord.ext import commands

from core.database import create_character, get_characters, get_connection, transaction
from core.permissions import (
    Actor, InvalidState, PermissionDenied,
    require_character_owner, require_character_visible,
    review_character, submit_character,
)

LOG = logging.getLogger(__name__)

# 供 /角色填寫 使用的欄位：名稱、年齡、職業為資料表欄位，其餘存在 settings_json。
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

REQUIRED = (
    "gender", "nationality", "height_cm", "weight_kg", "main_track",
    "portrait_url", "portrait_source", "education", "languages",
    "experience_years", "biography",
)


def _data(character):
    try:
        payload = json.loads(character["settings_json"] or "{}")
    except (ValueError, TypeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _validate_value(field: str, raw: str, age: int | None):
    value = raw.strip()
    if not value:
        raise ValueError("欄位內容不可空白。")
    if field == "age":
        try:
            result = int(value)
        except ValueError as exc:
            raise ValueError("年齡必須是整數。") from exc
        if not 0 <= result <= 200:
            raise ValueError("年齡須介於 0～200 歲。")
        return result
    if field == "experience_years":
        try:
            result = int(value)
        except ValueError as exc:
            raise ValueError("資歷年數必須是整數。") from exc
        if result < 0 or (age is not None and result > max(0, age - 5)):
            raise ValueError("資歷不能小於零，且不得超過角色年齡減五年。")
        return result
    if field in ("height_cm", "weight_kg"):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("身高或體重必須是數字。") from exc
        if not result.is_finite() or result <= 0 or result > 500:
            raise ValueError("身高或體重須為合理的正數。")
        return str(result)
    if field == "name" and len(value) > 100:
        raise ValueError("姓名不得超過 100 字。")
    if field == "color_hex":
        import re
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError("代表色碼格式應為 #FFEBC5。")
    if field == "portrait_url" and not value.startswith(("https://", "http://")):
        raise ValueError("立繪網址必須是 http:// 或 https:// 開頭。")
    if len(value) > (2000 if field in {"biography", "special_notes", "notes"} else 500):
        raise ValueError("欄位內容太長，請縮短後再填寫。")
    return value


def _submission_missing(character):
    data = _data(character)
    missing = []
    for label, key in FIELDS.items():
        if key in REQUIRED and data.get(key) in (None, ""):
            missing.append(label)
    if not character["name"] or character["age"] is None or not character["occupation"]:
        missing.append("姓名／年齡／職業")
    # 尚未建立獨立屬性配點模組前，只能送完整基本角色資料進一審。
    # 正式實裝時必須在核准前額外驗證屬性、賽道與數值池，不能因本檔送審而略過。
    return missing


def _actor(interaction: discord.Interaction):
    return Actor.from_interaction(interaction)


async def _reply(interaction: discord.Interaction, message: str):
    if interaction.response.is_done():
        await interaction.followup.send(message[:1900], ephemeral=True)
    else:
        await interaction.response.send_message(message[:1900], ephemeral=True)


def _audit(conn, actor: Actor, action: str, target_id: int, payload: dict):
    conn.execute(
        "INSERT INTO audit_events(guild_id,actor_id,action,target_key,details_json) "
        "VALUES (?,?,?,?,?)",
        (actor.guild_id, actor.user_id, action, f"character:{target_id}",
         json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="建立角色", description="建立一份角色草稿（還不能直接參加遊戲）")
    @app_commands.describe(name="姓名", age="年齡", occupation="職業")
    async def create_profile(
        self, interaction: discord.Interaction, name: str,
        age: app_commands.Range[int, 0, 200], occupation: str,
    ):
        try:
            _actor(interaction)
            if len(occupation.strip()) > 100:
                raise ValueError("職業名稱過長。")
            character_id = create_character(
                interaction.guild_id, interaction.user.id, name, age, occupation.strip()
            )
            await _reply(interaction, (
                f"✅ 草稿 #{character_id} 已建立：{name}\n"
                "接著使用 /角色填寫 補齊所有必填資料。\n"
                "⚠️ 草稿不等於已啟用角色；必須經一審、二審並由本人確認啟用。"
            ))
        except (ValueError, PermissionDenied, InvalidState) as exc:
            await _reply(interaction, f"⚠️ {exc}")

    @app_commands.command(name="角色", description="查看自己持有的角色及其審核狀態")
    async def list_profiles(self, interaction: discord.Interaction):
        try:
            actor = _actor(interaction)
            rows = get_characters(actor.guild_id, actor.user_id)
            rows = [x for x in rows if x["status"] != "deleted"]
            if not rows:
                return await _reply(interaction, "尚無角色，請使用 /建立角色。")
            lines = [
                f"#{x['id']}｜{x['name']}｜{x['occupation']}｜{x['status']}｜"
                f"體力 {x['stamina']}/{x['max_stamina']}"
                for x in rows[:25]
            ]
            await _reply(interaction, "📋 **我的角色**\n" + "\n".join(lines))
        except PermissionDenied as exc:
            await _reply(interaction, f"⚠️ {exc}")

    @app_commands.command(name="角色填寫", description="編輯草稿的一項欄位，不會清除其他欄位")
    @app_commands.describe(character_id="角色編號", field="要填寫的欄位", value="欄位內容")
    @app_commands.choices(field=[app_commands.Choice(name=k, value=k) for k in FIELDS])
    async def fill_field(
        self, interaction: discord.Interaction, character_id: int,
        field: app_commands.Choice[str], value: str,
    ):
        try:
            actor = _actor(interaction)
            key = FIELDS.get(field.value)
            if key is None:
                raise ValueError("不支援的角色欄位。")
            with transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM characters WHERE guild_id=? AND id=?",
                    (actor.guild_id, character_id),
                ).fetchone()
                if row is None or row["owner_id"] != actor.user_id or row["character_type"] != "player":
                    raise PermissionDenied("只能修改自己的玩家角色。")
                if row["status"] != "draft":
                    raise InvalidState("目前只能修改草稿；送審中及通過後不能直接改動。")
                parsed = _validate_value(key, value, row["age"])
                if key in {"name", "age", "occupation"}:
                    conn.execute(
                        f"UPDATE characters SET {key}=?, version=version+1 "
                        "WHERE guild_id=? AND id=?",
                        (parsed, actor.guild_id, character_id),
                    )
                else:
                    info = _data(row)
                    info[key] = parsed
                    conn.execute(
                        "UPDATE characters SET settings_json=?,version=version+1 "
                        "WHERE guild_id=? AND id=?",
                        (json.dumps(info, ensure_ascii=False), actor.guild_id, character_id),
                    )
                _audit(conn, actor, "character.edit", character_id, {"field": key})
            await _reply(interaction, f"✅ 角色 #{character_id} 的「{field.value}」已儲存。")
        except (PermissionDenied, InvalidState, ValueError) as exc:
            await _reply(interaction, f"⚠️ {exc}")

    @app_commands.command(name="角色檔案", description="查看個人完整草稿或有權檢視的角色檔案")
    @app_commands.describe(character_id="角色編號")
    async def profile_detail(self, interaction: discord.Interaction, character_id: int):
        try:
            actor = _actor(interaction)
            row = require_character_visible(actor, character_id)
            data = _data(row)
            lines = [
                f"📄 **#{row['id']} {row['name']}**",
                f"狀態：{row['status']}｜年齡：{row['age']}｜職業：{row['occupation']}",
            ]
            for label, key in FIELDS.items():
                if key not in {"name", "age", "occupation"}:
                    lines.append(f"{label}：{str(data.get(key, '未填'))[:120]}")
            missing = _submission_missing(row)
            if missing:
                lines.append("尚缺必填：" + "、".join(missing))
            await _reply(interaction, "\n".join(lines))
        except (PermissionDenied, InvalidState) as exc:
            await _reply(interaction, f"⚠️ {exc}")

    @app_commands.command(name="角色送審", description="補齊必填資料後，將草稿送交第一審")
    @app_commands.describe(character_id="角色編號")
    async def submit_profile(self, interaction: discord.Interaction, character_id: int):
        try:
            actor = _actor(interaction)
            row = require_character_owner(actor, character_id)
            missing = _submission_missing(row)
            if missing:
                raise InvalidState("必填資料尚未完成：" + "、".join(missing))
            new_status = submit_character(actor, character_id)
            await _reply(interaction, f"✅ 已送審角色 #{character_id}，狀態：{new_status}。")
        except (PermissionDenied, InvalidState) as exc:
            await _reply(interaction, f"⚠️ {exc}")

    @app_commands.command(name="角色審核", description="管理員執行角色一審、二審或附理由退件")
    @app_commands.describe(character_id="角色編號", approve="是否通過", reason="退件理由；通過時可留意見")
    async def review_profile(
        self, interaction: discord.Interaction, character_id: int,
        approve: bool, reason: str = "",
    ):
        try:
            actor = _actor(interaction)
            status = review_character(actor, character_id, approve=approve, reason=reason)
            await _reply(interaction, f"✅ 角色 #{character_id} 審核處理完成，新狀態：{status}。")
        except (PermissionDenied, InvalidState, ValueError) as exc:
            await _reply(interaction, f"⚠️ {exc}")

    @app_commands.command(name="角色啟用", description="二審通過後，由擁有者確認正式啟用")
    @app_commands.describe(character_id="角色編號")
    async def activate_profile(self, interaction: discord.Interaction, character_id: int):
        try:
            actor = _actor(interaction)
            with transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM characters WHERE guild_id=? AND id=?",
                    (actor.guild_id, character_id),
                ).fetchone()
                if row is None or row["owner_id"] != actor.user_id or row["character_type"] != "player":
                    raise PermissionDenied("只有角色擁有者可以啟用。")
                if row["status"] != "approved":
                    raise InvalidState("只有二審通過的角色可以啟用。")
                # 正式初始屬性配點驗證仍由後續 attributes 模組補齊。
                # 在該模組尚未接入前，不允許直接啟用不完整角色。
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM attributes WHERE guild_id=? AND character_id=?",
                    (actor.guild_id, character_id),
                ).fetchone()["n"]
                if n < 13:
                    raise InvalidState("尚未完成十三項初始屬性建檔，為保護規則暫不能啟用。")
                updated = conn.execute(
                    "UPDATE characters SET status='active',version=version+1 "
                    "WHERE guild_id=? AND id=? AND status='approved'",
                    (actor.guild_id, character_id),
                )
                if updated.rowcount != 1:
                    raise InvalidState("角色狀態已變更，請重試。")
                _audit(conn, actor, "character.activate", character_id, {"from": "approved", "to": "active"})
            await _reply(interaction, f"🎉 角色 #{character_id} 已正式啟用！")
        except (PermissionDenied, InvalidState) as exc:
            await _reply(interaction, f"⚠️ {exc}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
