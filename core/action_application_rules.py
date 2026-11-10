"""行動申請規則與預覽定價（純 Python，Discord 介面共用）。"""
from __future__ import annotations
import re
from typing import Any
from modules.action_reward_rules import RESTRICTED_DIRECT_STATS

class ProposalError(ValueError):
    pass


def parse_rewards(text: str) -> list[dict[str, Any]]:
    """每行一項：容貌:5 或 才智:隨機；最多三項；影響指數永久排除。"""
    cleaned = text.strip()
    if not cleaned or cleaned in ("無", "沒有", "不提供"):
        return []
    entries = [x.strip() for x in re.split(r"[\n,，;；]+", cleaned) if x.strip()]
    if len(entries) > 3:
        raise ProposalError("每項行動最多選擇三種不同數值獎勵")
    seen = set()
    rewards = []
    for entry in entries:
        m = re.fullmatch(r"([^：:]+)[：:]\s*(隨機|随机|\d+)", entry)
        if not m:
            raise ProposalError("請使用『容貌:5』或『才智:隨機』，每行一項")
        name, amount = m.group(1).strip(), m.group(2)
        if name in RESTRICTED_DIRECT_STATS or name == "影響指數":
            raise ProposalError("影響指數及其他管制數值不能作為一般行動獎勵")
        if not name or name in seen:
            raise ProposalError("數值名稱不可空白或重複")
        seen.add(name)
        if amount in ("隨機", "随机"):
            rewards.append({"name": name, "mode": "random", "min": 1, "max": 15})
        elif 1 <= int(amount) <= 10:
            rewards.append({"name": name, "mode": "fixed", "amount": int(amount)})
        else:
            raise ProposalError("固定數值必須為 1～10 點")
    return rewards


def preview_cost(category: str, base_price: int, rewards: list[dict]) -> dict:
    """B 案：執行時依當次實際點數計費；預覽只顯示可能的範圍。"""
    if category == "打工":
        return {"min": 0, "max": 0, "variable": False}
    unit = 300 if category == "活動" else 500
    lower = sum(p["amount"] if p["mode"] == "fixed" else p["min"] for p in rewards)
    upper = sum(p["amount"] if p["mode"] == "fixed" else p["max"] for p in rewards)
    return {"min": max(base_price, unit * lower),
            "max": max(base_price, unit * upper),
            "variable": lower != upper}

