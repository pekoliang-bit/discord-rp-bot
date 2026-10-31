"""娛樂城賭場規則引擎：純函式生成／判定遊戲結果（非 Discord Cog）。

此模組不負責從玩家帳戶扣款。正式遊戲必須使用可信任的伺服器端對局、
提交結果封存與原子金幣結算；尤其多人對賭不得直接使用 casino_service.purchase_round。
"""
from __future__ import annotations

import secrets
from collections import Counter
from dataclasses import dataclass
from typing import Any

from modules.casino_service import CasinoError

DEFAULT_BETS = {'minimum': 100, 'maximum': 100_000}


def _bet(bet: int, *, minimum: int = 100, maximum: int = 100_000) -> int:
    if type(bet) is not int or type(minimum) is not int or type(maximum) is not int:
        raise CasinoError('下注必須是整數金幣')
    if minimum <= 0 or not minimum <= bet <= maximum:
        raise CasinoError('下注金額超出遊戲範圍')
    return bet


def new_deck(*, decks: int = 6) -> list[tuple[int, str]]:
    if type(decks) is not int or not 1 <= decks <= 8:
        raise CasinoError('牌組數量不合法')
    cards = [(rank, suit) for _ in range(decks) for suit in ('S', 'H', 'D', 'C') for rank in range(1, 14)]
    secrets.SystemRandom().shuffle(cards)
    return cards


def blackjack_score(cards: list[tuple[int, str]]) -> int:
    if not isinstance(cards, list) or not cards or any(
        not isinstance(card, tuple) or len(card) != 2 or
        type(card[0]) is not int or not 1 <= card[0] <= 13 or card[1] not in ('S','H','D','C')
        for card in cards
    ):
        raise CasinoError('牌面格式錯誤')
    total = sum(11 if rank == 1 else min(rank, 10) for rank, _ in cards)
    aces = sum(rank == 1 for rank, _ in cards)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def blackjack_result(player: list[tuple[int, str]], dealer: list[tuple[int, str]],
                     bet: int) -> dict[str, Any]:
    """payout 為含原下注本金的返還總額。天然21點以3:2淨賠。"""
    _bet(bet)
    p, d = blackjack_score(player), blackjack_score(dealer)
    pn, dn = len(player) == 2 and p == 21, len(dealer) == 2 and d == 21
    if p > 21:
        result, numerator, denominator = 'lose', 0, 1
    elif pn and not dn:
        result, numerator, denominator = 'blackjack', 5, 2
    elif dn and not pn:
        result, numerator, denominator = 'lose', 0, 1
    elif p == d:
        result, numerator, denominator = 'push', 1, 1
    elif d > 21 or p > d:
        result, numerator, denominator = 'win', 2, 1
    else:
        result, numerator, denominator = 'lose', 0, 1
    payout = bet * numerator // denominator
    return {'result': result, 'player_score': p, 'dealer_score': d,
            'payout': payout, 'net': payout-bet, 'bet': bet}


ROULETTE_RED = frozenset((1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36))


def roulette_spin() -> int:
    return secrets.randbelow(37)


def roulette_result(*, number: int, bet: int, choice: str, target: int | None = None) -> dict[str, Any]:
    """歐式單零輪盤：紅黑、奇偶、大小及單號。"""
    _bet(bet)
    if type(number) is not int or not 0 <= number <= 36:
        raise CasinoError('輪盤結果不合法')
    if choice == 'straight':
        if type(target) is not int or not 0 <= target <= 36:
            raise CasinoError('請指定0至36的投注號碼')
        won, net_multiplier = number == target, 35
    elif choice in ('red','black','odd','even','low','high'):
        won = number != 0 and {
            'red': number in ROULETTE_RED,
            'black': number not in ROULETTE_RED,
            'odd': number % 2 == 1,
            'even': number % 2 == 0,
            'low': number <= 18,
            'high': number >= 19,
        }[choice]
        net_multiplier = 1
    else:
        raise CasinoError('不支援的輪盤投注方式')
    payout = bet * (net_multiplier + 1) if won else 0
    return {'number':number,'choice':choice,'target':target,'win':won,
            'bet':bet,'payout':payout,'net':payout-bet}


def roll_sicbo() -> tuple[int,int,int]:
    return tuple(secrets.randbelow(6)+1 for _ in range(3))


def sicbo_result(*, dice: tuple[int,int,int], bet: int, choice: str,
                target: int | None = None) -> dict[str, Any]:
    _bet(bet)
    if not isinstance(dice,tuple) or len(dice)!=3 or any(type(d) is not int or d not in range(1,7) for d in dice):
        raise CasinoError('骰子結果不合法')
    triple = len(set(dice)) == 1
    total = sum(dice)
    if choice in ('small','big'):
        won = not triple and (4 <= total <= 10 if choice=='small' else 11 <= total <= 17)
        multiplier = 1
    elif choice == 'specific_triple':
        if type(target) is not int or not 1 <= target <= 6:
            raise CasinoError('豹子點數必須為1至6')
        won = dice == (target,target,target)
        multiplier = 150
    elif choice == 'any_triple':
        won = triple
        multiplier = 24
    else:
        raise CasinoError('不支援的骰寶下注')
    payout = bet*(multiplier+1) if won else 0
    return {'dice':list(dice),'total':total,'choice':choice,'win':won,
            'bet':bet,'payout':payout,'net':payout-bet}


def slot_spin(*, symbols: list[str], weights: list[int], reels: int = 3) -> list[str]:
    if not isinstance(symbols,list) or not isinstance(weights,list) or not 2<=len(symbols)==len(weights)<=30:
        raise CasinoError('機台圖案設定不合法')
    if any(not isinstance(s,str) or not s or len(s)>60 for s in symbols) or len(set(symbols))!=len(symbols):
        raise CasinoError('圖案無效或重複')
    if any(type(w) is not int or w<=0 for w in weights) or type(reels) is not int or reels not in (3,5):
        raise CasinoError('機台權重或軸數不合法')
    total=sum(weights)
    output=[]
    for _ in range(reels):
        roll=secrets.randbelow(total)
        for symbol, weight in zip(symbols,weights):
            roll-=weight
            if roll<0:
                output.append(symbol)
                break
    return output


def slot_result(*, spun: list[str], bet: int, paytable: dict[str,int]) -> dict[str,Any]:
    """全軸同圖案結算；賠率表是含本金的總回報倍率。"""
    _bet(bet)
    if not isinstance(spun,list) or len(spun) not in (3,5) or any(not isinstance(s,str) for s in spun):
        raise CasinoError('機台結果不合法')
    if not isinstance(paytable,dict) or any(type(x) is not int or not 0<=x<=100000 for x in paytable.values()):
        raise CasinoError('賠率表錯誤')
    multiplier=paytable.get(spun[0],0) if len(set(spun))==1 else 0
    payout=bet*multiplier
    return {'symbols':spun,'bet':bet,'payout':payout,'net':payout-bet}


def bingo_draw(*, pool: int = 75, count: int = 25) -> list[int]:
    if type(pool) is not int or type(count) is not int or not 1<=count<=pool<=100:
        raise CasinoError('賓果開獎數量不合法')
    return secrets.SystemRandom().sample(range(1,pool+1),count)


def bingo_lines(card: list[list[int]], drawn: list[int]) -> int:
    """5x5 賓果：列、行、兩條對角線，沒有自動中央免費格。"""
    if not isinstance(card,list) or len(card)!=5 or any(not isinstance(r,list) or len(r)!=5 for r in card):
        raise CasinoError('賓果卡必須為5x5')
    nums=[x for row in card for x in row]
    if any(type(x) is not int or x<=0 for x in nums) or len(set(nums))!=25:
        raise CasinoError('賓果卡號碼不可重複')
    if not isinstance(drawn,list) or len(set(drawn))!=len(drawn):
        raise CasinoError('開獎結果無效')
    marked=set(drawn)
    lines=card + [[card[r][c] for r in range(5)] for c in range(5)] + [
        [card[i][i] for i in range(5)], [card[i][4-i] for i in range(5)]]
    return sum(all(v in marked for v in line) for line in lines)


def pvp_distribution(stakes: dict[str,int], winners: list[str], *, fee_ppm: int = 0) -> dict[str,Any]:
    """純函式計算多人對賭共同池；不處理實際扣款及授權。"""
    if not isinstance(stakes,dict) or len(stakes)<2 or any(
        not isinstance(uid,str) or not uid or type(amount) is not int or amount<=0
        for uid,amount in stakes.items()
    ) or not isinstance(winners,list) or not winners or len(set(winners))!=len(winners) or any(
        w not in stakes for w in winners
    ):
        raise CasinoError('對賭參與者或贏家不合法')
    if type(fee_ppm) is not int or not 0<=fee_ppm<=1000000:
        raise CasinoError('手續費比例錯誤')
    total=sum(stakes.values())
    fee=total*fee_ppm//1000000
    distributable=total-fee
    # 依贏家投入占比分配，尾差確定性分給第一名（依ID排序）。
    winners=sorted(winners)
    weight=sum(stakes[w] for w in winners)
    payouts={uid:0 for uid in stakes}
    left=distributable
    for winner in winners[1:]:
        amount=distributable*stakes[winner]//weight
        payouts[winner]=amount
        left-=amount
    payouts[winners[0]]=left
    assert sum(payouts.values())+fee==total
    return {'total':total,'fee':fee,'payouts':payouts}
