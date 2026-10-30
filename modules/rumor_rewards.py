"""傳聞系統｜公關數值獎懲橋接（開發版）。

安裝：modules/rumor_rewards.py
這是後端工具模組，不是 discord.py Cog，不需單獨 load_extension。

僅在上游已確認案件、角色、評級及結算權限後呼叫。
* 使用既有 core.stat_rewards.grant_stats，避免另起一套屬性表。
* 同一案件、角色、結算階段的獎懲使用固定 source_id 去重。
* 不能透過一般傳聞增加或減少「影響指數」。
* 金幣與未實裝的「死忠粉」「黑粉」不在此模組發放。
* 本模組不負責案件狀態、72 小時排程或金流交易，整合前勿正式部署。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.database import get_connection
from core.stat_rewards import ATTRIBUTES, METRICS, grant_stats

GRADES = frozenset({"S", "A", "B", "C", "D"})
# 僅使用既有受支援數值；未正式接通的特殊粉絲數值不可混入。
ALLOWED = (ATTRIBUTES | METRICS) - {"影響指數", "CP好感度"}


@dataclass(frozen=True)
class SettlementPreview:
    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    changes: dict[str, int]
    source_id: str


def _validate_changes(changes: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("獎懲內容必須是非空的數值字典。")
    cleaned: dict[str, int] = {}
    for key, value in changes.items():
        name = str(key).strip()
        if name not in ALLOWED:
            raise ValueError(f"傳聞獎懲暫不支援：{name}")
        if type(value) is not int or value == 0:
            raise ValueError(f"{name} 的變動必須是非零整數。")
        if value < 0:
            raise ValueError("舊版統一數值核心尚不支援安全扣減；負面獎懲須待核心修正後啟用。")
        if abs(value) > 1_000_000:
            raise ValueError("單次數值變動超出暫定安全界限。")
        cleaned[name] = value
    return cleaned


def preview_settlement(guild_id, rumor_id, character_id, grade, changes,
                       *, phase="final") -> SettlementPreview:
    """僅驗證輸入與現有案件，並不發獎。

    `phase` 必須由可信任結算器給予固定值，例如 final、police_1。
    勿讓玩家自己控制階段或傳入 changes。
    """
    gid = str(guild_id).strip()
    rid, cid = int(rumor_id), int(character_id)
    level = str(grade).strip().upper()
    phase = str(phase).strip()
    if not gid or rid <= 0 or cid <= 0 or level not in GRADES:
        raise ValueError("伺服器、案件、角色或評級不合法。")
    if not phase or len(phase) > 40 or not all(c.isascii() and (c.isalnum() or c in "_-" ) for c in phase):
        raise ValueError("結算階段識別碼不合法。")
    normalized = _validate_changes(changes)
    with get_connection() as db:
        rumor = db.execute(
            "SELECT character_id FROM rp_company_rumors WHERE guild_id=? AND id=?",
            (gid, rid),
        ).fetchone()
        if rumor is None:
            raise ValueError("案件不存在，請先完成傳聞核心資料庫初始化。")
        if int(rumor["character_id"]) != cid:
            raise ValueError("這個角色不是該傳聞的主要當事人；關聯角色需另行授權與建立結算紀錄。")
    return SettlementPreview(gid, rid, cid, level, normalized, f"rumor:{rid}:{phase}")


def apply_settlement(preview: SettlementPreview, *, authorized: bool = False):
    """交由可信任的結算協調器呼叫；玩家指令不可直接暴露此函式。

    既有 grant_stats 會在單一資料庫交易內寫入數值與去重日誌。
    重試同內容會回傳 duplicate=True；不同內容重用相同事件 ID 會報錯。
    注意：此函式不會同時結案，因此正式整合時需由協調器處理狀態與重試。
    """
    if not authorized:
        raise PermissionError("只有可信任的系統結算器可以發放傳聞獎懲。")
    if not isinstance(preview, SettlementPreview):
        raise TypeError("必須先建立 SettlementPreview。")
    # 重新驗證，防止預覽後被替換的資料物件直接繞過驗證。
    verified = preview_settlement(
        preview.guild_id, preview.rumor_id, preview.character_id,
        preview.grade, preview.changes, phase=preview.source_id.split(":", 2)[-1],
    )
    if verified != preview:
        raise ValueError("結算內容與預覽不一致。")
    return grant_stats(
        verified.guild_id,
        verified.character_id,
        verified.changes,
        source="rumor",
        source_id=verified.source_id,
        reason=f"傳聞案件 #{verified.rumor_id} {verified.grade} 級獎懲",
        random=False,
        purchased=False,
    )
