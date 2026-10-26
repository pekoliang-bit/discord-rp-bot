
"""Discord RP 共用權限系統。

提供：
- Actor：Discord 操作者資料
- PermissionDenied：權限不足錯誤
- InvalidState：不符合操作條件錯誤
- require_admin：管理員權限檢查
- require_owner：角色所有者檢查

此模組不會註冊 Discord 指令。
"""

from __future__ import annotations

from dataclasses import dataclass

import discord


class PermissionDenied(Exception):
    """操作者沒有執行指定操作的權限。"""
    pass


class InvalidState(Exception):
    """資料狀態不符合操作條件。"""
    pass


@dataclass(frozen=True)
class Actor:
    """Discord 指令操作者。"""

    guild_id: str
    user_id: str
    is_admin: bool = False

    @classmethod
    def from_interaction(
        cls,
        interaction: discord.Interaction,
    ) -> "Actor":
        """從 Discord 互動建立操作者資料。"""

        if interaction.guild_id is None:
            raise InvalidState(
                "此功能只能在 Discord 伺服器內使用。"
            )

        if interaction.user is None:
            raise PermissionDenied(
                "無法識別操作者。"
            )

        permissions = interaction.permissions

        is_admin = (
            permissions.administrator
            or permissions.manage_guild
        )

        return cls(
            guild_id=str(interaction.guild_id),
            user_id=str(interaction.user.id),
            is_admin=bool(is_admin),
        )


def require_admin(actor: Actor) -> None:
    """確認操作者擁有伺服器管理權限。"""

    if not actor.is_admin:
        raise PermissionDenied(
            "此操作僅限伺服器管理員使用。"
        )


def require_owner(
    actor: Actor,
    owner_id: str | int,
    *,
    allow_admin: bool = True,
) -> None:
    """確認操作者是角色擁有者或獲允許的管理員。"""

    if str(owner_id) == actor.user_id:
        return

    if allow_admin and actor.is_admin:
        return

    raise PermissionDenied(
        "你沒有操作這名角色的權限。"
    )


def require_guild(
    actor: Actor,
    guild_id: str | int,
) -> None:
    """防止跨 Discord 伺服器操作。"""

    if actor.guild_id != str(guild_id):
        raise PermissionDenied(
            "不能操作其他 Discord 伺服器的資料。"
        )

