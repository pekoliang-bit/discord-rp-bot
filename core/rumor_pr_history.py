"""玩家本人公關歷史與累積分數（唯讀，不發獎、不評級）。"""
from __future__ import annotations
from dataclasses import dataclass
from core.database import get_connection
from core.rumor_pr_score_summary import summarize_pr_score


@dataclass(frozen=True)
class PRHistory:
    rumor_id: int
    character_id: int
    status: str
    score: int
    count: int
    coins: int
    paid_actions: int
    lines: tuple[str, ...]


def get_pr_history(guild_id, actor_id, rumor_id: int, character_id: int, *, limit: int = 10) -> PRHistory:
    if not str(guild_id).strip() or not str(actor_id).strip():
        raise ValueError('缺少伺服器或操作玩家')
    if type(rumor_id) is not int or rumor_id <= 0 or type(character_id) is not int or character_id <= 0:
        raise ValueError('傳聞或角色編號錯誤')
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError('歷史筆數須介於 1～20')
    gid = str(guild_id)
    with get_connection() as db:
        owner = db.execute('SELECT owner_id FROM characters WHERE guild_id=? AND id=?', (gid, character_id)).fetchone()
        if owner is None or str(owner['owner_id']) != str(actor_id):
            raise PermissionError('只能查看自己角色的公關紀錄')
        case = db.execute('SELECT character_id,status FROM rp_company_rumors WHERE guild_id=? AND id=?', (gid, rumor_id)).fetchone()
        if case is None or int(case['character_id']) != character_id:
            raise LookupError('找不到此角色的傳聞')
    # 此服務會嚴格核對每筆扣費與效果的關聯，異常時拒絕顯示偽造分數。
    summary = summarize_pr_score(gid, rumor_id, character_id)
    with get_connection() as db:
        rows = db.execute('''
            SELECT c.id, c.strategy, c.resource, c.cost,
                   c.repeat_count, e.effective_score
            FROM rp_rumor_pr_charges AS c
            INNER JOIN rp_rumor_pr_effects AS e
              ON e.guild_id=c.guild_id AND e.action_id=c.id
             AND e.rumor_id=c.rumor_id AND e.character_id=c.character_id
            WHERE c.guild_id=? AND c.rumor_id=? AND c.character_id=?
            ORDER BY c.id DESC LIMIT ?
        ''', (gid, rumor_id, character_id, limit)).fetchall()
    history = tuple(
        f"#{int(r['id'])}｜{r['strategy']}｜第{int(r['repeat_count'])}次｜"
        f"{int(r['cost'])}{'金幣' if r['resource']=='coins' else '行動值'}｜"
        f"+{int(r['effective_score'])}分"
        for r in rows
    )
    return PRHistory(rumor_id, character_id, str(case['status']), summary.score,
                     summary.action_count, summary.total_coins_spent,
                     summary.total_paid_actions_spent, history)
