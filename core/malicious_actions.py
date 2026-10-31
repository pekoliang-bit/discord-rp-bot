"""角色對角色的惡意行動核心（不含 Discord 指令）。

使用者必須先通過私人論壇／機器人帖子權限檢查。
扣款由可信的 debit 回呼在同一個 SQLite 交易內完成。
不會因為操作成功就宣稱造謠內容為真。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from core.database import transaction

LEVEL_COST = {"light": 1000, "normal": 5000, "medium": 20000,
              "heavy": 50000, "special": 200000}
DIFFICULTY = {"light": 0, "normal": 5, "medium": 12,
              "heavy": 22, "special": 35}
TYPES = {"rumor": "其他", "business": "商業", "work": "工作",
         "relationship": "人際", "intelligence": "其他", "special": "其他"}
DAILY_LIMIT = 3
TARGET_COOLDOWN = 86400


@dataclass(frozen=True)
class MaliciousRequest:
    guild_id: str
    actor_user_id: str
    actor_character_id: int
    target_character_id: int
    action_type: str
    level: str = "normal"
    anonymous: bool = True
    title: str = ""
    description: str = ""
    # 已核實的遊戲能力／防備調整，並非從玩家文字輸入取得。
    ability_bonus: int = 0
    defense_penalty: int = 0


@dataclass(frozen=True)
class MaliciousResult:
    action_id: int
    status: str
    succeeded: bool
    exposed: bool
    cost: int
    success_chance: int
    rumor_id: int | None = None


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_malicious_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        actor_user_id TEXT NOT NULL,
        actor_character_id INTEGER NOT NULL,
        target_character_id INTEGER NOT NULL,
        action_type TEXT NOT NULL,
        level TEXT NOT NULL,
        anonymous INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        cost INTEGER NOT NULL,
        success_chance INTEGER NOT NULL,
        succeeded INTEGER NOT NULL,
        exposed INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'resolved',
        rumor_id INTEGER,
        created_at INTEGER NOT NULL,
        UNIQUE(guild_id,id)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_malicious_actor_daily
        ON rp_malicious_actions(guild_id,actor_character_id,created_at)""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_malicious_target_cooldown
        ON rp_malicious_actions(guild_id,actor_character_id,target_character_id,created_at)""")


def _check_request(r: MaliciousRequest, owner_override: bool):
    if not str(r.guild_id).strip() or not str(r.actor_user_id).strip():
        raise ValueError("伺服器與操作人不可空白")
    if any(type(x) is not int or x <= 0 for x in
           (r.actor_character_id, r.target_character_id)):
        raise ValueError("角色編號必須是正整數")
    if r.actor_character_id == r.target_character_id:
        raise ValueError("不能針對同一角色自己發動")
    if r.action_type not in TYPES or r.level not in LEVEL_COST:
        raise ValueError("未知惡意行動或強度")
    if len(r.title.strip()) not in range(1, 81):
        raise ValueError("事件標題須為 1～80 字")
    if len(r.description.strip()) not in range(1, 501):
        raise ValueError("事件內容須為 1～500 字")
    for x in (r.ability_bonus, r.defense_penalty):
        if type(x) is not int or not -30 <= x <= 30:
            raise ValueError("能力與防備修正必須在 -30～30")
    if r.level == "special" and not owner_override:
        raise PermissionError("特殊惡意行動必須通過官方審核")


def resolve_action(
    request: MaliciousRequest,
    *,
    debit: Callable,  # debit(db, guild_id, actor_character_id, amount, unique_key)
    forum_authorized: bool = False,
    trusted_stats: bool = False,
    official_approved: bool = False,
    owner_override: bool = False,
    now: int | None = None,
    rng=None,
) -> MaliciousResult:
    """一次性判定並扣款；同筆交易內寫入惡意紀錄。

    debit 必須以 unique_key 執行冪等帳本扣款，不可另開交易。
    上游必須先驗證自己的角色論壇、指定機器人帖子及角色持有人。
    """
    if not forum_authorized or not trusted_stats:
        raise PermissionError("未通過私人論壇／角色或能力資料驗證")
    if not callable(debit):
        raise TypeError("缺少可信的金幣帳本扣款函式")
    _check_request(request, owner_override or official_approved)
    clock = int(time.time()) if now is None else now
    if type(clock) is not int or clock <= 0:
        raise ValueError("時間無效")
    generator = rng or secrets.SystemRandom()
    cost = LEVEL_COST[request.level]
    chance = max(5, min(90, 40 + request.ability_bonus
                        + min(20, (cost // 10000) * 2)
                        - request.defense_penalty - DIFFICULTY[request.level]))
    with transaction() as db:
        initialize(db)
        for cid in (request.actor_character_id, request.target_character_id):
            character = db.execute("""SELECT owner_id,status FROM characters
                WHERE guild_id=? AND id=?""", (str(request.guild_id), cid)).fetchone()
            if character is None or character["status"] != "active":
                raise ValueError("發起及目標角色必須已啟用")
            if cid == request.actor_character_id and not owner_override:
                if str(character["owner_id"]) != str(request.actor_user_id):
                    raise PermissionError("你不是發起角色的持有人")
        if not owner_override:
            daily = db.execute("""SELECT COUNT(*) FROM rp_malicious_actions
                WHERE guild_id=? AND actor_character_id=? AND created_at>=?""",
                (str(request.guild_id), request.actor_character_id, clock-86400)).fetchone()[0]
            if daily >= DAILY_LIMIT:
                raise ValueError("已達每日惡意行動上限")
            recent = db.execute("""SELECT 1 FROM rp_malicious_actions
                WHERE guild_id=? AND actor_character_id=? AND target_character_id=?
                AND created_at>=? LIMIT 1""",
                (str(request.guild_id), request.actor_character_id,
                 request.target_character_id, clock-TARGET_COOLDOWN)).fetchone()
            if recent:
                raise ValueError("同一目標尚在冷卻期間")
        succeeded = generator.random() < chance / 100
        exposed = generator.random() < (0.1 if request.anonymous else 0.3)
        cursor = db.execute("""INSERT INTO rp_malicious_actions
            (guild_id,actor_user_id,actor_character_id,target_character_id,
             action_type,level,anonymous,title,description,cost,success_chance,
             succeeded,exposed,created_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(request.guild_id),str(request.actor_user_id),request.actor_character_id,
             request.target_character_id,request.action_type,request.level,
             int(request.anonymous),request.title.strip(),request.description.strip(),
             cost,chance,int(succeeded),int(exposed),clock))
        action_id = int(cursor.lastrowid)
        # 發生任何失敗時整個 SQLite 交易回滾，含已執行的帳本扣款。
        debit(db, str(request.guild_id), request.actor_character_id,
              cost, f"malicious:{request.guild_id}:{action_id}")
        return MaliciousResult(action_id, "resolved", succeeded, exposed, cost, chance)


def read_action(guild_id: str | int, action_id: int) -> dict:
    if type(action_id) is not int or action_id <= 0:
        raise ValueError("無效行動編號")
    from core.database import get_connection
    with get_connection() as db:
        row = db.execute("SELECT * FROM rp_malicious_actions WHERE guild_id=? AND id=?",
                         (str(guild_id), action_id)).fetchone()
        if row is None:
            raise LookupError("惡意行動不存在")
        return dict(row)  # 只允許授權管理端使用；含匿名發起者資料。
