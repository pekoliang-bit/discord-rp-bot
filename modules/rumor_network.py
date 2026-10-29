"""傳聞網路曝光、群眾線索、連鎖爆料（資料層）。

本模組不發送真實社群貼文、不購買真實流量、不扣遊戲金幣。
付費營銷號與熱搜競價要由完整的網路／金流模組負責執行。
"""

from __future__ import annotations

import hashlib
import json
import time

from core.database import get_connection, transaction
from modules.pr_rumor import initialize as initialize_rumors, rumor_allowed

EXPOSURE_MODES = ("公開爆料", "營銷號轉載", "媒體報導")
CLUE_CATEGORIES = ("目擊線索", "內容比對", "知情者爆料", "意外牽連")
CLUE_COOLDOWN = 6 * 3600


def initialize():
    initialize_rumors()
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_network_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            actor_id TEXT NOT NULL, actor_character_id INTEGER NOT NULL,
            mode TEXT NOT NULL, title TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(guild_id,rumor_id,actor_character_id,mode)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_crowd_clues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            actor_id TEXT NOT NULL, actor_character_id INTEGER NOT NULL,
            clue_type TEXT NOT NULL, description TEXT NOT NULL,
            verification TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_rumor_crowd_cooldown
            ON rp_rumor_crowd_clues(guild_id,rumor_id,actor_character_id,created_at)""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_network_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
            event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        db.commit()


def _owned_character(db, gid, uid, cid):
    row = db.execute("""SELECT id FROM characters
        WHERE guild_id=? AND id=? AND owner_id=? AND status='active'""",
        (gid, cid, uid)).fetchone()
    if row is None:
        raise PermissionError("只能使用自己已啟用的角色執行此操作。")


def _rumor(db, gid, rid):
    row = db.execute("""SELECT r.*,COALESCE(m.category,'其他') AS category
        FROM rp_company_rumors AS r LEFT JOIN rp_rumor_case_meta AS m
        ON m.guild_id=r.guild_id AND m.rumor_id=r.id
        WHERE r.guild_id=? AND r.id=?""", (gid, rid)).fetchone()
    if row is None:
        raise ValueError("找不到指定的傳聞案件。")
    return row


def publish_exposure(guild_id, actor_id, rumor_id, actor_character_id, mode, title):
    """在遊戲內標記曝光；相同角色相同類型不能重複灌流量。"""
    initialize()
    gid, uid, rid, cid = str(guild_id), str(actor_id), int(rumor_id), int(actor_character_id)
    mode, title = str(mode), str(title).strip()
    if mode not in EXPOSURE_MODES:
        raise ValueError("不支援的曝光方式。")
    if not 1 <= len(title) <= 80:
        raise ValueError("標題需介於 1 至 80 字。")
    if mode == "營銷號轉載":
        raise ValueError("營銷號操作須先完成帳號持有權與金流驗證。")
    with transaction() as db:
        _owned_character(db, gid, uid, cid)
        rumor = _rumor(db, gid, rid)
        if not rumor_allowed(db, gid, rumor["character_id"], rumor["category"]):
            raise PermissionError("當事角色已關閉此類傳聞參與授權。")
        try:
            cur = db.execute("""INSERT INTO rp_rumor_network_posts
                (guild_id,rumor_id,actor_id,actor_character_id,mode,title,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (gid, rid, uid, cid, mode, title, int(time.time())))
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError("這個角色已經使用過相同曝光方式。") from exc
            raise
        return cur.lastrowid


def submit_crowd_clue(guild_id, actor_id, rumor_id, actor_character_id, clue_type, description):
    """玩家提供待驗證線索，不能直接提高追查度或修改傳聞真假。"""
    initialize()
    gid, uid, rid, cid = str(guild_id), str(actor_id), int(rumor_id), int(actor_character_id)
    clue_type, description = str(clue_type), str(description).strip()
    if clue_type not in CLUE_CATEGORIES:
        raise ValueError("不支援的線索類型。")
    if not 5 <= len(description) <= 500:
        raise ValueError("線索內容需介於 5 至 500 字。")
    with transaction() as db:
        _owned_character(db, gid, uid, cid)
        rumor = _rumor(db, gid, rid)
        if not rumor_allowed(db, gid, rumor["character_id"], rumor["category"]):
            raise PermissionError("當事角色已關閉此類傳聞參與授權。")
        now = int(time.time())
        latest = db.execute("""SELECT created_at FROM rp_rumor_crowd_clues
            WHERE guild_id=? AND rumor_id=? AND actor_character_id=?
            ORDER BY created_at DESC,id DESC LIMIT 1""", (gid, rid, cid)).fetchone()
        if latest and now - latest["created_at"] < CLUE_COOLDOWN:
            raise ValueError("每位角色對同一事件每六小時只能提供一條線索。")
        cur = db.execute("""INSERT INTO rp_rumor_crowd_clues
            (guild_id,rumor_id,actor_id,actor_character_id,clue_type,description,created_at)
            VALUES (?,?,?,?,?,?,?)""", (gid, rid, uid, cid, clue_type, description, now))
        return cur.lastrowid


def list_public_exposure(guild_id, rumor_id):
    initialize()
    gid, rid = str(guild_id), int(rumor_id)
    with get_connection() as db:
        rumor = _rumor(db, gid, rid)
        if not rumor_allowed(db, gid, rumor["character_id"], rumor["category"]):
            return []
        return [dict(row) for row in db.execute("""SELECT id,rumor_id,mode,title,created_at
            FROM rp_rumor_network_posts WHERE guild_id=? AND rumor_id=?
            ORDER BY id DESC LIMIT 100""", (gid, rid)).fetchall()]


def _is_reachable(db, gid, start, destination):
    """防止 A→B→A 循環，避免無限連鎖觸發與重複結算。"""
    frontier, visited = [start], set()
    while frontier:
        current = frontier.pop()
        if current == destination:
            return True
        if current in visited:
            continue
        visited.add(current)
        frontier.extend(row["child_id"] for row in db.execute("""SELECT child_id
            FROM rp_rumor_case_links WHERE guild_id=? AND parent_id=?""", (gid, current)))
    return False


def link_chain_revelation(guild_id, parent_rumor_id, child_rumor_id, reason):
    """只連接已存在且授權有效的案件；由可信任的事件引擎呼叫。

    新的傳聞本身須先經正式的審核／生成流程，不允許玩家直接利用此函式造謠。
    層數不設固定上限，但禁止環與重複連結。
    """
    initialize()
    gid, parent, child = str(guild_id), int(parent_rumor_id), int(child_rumor_id)
    reason = str(reason).strip()
    if not reason or len(reason) > 200:
        raise ValueError("牽連原因必須介於 1 至 200 字。")
    if parent == child:
        raise ValueError("傳聞不能牽連自己。")
    with transaction() as db:
        original, related = _rumor(db, gid, parent), _rumor(db, gid, child)
        for rumor in (original, related):
            if not rumor_allowed(db, gid, rumor["character_id"], rumor["category"]):
                raise PermissionError("有角色未授權參與此類傳聞，無法建立連鎖爆料。")
        if _is_reachable(db, gid, child, parent):
            raise ValueError("此關聯會造成循環傳聞。")
        cur = db.execute("""INSERT OR IGNORE INTO rp_rumor_case_links
            (guild_id,parent_id,child_id,reason) VALUES (?,?,?,?)""",
            (gid, parent, child, reason))
        if cur.rowcount:
            db.execute("""INSERT INTO rp_rumor_network_events
                (guild_id,rumor_id,event_type,payload_json,created_at) VALUES (?,?,?,?,?)""",
                (gid, parent, "chain_revelation", json.dumps({"child": child, "reason": reason}, ensure_ascii=False),
                 int(time.time())))
        return bool(cur.rowcount)


def system_clue_candidate(guild_id, rumor_id, window=0):
    """產生穩定的劇情候選值；不自行核准爆料、不直接創造案件。"""
    digest = hashlib.sha256(f"{guild_id}:{int(rumor_id)}:{int(window)}:crowd".encode()).digest()
    return {"may_surface": digest[0] < 64,
            "category": CLUE_CATEGORIES[digest[1] % len(CLUE_CATEGORIES)]}
