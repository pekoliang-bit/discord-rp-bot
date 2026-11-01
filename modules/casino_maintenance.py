"""娛樂城彩票到期維護：鎖定開獎、分批結算、補建通知事件。

放置位置：modules/casino_maintenance.py
從專案根目錄執行：python scripts/run_casino_lottery_maintenance.py GUILD_ID

非 Discord Extension，無頂層 setup；不自行排程。
只處理已啟用、已核准、direct、非累積型彩票。
個別期別失敗不影響其他期別，重新執行可以接續尚未完成的票券。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.database import get_connection
from modules.casino_effects import init_effects_schema, queue_settled_round_effects
from modules.casino_lottery import (
    _settings,
    _utc,
    draw_lottery,
    init_lottery_schema,
    settle_lottery_tickets,
)
from modules.casino_repository import init_casino_schema


def _eligible_draws(guild_id: str, max_draws: int) -> list[dict[str, Any]]:
    """只列出到期且符合現有安全限制的彩票期別。"""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT d.id, d.draw_at, d.state, d.result_json,
                      g.code, g.kind, g.enabled, g.funding_mode, g.settings_json
                 FROM casino_lottery_draws d
                 JOIN casino_games g ON g.guild_id=d.guild_id AND g.id=d.game_id
                WHERE d.guild_id=? AND d.state IN ('selling','closed','drawn')
                ORDER BY d.draw_at, d.id""",
            (guild_id,),
        ).fetchall()
    now = datetime.now(timezone.utc)
    valid: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item['enabled'] != 1 or item['kind'] != 'lottery' or item['funding_mode'] != 'direct':
            continue
        try:
            _settings(item)
            if _utc(item['draw_at']) > now:
                continue
            # 已開獎但沒有封存結果的異常狀態，不能重新抽號。
            if item['state'] == 'drawn' and not item['result_json']:
                continue
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        valid.append(item)
        if len(valid) >= max_draws:
            break
    return valid


def _pending_tickets(guild_id: str, draw_id: int) -> int:
    with get_connection() as conn:
        return int(conn.execute(
            """SELECT COUNT(*) FROM casino_lottery_tickets t
                 JOIN casino_rounds r ON r.guild_id=t.guild_id AND r.id=t.round_id
                WHERE t.guild_id=? AND t.draw_id=? AND r.status='pending'""",
            (guild_id, draw_id),
        ).fetchone()[0])


def _missing_effect_rounds(guild_id: str, draw_id: int, limit: int) -> list[int]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT r.id FROM casino_lottery_tickets t
                 JOIN casino_rounds r ON r.guild_id=t.guild_id AND r.id=t.round_id
                WHERE t.guild_id=? AND t.draw_id=? AND r.status='settled'
                  AND NOT EXISTS (
                    SELECT 1 FROM casino_effect_events e
                     WHERE e.guild_id=r.guild_id
                       AND e.event_key='round:' || r.id || ':private_result'
                  ) ORDER BY r.id LIMIT ?""",
            (guild_id, draw_id, limit),
        ).fetchall()
    return [int(r['id']) for r in rows]


def process_due_lotteries(*, guild_id: int | str,
                          max_draws: int = 20,
                          batch_size: int = 100) -> dict[str, Any]:
    """處理到期彩票，返回執行數量與錯誤；不直接發送 Discord 訊息。

    這是明確呼叫的維護入口，而非自動排程。每張彩票的發獎
    依賴 settle_round 本身的冪等結算；重跑只處理 pending 票券。
    """
    if not str(guild_id).isdigit() or int(guild_id) <= 0:
        raise ValueError('guild_id 必須是正整數')
    if type(max_draws) is not int or not 1 <= max_draws <= 100:
        raise ValueError('max_draws 必須介於 1～100')
    if type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise ValueError('batch_size 必須介於 1～500')

    gid = str(guild_id)
    init_casino_schema()
    init_lottery_schema()
    init_effects_schema()
    outcome: dict[str, Any] = {
        'guild_id': gid, 'processed_draws': 0, 'newly_drawn': 0,
        'settled_tickets': 0, 'queued_effect_rounds': 0,
        'pending_tickets': 0, 'errors': [],
    }
    for draw in _eligible_draws(gid, max_draws):
        did = int(draw['id'])
        try:
            # draw_lottery 在資料庫交易內鎖定結果；重啟不會重抽。
            draw_lottery(guild_id=gid, draw_id=did)
            if draw['state'] != 'drawn':
                outcome['newly_drawn'] += 1
            settled = settle_lottery_tickets(guild_id=gid, draw_id=did, limit=batch_size)
            outcome['settled_tickets'] += int(settled['settled'])
            # 只補一批通知事件；排程重跑時接續其餘票券。
            for rid in _missing_effect_rounds(gid, did, batch_size):
                queue_settled_round_effects(gid, rid)
                outcome['queued_effect_rounds'] += 1
            outcome['pending_tickets'] += _pending_tickets(gid, did)
            outcome['processed_draws'] += 1
        except Exception as exc:
            # 只輸出期別與例外類型，不洩露角色資料或帳務內容。
            outcome['errors'].append({'draw_id': did, 'error_type': type(exc).__name__})
    return outcome
