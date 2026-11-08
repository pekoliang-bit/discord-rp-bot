"""論壇標籤 ↔ 已核准 960 則網友評論的 12 個正式主分類。

論壇標籤由 Discord 群主設定，本模組不自動更動論壇標籤。
未匹配的標籤不會猜測成某種評論類別。
"""
from __future__ import annotations
from core.comment_library import CATEGORIES

# 每一項都是現有評論庫的原生分類；可以在 Discord 論壇直接建立同名標籤。
SUGGESTED_TAGS = tuple(CATEGORIES[i] for i in range(1,13))

def category_id_for_tag(name):
    normalized=str(name or '').strip()
    for cid,exact in CATEGORIES.items():
        if normalized==exact or normalized.startswith(exact+'｜') or normalized.startswith(exact+' '):
            return cid
    return None

def classify_forum_tags(tag_names):
    ids={category_id_for_tag(name) for name in tag_names}
    ids.discard(None)
    # 複合標籤先保留多分類，不自行抽選敏感／未驗證評論。
    return tuple(sorted(ids))
