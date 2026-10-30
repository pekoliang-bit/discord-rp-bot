"""傳聞官方規則設定服務（後端，非 Discord extension）。

位置：core/rumor_official_policy_service.py

此服務由 /官方 的群主身分驗證後呼叫；本檔不註冊額外斜線指令。
規則登錄與啟用是獨立操作，不能將未核准設定直接套用到案件。
設定變動只影響新案件，既有案件保留自己的規則／評級快照。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.rumor_grade_policy import (
    register_grade_policy,
    activate_grade_policy,
    get_active_grade_policy,
)
from core.rumor_tier_policy import set_tier_policy
from core.rumor_rule_registry import (
    register_rules,
    activate_rules,
    get_active_rules,
)
from core.rumor_reward_planner import RuleConfig


@dataclass(frozen=True)
class OfficialPolicyStatus:
    guild_id: str
    reward_version: str | None
    grade_version: str | None
    ready: bool
    missing: tuple[str, ...]


def _require_official(guild_id: str | int, *, authorized: bool) -> str:
    """authorized 必須由 Discord 入口以實際 guild.owner_id 核定。"""
    if not authorized:
        raise PermissionError("此設定僅限群主操作")
    gid = str(guild_id).strip()
    if not gid:
        raise ValueError("伺服器編號不可空白")
    return gid


def register_reward_version(
    guild_id: str | int, rules: RuleConfig, *, authorized: bool = False
) -> str:
    gid = _require_official(guild_id, authorized=authorized)
    return register_rules(gid, rules, trusted=True)


def enable_reward_version(
    guild_id: str | int, version: str, *, authorized: bool = False
) -> None:
    gid = _require_official(guild_id, authorized=authorized)
    activate_rules(gid, version, trusted=True)


def register_grade_version(
    guild_id: str | int,
    version: str,
    thresholds: Mapping[str, int],
    *, authorized: bool = False,
) -> str:
    gid = _require_official(guild_id, authorized=authorized)
    policy = register_grade_policy(gid, version, thresholds, trusted=True)
    return policy.digest


def enable_grade_version(
    guild_id: str | int, version: str, *, authorized: bool = False
) -> None:
    gid = _require_official(guild_id, authorized=authorized)
    activate_grade_policy(gid, version, trusted=True)


def configure_default_tier(
    guild_id: str | int,
    category: str,
    source_type: str,
    tier: int,
    *, authorized: bool = False,
) -> int:
    gid = _require_official(guild_id, authorized=authorized)
    return set_tier_policy(gid, category, source_type, tier, trusted=True)


def read_policy_status(guild_id: str | int) -> OfficialPolicyStatus:
    """唯讀查詢；不將缺少設定自動補上預設值。"""
    gid = str(guild_id).strip()
    if not gid:
        raise ValueError("伺服器編號不可空白")

    missing = []
    reward_version = None
    grade_version = None
    try:
        reward_version = get_active_rules(gid).version
    except LookupError:
        missing.append("獎懲規則版本")
    try:
        grade_version = get_active_grade_policy(gid).version
    except LookupError:
        missing.append("公關評級門檻版本")

    return OfficialPolicyStatus(
        guild_id=gid,
        reward_version=reward_version,
        grade_version=grade_version,
        ready=not missing,
        missing=tuple(missing),
    )
