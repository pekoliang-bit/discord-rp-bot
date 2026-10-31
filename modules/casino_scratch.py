"""娛樂城刮刮樂規則引擎（非 Discord Extension）。

僅提供受信任的伺服器端呼叫；請勿將 outcome/reward 自玩家輸入傳入。
開獎於首次呼叫時固定寫入，重試不重新抽獎。發獎由 casino_service 原子處理。
目前僅對 direct（金幣直接發放）模式開放；商城物品及其他資金模式待結算器接通。
"""
from __future__ import annotations

import json
import secrets
from decimal import Decimal
from typing import Any

from core.database import transaction
from modules.casino_repository import get_game, upsert_game
from modules.casino_service import CasinoError, purchase_round, settle_round

# 正式核定的票名、售價、頭獎與目標 RTP；獎級機率須由管理端輸入並通過驗算才可啟用。
DEFAULT_SCRATCH = (
    ('scratch_star', '幸運新星', 500, 50_000, '0.80'),
    ('scratch_gold', '黃金盛宴', 2_000, 300_000, '0.82'),
    ('scratch_diamond', '鑽石之夢', 5_000, 1_000_000, '0.85'),
    ('scratch_royal', '皇家寶藏', 10_000, 3_000_000, '0.85'),
    ('scratch_crown', '命運之冠', 50_000, 15_000_000, '0.88'),
)
MILLION = 1_000_000


def _weights(settings: dict[str, Any], price: int, *, max_reward: int) -> tuple[list[dict[str, Any]], int]:
    """以整數百萬分比避免機率累加的浮點誤差；驗證含銘謝惠顧。"""
    prizes = settings.get('prizes')
    if not isinstance(prizes, list) or not 2 <= len(prizes) <= 11:
        raise CasinoError('刮刮樂需有2至10個有獎獎級，並可另設銘謝惠顧')
    seen: set[str] = set()
    cumulative = 0
    nonzero = 0
    empty = False
    output = []
    expected = Decimal(0)
    for item in prizes:
        if not isinstance(item, dict):
            raise CasinoError('獎級格式錯誤')
        code = item.get('code')
        weight, coins = item.get('weight'), item.get('coins')
        if not isinstance(code, str) or not code or code in seen:
            raise CasinoError('獎級代碼重複或空白')
        if type(weight) is not int or weight < 0 or weight > MILLION:
            raise CasinoError('中獎權重必須為0至1000000的整數')
        if type(coins) is not int or coins < 0 or coins > max_reward:
            raise CasinoError('獎金超過核定上限')
        if code == 'no_prize':
            if coins != 0:
                raise CasinoError('銘謝惠顧獎金只能為零')
            empty = True
        elif coins == 0:
            raise CasinoError('有獎獎級的獎金必須大於零')
        else:
            nonzero += 1
        seen.add(code)
        cumulative += weight
        expected += Decimal(weight) * Decimal(coins) / MILLION
        output.append({'code': code, 'name': str(item.get('name') or ('銘謝惠顧' if code == 'no_prize' else code))[:80],
                       'weight': weight, 'coins': coins})
    if cumulative != MILLION or nonzero < 2:
        raise CasinoError('機率合計必須為100%，且至少有兩個有獎獎級')
    if not empty:
        raise CasinoError('每款預設刮刮樂都須包含銘謝惠顧（可設定0%）')
    cap = settings.get('rtp_max')
    floor = settings.get('rtp_min')
    rtp = expected / Decimal(price)
    if cap is not None and rtp > Decimal(str(cap)):
        raise CasinoError('獎表 RTP 超過上限')
    if floor is not None and rtp < Decimal(str(floor)):
        raise CasinoError('獎表 RTP 低於下限')
    return output, int((rtp * MILLION).to_integral_value())


def validate_scratch(game: dict[str, Any]) -> dict[str, Any]:
    if game['kind'] != 'scratch':
        raise CasinoError('不是刮刮樂遊戲')
    if game['funding_mode'] != 'direct':
        raise CasinoError('此獎金來源尚未接通安全發獎流程')
    settings = json.loads(game['settings_json'])
    if not isinstance(settings, dict):
        raise CasinoError('設定格式錯誤')
    if settings.get('model', 'independent') != 'independent':
        raise CasinoError('有限獎票模式尚未連接原子獎票庫存，不可購買')
    if not settings.get('rules_verified'):
        raise CasinoError('獎表尚未核准，不可販售')
    prizes, rtp = _weights(settings, int(game['ticket_price']), max_reward=int(settings.get('max_single_reward', 0)))
    return {'prizes': prizes, 'rtp_ppm': rtp}


def install_default_scratch(guild_id: str | int, *, actor_id: str | int = 'system') -> list[dict[str, Any]]:
    """建立五款*停用*預設票種；先由管理員完成獎級機率才可啟用。"""
    results = []
    for code, name, price, jackpot, target in DEFAULT_SCRATCH:
        if get_game(guild_id, code):
            continue  # 禁止重置已編輯票種及版本
        results.append(upsert_game(guild_id=guild_id, code=code, name=name, kind='scratch',
                                   ticket_price=price, funding_mode='direct', enabled=False,
                                   actor_id=actor_id, settings={'model': 'independent', 'target_rtp': target,
                                   'max_single_reward': jackpot, 'rules_verified': False,
                                   'daily_purchase_limit': 20,
                                   'prizes': [], 'announcements': {'jackpot': True}}))
    return results


def scratch_ticket(*, guild_id: str | int, player_id: str | int, character_id: int,
                   game_code: str, request_key: str, payment_source: str = 'wallet') -> dict[str, Any]:
    """購票後抽取並永久封存結果；結算失敗可使用相同 request_key 續跑。"""
    game = get_game(guild_id, game_code)
    if not game:
        raise CasinoError('找不到刮刮樂')
    validated = validate_scratch(game)
    round_row = purchase_round(guild_id=guild_id, player_id=player_id, character_id=character_id,
                               game_code=game_code, request_key=request_key, payment_source=payment_source)
    gid = str(guild_id)
    with transaction() as conn:
        row = conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND id=?', (gid, round_row['id'])).fetchone()
        if int(row['config_version']) != int(game['config_version']):
            raise CasinoError('票券購買時的獎表版本已變動，需人工核對')
        if row['status'] == 'cancelled':
            raise CasinoError('票券已取消')
        sealed = conn.execute('''SELECT details_json FROM casino_audit
            WHERE guild_id=? AND action='scratch_sealed' AND ref_key=?''', (gid, str(row['id']))).fetchone()
        if sealed:
            outcome = json.loads(sealed['details_json'])
        elif row['status'] == 'settled':
            outcome = json.loads(row['outcome_json'])
        else:
            roll = secrets.randbelow(MILLION)
            running = 0
            selected = None
            for prize in validated['prizes']:
                running += prize['weight']
                if roll < running:
                    selected = prize
                    break
            if selected is None:
                raise CasinoError('刮刮樂機率表損壞')
            outcome = {'prize_code': selected['code'], 'prize_name': selected['name'],
                       'coins': selected['coins'], 'reveal': 'scratch',
                       'jackpot': selected['code'] == 'jackpot'}
            conn.execute('INSERT INTO casino_audit(guild_id,actor_id,action,ref_key,details_json) VALUES(?,?,?,?,?)',
                         (gid, 'system', 'scratch_sealed', str(row['id']), json.dumps(outcome, ensure_ascii=False, sort_keys=True)))
    return settle_round(guild_id=gid, round_id=int(round_row['id']), outcome=outcome,
                        reward_coins=int(outcome['coins']), expected_kind='scratch')
