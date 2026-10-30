"""Discord RP 虛構網友出場選擇器（純計算，無副作用）。"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable, Mapping

RELATIONS = frozenset({'neutral', 'fan', 'superfan', 'fansite', 'solo_fan', 'anti', 'cp_fan'})
EVENT_BONUSES = {
    'daily': {'neutral': 1.3, 'fan': 1.2},
    'performance': {'fan': 1.4, 'superfan': 1.8, 'fansite': 2.0, 'solo_fan': 1.5},
    'birthday': {'fan': 1.6, 'superfan': 2.1, 'fansite': 1.7, 'solo_fan': 1.8},
    'work_release': {'fan': 1.5, 'superfan': 1.7, 'fansite': 1.3},
    'brand': {'fan': 1.3, 'superfan': 1.4, 'neutral': 1.2},
    'controversy': {'anti': 1.8, 'neutral': 1.7, 'fan': 1.2},
    'public_response': {'anti': 1.5, 'neutral': 1.6, 'fan': 1.3},
    'trending': {'neutral': 2.0, 'anti': 1.3, 'fan': 1.2},
    'fan_event': {'fan': 1.8, 'superfan': 2.1, 'fansite': 1.8, 'solo_fan': 1.8},
    'cp_event': {'cp_fan': 2.2, 'neutral': 1.1},
}
EVENT_TYPES = frozenset(EVENT_BONUSES)

@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    max_per_event: int = 12
    max_anti_ratio: float = 0.25
    recent_cooldown_events: int = 3
    unique_per_event: bool = True
    allow_anti: bool = True
    min_weight: float = 0.01

@dataclass(frozen=True, slots=True)
class VirtualCommenter:
    id: int
    username: str
    relation_type: str
    familiarity: int
    appearance_weight: int
    influence: int = 0
    last_appearance_at: str | None = None
    appearance_count: int = 0

@dataclass(frozen=True, slots=True)
class SelectionResult:
    event_id: str
    event_type: str
    selected: tuple[VirtualCommenter, ...]
    considered: int
    excluded: int
    weights: Mapping[int, float] = field(default_factory=dict)

def _integer(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} 必須是 {low}～{high} 的整數')
    return value

def validate_policy(policy: SelectionPolicy) -> None:
    _integer(policy.max_per_event, 'max_per_event', 0, 100)
    _integer(policy.recent_cooldown_events, 'recent_cooldown_events', 0, 100)
    if (not isinstance(policy.max_anti_ratio, (float, int))
            or isinstance(policy.max_anti_ratio, bool)
            or not math.isfinite(policy.max_anti_ratio)
            or not 0 <= policy.max_anti_ratio <= 1):
        raise ValueError('max_anti_ratio 必須是 0～1')
    if (not isinstance(policy.min_weight, (float, int))
            or isinstance(policy.min_weight, bool)
            or not math.isfinite(policy.min_weight)
            or policy.min_weight < 0):
        raise ValueError('min_weight 必須是非負有限數值')

def normalize_commenter(row: Mapping) -> VirtualCommenter:
    uid = _integer(row['id'], '網友 ID', 1, 10**15)
    name = str(row['username']).strip()
    if not name:
        raise ValueError('網名不可為空')
    relation = str(row['relation_type'])
    if relation not in RELATIONS:
        raise ValueError(f'無效網友關係：{relation}')
    return VirtualCommenter(
        id=uid, username=name, relation_type=relation,
        familiarity=_integer(row.get('familiarity', 0), '熟悉度', 0, 100),
        appearance_weight=_integer(row.get('appearance_weight', 20), '出現權重', 0, 100),
        influence=_integer(row.get('influence', 0), '影響力', 0, 100),
        last_appearance_at=row.get('last_appearance_at'),
        appearance_count=_integer(row.get('appearance_count', 0), '出現次數', 0, 10**12),
    )

def calculate_weight(
    commenter: VirtualCommenter,
    event_type: str,
    *,
    recent_event_ids: Iterable[int] = (),
) -> float:
    if event_type not in EVENT_TYPES:
        raise ValueError(f'未知事件類型：{event_type}')
    if commenter.appearance_weight == 0:
        return 0.0
    weight = (
        commenter.appearance_weight
        * (1 + commenter.familiarity / 200)
        * (1 + commenter.influence / 500)
        * EVENT_BONUSES[event_type].get(commenter.relation_type, 1.0)
    )
    if commenter.id in recent_event_ids:
        weight *= 0.2
    return weight

def _weighted_pick(candidates: list[VirtualCommenter], weights: list[float], rng: random.Random) -> int:
    total = sum(weights)
    if total <= 0:
        raise ValueError('沒有正權重候選人')
    threshold = rng.random() * total
    subtotal = 0.0
    for index, weight in enumerate(weights):
        subtotal += weight
        if threshold < subtotal:
            return index
    return len(weights) - 1

def select_commenters(
    event_id: str,
    event_type: str,
    rows: Iterable[Mapping],
    *,
    policy: SelectionPolicy | None = None,
    recent_event_ids: Iterable[int] = (),
    rng: random.Random | None = None,
) -> SelectionResult:
    event_id = str(event_id).strip()
    if not event_id:
        raise ValueError('event_id 不可為空')
    if event_type not in EVENT_TYPES:
        raise ValueError(f'不支援事件：{event_type}')
    policy = policy or SelectionPolicy()
    validate_policy(policy)
    generator = rng or random.SystemRandom()
    recent = frozenset(recent_event_ids)
    candidates: list[VirtualCommenter] = []
    weights: dict[int, float] = {}
    seen_ids: set[int] = set()
    considered = 0
    excluded = 0
    for row in rows:
        considered += 1
        if not bool(row.get('active', 1)):
            excluded += 1
            continue
        candidate = normalize_commenter(row)
        if candidate.id in seen_ids:
            excluded += 1
            continue
        seen_ids.add(candidate.id)
        if candidate.relation_type == 'anti' and not policy.allow_anti:
            excluded += 1
            continue
        weight = calculate_weight(candidate, event_type, recent_event_ids=recent)
        if weight <= policy.min_weight:
            excluded += 1
            continue
        candidates.append(candidate)
        weights[candidate.id] = weight
    max_slots = min(policy.max_per_event, len(candidates))
    anti_limit = math.floor(max_slots * policy.max_anti_ratio)
    chosen: list[VirtualCommenter] = []
    anti_count = 0
    pool = list(candidates)
    while pool and len(chosen) < max_slots:
        eligible = [c for c in pool if c.relation_type != 'anti' or anti_count < anti_limit]
        if not eligible:
            break
        index = _weighted_pick(eligible, [weights[c.id] for c in eligible], generator)
        picked = eligible[index]
        chosen.append(picked)
        if picked.relation_type == 'anti':
            anti_count += 1
        pool = [c for c in pool if c.id != picked.id]
    return SelectionResult(
        event_id=event_id,
        event_type=event_type,
        selected=tuple(chosen),
        considered=considered,
        excluded=excluded,
        weights=weights,
    )
