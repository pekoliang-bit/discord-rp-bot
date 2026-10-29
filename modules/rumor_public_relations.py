"""傳聞公關策略、效益遞減與五級結算（後端階段版）。

目前只處理策略紀錄及結算評級；正式金幣／行動值消耗與屬性獎懲
須經同一筆資料庫交易整合後開放，避免玩家無成本刷取獎勵。
"""

from __future__ import annotations

import json
import time

from core.database import get_connection, transaction
from modules.company_business import has_permission
from modules.pr_rumor import initialize as initialize_rumors

STRATEGIES = {
    "公開澄清": 18,
    "提出證據": 25,
    "接受訪問": 12,
    "公開道歉": 14,
    "法律聲明": 15,
    "沉默觀察": 4,
}
REPEAT_MULTIPLIERS = (100, 75, 50, 25, 10)
MAX_FREE_ACTION_SCORE = 200
RANKS = {
    "S": "輿論逆轉",
    "A": "有效澄清",
    "B": "反應平淡",
    "C": "輿論反噬",
    "D": "全面失控",
}


def initialize():
    initialize_rumors()
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_pr_scores (
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            action_score INTEGER NOT NULL DEFAULT 0,
            action_count INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,rumor_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_pr_results (
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            rank TEXT NOT NULL, label TEXT NOT NULL,
            score INTEGER NOT NULL, settled_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,rumor_id)
        )""")
        db.commit()


def _case(db, gid, rid):
    row = db.execute("""SELECT * FROM rp_company_rumors
        WHERE guild_id=? AND id=?""", (gid, rid)).fetchone()
    if not row:
        raise ValueError("找不到該傳聞案件。")
    return row


def _strategy_uses(db, gid, rid, cid, strategy):
    row = db.execute("""SELECT COUNT(*) AS count FROM rp_rumor_actions
        WHERE guild_id=? AND rumor_id=? AND character_id=? AND action_type=?""",
        (gid, rid, cid, f"pr:{strategy}")).fetchone()
    return int(row["count"])


def preview_strategy(guild_id, rumor_id, actor_character_id, strategy, ability_bonus=0):
    """回傳下一次同策略的預估效果；預覽不造成任何變更。"""
    initialize()
    gid, rid, cid = str(guild_id), int(rumor_id), int(actor_character_id)
    if strategy not in STRATEGIES:
        raise ValueError("不支援的公關策略。")
    ability = int(ability_bonus)
    if not 0 <= ability <= 30:
        raise ValueError("公關能力加成須介於 0 至 30。")
    with get_connection() as db:
        _case(db, gid, rid)
        uses = _strategy_uses(db, gid, rid, cid, strategy)
    multiplier = REPEAT_MULTIPLIERS[min(uses, len(REPEAT_MULTIPLIERS) - 1)]
    points = ((STRATEGIES[strategy] + ability) * multiplier) // 100
    return {"strategy": strategy, "repeat_count": uses, "multiplier_percent": multiplier,
            "estimated_score": points}


def record_free_pr_action(guild_id, actor_id, rumor_id, actor_character_id,
                          strategy, *, ability_bonus=0):
    """限公司公關授權者執行。付費策略不得使用這個免費入口。

    不設定冷卻或行動次數上限；免費策略的案件總分最多 200，
    避免無成本無限刷分。正式版的資源型策略需另接交易式扣款服務。
    """
    initialize()
    gid, uid, rid, cid = str(guild_id), str(actor_id), int(rumor_id), int(actor_character_id)
    if strategy not in STRATEGIES:
        raise ValueError("不支援的公關策略。")
    ability = int(ability_bonus)
    if not 0 <= ability <= 30:
        raise ValueError("公關能力加成須介於 0 至 30。")
    with transaction() as db:
        case = _case(db, gid, rid)
        if case["status"] != "active" or int(time.time()) >= case["settle_at"]:
            raise ValueError("案件已結算或到達結算時間，不可繼續採取公關行動。")
        character = db.execute("""SELECT id,owner_id,status FROM characters
            WHERE guild_id=? AND id=?""", (gid, cid)).fetchone()
        if not character or character["status"] != "active" or str(character["owner_id"]) != uid:
            raise PermissionError("只能使用自己持有的已啟用角色。")
        if not has_permission(db, gid, case["business_id"], uid, "public_relations"):
            raise PermissionError("必須擁有該公司的公關管理權限。")
        uses = _strategy_uses(db, gid, rid, cid, strategy)
        multiplier = REPEAT_MULTIPLIERS[min(uses, len(REPEAT_MULTIPLIERS) - 1)]
        row = db.execute("""SELECT action_score FROM rp_rumor_pr_scores
            WHERE guild_id=? AND rumor_id=?""", (gid, rid)).fetchone()
        current_score = int(row["action_score"]) if row else 0
        points = min(MAX_FREE_ACTION_SCORE - current_score,
                     ((STRATEGIES[strategy] + ability) * multiplier) // 100)
        details = {"points": points, "multiplier_percent": multiplier, "ability_bonus": ability}
        db.execute("""INSERT INTO rp_rumor_actions
            (guild_id,rumor_id,character_id,actor_id,action_type,
             resource_type,resource_amount,result_json,created_at)
             VALUES (?,?,?,?,?,'free',0,?,?)""",
            (gid, rid, cid, uid, f"pr:{strategy}", json.dumps(details, ensure_ascii=False), int(time.time())))
        db.execute("""INSERT INTO rp_rumor_pr_scores
            (guild_id,rumor_id,action_score,action_count,updated_at)
            VALUES (?,?,?,1,?)
            ON CONFLICT(guild_id,rumor_id) DO UPDATE SET
            action_score=action_score+excluded.action_score,
            action_count=action_count+1,updated_at=excluded.updated_at""",
            (gid, rid, points, int(time.time())))
    return details


def rank_for_score(score):
    """此區間為暫定平衡參數；全套測試後可集中調整。"""
    score = int(score)
    if score >= 150:
        return "S"
    if score >= 90:
        return "A"
    if score >= 35:
        return "B"
    if score >= 10:
        return "C"
    return "D"


def preview_settlement(guild_id, rumor_id):
    initialize()
    gid, rid = str(guild_id), int(rumor_id)
    with get_connection() as db:
        case = _case(db, gid, rid)
        row = db.execute("""SELECT action_score,action_count FROM rp_rumor_pr_scores
            WHERE guild_id=? AND rumor_id=?""", (gid, rid)).fetchone()
    score = int(row["action_score"]) if row else 0
    rank = rank_for_score(score)
    return {"rumor_id": rid, "case_status": case["status"], "score": score,
            "action_count": int(row["action_count"]) if row else 0,
            "rank": rank, "label": RANKS[rank]}


def settle_one_case(guild_id, rumor_id, *, now=None):
    """寫入一次性評級，不發放未經審核的獎勵或影響指數。

    在正式整合時，原 `pr_rumor.settle_due` 需改為呼叫統一結算入口，
    否則兩個計時流程可能競爭。此函式會拒絕重複結算。
    """
    initialize()
    gid, rid = str(guild_id), int(rumor_id)
    timestamp = int(time.time()) if now is None else int(now)
    with transaction() as db:
        case = _case(db, gid, rid)
        if case["status"] != "active":
            raise ValueError("案件不在可結算狀態。")
        if timestamp < case["settle_at"]:
            raise ValueError("尚未到達自然結算時間。")
        row = db.execute("""SELECT action_score FROM rp_rumor_pr_scores
            WHERE guild_id=? AND rumor_id=?""", (gid, rid)).fetchone()
        score = int(row["action_score"]) if row else 0
        rank = rank_for_score(score)
        db.execute("""INSERT INTO rp_rumor_pr_results
            (guild_id,rumor_id,rank,label,score,settled_at)
            VALUES (?,?,?,?,?,?)""", (gid, rid, rank, RANKS[rank], score, timestamp))
        updated = db.execute("""UPDATE rp_company_rumors
            SET status='settled',settled_at=?
            WHERE guild_id=? AND id=? AND status='active'""", (timestamp, gid, rid))
        if updated.rowcount != 1:
            raise ValueError("案件結算競爭，請重新讀取狀態。")
        db.execute("""INSERT INTO rp_rumor_settlement_ledger
            (guild_id,rumor_id,settlement_type,recipient_id,reward_json,created_at)
            VALUES (?,?,?,'system',?,?)""",
            (gid, rid, "pr_rank", json.dumps({"rank": rank, "score": score,
                                               "rewards_applied": False}, ensure_ascii=False), timestamp))
    return {"rumor_id": rid, "rank": rank, "label": RANKS[rank], "score": score}
