"""傳聞調查與證據的資料層（第二階段，尚未連接 Discord 操作面板）。

此模組只管理案件與證據，不直接扣除金幣、增加屬性或操控報警進度。
所有入口都要使用角色持有人權限；付款調查須待金流交易整合後啟用。
"""

from __future__ import annotations

import secrets
import time

from core.database import get_connection, transaction
from modules.pr_rumor import initialize as initialize_rumors

EVIDENCE_LEVELS = ("低", "中", "高", "決定性")
EVIDENCE_TYPES = ("目擊", "對話", "照片", "影音", "文件", "交易", "其他")
MAX_PROGRESS = 100


def _case(db, guild_id: str, rumor_id: int):
    row = db.execute(
        "SELECT * FROM rp_company_rumors WHERE guild_id=? AND id=?",
        (guild_id, rumor_id),
    ).fetchone()
    if row is None:
        raise ValueError("查無該傳聞案件。")
    return row


def _owned_active_character(db, guild_id: str, actor_id: str, character_id: int):
    row = db.execute(
        "SELECT id,owner_id,status FROM characters WHERE guild_id=? AND id=?",
        (guild_id, character_id),
    ).fetchone()
    if row is None or row["status"] != "active":
        raise ValueError("角色不存在或尚未啟用。")
    if str(row["owner_id"]) != actor_id:
        raise PermissionError("只能操作自己持有的角色。")
    return row


def register_evidence(
    guild_id, actor_id, rumor_id, holder_character_id,
    evidence_type, credibility="低", *, authentic=True,
):
    """登記證據。偽造證據會被明確標記；本函式不判定真偽。"""
    initialize_rumors()
    gid, uid = str(guild_id), str(actor_id)
    rid, cid = int(rumor_id), int(holder_character_id)
    evidence_type, credibility = str(evidence_type), str(credibility)
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError("不支援的證據類型。")
    if credibility not in EVIDENCE_LEVELS:
        raise ValueError("不支援的可信度等級。")
    with transaction() as db:
        _case(db, gid, rid)
        _owned_active_character(db, gid, uid, cid)
        cur = db.execute(
            """INSERT INTO rp_rumor_evidence
               (guild_id,rumor_id,holder_character_id,evidence_type,credibility,authentic,disclosed,created_at)
               VALUES (?,?,?,?,?,?,0,?)""",
            (gid, rid, cid, evidence_type, credibility, int(bool(authentic)), int(time.time())),
        )
        return cur.lastrowid


def list_evidence(guild_id, actor_id, holder_character_id, rumor_id=None):
    """只列出角色自己的證據；不洩露其他人的未公開資料。"""
    initialize_rumors()
    gid, uid, cid = str(guild_id), str(actor_id), int(holder_character_id)
    with get_connection() as db:
        _owned_active_character(db, gid, uid, cid)
        sql = "SELECT * FROM rp_rumor_evidence WHERE guild_id=? AND holder_character_id=?"
        params = [gid, cid]
        if rumor_id is not None:
            sql += " AND rumor_id=?"
            params.append(int(rumor_id))
        sql += " ORDER BY id DESC LIMIT 100"
        return [dict(row) for row in db.execute(sql, params).fetchall()]


def disclose_evidence(guild_id, actor_id, holder_character_id, evidence_id):
    """公開後不可自行撤回；公開資料可供後續網路與報警功能引用。"""
    initialize_rumors()
    gid, uid, cid, eid = str(guild_id), str(actor_id), int(holder_character_id), int(evidence_id)
    with transaction() as db:
        _owned_active_character(db, gid, uid, cid)
        row = db.execute(
            "SELECT id FROM rp_rumor_evidence WHERE guild_id=? AND id=? AND holder_character_id=?",
            (gid, eid, cid),
        ).fetchone()
        if row is None:
            raise ValueError("找不到此角色持有的證據。")
        db.execute("UPDATE rp_rumor_evidence SET disclosed=1 WHERE guild_id=? AND id=?", (gid, eid))


def transfer_evidence(guild_id, actor_id, from_character_id, to_character_id, evidence_id):
    """證據轉交必須由持有人發起；收件者只能是已啟用角色。"""
    initialize_rumors()
    gid, uid = str(guild_id), str(actor_id)
    origin, target, eid = int(from_character_id), int(to_character_id), int(evidence_id)
    if origin == target:
        raise ValueError("不可將證據轉交給同一角色。")
    with transaction() as db:
        _owned_active_character(db, gid, uid, origin)
        receiver = db.execute(
            "SELECT id FROM characters WHERE guild_id=? AND id=? AND status='active'", (gid, target)
        ).fetchone()
        if receiver is None:
            raise ValueError("收件角色不存在或尚未啟用。")
        cur = db.execute(
            """UPDATE rp_rumor_evidence SET holder_character_id=?
               WHERE guild_id=? AND id=? AND holder_character_id=?""", (target, gid, eid, origin)
        )
        if cur.rowcount != 1:
            raise ValueError("此角色並未持有指定證據。")


def investigation_status(guild_id, actor_id, rumor_id, character_id):
    initialize_rumors()
    gid, uid, rid, cid = str(guild_id), str(actor_id), int(rumor_id), int(character_id)
    with get_connection() as db:
        _owned_active_character(db, gid, uid, cid)
        _case(db, gid, rid)
        row = db.execute(
            """SELECT progress,attempts FROM rp_rumor_investigations
               WHERE guild_id=? AND rumor_id=? AND character_id=?""", (gid, rid, cid)
        ).fetchone()
        return dict(row) if row else {"progress": 0, "attempts": 0}


def attempt_basic_investigation(guild_id, actor_id, rumor_id, character_id, *,
                                ability_bonus=0, defense=0, roll=None):
    """免費基礎調查一次。能力、防備由可信任的遊戲邏輯傳入。

    付費加成不在此處受理，以防止沒有金流交易卻獲得加成。
    每位角色獨立累積進度；成功時增加 10，失敗時增加 2。
    `roll` 僅供測試注入，實際執行由系統產生 1～100 的亂數。
    """
    initialize_rumors()
    gid, uid, rid, cid = str(guild_id), str(actor_id), int(rumor_id), int(character_id)
    ability, protection = int(ability_bonus), int(defense)
    if not (0 <= ability <= 50 and 0 <= protection <= 85):
        raise ValueError("調查能力加成或防備數值超出允許範圍。")
    chance = max(5, min(90, 40 + ability - protection))
    number = secrets.randbelow(100) + 1 if roll is None else int(roll)
    if not 1 <= number <= 100:
        raise ValueError("判定數值必須介於 1～100。")
    with transaction() as db:
        _owned_active_character(db, gid, uid, cid)
        case = _case(db, gid, rid)
        if case["status"] != "active":
            raise ValueError("傳聞已結案；後續調查須由獨立案件流程處理。")
        previous = db.execute(
            """SELECT progress,attempts FROM rp_rumor_investigations
               WHERE guild_id=? AND rumor_id=? AND character_id=?""", (gid, rid, cid)
        ).fetchone()
        previous_progress = int(previous["progress"]) if previous else 0
        if previous_progress >= MAX_PROGRESS:
            raise ValueError("該角色已完成本案調查。")
        success = number <= chance
        new_progress = min(MAX_PROGRESS, previous_progress + (10 if success else 2))
        db.execute(
            """INSERT INTO rp_rumor_investigations(guild_id,rumor_id,character_id,progress,attempts)
               VALUES (?,?,?,?,1) ON CONFLICT(guild_id,rumor_id,character_id)
               DO UPDATE SET progress=excluded.progress,attempts=attempts+1""",
            (gid, rid, cid, new_progress),
        )
        db.execute(
            """INSERT INTO rp_rumor_actions
               (guild_id,rumor_id,character_id,actor_id,action_type,resource_type,resource_amount,result_json,created_at)
               VALUES (?,?,?,?,?,'free',0,?,?)""",
            (gid, rid, cid, uid, "basic_investigation",
             '{"success":' + ('true' if success else 'false') + ',"chance":' + str(chance) + '}',
             int(time.time())),
        )
    return {"success": success, "chance": chance, "progress": new_progress,
            "completed": new_progress == MAX_PROGRESS}
