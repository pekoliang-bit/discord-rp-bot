"""娛樂城十連抽，完整交易式金幣抽獎。放置於 modules/casino_gacha_batch.py。

限制：此批次引擎只允許「全金幣獎池 + direct 發獎」；混合商城獎池
須在日後建立商品與金幣跨類別原子結算後才能開放。每批十張，原子扣款。
"""
from __future__ import annotations

import json
from typing import Any

from core.database import transaction, get_or_create_account
from core.economy import _payment, MAX_AMOUNT
from modules.casino_service import CasinoError, _eligible_character
from modules.casino_gacha import validate_gacha, _roll_reward, RARITIES


def draw_ten_atomic(*, guild_id: str | int, player_id: str | int, character_id: int,
                    game_code: str, batch_key: str, payment_source: str = 'wallet') -> list[dict[str, Any]]:
    """相同 batch_key 僅回傳既有十連，不重新扣款/保底/發獎。"""
    if payment_source not in ('wallet', 'bank') or not isinstance(batch_key, str) or not 1 <= len(batch_key) <= 75:
        raise CasinoError('付款來源或批次編號不合法')
    if type(character_id) is not int or character_id < 1:
        raise CasinoError('角色編號不合法')
    gid = str(guild_id)
    # 先以標準介面驗證設定；同一交易中再次取回版本，確保不混用設定。
    from modules.casino_repository import get_game
    current = get_game(gid, game_code)
    if current is None:
        raise CasinoError('找不到獎池')
    spec = validate_gacha(current)
    if spec['has_items']:
        raise CasinoError('此十連抽只開放全金幣獎池；商城商品池尚未完成批次出貨')
    if current['funding_mode'] != 'direct':
        raise CasinoError('獎池資金來源尚未支援十連')
    keys = [f'casino10:{batch_key}:{i}' for i in range(10)]
    if any(len(k) > 100 for k in keys):
        raise CasinoError('批次編號過長')
    with transaction() as conn:
        _eligible_character(conn, gid, character_id, player_id)
        rows = conn.execute('''SELECT * FROM casino_rounds WHERE guild_id=? AND request_key IN
            (?,?,?,?,?,?,?,?,?,?) ORDER BY id''', (gid, *keys)).fetchall()
        if rows:
            if len(rows) != 10 or {r['request_key'] for r in rows} != set(keys) or any(
                r['character_id'] != character_id or r['payment_source'] != payment_source
                or r['status'] != 'settled' or r['game_kind'] != 'gacha'
                or r['game_id'] != current['id'] for r in rows
            ):
                raise CasinoError('十連抽批次紀錄不完整或與本次要求不符')
            return [dict(r) for r in rows]
        game = conn.execute('SELECT * FROM casino_games WHERE guild_id=? AND code=?',
                            (gid, game_code)).fetchone()
        if game is None or not game['enabled'] or game['config_version'] != current['config_version']:
            raise CasinoError('獎池已停用或設定變更，請重新操作')
        cfg = spec['config']
        limit = cfg.get('daily_purchase_limit', 20)
        if type(limit) is not int or limit < 10:
            raise CasinoError('十連抽超過每日購買限制')
        existing = conn.execute('''SELECT COUNT(*) FROM casino_rounds WHERE guild_id=?
            AND character_id=? AND game_id=? AND status!='cancelled'
            AND date(created_at)=date('now')''', (gid, character_id, game['id'])).fetchone()[0]
        if existing + 10 > limit:
            raise CasinoError('今日剩餘次數不足以十連抽')
        price = int(game['ticket_price'])
        total = price * 10
        if not 0 < total <= MAX_AMOUNT:
            raise CasinoError('十連抽費用超過金額限制')
        payer = get_or_create_account(conn, gid, 'character', str(character_id), payment_source)
        receiver = get_or_create_account(conn, gid, 'system', 'casino_receipts', 'treasury')
        payment = _payment(conn, gid, f'casino:batch:{batch_key}', payer, receiver,
                           total, f'娛樂城十連抽 {game_code}')
        family = cfg['pool_family']
        record = conn.execute('''SELECT since_ssr,since_ur FROM casino_pity
            WHERE guild_id=? AND character_id=? AND pool_family=?''', (gid, character_id, family)).fetchone()
        ssr, ur = (int(record['since_ssr']), int(record['since_ur'])) if record else (0, 0)
        output = []
        ten_rewards = []
        for i, key in enumerate(keys):
            # 在第十抽時確保本批至少一次 SR 以上；若前九抽已有 SR 則正常抽。
            minimum = 'SR' if i == 9 and cfg.get('guarantee_sr_ten', True) and not any(
                RARITIES.index(x['rarity']) >= 2 for x in ten_rewards) else 'N'
            prize = _roll_reward(spec, ssr, ur, minimum=minimum)
            rarity, coins = prize['rarity'], prize['coins']
            if type(coins) is not int or not 0 < coins <= cfg['max_single_reward']:
                raise CasinoError('抽獎金幣獎品不合法')
            ssr = 0 if RARITIES.index(rarity) >= 3 else ssr + 1
            ur = 0 if rarity == 'UR' else ur + 1
            outcome = {'rarity': rarity, 'type': 'coins', 'coins': coins,
                       'pool_family': family, 'prize_code': str(prize.get('code', rarity))}
            ten_rewards.append(outcome)
            cur = conn.execute('''INSERT INTO casino_rounds(guild_id,game_id,character_id,request_key,
                game_kind,config_version,ticket_price,payment_source,funding_mode,status,
                outcome_json,reward_coins,pay_tx_id,settled_at)
                VALUES(?,?,?,?,?,?,?,?,?,'settled',?,?,?,CURRENT_TIMESTAMP)''',
                (gid, game['id'], character_id, key, 'gacha', game['config_version'], price,
                 payment_source, 'direct', json.dumps(outcome,ensure_ascii=False,sort_keys=True),coins,
                 payment.transaction_id))
            rid = int(cur.lastrowid)
            sender = get_or_create_account(conn, gid, 'system', 'issuance', 'treasury')
            beneficiary = get_or_create_account(conn, gid, 'character', str(character_id), 'wallet')
            reward = _payment(conn, gid, f'casino:reward:{rid}', sender, beneficiary, coins,
                              f'娛樂城十連抽 {rid} 獎金')
            conn.execute('UPDATE casino_rounds SET reward_tx_id=? WHERE id=? AND guild_id=?',
                         (reward.transaction_id, rid, gid))
            conn.execute('''INSERT INTO casino_rewards(guild_id,round_id,reward_key,reward_type,
                quantity,state,delivered_at) VALUES(?,?,?,'coins',?,'delivered',CURRENT_TIMESTAMP)''',
                (gid, rid, 'coins', coins))
            output.append(dict(conn.execute('SELECT * FROM casino_rounds WHERE id=?', (rid,)).fetchone()))
        conn.execute('''INSERT INTO casino_pity(guild_id,character_id,pool_family,since_ssr,since_ur)
            VALUES(?,?,?,?,?) ON CONFLICT(guild_id,character_id,pool_family) DO UPDATE SET
            since_ssr=excluded.since_ssr,since_ur=excluded.since_ur,updated_at=CURRENT_TIMESTAMP''',
            (gid, character_id, family, ssr, ur))
        return output
