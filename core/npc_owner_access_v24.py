"""NPC 角色歸屬及伺服器管理員授權 V2.4。

登記人不代表角色持有人；官方 NPC 只授權具 Administrator 權限
或伺服器群主的管理員操作。玩家 NPC 仍由指定玩家自行認領雙審。
"""
from __future__ import annotations


def is_server_admin(interaction) -> bool:
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    if guild is None or user is None:
        return False
    if int(user.id) == int(guild.owner_id):
        return True
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and getattr(perms, "administrator", False))


def member_is_admin(guild, member) -> bool:
    if member is None or getattr(member, "bot", False):
        return False
    if guild is None or getattr(member, "guild", None) is None:
        return False
    if int(member.guild.id) != int(guild.id):
        return False
    return int(member.id) == int(guild.owner_id) or bool(
        getattr(getattr(member, "guild_permissions", None), "administrator", False)
    )


def ensure_npc_owner(guild, member, kind: str) -> int:
    """驗證指定的實際持有人；不接受機器人、外服或不存在成員。"""
    if guild is None or member is None or getattr(member, "guild", None) is None:
        raise ValueError("請從本伺服器的成員選單指定角色持有人。")
    if int(member.guild.id) != int(guild.id) or getattr(member, "bot", False):
        raise PermissionError("角色持有人必須是本伺服器的真人成員。")
    if kind == "official_npc" and not member_is_admin(guild, member):
        raise PermissionError("官方 NPC 的主要持有人必須具備管理員權限。")
    if kind not in ("player_npc", "official_npc"):
        raise ValueError("NPC 類型不正確。")
    return int(member.id)


def can_operate_npc(actor, character) -> bool:
    """用於角色面板與需要正式角色授權的操作，不轉移 owner_id。"""
    kind = character["character_type"]
    if kind == "official_npc":
        return bool(getattr(actor, "is_discord_administrator", False))
    return str(character["owner_id"]) == str(actor.user_id)
