"""傳聞公關資源扣費核心（新增至 core/rumor_payment.py）。

不註冊 Discord 指令；由既有公關操作呼叫。扣費、行動紀錄與可選
效果寫入回呼在同一個 SQLite transaction() 內完成。

注意：本檔只支援角色自己的 wallet 或付費行動值，未擅自支援公司金庫、
免費／贈送行動值。呼叫端必須在通過自身權限檢查後使用。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.database import transaction, get_or_create_account
from core.economy import _payment

# 正式確認的公關嚴重度費率：金幣 / 行動值
PR_COSTS = {1: (1000, 1), 2: (5000, 2), 3: (15000, 3),
            4: (50000, 5), 5: (100000, 8)}
REPEAT_MULTIPLIERS = (1.0, 0.75, 0.50, 0.25, 0.10)


class RumorPaymentError(ValueError):
    pass


@dataclass(frozen=True)
class ChargeResult:
    action_id: int
    cost: int
    resource: str
    repeat_count: int
    effectiveness: float
    duplicated: bool


def initialize(conn):
    """僅新增支援表；不修改或刪除既有玩家資料。"""
    conn.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_pr_charges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        request_key TEXT NOT NULL,
        rumor_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        strategy TEXT NOT NULL,
        resource TEXT NOT NULL CHECK(resource IN ('coins','paid_actions')),
        cost INTEGER NOT NULL CHECK(cost>0),
        repeat_count INTEGER NOT NULL,
        effectiveness REAL NOT NULL,
        created_at INTEGER NOT NULL,
        UNIQUE(guild_id,request_key)
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_rumor_pr_charge_repeat
        ON rp_rumor_pr_charges(guild_id,rumor_id,character_id,strategy)""")


def _existing(conn, gid, key):
    return conn.execute("""SELECT * FROM rp_rumor_pr_charges
        WHERE guild_id=? AND request_key=?""", (gid, key)).fetchone()


def spend_for_pr_action(
    guild_id, actor_id, character_id, rumor_id, severity, strategy,
    resource, request_key, *, effect_writer: Optional[Callable] = None,
    admin_override: bool = False,
):
    """對角色公關行動扣費並原子記錄。

    - request_key: 單次互動唯一鍵（例如 Discord interaction.id），重試不重扣。
    - effect_writer(conn, effectiveness, action_id): 在同一交易內寫入公關效果。
      如拋出異常，扣費和記錄全部回滾。不得在此回呼做外部 HTTP/Discord 操作。
    - severity: 必須由已驗證的案件等級提供，不能直接信任玩家表單輸入。
    - admin_override: 只能由已驗證的群主後台使用，不能使用者自行指定。
    不授權公司金庫扣款；需另做明確的公司授權流程。
    """
    gid, uid = str(guild_id), str(actor_id)
    cid, rid = int(character_id), int(rumor_id)
    key, strategy = str(request_key).strip(), str(strategy).strip()
    if len(key) == 0 or len(key) > 200:
        raise RumorPaymentError('行動唯一編號不合法。')
    if not strategy or len(strategy) > 80:
        raise RumorPaymentError('公關策略名稱不合法。')
    if type(severity) is not int or severity not in PR_COSTS:
        raise RumorPaymentError('案件嚴重度應為 T1～T5。')
    if resource not in ('coins', 'paid_actions'):
        raise RumorPaymentError('資源只能選擇 coins 或 paid_actions。')
    if cid <= 0 or rid <= 0:
        raise RumorPaymentError('角色或案件編號不合法。')

    cost = PR_COSTS[severity][0 if resource == 'coins' else 1]
    with transaction() as conn:
        initialize(conn)
        old = _existing(conn, gid, key)
        if old:
            if (int(old['rumor_id']) != rid or int(old['character_id']) != cid
                    or old['strategy'] != strategy or old['resource'] != resource
                    or int(old['cost']) != cost):
                raise RumorPaymentError('相同請求編號已用於不同公關操作。')
            return ChargeResult(int(old['id']), int(old['cost']), old['resource'],
                                int(old['repeat_count']), float(old['effectiveness']), True)

        character = conn.execute("""SELECT owner_id,status FROM characters
            WHERE guild_id=? AND id=?""", (gid, cid)).fetchone()
        if not character or character['status'] != 'active':
            raise RumorPaymentError('角色不存在或尚未啟用。')
        if not admin_override and str(character['owner_id']) != uid:
            raise PermissionError('只能使用自己的已啟用角色支付公關行動。')

        case = conn.execute("""SELECT id,status,character_id,settle_at
            FROM rp_company_rumors WHERE guild_id=? AND id=?""", (gid, rid)).fetchone()
        if not case or case['status'] != 'active':
            raise RumorPaymentError('傳聞不存在或已經結案。')
        if int(case['character_id']) != cid:
            raise RumorPaymentError('目前僅支援當事角色為自己的案件付款。')
        if int(case['settle_at']) <= int(time.time()):
            raise RumorPaymentError('傳聞已到結算時間，不能繼續支付公關行動。')

        count = int(conn.execute("""SELECT COUNT(*) FROM rp_rumor_pr_charges
            WHERE guild_id=? AND rumor_id=? AND character_id=? AND strategy=?""",
            (gid, rid, cid, strategy)).fetchone()[0])
        effectiveness = REPEAT_MULTIPLIERS[min(count, len(REPEAT_MULTIPLIERS)-1)]

        if resource == 'coins':
            sender = get_or_create_account(conn, gid, 'character', cid, 'wallet')
            receiver = get_or_create_account(conn, gid, 'system',
                                             'rumor-pr-expense', 'treasury')
            _payment(conn, gid, 'rumor-pr:' + key, sender, receiver, cost,
                     f'傳聞 #{rid} 公關策略 {strategy}')
        else:
            # 行動值既有模組須由同一交易扣除；不可另外開啟交易。
            from modules.zzz_shop_home import spend_paid_actions
            spend_paid_actions(conn, gid, cid, cost)

        cur = conn.execute("""INSERT INTO rp_rumor_pr_charges
            (guild_id,request_key,rumor_id,character_id,strategy,resource,cost,
             repeat_count,effectiveness,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (gid, key, rid, cid, strategy, resource, cost,
             count + 1, effectiveness, int(time.time())))
        action_id = int(cur.lastrowid)
        # 寫入效果與扣款共享交易；失敗時一次回滾。
        if effect_writer is not None:
            effect_writer(conn, effectiveness, action_id)
        return ChargeResult(action_id, cost, resource, count + 1,
                            effectiveness, False)
