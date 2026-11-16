"""官方地點生日禮物資料讀取器（不修改原始世界觀／玩家商店）。

資料檔預設停用。只有在核准行動與實際商品代碼完成對應後，
才可啟用買 X 送 Y；純紀念禮物不在本模組自行發放。
"""
from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache

SOURCE = Path(__file__).resolve().parent.parent / "assets" / "birthday_locations" / "official_birthday_gifts.json"
OFFICIAL_GUILD = "1544051203493601380"

@lru_cache(maxsize=1)
def load():
    data = json.loads(SOURCE.read_text(encoding="utf-8-sig"))
    if data.get("scope") != "official_locations_only":
        raise ValueError("生日資料必須只包含官方地點")
    places = {}
    for item in data.get("locations", []):
        key = str(item.get("location_key", ""))
        if not key or key in places or item.get("official_only") is not True:
            raise ValueError("官方生日地點代碼重複或內容不合法")
        places[key] = item
    return places


def location(guild_id, location_key, channel_id=None):
    if str(guild_id) != OFFICIAL_GUILD:
        return None
    item = load().get(str(location_key))
    if item is None or (channel_id is not None and str(channel_id) != str(item.get("channel_id"))):
        return None
    return item


def eligible_bundle(guild_id, place, action):
    """只回傳已啟用且綁定已核准行動的可購買福利。"""
    if not isinstance(place, dict) or not isinstance(action, dict):
        return None
    item = location(guild_id, place.get("key"), place.get("channel_id"))
    if not item or item.get("enabled") is not True:
        return None
    gift = item.get("gift") or {}
    if gift.get("kind") != "buy_x_get_y" or action.get("approved") is not True:
        return None
    key = gift.get("action_key")
    # 不以地點名稱或字串猜測可結算的商品／課程。
    if not key or str(key) not in {str(action.get("key") or ""), str(action.get("name") or "")}:
        return None
    paid, bonus = gift.get("paid_count"), gift.get("bonus_count")
    if type(paid) is not int or type(bonus) is not int or paid < 1 or bonus < 1:
        return None
    if int(item.get("per_anniversary", 0)) != 1 or int(item.get("claim_window_hours", 0)) != 72:
        return None
    return {"pay_count": paid, "bonus_count": bonus, "title": str(gift.get("description", "生日地點福利"))}
