"""角色查看的傳聞參與授權：共用傳聞系統既有資料表與檢查邏輯。"""
from __future__ import annotations

from core.database import get_connection
from modules import pr_rumor


def read_authorization(guild_id, owner_id, character_id):
    """僅向角色本人返回總授權、分類設定與實際有效狀態。"""
    pr_rumor.initialize()
    gid, uid, cid = str(guild_id), str(owner_id), int(character_id)
    with get_connection() as db:
        character = db.execute(
            "SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?", (gid, cid)
        ).fetchone()
        if character is None or str(character["owner_id"]) != uid or character["status"] != "active":
            raise PermissionError("只有已啟用角色的持有人可以管理傳聞授權。")
        general = db.execute(
            "SELECT enabled FROM rp_rumor_optout WHERE guild_id=? AND character_id=?", (gid, cid)
        ).fetchone()
        enabled = bool(general["enabled"]) if general is not None else True
        overrides = {
            row["category"]: bool(row["enabled"])
            for row in db.execute(
                "SELECT category,enabled FROM rp_rumor_category_consent WHERE guild_id=? AND character_id=?", (gid, cid)
            )
        }
        return {
            "enabled": enabled,
            "categories": {c: overrides.get(c, True) for c in pr_rumor.RUMOR_CATEGORIES},
            "effective": {c: pr_rumor.rumor_allowed(db, gid, cid, c) for c in pr_rumor.RUMOR_CATEGORIES},
        }


def set_authorization(guild_id, owner_id, character_id, *, category=None, enabled: bool):
    if not isinstance(enabled, bool):
        raise ValueError("傳聞授權必須明確開啟或關閉。")
    # 寫入前再次驗證角色仍為本人已啟用；下層亦會再次驗證持有人。
    read_authorization(guild_id, owner_id, character_id)
    if category is None:
        pr_rumor.set_consent(guild_id, owner_id, character_id, enabled)
    else:
        if category not in pr_rumor.RUMOR_CATEGORIES:
            raise ValueError("未知傳聞分類。")
        pr_rumor.set_category_consent(guild_id, owner_id, character_id, category, enabled)
    return read_authorization(guild_id, owner_id, character_id)
