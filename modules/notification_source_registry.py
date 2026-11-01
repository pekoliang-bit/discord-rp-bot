"""第五批 15/15：已公開內容的更新來源輔助。提供明確類別的記錄入口。"""
from __future__ import annotations
from modules.notification_event_bridge import publish_story_change, publish_content_change
from modules.notification_categories import LABELS


def make_discord_link(guild_id: int, channel_id: int, message_id: int | None = None) -> str:
    """產生 Discord 頻道/討論帖/訊息網址；僅作連結，不代替權限驗證。"""
    if int(guild_id) <= 0 or int(channel_id) <= 0 or (message_id is not None and int(message_id) <= 0):
        raise ValueError('Discord ID 必須是正整數')
    root = f'https://discord.com/channels/{int(guild_id)}/{int(channel_id)}'
    return f'{root}/{int(message_id)}' if message_id is not None else root


def record_content_release(guild_id: int, category: str, title: str, *,
                           source_channel_id: int, content_id: str, revision: int,
                           action: str = '新增', message_id: int | None = None,
                           character_name: str | None = None,
                           published: bool = False) -> bool:
    """供各業務系統在完成審核及正式公開後調用；不自動讀取未公開草稿。

    content_id 應使用資料庫內容 ID（不要只用標題）；revision 更新時需遞增。
    """
    if not published:
        raise ValueError('僅能記錄正式公開的內容')
    if category not in LABELS:
        raise ValueError('不支援的公告分類')
    if not content_id or not str(content_id).strip() or revision < 1:
        raise ValueError('內容 ID 不可空白，版本號需大於零')
    source = make_discord_link(guild_id, source_channel_id, message_id)
    key = f'{category}:{content_id}:revision:{revision}'
    if category == 'story':
        if not character_name or not character_name.strip():
            raise ValueError('攻略劇情需提供角色名稱')
        return publish_story_change(guild_id,character_name,title,source_url=source,stable_id=key,action=action)
    return publish_content_change(guild_id,category,title,source_url=source,stable_id=key,action=action)
