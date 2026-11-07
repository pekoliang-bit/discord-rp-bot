"""已核准 V2.1 大眾印象效果：僅對明確核實的事件判定進行相對百分比調整。

事件處理器先驗證情境與前置條件，再傳入 (標籤編號, 該標籤的效果序號)。
本模組不修改角色屬性、金幣、粉絲、影響指數或既有結算資料。
未指定條件、並無百分比的風格標籤及未持有的標籤絕不生效。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from modules.tag_library import held, numeric_rules, apply_relative, catalog


@dataclass(frozen=True)
class TagEffectResult:
    base: float
    adjusted: float
    positive_percent: int
    negative_percent: int
    sources: tuple[tuple[str, int, str], ...]


def evaluate_tag_effects(guild_id: int | str, character_id: int,
                         base: float, *, stage: str,
                         verified_effects: Iterable[tuple[str, int]],
                         cap: tuple[float, float] | None = None) -> TagEffectResult:
    """計算一個事件階段的標籤加成，不對不同階段套兩次。

    `verified_effects` 是由可信任事件處理器核實情境後傳入的 (編號, 子規則序號)。
    同一事件階段正向僅取最高，負向僅取幅度最大的負效果；正負各取一個。
    不允許以玩家輸入的任意標籤或未經核實的事件文字來觸發效果。
    """
    if not isinstance(stage, str) or not stage.strip():
        raise ValueError('標籤效果需要明確的事件判定階段')
    base = float(base)
    verified = set()
    for code, idx in verified_effects:
        if not isinstance(code, str) or not code.isdigit() or not isinstance(idx, int) or idx < 0:
            raise ValueError('無效的標籤情境憑證')
        verified.add((code.zfill(3), idx))
    if not verified:
        return TagEffectResult(base, base, 0, 0, ())
    approved = {r['標籤名稱']: r for r in catalog() if r['效果審核狀態'] == '已核准'}
    available = []
    for tag in held(guild_id, character_id):
        row = approved.get(tag['name'])
        if not row:
            continue
        for rule in numeric_rules(tag['name']):
            if (row['編號'], rule['rule_index']) in verified and rule['approval'] == '已核准':
                available.append((rule['relative_percent'], row['編號'], rule['rule_index'], rule['context']))
    best_positive = max((rule for rule in available if rule[0] > 0), default=None,
                        key=lambda rule: rule[0])
    worst_negative = min((rule for rule in available if rule[0] < 0), default=None,
                         key=lambda rule: rule[0])
    chosen = [rule for rule in (best_positive, worst_negative) if rule is not None]
    positive = best_positive[0] if best_positive else 0
    negative = worst_negative[0] if worst_negative else 0
    # 分別乘倍率，不將加成當成固定百分點；最高值限制需由判定場景提供。
    result = apply_relative(apply_relative(base, positive), negative)
    if cap is not None:
        low, high = map(float, cap)
        if low > high:
            raise ValueError('標籤判定界線不合法')
        result = max(low, min(high, result))
    return TagEffectResult(base, result, positive, negative,
                           tuple((r[1], r[2], r[3]) for r in chosen))
