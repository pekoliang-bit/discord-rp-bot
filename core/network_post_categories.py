"""公開論壇標籤與已核准網路評論庫的十二個主分類之間的純映射。

這個模組不得操作帳號、建立資料表或寫入玩家數值；社交資料由
``core.network_social_posts`` 負責。未知論壇標籤不觸發系統評論。
"""
from __future__ import annotations

from collections.abc import Iterable

SUGGESTED_TAGS: tuple[str, ...] = (
    '日常生活',
    '影視作品',
    '演藝活動',
    '商業活動',
    '粉絲互動',
    '感情關係／CP',
    '爭議事件',
    '公關回應',
    '熱搜話題',
    '營銷號',
    '社會議題',
    '特殊事件',
)

_CATEGORY_IDS = {name: index for index, name in enumerate(SUGGESTED_TAGS, 1)}


def classify_forum_tags(tags: Iterable[str]) -> tuple[int, ...]:
    """依評論庫分類編號回傳已辨認標籤，去重並按分類編號排序。

    比對前僅去除前後空白及裝飾用表情；不進行任意模糊匹配，以免
    非官方標籤不小心觸發未經核准的評論內容。
    """
    if isinstance(tags, str):
        tags = (tags,)
    matched: set[int] = set()
    for raw in tags:
        value = str(raw or '').strip()
        if value in _CATEGORY_IDS:
            matched.add(_CATEGORY_IDS[value])
            continue
        # Discord 使用者可能將裝飾性 emoji 放在標籤開頭。
        for name, category_id in _CATEGORY_IDS.items():
            if value.endswith(name):
                prefix = value[:-len(name)].strip()
                if prefix and not any(char.isalnum() for char in prefix):
                    matched.add(category_id)
                break
    return tuple(sorted(matched))
