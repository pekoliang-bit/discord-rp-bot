"""娛樂城交易核心（非 Discord Cog）。

本階段僅處理開局扣款與經授權的結算；各遊戲的隨機結果必須
由 casino_scratch / casino_lottery / casino_gacha / casino_games 等模組產生。
為防止玩家傳入任意獎金，settle_round 只供受信任的遊戲規則服務呼叫。
"""
from __future__ import annotations

import json
from typing import Any

from core.database import transaction, get_or_create_account
from core.economy import _payment, MAX_AMOUNT
from modules.casino_repository import GAME_KINDS, FUNDING_KINDS


class CasinoError(ValueError):
    pass


def _positive(value: int, label: str) -> int:
    if type(value) is not int or value <= 0 or value > MAX_AMOUNT:
        raise CasinoError(f'{label}必須是合法正整數')
    return value


def _eligible_character(conn, guild_id: str, character_id: int, player_id: str | int) -> dict[str, Any]:
    row = conn.execute('''SELECT id,owner_id,age,status,character_type FROM characters
                          WHERE guild_id=? AND id=?''',(guild_id,character_id)).fetchone()
    if not row or str(row['owner_id']) != str(player_id):
        raise CasinoError('角色不存在或不屬於此玩家')
    if row['status'] not in ('active','approved'):
        raise CasinoError('只有正式啟用角色能進入娛樂城')
    if row['age'] is None or int(row['age']) < 20:
        raise CasinoError('角色必須年滿20歲')
    if row['character_type'] != 'player':
        raise CasinoError('目前僅支援正式原創玩家角色')
    return dict(row)


def _json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise CasinoError('遊戲設定損壞') from exc
    if not isinstance(data, dict):
        raise CasinoError('遊戲設定格式錯誤')
    return data


def _balance(conn, guild_id: str, owner_type: str, owner_id: str, account_type: str) -> int:
    row = conn.execute('''SELECT COALESCE(SUM(e.amount),0) AS balance FROM accounts a
        LEFT JOIN ledger_entries e ON e.guild_id=a.guild_id AND e.account_id=a.id
        WHERE a.guild_id=? AND a.owner_type=? AND a.owner_id=? AND a.account_type=?''',
        (guild_id,owner_type,owner_id,account_type)).fetchone()
    return int(row['balance'] or 0)


def _account(conn, guild_id: str, owner_type: str, owner_id: str, account_type: str) -> int:
    return get_or_create_account(conn,guild_id,owner_type,owner_id,account_type)


def purchase_round(*, guild_id: str | int, player_id: str | int,
                   character_id: int, game_code: str, request_key: str,
                   payment_source: str = 'wallet') -> dict[str, Any]:
    """原子扣款並建立待結算遊戲紀錄。重試相同 key 回傳原始紀錄。

    購買階段一次只買一份；批次購買由後續遊戲模組依唯一 key 逐張處理。
    遊戲-specific 次數、獎池、下注上限由後續規則模組附加檢查。
    """
    gid = str(guild_id)
    if not gid or type(character_id) is not int or character_id <= 0:
        raise CasinoError('角色或伺服器不合法')
    if payment_source not in ('wallet','bank'):
        raise CasinoError('只能從角色錢包或銀行活期付款')
    if not isinstance(request_key,str) or not (1 <= len(request_key) <= 100):
        raise CasinoError('操作編號不合法')
    with transaction() as conn:
        _eligible_character(conn,gid,character_id,player_id)
        prior = conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND request_key=?',(gid,request_key)).fetchone()
        if prior:
            if prior['character_id'] != character_id or prior['payment_source'] != payment_source:
                raise CasinoError('操作編號已用於其他購買')
            expected = conn.execute('SELECT code FROM casino_games WHERE guild_id=? AND id=?',(gid,prior['game_id'])).fetchone()
            if not expected or expected['code'] != game_code:
                raise CasinoError('操作編號已用於其他票種')
            return dict(prior)
        game = conn.execute('SELECT * FROM casino_games WHERE guild_id=? AND code=?',(gid,game_code)).fetchone()
        if not game or not game['enabled']:
            raise CasinoError('該遊戲未開放')
        if game['kind'] not in GAME_KINDS or game['funding_mode'] not in FUNDING_KINDS:
            raise CasinoError('遊戲配置不合法')
        rules = _json_object(game['settings_json'])
        if game['kind'] not in ('scratch','lottery','gacha') and not rules.get('purchase_flow_enabled',False):
            raise CasinoError('該遊戲必須使用專屬對局流程')
        price = _positive(game['ticket_price'],'票價')
        # 本階段保守限制：公開前必須由後續模組完成各遊戲限額與獎表檢查。
        if not rules.get('rules_verified',False):
            raise CasinoError('遊戲尚未完成規則與獎表驗證，不可購買')
        day_limit = rules.get('daily_purchase_limit',20)
        if type(day_limit) is not int or not 1 <= day_limit <= 10000:
            raise CasinoError('每日購買上限配置錯誤')
        count = conn.execute('''SELECT COUNT(*) AS n FROM casino_rounds
            WHERE guild_id=? AND character_id=? AND game_id=?
            AND status != 'cancelled' AND date(created_at)=date('now')''',
            (gid,character_id,game['id'])).fetchone()['n']
        if count >= day_limit:
            raise CasinoError('今天已達此遊戲購買上限')
        # 已驗證金額之後，透過既有 core.economy._payment 在同一事務內扣款。
        payer = _account(conn,gid,'character',str(character_id),payment_source)
        receiver = _account(conn,gid,'system','casino_receipts','treasury')
        payment = _payment(conn,gid,f'casino:buy:{request_key}',payer,receiver,price,f'娛樂城購買 {game_code}')
        cur = conn.execute('''INSERT INTO casino_rounds(guild_id,game_id,character_id,request_key,game_kind,
                config_version,ticket_price,payment_source,funding_mode,pay_tx_id)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (gid,game['id'],character_id,request_key,game['kind'],game['config_version'],price,
                 payment_source,game['funding_mode'],payment.transaction_id))
        row = conn.execute('SELECT * FROM casino_rounds WHERE id=?',(cur.lastrowid,)).fetchone()
        return dict(row)


def settle_round(*, guild_id: str | int, round_id: int,
                 outcome: dict[str, Any], reward_coins: int = 0,
                 expected_kind: str | None = None) -> dict[str, Any]:
    """僅供受信任的遊戲引擎調用，不可接受玩家自行指定 outcome/reward_coins。

    第一階段僅允許 direct 模式結算金幣；其他資金來源、商品、頭獎效果
    需後續模組完成規則後才開放，避免錯誤發行或雙重付款。
    """
    gid = str(guild_id)
    if type(round_id) is not int or round_id <= 0:
        raise CasinoError('對局編號不合法')
    if type(reward_coins) is not int or reward_coins < 0 or reward_coins > MAX_AMOUNT:
        raise CasinoError('獎金不合法')
    if not isinstance(outcome,dict):
        raise CasinoError('開獎結果必須為物件')
    outcome_json = json.dumps(outcome,ensure_ascii=False,sort_keys=True)
    if len(outcome_json) > 30000:
        raise CasinoError('開獎資料過大')
    with transaction() as conn:
        row = conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND id=?',(gid,round_id)).fetchone()
        if not row:
            raise CasinoError('找不到對局')
        if expected_kind is not None and row['game_kind'] != expected_kind:
            raise CasinoError('對局種類不符')
        if row['status'] == 'settled':
            if row['outcome_json'] != outcome_json or row['reward_coins'] != reward_coins:
                raise CasinoError('已完成對局不能更改結果')
            return dict(row)
        if row['status'] != 'pending':
            raise CasinoError('這筆對局不能結算')
        if row['funding_mode'] != 'direct':
            raise CasinoError('此資金來源尚未完成安全結算支援')
        game = conn.execute('SELECT settings_json FROM casino_games WHERE guild_id=? AND id=?',(gid,row['game_id'])).fetchone()
        if not game:
            raise CasinoError('遊戲已不存在')
        rules = _json_object(game['settings_json'])
        max_reward = rules.get('max_single_reward',0)
        if type(max_reward) is not int or max_reward < 0 or reward_coins > max_reward:
            raise CasinoError('獎金超出已核准上限')
        reward_tx = None
        if reward_coins:
            sender = _account(conn,gid,'system','issuance','treasury')
            receiver = _account(conn,gid,'character',str(row['character_id']),'wallet')
            result = _payment(conn,gid,f'casino:reward:{round_id}',sender,receiver,
                              reward_coins,f'娛樂城對局 {round_id} 獎金')
            reward_tx = result.transaction_id
            conn.execute('''INSERT INTO casino_rewards(guild_id,round_id,reward_key,reward_type,quantity,state,delivered_at)
                            VALUES(?,?,?,?,?,'delivered',CURRENT_TIMESTAMP)''',
                         (gid,round_id,'coins','coins',reward_coins))
        conn.execute('''UPDATE casino_rounds SET status='settled',outcome_json=?,reward_coins=?,
                        reward_tx_id=?,settled_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?''',
                     (outcome_json,reward_coins,reward_tx,gid,round_id))
        return dict(conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND id=?',(gid,round_id)).fetchone())
