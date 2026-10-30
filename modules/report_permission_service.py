"""Discord RP 舉報權限服務（第 2 檔）。

放置：modules/report_permission_service.py
依賴：discord.py、core.database、modules.official_center、
      modules.report_repository（第 1 檔）。

原則：
* 只有 guild.owner_id 為真正群主；Administrator 並非群主。
* 舉報正式審核員、紀律人員或其他官方人員，一律群主專屬。
* 紀律人員不得看到群主專屬案件；任何非群主不得取得舉報人 ID。
* 所有由機器人建立的私人頻道，應明確保留群主檢視權限。
* 本模組不新增指令、不自動掃描或改動現有頻道。

注意：匿名保護還必須由其他模組遵守，絕不能將舉報者 ID 寫進
紀律人員可見的事件日誌、通知、檔案、Embed 或自訂 ID。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import discord
from core.database import get_connection
from modules.official_center import has_staff_permission
from modules import report_repository as repo


class ReportPermissionError(PermissionError):
    """舉報系統存取權限不足。"""


def _guild_and_actor(interaction: discord.Interaction) -> tuple[discord.Guild, discord.abc.User]:
    guild = interaction.guild
    user = interaction.user
    if guild is None or user is None or interaction.guild_id != guild.id:
        raise ReportPermissionError("舉報操作只允許在伺服器內執行。")
    return guild, user


def is_guild_owner(interaction: discord.Interaction) -> bool:
    """不以 administrator、manage_guild 或機器人擁有者代替伺服器群主。"""
    guild = interaction.guild
    return bool(guild and interaction.user and interaction.user.id == guild.owner_id)


def require_guild_owner(interaction: discord.Interaction) -> None:
    _guild_and_actor(interaction)
    if not is_guild_owner(interaction):
        raise ReportPermissionError("只有 Discord 伺服器群主可以進行這項操作。")


def is_discipline_staff(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    return bool(has_staff_permission(interaction, "discipline"))


def is_reviewer_staff(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    return bool(has_staff_permission(interaction, "reviewer"))


def require_discipline_or_owner(interaction: discord.Interaction) -> None:
    _guild_and_actor(interaction)
    if not (is_guild_owner(interaction) or is_discipline_staff(interaction)):
        raise ReportPermissionError("只有正式紀律人員或群主可以查看舉報案件。")


def _configured_staff_member(guild: discord.Guild, member: discord.Member) -> bool:
    """額外辨識官方身分組，避免已離職但角色權限尚未同步者被錯誤路由。

    判斷目的是決定案件必須走群主專屬，不授予任何審核權限。
    """
    role_ids = {role.id for role in member.roles}
    with get_connection() as conn:
        try:
            rows = conn.execute(
                "SELECT role_id FROM official_role_config WHERE guild_id=?",
                (str(guild.id),),
            ).fetchall()
        except Exception as exc:
            # 不能因資料庫故障而默默把官方舉報變成一般案件。
            raise RuntimeError("無法驗證官方人員身分組，請稍後重試。") from exc
    for row in rows:
        try:
            if int(row["role_id"]) in role_ids:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _has_official_grant(guild_id: int, user_id: int) -> bool:
    with get_connection() as conn:
        try:
            row = conn.execute(
                "SELECT 1 FROM official_staff_grants WHERE guild_id=? AND user_id=? LIMIT 1",
                (str(guild_id), str(user_id)),
            ).fetchone()
        except Exception as exc:
            raise RuntimeError("無法查驗官方授權紀錄，請稍後重試。") from exc
    return row is not None


async def must_route_to_owner(guild: discord.Guild, target_user_id: int, *,
                              report_type: str = "player") -> bool:
    """可靠地判斷是否為群主專屬舉報。

    工作人員（審核員／紀律人員）、群主及其他具有官方職位者均須群主處理。
    若無法驗證目標成員身分，採保守路由至群主，避免洩漏。
    """
    if report_type == "staff" or target_user_id == guild.owner_id:
        return True
    if _has_official_grant(guild.id, target_user_id):
        return True
    member = guild.get_member(target_user_id)
    if member is None:
        try:
            member = await guild.fetch_member(target_user_id)
        except discord.NotFound:
            # 曾任官方者可能已離群；仍需由授權紀錄判斷。
            return False
        except (discord.Forbidden, discord.HTTPException):
            return True  # 無法確認身分時，安全地僅交群主
    if _configured_staff_member(guild, member):
        return True
    # Discord 管理權限本身不賦予紀律處理權，但涉及高權限者交群主。
    return bool(member.guild_permissions.administrator)


@dataclass(frozen=True)
class ReportAccess:
    guild_id: str
    actor_user_id: str
    is_owner: bool
    is_discipline: bool


async def resolve_access(interaction: discord.Interaction) -> ReportAccess:
    guild, user = _guild_and_actor(interaction)
    owner = is_guild_owner(interaction)
    return ReportAccess(str(guild.id), str(user.id), owner,
                        owner or is_discipline_staff(interaction))


async def create_report_checked(
    interaction: discord.Interaction, *, target_user_id: int,
    report_type: str, reason_code: str, description: str,
    evidence_urls: list[str], target_character_id: Optional[int] = None,
    occurrence_note: str = "",
) -> int:
    """建案入口：後端判斷 owner_only，不能信任玩家表單傳來的值。"""
    guild, actor = _guild_and_actor(interaction)
    owner_only = await must_route_to_owner(guild, int(target_user_id), report_type=report_type)
    return repo.create_report(
        guild_id=guild.id, reporter_user_id=actor.id,
        target_user_id=int(target_user_id), report_type=report_type,
        reason_code=reason_code, description=description,
        evidence_urls=evidence_urls, target_character_id=target_character_id,
        occurrence_note=occurrence_note, owner_only=owner_only,
    )


async def get_reviewable_report(interaction: discord.Interaction, report_id: int) -> dict:
    """回傳不含舉報人身分的案件，並禁止被舉報者接觸自己的案件。"""
    guild, actor = _guild_and_actor(interaction)
    require_discipline_or_owner(interaction)
    result = repo.get_report_for_reviewer(
        guild_id=guild.id, report_id=report_id,
        is_owner=is_guild_owner(interaction),
        is_discipline=is_discipline_staff(interaction),
    )
    if str(result["target_user_id"]) == str(actor.id) and not is_guild_owner(interaction):
        raise ReportPermissionError("被舉報者不能處理自己的案件。")
    return result


async def list_reviewable_reports(interaction: discord.Interaction, *, limit: int = 25) -> list[dict]:
    guild, actor = _guild_and_actor(interaction)
    require_discipline_or_owner(interaction)
    rows = repo.list_pending_reports(
        guild_id=guild.id, is_owner=is_guild_owner(interaction),
        is_discipline=is_discipline_staff(interaction), limit=limit,
    )
    if not is_guild_owner(interaction):
        rows = [row for row in rows if str(row["target_user_id"]) != str(actor.id)]
    return rows


def get_reporter_identity_for_owner(interaction: discord.Interaction, report_id: int) -> str:
    """唯一允許取得舉報人 ID 的公開服務入口。"""
    guild, _ = _guild_and_actor(interaction)
    require_guild_owner(interaction)
    return repo.get_reporter_for_owner(
        guild_id=guild.id, report_id=report_id, is_guild_owner=True,
    )


def require_report_owner(interaction: discord.Interaction, report_id: int) -> None:
    """只有原舉報人能操作「我的案件」；管理員不可冒充舉報人。"""
    guild, actor = _guild_and_actor(interaction)
    # 只讀權限確認，不回傳舉報人身分；資料庫不應向呼叫端洩漏秘密欄位。
    with get_connection() as conn:
        exists = conn.execute(
            """SELECT 1 FROM rp_reports r
               JOIN rp_reporter_secrets s ON s.report_id=r.id
               WHERE r.guild_id=? AND r.id=? AND s.reporter_user_id=?""",
            (str(guild.id), int(report_id), str(actor.id)),
        ).fetchone()
    if exists is None:
        raise ReportPermissionError("你無權操作這筆舉報案件。")


def private_channel_owner_overwrite(guild: discord.Guild) -> dict:
    """提供給所有私人頻道建立模組使用的 owner 權限覆寫。

    Discord 伺服器擁有者本身有最高權限，仍保留明確 allow。
    若群主成員物件不可用，應先 fetch 後再建立私人頻道。
    """
    owner = guild.owner or guild.get_member(guild.owner_id)
    if owner is None:
        raise RuntimeError("無法取得群主成員資料，請先取得成員後重試。")
    return {owner: discord.PermissionOverwrite(
        view_channel=True, read_message_history=True,
        send_messages=True, manage_channels=True, manage_messages=True,
    )}


async def ensure_owner_channel_access(channel: discord.abc.GuildChannel, *,
                                      reason: str = "保留群主私人頻道存取權") -> bool:
    """補上私人頻道的群主覆寫；回傳 True 代表有變更。

    由私人頻道建立流程在 create_*_channel 後呼叫。
    不靜默吞掉 Forbidden：權限不足時應明確失敗與通知群主。
    """
    guild = channel.guild
    owner = guild.owner or guild.get_member(guild.owner_id)
    if owner is None:
        owner = await guild.fetch_member(guild.owner_id)
    current = channel.overwrites_for(owner)
    wanted = discord.PermissionOverwrite.from_pair(*current.pair())
    changed = False
    for flag in ("view_channel", "read_message_history", "send_messages",
                 "manage_channels", "manage_messages"):
        if getattr(wanted, flag) is not True:
            setattr(wanted, flag, True)
            changed = True
    if changed:
        await channel.set_permissions(owner, overwrite=wanted, reason=reason)
    return changed


# 本檔是服務層，不應提供 setup(bot)，避免重複註冊指令。
