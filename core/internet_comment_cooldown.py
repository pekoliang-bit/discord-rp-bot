"""虛構網友近期冷卻與事件選人銜接（純計算／預覽）。

不會更新 SQLite 或自動發文。由上層先讀取最近出場 ID，
再交給既有 internet_user_selector.select_commenters() 選人。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Mapping

from core.internet_user_selector import (
    SelectionPolicy,
    SelectionResult,
    select_commenters,
)


@dataclass(frozen=True)
class CooldownDecision:
    result: SelectionResult
    recent_ids: frozenset[int]
    blocked_ids: frozenset[int]
    available_count: int


def select_with_cooldown(*, event_id: str, event_type: str,
                         rows: Iterable[Mapping],
                         recent_posted_ids: Iterable[int] = (),
                         policy: SelectionPolicy | None = None,
                         strict_cooldown: bool = False,
                         rng: random.Random | None = None) -> CooldownDecision:
    """利用既有出場選擇器進行一次純預覽。

    strict_cooldown=False: 最近出現者降權（保留老粉回歸機會）。
    strict_cooldown=True: 最近出現者暫時不可入選。
    注意：不能只用此函式做正式發文授權，仍須確認事件與模板。
    """
    policy = policy or SelectionPolicy()
    recent = frozenset(recent_posted_ids)
    if any(type(i) is not int or i <= 0 for i in recent):
        raise ValueError('recent_posted_ids 必須是正整數 ID')
    source_rows = tuple(rows)
    excluded = frozenset(
        int(row['id']) for row in source_rows
        if strict_cooldown and int(row['id']) in recent
    )
    eligible_rows = (
        tuple(row for row in source_rows if int(row['id']) not in recent)
        if strict_cooldown else source_rows
    )
    result = select_commenters(
        event_id, event_type, eligible_rows,
        policy=policy,
        recent_event_ids=recent if not strict_cooldown else (),
        rng=rng,
    )
    return CooldownDecision(
        result=result,
        recent_ids=recent,
        blocked_ids=excluded,
        available_count=len(eligible_rows),
    )


def select_from_history(*, guild_id: str | int, character_id: int,
                        event_id: str, event_type: str,
                        rows: Iterable[Mapping],
                        policy: SelectionPolicy | None = None,
                        strict_cooldown: bool = False,
                        rng: random.Random | None = None) -> CooldownDecision:
    """查最近 N 個已發布事件後選人（唯讀，不寫入任何紀錄）。

    呼叫端必須保證 rows 只含同一伺服器、目標角色、已核准的虛構帳號。
    """
    from core.internet_comment_history import recent_posted_user_ids

    effective = policy or SelectionPolicy()
    recent = recent_posted_user_ids(
        guild_id=guild_id,
        character_id=character_id,
        event_limit=effective.recent_cooldown_events,
    )
    return select_with_cooldown(
        event_id=event_id,
        event_type=event_type,
        rows=rows,
        recent_posted_ids=recent,
        policy=effective,
        strict_cooldown=strict_cooldown,
        rng=rng,
    )
