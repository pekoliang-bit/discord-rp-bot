"""公關操作確認前的唯讀報價；最終扣費必須重新驗證。"""
from __future__ import annotations
from dataclasses import dataclass
import time
from core.database import get_connection
from core.rumor_payment import PR_COSTS, REPEAT_MULTIPLIERS

@dataclass(frozen=True)
class PRQuote:
    tier: int
    cost: int
    resource: str
    repeat_count: int
    multiplier: float
    base_score: int
    estimated_score: int


def quote_pr_action(guild_id, actor_id, character_id, rumor_id, strategy, resource):
    gid = str(guild_id).strip()
    if not gid or not strategy or resource not in ('coins', 'paid_actions'):
        raise ValueError('公關報價參數錯誤')
    if type(character_id) is not int or type(rumor_id) is not int or min(character_id, rumor_id) <= 0:
        raise ValueError('角色或案件編號錯誤')
    with get_connection() as db:
        ch = db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?', (gid,character_id)).fetchone()
        if ch is None or ch['status'] != 'active' or str(ch['owner_id']) != str(actor_id):
            raise PermissionError('只能替自己已啟用的角色操作')
        case = db.execute('SELECT character_id,status,settle_at FROM rp_company_rumors WHERE guild_id=? AND id=?',(gid,rumor_id)).fetchone()
        if case is None or case['status'] != 'active' or int(case['character_id']) != character_id or int(case['settle_at']) <= int(time.time()):
            raise ValueError('傳聞不是可操作的本人未到期案件')
        tier = db.execute('SELECT tier FROM rp_rumor_case_tiers WHERE guild_id=? AND rumor_id=?',(gid,rumor_id)).fetchone()
        if tier is None or int(tier['tier']) not in PR_COSTS:
            raise ValueError('案件尚未鎖定正式 T1～T5 等級')
        cfg = db.execute('SELECT base_score FROM rp_rumor_pr_strategy_config WHERE guild_id=? AND strategy=? AND enabled=1',(gid,strategy)).fetchone()
        if cfg is None:
            raise ValueError('此公關策略尚未經官方啟用')
        count = db.execute('SELECT COUNT(*) FROM rp_rumor_pr_charges WHERE guild_id=? AND rumor_id=? AND character_id=? AND strategy=?',(gid,rumor_id,character_id,strategy)).fetchone()[0]
    tier_num = int(tier['tier'])
    multiplier = REPEAT_MULTIPLIERS[min(int(count), len(REPEAT_MULTIPLIERS)-1)]
    base = int(cfg['base_score'])
    from decimal import Decimal, ROUND_HALF_UP
    score = int((Decimal(base)*Decimal(str(multiplier))).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return PRQuote(tier_num,PR_COSTS[tier_num][0 if resource=='coins' else 1],resource,int(count)+1,multiplier,base,score)
