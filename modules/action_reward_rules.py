"""場所行動通用規則：僅檢查預設內容，不代替正式交易及審核。"""
from __future__ import annotations

POINT_POOLS = frozenset({"自由點數", "專業點數", "技能點數"})
POINT_POOL_RULES = {
    "自由點數": "角色的一般合法屬性（不含影響指數）",
    "專業點數": "主攻賽道及其他技能",
    "技能點數": "專業（主攻及其他技能）及個人才藝",
}
RESTRICTED_DIRECT_STATS = frozenset({"影響指數", "流量熱度", "商業價值", "忠粉", "黑粉", "活躍黑粉", "大眾認知度"})
CATEGORIES = frozenset({"打工", "活動", "課程"})


def validate_action(action: dict) -> tuple[bool, str]:
    """校驗最低價格與點數獎勵，不能以此授權執行。"""
    if not isinstance(action, dict):
        return False, "行動必須是物件"
    category = action.get("category")
    if category not in CATEGORIES:
        return False, "未知行動類別"
    fields = ("cost_coins", "reward_coins", "attribute_increase", "action_points_cost")
    values = {}
    for field in fields:
        value = action.get(field, 0)
        if type(value) is not int or value < 0:
            return False, f"{field} 必須是非負整數"
        values[field] = value
    cost = values["cost_coins"]
    earned = values["reward_coins"]
    increase = values["attribute_increase"]
    ap = values["action_points_cost"]
    attr = action.get("attribute")
    if increase and not isinstance(attr, str):
        return False, "增加屬性必須指定欄位"
    if increase and attr in RESTRICTED_DIRECT_STATS:
        return False, "一般行動不可直接增加特殊管制數值"
    health = action.get('health_delta', 0)
    if type(health) is not int:
        return False, '健康變動必須是整數'
    if increase:
        from modules.leaderboard_points_catalog import validate_target
        name = str(action.get('skill_name') or attr)
        if name in RESTRICTED_DIRECT_STATS:
            return False, '一般行動不可直接增加特殊管制數值'
        try:
            validate_target('skill', 'skill', name)
        except ValueError:
            try:
                validate_target('free', 'attribute', name)
            except ValueError:
                return False, '未核准的屬性或技能目標'
    rewards = action.get("point_rewards", {})
    if not isinstance(rewards, dict):
        return False, "point_rewards 必須是物件"
    if any(pool not in POINT_POOLS or type(amount) is not int or amount < 0 for pool, amount in rewards.items()):
        return False, "點數池名稱或獎勵數量不正確"
    # 逐次抽取的多項數值：申請表最多三項，價格在實際交易抽完後計算。
    extra = action.get("stat_rewards", [])
    if not isinstance(extra, list) or len(extra) > 3:
        return False, "單次行動最多三項數值獎勵"
    seen = set()
    for entry in extra:
        if not isinstance(entry, dict):
            return False, "數值獎勵格式錯誤"
        name, mode = entry.get("name"), entry.get("mode")
        if not isinstance(name, str) or not name.strip() or name in seen or name in RESTRICTED_DIRECT_STATS:
            return False, "重複、空白或管制數值不得用於一般行動"
        seen.add(name)
        if mode == "fixed":
            if type(entry.get("amount")) is not int or not 1 <= entry["amount"] <= 10:
                return False, "固定數值必須是1～10"
        elif mode == "random":
            if entry.get("min") != 1 or entry.get("max") != 15:
                return False, "隨機數值只能為1～15"
        else:
            return False, "數值獎勵模式錯誤"
        from modules.leaderboard_points_catalog import validate_target
        try:
            validate_target('skill', 'skill', name)
        except ValueError:
            try:
                validate_target('free', 'attribute', name)
            except ValueError:
                return False, "數值未列入合法屬性或技能"
    total = increase + sum(rewards.values())
    if category == "打工":
        if cost or earned <= 0 or ap != 1:
            return False, "打工必須免費、設定正數金幣薪資、消耗1行動值"
    else:
        if earned:
            return False, "消費活動或課程不得同時發放工作薪資"
        if ap != (1 if category == "活動" else 0):
            return False, "行動值消耗與類別不符"
        floor = 300 if category == "活動" else 500
        if not extra and cost < floor * total:
            return False, f"每點最低收費 {floor} 金幣；可高於底價"
        # 多項屬性按照實際抽取增量計價；舊 cost 作為申請者指定底價。
        # 不因為設定費用低於最低可能值而擋掉整項申請。
    return True, "OK"


def all_valid_actions(actions: list[dict]) -> list[dict]:
    return [
        a for a in actions
        if a.get("approved") is True and validate_action(a)[0]
    ]
