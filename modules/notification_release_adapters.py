"""第24檔：其餘八類正式內容更新的安全登錄入口。

各業務模組在『審核通過且已對玩家公開』之後呼叫；不自行偵測資料表變動。
"""
from __future__ import annotations
from modules.notification_story_publication import parse_discord_source
from modules.notification_release_outbox import enqueue_release

VALID_CATEGORIES = ('system', 'activity', 'character', 'shop',
                    'company', 'faction', 'world', 'other')


def enqueue_published_change(guild_id: int, *, category: str, content_id: str,
                             revision: int, title: str, source_url: str,
                             action: str = '新增', published: bool = False) -> bool:
    if category not in VALID_CATEGORIES:
        raise ValueError('未知分類；攻略劇情請使用專用發布接口')
    if int(revision) < 1 or not str(content_id).strip():
        raise ValueError('內容 ID / 版本號不合法')
    if action not in ('新增','更新','移除'):
        raise ValueError('更新類型只支援新增、更新、移除')
    source = parse_discord_source(source_url, guild_id)
    return enqueue_release(guild_id, release_key=f'{category}:{content_id}:rev:{revision}',
                           category=category, title=title, source_url=source,
                           action=action, published=published)
