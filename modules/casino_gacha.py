"""娛樂城抽抽樂：機率、保底、商品獎池驗證及安全金幣池執行。

非 Discord Extension。商城物品出貨尚未接通時，商品池禁止購買；不會扣款後只留下待領商品。
管理設定應由已授權的後台保存，玩家不可自選獎品或稀有度。
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from core.database import transaction
from modules.casino_repository import get_game, upsert_game
from modules.casino_service import CasinoError, purchase_round, settle_round

RARITIES = ('N', 'R', 'SR', 'SSR', 'UR')
DEFAULT_WEIGHTS = {'N': 500000, 'R': 300000, 'SR': 150000, 'SSR': 40000, 'UR': 10000}
DEFAULT_POOLS = (
    ('gacha_daily', '日常驚喜', 500),
    ('gacha_premium', '精品收藏', 2000),
    ('gacha_royal', '皇家珍藏', 5000),
    ('gacha_limited', '限定夢幻', 10000),
)


def install_default_gacha(guild_id: str | int, *, actor_id: str | int = 'system') -> list[dict[str, Any]]:
    """只新增缺少的停用池；永不覆蓋管理員已配置獎池。"""
    output = []
    for code, name, price in DEFAULT_POOLS:
        if get_game(guild_id, code):
            continue
        output.append(upsert_game(
            guild_id=guild_id, code=code, name=name, kind='gacha', ticket_price=price,
            funding_mode='direct', enabled=False, actor_id=actor_id,
            settings={'rules_verified': False, 'pool_family': code,
                      'rarity_weights': DEFAULT_WEIGHTS, 'prizes': [],
                      'daily_purchase_limit': 20, 'max_single_reward': 0,
                      'guarantee_sr_ten': True, 'ssr_pity': 50, 'ur_pity': 100},
        ))
    return output


def validate_gacha(game: dict[str, Any]) -> dict[str, Any]:
    """嚴格驗證池配置；獎品必須是後台核准的金幣或商城商品 ID。"""
    if game.get('kind') != 'gacha':
        raise CasinoError('不是抽抽樂獎池')
    if game.get('funding_mode') != 'direct':
        raise CasinoError('非直接發獎模式尚未接通')
    try:
        config = json.loads(game['settings_json'])
    except (ValueError, TypeError, KeyError) as exc:
        raise CasinoError('抽抽樂設定損壞') from exc
    if not isinstance(config, dict) or not config.get('rules_verified'):
        raise CasinoError('抽抽樂獎池尚未核准')
    weights = config.get('rarity_weights')
    if not isinstance(weights, dict) or set(weights) != set(RARITIES) or any(
        type(v) is not int or v < 0 for v in weights.values()
    ) or sum(weights.values()) != 1_000_000:
        raise CasinoError('稀有度機率必須為整數百萬分比且合計100%')
    family = config.get('pool_family')
    if not isinstance(family, str) or not 1 <= len(family) <= 80:
        raise CasinoError('保底系列代碼不合法')
    for key, maximum in (('ssr_pity', 10000), ('ur_pity', 10000)):
        if type(config.get(key)) is not int or not 1 <= config[key] <= maximum:
            raise CasinoError(f'{key}不合法')
    prizes = config.get('prizes')
    if not isinstance(prizes, list) or not prizes:
        raise CasinoError('尚未設定獎品')
    normalized = []
    for item in prizes:
        if not isinstance(item, dict) or item.get('rarity') not in RARITIES:
            raise CasinoError('獎品稀有度不合法')
        kind = item.get('type')
        if kind not in ('coins', 'official_shop_item'):
            raise CasinoError('只能設定金幣或官方商城商品')
        weight = item.get('weight')
        if type(weight) is not int or weight <= 0:
            raise CasinoError('獎品權重必須大於零')
        if kind == 'coins':
            amount = item.get('coins')
            if type(amount) is not int or not 0 < amount <= config.get('max_single_reward', 0):
                raise CasinoError('金幣獎品超過核准上限')
        else:
            # 實際商城商品是否仍有效，以及可否安全入背包，必須由商城橋接層檢查。
            if not isinstance(item.get('item_id'), (str, int)) or not str(item['item_id']).strip():
                raise CasinoError('官方商城商品編號不可空白')
        normalized.append(dict(item))
    for rarity, chance in weights.items():
        if chance and not any(p['rarity'] == rarity for p in normalized):
            raise CasinoError(f'{rarity}有抽取機率卻沒有對應獎品')
    return {'config': config, 'prizes': normalized, 'weights': weights,
            'has_items': any(p['type'] == 'official_shop_item' for p in normalized)}


def _weighted(options: list[tuple[Any, int]]) -> Any:
    total = sum(weight for _, weight in options)
    if total <= 0:
        raise CasinoError('獎池無可抽選的內容')
    roll = secrets.randbelow(total)
    for value, weight in options:
        roll -= weight
        if roll < 0:
            return value
    raise CasinoError('抽獎結果異常')


def _roll_reward(spec: dict[str, Any], since_ssr: int, since_ur: int,
                 *, minimum: str = 'N') -> dict[str, Any]:
    cfg, weights, prizes = spec['config'], spec['weights'], spec['prizes']
    # 強制保底時替換當次結果，不額外生成第十一件商品。
    required = ('UR' if since_ur + 1 >= cfg['ur_pity'] else
                'SSR' if since_ssr + 1 >= cfg['ssr_pity'] else minimum)
    rank = RARITIES.index(required)
    choices = [(r, weights[r]) for r in RARITIES[rank:] if weights[r] > 0]
    if not choices:
        # 若管理員禁用高稀有度的自然機率，保底仍須有該獎品才能生效。
        choices = [(r, 1) for r in RARITIES[rank:] if any(x['rarity'] == r for x in prizes)]
    rarity = _weighted(choices)
    candidates = [(p, p['weight']) for p in prizes if p['rarity'] == rarity]
    return dict(_weighted(candidates))


def draw_gacha(*, guild_id: str | int, player_id: str | int, character_id: int,
               game_code: str, request_key: str, payment_source: str = 'wallet') -> dict[str, Any]:
    """單抽安全版：金幣獎池可結算；商城商品池在接入背包發貨前禁止扣款。

    保底數據和結果先封存；同一 request_key 重試不會增加保底計數。
    """
    game = get_game(guild_id, game_code)
    if not game:
        raise CasinoError('找不到抽抽樂獎池')
    spec = validate_gacha(game)
    if spec['has_items']:
        raise CasinoError('此獎池包含官方商城物品，需等待背包出貨橋接完成後才能開放購買')
    gid = str(guild_id)
    family = spec['config']['pool_family']
    purchased = purchase_round(guild_id=gid, player_id=player_id, character_id=character_id,
                               game_code=game_code, request_key=request_key,
                               payment_source=payment_source)
    round_id = purchased['id']
    with transaction() as conn:
        row = conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND id=?',
                           (gid, round_id)).fetchone()
        if int(row['config_version']) != int(game['config_version']):
            raise CasinoError('獎池版本變動，已購買的對局需人工核對')
        sealed = conn.execute('SELECT details_json FROM casino_audit WHERE guild_id=? AND action=? AND ref_key=?',
                              (gid, 'gacha_sealed', str(round_id))).fetchone()
        if sealed:
            outcome = json.loads(sealed['details_json'])
        elif row['status'] == 'settled':
            outcome = json.loads(row['outcome_json'])
        elif row['status'] == 'pending':
            current = conn.execute('SELECT since_ssr,since_ur FROM casino_pity WHERE guild_id=? AND character_id=? AND pool_family=?',
                                   (gid, character_id, family)).fetchone()
            ssr, ur = (int(current['since_ssr']), int(current['since_ur'])) if current else (0, 0)
            prize = _roll_reward(spec, ssr, ur)
            rarity = prize['rarity']
            new_ssr = 0 if RARITIES.index(rarity) >= 3 else ssr + 1
            new_ur = 0 if rarity == 'UR' else ur + 1
            conn.execute('INSERT INTO casino_pity(guild_id,character_id,pool_family,since_ssr,since_ur) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,character_id,pool_family) DO UPDATE SET since_ssr=excluded.since_ssr,since_ur=excluded.since_ur,updated_at=CURRENT_TIMESTAMP',
                         (gid, character_id, family, new_ssr, new_ur))
            outcome = {'rarity': rarity, 'type': 'coins', 'coins': prize['coins'],
                       'pool_family': family, 'prize_code': str(prize.get('code', rarity))}
            conn.execute('INSERT INTO casino_audit(guild_id,actor_id,action,ref_key,details_json) VALUES(?,?,?,?,?)',
                         (gid, 'system', 'gacha_sealed', str(round_id),
                          json.dumps(outcome, sort_keys=True, ensure_ascii=False)))
        else:
            raise CasinoError('此抽獎對局無法結算')
    return settle_round(guild_id=gid, round_id=round_id, outcome=outcome,
                        reward_coins=outcome['coins'], expected_kind='gacha')


def draw_ten(*, guild_id: str | int, player_id: str | int, character_id: int,
             game_code: str, batch_key: str, payment_source: str = 'wallet') -> list[dict[str, Any]]:
    """十連抽，每抽有固定唯一鍵；在商品池接線前同樣禁止購買。

    注意：十連保證需完整原子批次結算；目前不提供開放式十連，避免只完成部分抽取。
    """
    raise CasinoError('十連保底需等待原子批次購買模組完成，目前不可開放')
