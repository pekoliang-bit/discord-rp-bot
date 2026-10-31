"""惡意行動與既有傳聞案件的原子橋接。

只對已完成的惡意行動建立傳聞；不讓未證實造謠變成已證實事實。
不負責公開 Discord 訊息，也不重複扣除惡意行動的費用。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.database import transaction
from core.malicious_actions import initialize as initialize_malicious, TYPES


@dataclass(frozen=True)
class RumorCreationSettings:
    business_id: str
    tier: int
    complexity: int
    complexity_version: str
    rules: object  # 實際必須是 core.rumor_reward_planner.RuleConfig
    settle_seconds: int = 72 * 3600


def create_rumor_for_action(
    guild_id: str | int,
    action_id: int,
    *,
    settings: RumorCreationSettings,
    case_authorized: bool = False,
    publicly_visible: bool = True,
) -> int | None:
    """成功行動建立傳聞；失敗且暴露者可由官方另開反噬案。

    本函式要求官方已核定的案件快照設定。若尚未核定或未取得
    傳聞授權，整筆交易回滾，不會只生成半筆傳聞資料。

    回傳 None：行動未成功，不建立針對目標的傳聞。
    回傳 int：新建或既有的 rumor_id（重試時不重複建案）。
    """
    if not case_authorized:
        raise PermissionError("尚未取得傳聞案件建立權限")
    if type(action_id) is not int or action_id <= 0:
        raise ValueError("action_id 必須為正整數")
    if not isinstance(settings, RumorCreationSettings):
        raise TypeError("settings 格式錯誤")
    # 延後 import，避免純惡意核心載入時啟動其他模組。
    from core.rumor_reward_planner import RuleConfig
    from core.rumor_case_creation import insert_case_and_snapshot
    from modules.pr_rumor import rumor_allowed

    if not isinstance(settings.rules, RuleConfig):
        raise TypeError("必須使用經官方核准的 RuleConfig")
    if not settings.business_id.strip():
        raise ValueError("缺少正式傳聞業務來源 ID")

    gid = str(guild_id)
    with transaction() as db:
        initialize_malicious(db)
        row = db.execute("""SELECT * FROM rp_malicious_actions
            WHERE guild_id=? AND id=?""", (gid, action_id)).fetchone()
        if row is None:
            raise LookupError("找不到惡意行動")
        if row["rumor_id"] is not None:
            return int(row["rumor_id"])
        if row["status"] != "resolved":
            raise ValueError("惡意行動尚未結算")
        if not row["succeeded"]:
            return None
        category = TYPES[row["action_type"]]
        if not rumor_allowed(db, gid, int(row["target_character_id"]), category):
            raise PermissionError("目標角色已拒絕此類傳聞")
        source = "造謠" if row["action_type"] == "rumor" else "玩家"
        rumor_id = insert_case_and_snapshot(
            db,
            guild_id=gid,
            business_id=settings.business_id,
            character_id=int(row["target_character_id"]),
            source_type=source,
            creator_id=str(row["actor_user_id"]),
            title=str(row["title"]),
            description=str(row["description"]),
            category=category,
            settle_seconds=settings.settle_seconds,
            tier=settings.tier,
            complexity=settings.complexity,
            complexity_version=settings.complexity_version,
            rules=settings.rules,
            trusted=True,
        )
        # 非公開案件不會自行觸發網友評論。
        if not publicly_visible:
            db.execute("""UPDATE rp_rumor_case_meta SET visibility='private'
                WHERE guild_id=? AND rumor_id=?""", (gid, rumor_id))
        result = db.execute("""UPDATE rp_malicious_actions SET rumor_id=?
            WHERE guild_id=? AND id=? AND rumor_id IS NULL""",
            (rumor_id, gid, action_id))
        if result.rowcount != 1:
            raise RuntimeError("傳聞關聯並行寫入失敗")
        return rumor_id
