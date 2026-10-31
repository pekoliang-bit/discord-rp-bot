"""娛樂城彩票：選號、售票、鎖定開獎與可恢復的逐注結算。

不是 Discord Extension。單次開獎結果固定後不可重抽。
目前只支援 direct 金幣獎；累積獎池及其他資金來源須等金流模組擴充後再啟用。
"""
from __future__ import annotations

import json
import math
import secrets
from datetime import datetime, timezone
from typing import Any

from core.database import get_connection, transaction
from modules.casino_repository import get_game, upsert_game
from modules.casino_service import CasinoError, settle_round, _eligible_character, _account, _positive
from core.economy import _payment

DEFAULT_LOTTERY = (
    ('lottery_daily','每日幸運彩',100,20,4,100_000),
    ('lottery_royal','皇家樂透',1000,30,6,5_000_000),
    ('lottery_dream','超級夢想彩',5000,40,6,30_000_000),
)

LOTO_DDL = (
    '''CREATE TABLE IF NOT EXISTS casino_lottery_draws (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        game_id INTEGER NOT NULL, draw_code TEXT NOT NULL, cutoff_at TEXT NOT NULL,
        draw_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'selling'
        CHECK(state IN ('selling','closed','drawn')),
        result_json TEXT, drawn_at TEXT,
        UNIQUE(guild_id,game_id,draw_code),
        UNIQUE(guild_id,id),
        FOREIGN KEY(guild_id,game_id) REFERENCES casino_games(guild_id,id))''',
    '''CREATE TABLE IF NOT EXISTS casino_lottery_tickets (
        guild_id TEXT NOT NULL, round_id INTEGER NOT NULL PRIMARY KEY,
        draw_id INTEGER NOT NULL, numbers_json TEXT NOT NULL,
        FOREIGN KEY(guild_id,round_id) REFERENCES casino_rounds(guild_id,id),
        FOREIGN KEY(guild_id,draw_id) REFERENCES casino_lottery_draws(guild_id,id))''',
    'CREATE INDEX IF NOT EXISTS idx_casino_lottery_tickets_draw ON casino_lottery_tickets(guild_id,draw_id)',
)


def init_lottery_schema() -> None:
    with transaction() as conn:
        for statement in LOTO_DDL:
            conn.execute(statement)


def install_default_lottery(guild_id: str | int, *, actor_id: str | int = 'system') -> list[dict[str, Any]]:
    """只安裝停用的彩票範本；需設定獎表、截止時間、核准後才開放。"""
    output = []
    for code, name, price, pool, picks, jackpot in DEFAULT_LOTTERY:
        if get_game(guild_id, code):
            continue
        output.append(upsert_game(guild_id=guild_id, code=code, name=name, kind='lottery', enabled=False,
                      ticket_price=price, funding_mode='direct', actor_id=actor_id,
                      settings={'number_pool': pool, 'pick_count': picks, 'max_single_reward': jackpot,
                                'prizes': {}, 'rules_verified': False, 'daily_purchase_limit': 100,
                                'rollover': False, 'announcements': {'jackpot': True}}))
    return output


def _settings(game: dict[str, Any]) -> dict[str, Any]:
    if game['kind'] != 'lottery' or game['funding_mode'] != 'direct':
        raise CasinoError('目前僅能開放系統直接發獎的彩票')
    config = json.loads(game['settings_json'])
    pool, picks = config.get('number_pool'), config.get('pick_count')
    if type(pool) is not int or type(picks) is not int or not (2 <= picks <= pool <= 100):
        raise CasinoError('選號規則不合法')
    table = config.get('prizes')
    if not isinstance(table, dict) or not table:
        raise CasinoError('彩票獎表尚未設定')
    for key, val in table.items():
        if not str(key).isdigit() or not (0 <= int(key) <= picks) or type(val) is not int or val < 0:
            raise CasinoError('彩票獎表格式錯誤')
        if val > config.get('max_single_reward', 0):
            raise CasinoError('彩票獎金超出上限')
    if not config.get('rules_verified'):
        raise CasinoError('彩票規則尚未核准')
    if config.get('rollover'):
        raise CasinoError('累積獎池尚未接通獨立帳本，不可開售')
    return config


def prize_probabilities(*, number_pool: int, pick_count: int) -> dict[int, float]:
    """對不計順序的 N 選 K 彩票，以超幾何分配計算實際命中機率。"""
    if type(number_pool) is not int or type(pick_count) is not int or not 2 <= pick_count <= number_pool <= 100:
        raise ValueError('選號設定不合法')
    total = math.comb(number_pool, pick_count)
    return {hits: math.comb(pick_count, hits) * math.comb(number_pool-pick_count, pick_count-hits) / total
            for hits in range(pick_count+1) if pick_count-hits <= number_pool-pick_count}


def _utc(value: str) -> datetime:
    try:
        d = datetime.fromisoformat(value.replace('Z','+00:00'))
        if d.tzinfo is None:
            raise ValueError
        return d.astimezone(timezone.utc)
    except (ValueError, AttributeError) as exc:
        raise CasinoError('時間須為含時區的 ISO 8601 日期時間') from exc


def create_draw(*, guild_id: str | int, game_code: str, draw_code: str,
                cutoff_at: str, draw_at: str) -> dict[str, Any]:
    """只供管理員權限檢查通過後呼叫；不可開放玩家自建。"""
    init_lottery_schema()
    game = get_game(guild_id, game_code)
    if not game:
        raise CasinoError('找不到彩票')
    _settings(game)
    cutoff, drawing = _utc(cutoff_at), _utc(draw_at)
    if cutoff >= drawing or drawing <= datetime.now(timezone.utc):
        raise CasinoError('截止時間必須早於未來開獎時間')
    if not draw_code or len(draw_code)>80:
        raise CasinoError('期別代碼不合法')
    gid = str(guild_id)
    with transaction() as conn:
        previous = conn.execute('SELECT * FROM casino_lottery_draws WHERE guild_id=? AND game_id=? AND draw_code=?',
                                (gid,game['id'],draw_code)).fetchone()
        if previous:
            if previous['cutoff_at'] != cutoff.isoformat() or previous['draw_at'] != drawing.isoformat():
                raise CasinoError('既存期別不可修改時間')
            return dict(previous)
        cur=conn.execute('''INSERT INTO casino_lottery_draws(guild_id,game_id,draw_code,cutoff_at,draw_at)
                            VALUES(?,?,?,?,?)''',(gid,game['id'],draw_code,cutoff.isoformat(),drawing.isoformat()))
        return dict(conn.execute('SELECT * FROM casino_lottery_draws WHERE id=?',(cur.lastrowid,)).fetchone())


def buy_lottery(*, guild_id: str | int, player_id: str | int, character_id: int, game_code: str,
                draw_id: int, request_key: str, numbers: list[int] | None = None,
                payment_source: str = 'wallet') -> dict[str, Any]:
    """單一原子交易：驗證期別、扣款與登記號碼，錯誤時全部回滾。"""
    init_lottery_schema()
    gid = str(guild_id)
    if payment_source not in ('wallet', 'bank') or not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
        raise CasinoError('付款來源或操作編號錯誤')
    with transaction() as conn:
        _eligible_character(conn, gid, character_id, player_id)
        previous = conn.execute('''SELECT r.*,t.draw_id,t.numbers_json FROM casino_rounds r
            LEFT JOIN casino_lottery_tickets t ON t.guild_id=r.guild_id AND t.round_id=r.id
            WHERE r.guild_id=? AND r.request_key=?''', (gid,request_key)).fetchone()
        if previous:
            g=conn.execute('SELECT code FROM casino_games WHERE guild_id=? AND id=?',
                           (gid,previous['game_id'])).fetchone()
            if (not g or g['code'] != game_code or previous['character_id'] != character_id or
                previous['payment_source'] != payment_source or previous['draw_id'] != draw_id):
                raise CasinoError('操作編號已被其他購買使用')
            if previous['numbers_json'] is None:
                raise CasinoError('既有購票未完整登記，需人工核對')
            old = json.loads(previous['numbers_json'])
            if numbers is not None and sorted(numbers) != old:
                raise CasinoError('不可修改既有彩票號碼')
            return {'round_id':previous['id'],'draw_id':draw_id,'numbers':old,'duplicate':True}
        game = conn.execute('SELECT * FROM casino_games WHERE guild_id=? AND code=?',
                            (gid,game_code)).fetchone()
        if not game or not game['enabled']:
            raise CasinoError('彩票尚未開放')
        cfg=_settings(dict(game))
        draw=conn.execute('SELECT * FROM casino_lottery_draws WHERE guild_id=? AND id=? AND game_id=?',
                          (gid,draw_id,game['id'])).fetchone()
        if not draw or draw['state']!='selling' or datetime.now(timezone.utc)>=_utc(draw['cutoff_at']):
            raise CasinoError('本期已停止售票')
        if numbers is None:
            selected=sorted(secrets.SystemRandom().sample(range(1,cfg['number_pool']+1),cfg['pick_count']))
        else:
            if (not isinstance(numbers,list) or len(numbers)!=cfg['pick_count'] or
                any(type(n) is not int or not 1<=n<=cfg['number_pool'] for n in numbers) or
                len(set(numbers))!=len(numbers)):
                raise CasinoError('彩票號碼無效或重複')
            selected=sorted(numbers)
        daily=cfg.get('daily_purchase_limit',100)
        if type(daily) is not int or not 1<=daily<=10000:
            raise CasinoError('每日限購設定錯誤')
        used=conn.execute('''SELECT COUNT(*) FROM casino_rounds WHERE guild_id=? AND game_id=?
            AND character_id=? AND status!='cancelled' AND date(created_at)=date('now')''',
            (gid,game['id'],character_id)).fetchone()[0]
        if used>=daily:
            raise CasinoError('今日購買次數已達上限')
        price=_positive(int(game['ticket_price']),'票價')
        payer=_account(conn,gid,'character',str(character_id),payment_source)
        receiver=_account(conn,gid,'system','casino_receipts','treasury')
        payment=_payment(conn,gid,f'casino:buy:{request_key}',payer,receiver,price,f'購買彩票 {game_code}')
        cur=conn.execute('''INSERT INTO casino_rounds(guild_id,game_id,character_id,request_key,game_kind,
            config_version,ticket_price,payment_source,funding_mode,pay_tx_id)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (gid,game['id'],character_id,request_key,'lottery',game['config_version'],price,
             payment_source,game['funding_mode'],payment.transaction_id))
        conn.execute('INSERT INTO casino_lottery_tickets(guild_id,round_id,draw_id,numbers_json) VALUES(?,?,?,?)',
                     (gid,cur.lastrowid,draw_id,json.dumps(selected)))
        return {'round_id':cur.lastrowid,'draw_id':draw_id,'numbers':selected,'duplicate':False}


def draw_lottery(*, guild_id: str | int, draw_id: int) -> dict[str, Any]:
    """到達開獎時間後鎖定號碼；重試一律取得相同號碼。"""
    gid=str(guild_id)
    with transaction() as conn:
        row=conn.execute('''SELECT d.*,g.settings_json,g.kind FROM casino_lottery_draws d
                            JOIN casino_games g ON g.guild_id=d.guild_id AND g.id=d.game_id
                            WHERE d.guild_id=? AND d.id=?''',(gid,draw_id)).fetchone()
        if not row:
            raise CasinoError('找不到期別')
        if row['state']=='drawn':
            return json.loads(row['result_json'])
        if datetime.now(timezone.utc)<_utc(row['draw_at']):
            raise CasinoError('尚未到開獎時間')
        cfg=json.loads(row['settings_json'])
        selected=sorted(secrets.SystemRandom().sample(range(1,cfg['number_pool']+1),cfg['pick_count']))
        outcome={'numbers':selected,'draw_id':draw_id,'draw_code':row['draw_code']}
        conn.execute('''UPDATE casino_lottery_draws SET state='drawn',result_json=?,drawn_at=CURRENT_TIMESTAMP
                        WHERE guild_id=? AND id=? AND state!='drawn' ''',
                     (json.dumps(outcome,sort_keys=True),gid,draw_id))
        return outcome


def settle_lottery_tickets(*, guild_id: str | int, draw_id: int, limit: int = 100) -> dict[str, int]:
    """每次結算一小批；故障後重新呼叫可接續，不會重複付獎。"""
    gid=str(guild_id)
    with get_connection() as conn:
        draw=conn.execute('''SELECT d.*,g.settings_json FROM casino_lottery_draws d
                             JOIN casino_games g ON g.guild_id=d.guild_id AND g.id=d.game_id
                             WHERE d.guild_id=? AND d.id=?''',(gid,draw_id)).fetchone()
        if not draw or draw['state']!='drawn':
            raise CasinoError('本期尚未開獎')
        cfg=json.loads(draw['settings_json'])
        result=json.loads(draw['result_json'])
        tickets=[dict(x) for x in conn.execute('''SELECT t.round_id,t.numbers_json FROM casino_lottery_tickets t
           JOIN casino_rounds r ON r.guild_id=t.guild_id AND r.id=t.round_id
           WHERE t.guild_id=? AND t.draw_id=? AND r.status='pending'
           ORDER BY t.round_id LIMIT ?''',(gid,draw_id,max(1,min(int(limit),500)))).fetchall()]
    done=0
    for ticket in tickets:
        matches=len(set(json.loads(ticket['numbers_json'])) & set(result['numbers']))
        payout=int(cfg['prizes'].get(str(matches),0))
        settle_round(guild_id=gid,round_id=int(ticket['round_id']), expected_kind='lottery',
                     outcome={'draw_id':draw_id,'numbers':result['numbers'],'matches':matches,'coins':payout},
                     reward_coins=payout)
        done+=1
    return {'settled':done,'remaining_unknown':int(done==max(1,min(int(limit),500)))}
