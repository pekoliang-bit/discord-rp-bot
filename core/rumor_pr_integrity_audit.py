"""付費公關資料一致性唯讀稽核。

位置 core/rumor_pr_integrity_audit.py
不初始化資料表、不扣費、不發獎、不修改案件，也不新增指令。
只有經權限驗證的後端才應調用；不回傳其他玩家的敏感明細。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from core.database import get_connection

_REQUIRED = frozenset({"characters", "rp_company_rumors", "rp_rumor_pr_charges", "rp_rumor_pr_effects"})
_MULTIPLIERS = (Decimal("1"), Decimal("0.75"), Decimal("0.5"), Decimal("0.25"), Decimal("0.1"))


@dataclass(frozen=True)
class PRIntegrityReport:
    guild_id: str
    rumor_id: int
    character_id: int
    charge_count: int
    effect_count: int
    total_score: int
    coins_spent: int
    paid_actions_spent: int
    issues: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return not self.issues


def audit_pr_case(guild_id, rumor_id, character_id, *, trusted=False) -> PRIntegrityReport:
    """檢查已保存的付費行動及效果是否一對一且內容一致。"""
    if not trusted:
        raise PermissionError("公關稽核僅供已授權後端使用")
    gid = str(guild_id).strip()
    if not gid or any(type(v) is not int or v <= 0 for v in (rumor_id, character_id)):
        raise ValueError("伺服器、案件或角色編號無效")

    with get_connection() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        absent = sorted(_REQUIRED - tables)
        if absent:
            return PRIntegrityReport(gid, rumor_id, character_id, 0, 0, 0, 0, 0,
                                     tuple(f"缺少資料表：{name}" for name in absent))

        case = db.execute("SELECT character_id FROM rp_company_rumors WHERE guild_id=? AND id=?",
                          (gid, rumor_id)).fetchone()
        if case is None or int(case["character_id"]) != character_id:
            raise LookupError("傳聞與角色歸屬不符")

        charges = db.execute("""
            SELECT id, strategy, resource, cost, repeat_count, effectiveness
            FROM rp_rumor_pr_charges
            WHERE guild_id=? AND rumor_id=? AND character_id=? ORDER BY id
        """, (gid, rumor_id, character_id)).fetchall()
        effects = db.execute("""
            SELECT action_id, strategy, base_score, multiplier, effective_score
            FROM rp_rumor_pr_effects
            WHERE guild_id=? AND rumor_id=? AND character_id=? ORDER BY action_id
        """, (gid, rumor_id, character_id)).fetchall()

    issues = []
    by_action = {}
    for row in effects:
        aid = int(row["action_id"])
        if aid in by_action:
            issues.append(f"效果重複：#{aid}")
        by_action[aid] = row

    known = set()
    score = coins = actions = 0
    for charge in charges:
        aid = int(charge["id"])
        known.add(aid)
        effect = by_action.get(aid)
        if effect is None:
            issues.append(f"缺少效果：#{aid}")
            continue
        if charge["strategy"] != effect["strategy"]:
            issues.append(f"策略不一致：#{aid}")
        try:
            mult = Decimal(str(charge["effectiveness"]))
            actual_mult = Decimal(str(effect["multiplier"]))
            base = int(effect["base_score"])
            actual_score = int(effect["effective_score"])
            expected = int((Decimal(base) * mult).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if mult not in _MULTIPLIERS or mult != actual_mult or not 0 <= base <= 10000 or expected != actual_score:
                issues.append(f"效果計算不一致：#{aid}")
            else:
                score += actual_score
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            issues.append(f"效果數字無效：#{aid}")
        try:
            cost = int(charge["cost"])
            repeat = int(charge["repeat_count"])
            if cost <= 0 or repeat <= 0:
                issues.append(f"扣費資料無效：#{aid}")
            elif charge["resource"] == "coins":
                coins += cost
            elif charge["resource"] == "paid_actions":
                actions += cost
            else:
                issues.append(f"未知付款資源：#{aid}")
        except (TypeError, ValueError):
            issues.append(f"扣費資料無效：#{aid}")

    for aid in sorted(by_action.keys() - known):
        issues.append(f"孤兒效果：#{aid}")

    return PRIntegrityReport(gid, rumor_id, character_id, len(charges), len(effects),
                             score, coins, actions, tuple(issues))
