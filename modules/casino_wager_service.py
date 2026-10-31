"""娛樂城即時賭桌金流：歐式輪盤、骰寶與老虎機。

只供可信任的 Discord 控制器呼叫，不接受客戶端提供開獎結果或獎金。
投注、隨機抽取、帳本分錄及對局封存均位於同一 SQLite 事務。
其他賭桌（Blackjack / Bingo / PvP）需另行設計回合狀態機，目前不可使用此服務。
"""
from __future__ import annotations

import json
from typing import Any
from core.database import transaction, get_or_create_account
from core.economy import _payment, MAX_AMOUNT
from modules.casino_service import CasinoError, _eligible_character
from modules.casino_games import roulette_spin, roulette_result, roll_sicbo, sicbo_result, slot_spin, slot_result

_ALLOWED = {'roulette', 'sicbo', 'slots'}


def play_wager(*, guild_id: int | str, player_id: int | str, character_id: int,
               game_code: str, request_key: str, choice: str = '',
               target: int | None = None, payment_source: str = 'wallet') -> dict[str, Any]:
    """整局原子結算；同 request_key 重試只回傳原結果。下注額使用已核准的遊戲票價。"""
    gid = str(guild_id)
    if not gid or type(character_id) is not int or character_id < 1:
        raise CasinoError('角色資料不合法')
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
        raise CasinoError('請提供唯一操作編號')
    if payment_source not in ('wallet', 'bank'):
        raise CasinoError('只能使用錢包或銀行活期存款')
    with transaction() as conn:
        _eligible_character(conn, gid, character_id, player_id)
        prior = conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND request_key=?', (gid, request_key)).fetchone()
        if prior:
            original = conn.execute('SELECT code FROM casino_games WHERE guild_id=? AND id=?', (gid, prior['game_id'])).fetchone()
            if prior['character_id'] != character_id or prior['payment_source'] != payment_source or not original or original['code'] != game_code:
                raise CasinoError('操作編號已使用於其他交易')
            if prior['status'] != 'settled':
                raise CasinoError('既有對局尚未完成，請聯絡管理員')
            recorded = json.loads(prior['outcome_json'] or '{}')
            if recorded.get('choice') != choice or recorded.get('target') != target:
                raise CasinoError('不能修改已成立對局的下注內容')
            return dict(prior)
        game = conn.execute('SELECT * FROM casino_games WHERE guild_id=? AND code=?', (gid, game_code)).fetchone()
        if not game or not game['enabled'] or game['kind'] not in _ALLOWED:
            raise CasinoError('遊戲尚未開放下注')
        if game['funding_mode'] != 'direct':
            raise CasinoError('目前賭桌只支援系統直接發獎')
        rules = json.loads(game['settings_json'])
        if not isinstance(rules, dict) or not rules.get('rules_verified', False) or not rules.get('wager_enabled', False):
            raise CasinoError('遊戲尚未核准完整下注規則')
        stake = game['ticket_price']
        if type(stake) is not int or not 100 <= stake <= 100_000:
            raise CasinoError('下注額不符合限制')
        daily_limit = rules.get('daily_purchase_limit', 20)
        if type(daily_limit) is not int or not 1 <= daily_limit <= 10000:
            raise CasinoError('每日下注次數設定不合法')
        used = conn.execute('''SELECT COUNT(*) FROM casino_rounds WHERE guild_id=? AND character_id=? AND game_id=?
            AND status!='cancelled' AND date(created_at)=date('now')''', (gid, character_id, game['id'])).fetchone()[0]
        if used >= daily_limit:
            raise CasinoError('今天已達下注次數上限')
        if game['kind'] == 'roulette':
            # 先驗證投注內容，再取亂數。
            roulette_result(number=0, bet=stake, choice=choice, target=target)
            outcome = roulette_result(number=roulette_spin(), bet=stake, choice=choice, target=target)
        elif game['kind'] == 'sicbo':
            sicbo_result(dice=(1,2,3), bet=stake, choice=choice, target=target)
            outcome = sicbo_result(dice=roll_sicbo(), bet=stake, choice=choice, target=target)
        else:
            if choice or target is not None:
                raise CasinoError('老虎機不接受投注選項')
            symbols, weights = rules.get('symbols'), rules.get('weights')
            reels = rules.get('reels', 3)
            paytable = rules.get('paytable')
            if not isinstance(paytable, dict):
                raise CasinoError('老虎機賠率表不合法')
            sample = slot_spin(symbols=symbols, weights=weights, reels=reels)
            # slot_spin 已校驗設定；第一組隨機結果直接使用，不再抽第二次。
            outcome = slot_result(spun=sample, bet=stake, paytable=paytable)
            choice, target = '', None
        payout = outcome['payout']
        ceiling = rules.get('max_single_reward', 0)
        if type(ceiling) is not int or ceiling < 0 or payout > ceiling or payout > MAX_AMOUNT:
            raise CasinoError('本局賠付超出已核准上限，未扣款')
        outcome['choice'] = choice
        outcome['target'] = target
        payer = get_or_create_account(conn, gid, 'character', str(character_id), payment_source)
        receipt = get_or_create_account(conn, gid, 'system', 'casino_receipts', 'treasury')
        debit = _payment(conn, gid, f'casino:wager:{request_key}', payer, receipt, stake, f'娛樂城下注 {game_code}')
        reward_tx_id = None
        if payout:
            issuer = get_or_create_account(conn, gid, 'system', 'issuance', 'treasury')
            wallet = get_or_create_account(conn, gid, 'character', str(character_id), 'wallet')
            credit = _payment(conn, gid, f'casino:wager:reward:{request_key}', issuer, wallet, payout, f'娛樂城賠付 {game_code}')
            reward_tx_id = credit.transaction_id
        cur = conn.execute('''INSERT INTO casino_rounds(guild_id,game_id,character_id,request_key,game_kind,
           config_version,ticket_price,payment_source,funding_mode,status,outcome_json,reward_coins,
           pay_tx_id,reward_tx_id,settled_at) VALUES(?,?,?,?,?,?,?,?,?,'settled',?,?,?,?,CURRENT_TIMESTAMP)''',
           (gid,game['id'],character_id,request_key,game['kind'],game['config_version'],stake,payment_source,
            game['funding_mode'],json.dumps(outcome,ensure_ascii=False,sort_keys=True),payout,
            debit.transaction_id,reward_tx_id))
        if payout:
            conn.execute('''INSERT INTO casino_rewards(guild_id,round_id,reward_key,reward_type,quantity,state,delivered_at)
                VALUES(?,?, 'coins','coins',?,'delivered',CURRENT_TIMESTAMP)''', (gid,cur.lastrowid,payout))
        return dict(conn.execute('SELECT * FROM casino_rounds WHERE id=?', (cur.lastrowid,)).fetchone())
