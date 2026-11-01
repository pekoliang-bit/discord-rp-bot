"""第六批 18/18：業務模組記錄正式更新的單一入口（不增設 Discord 指令）。

使用方式：業務資料交易及審核完成且內容確實公開後，呼叫 record_released_*。
尚未修改 /劇情、/商城、/公司等業務程式，所以不會自動監聽其新增事件。
"""
from __future__ import annotations

from modules.notification_source_registry import record_content_release


def record_released_story(guild_id: int, *, character_name: str, title: str,
                          source_channel_id: int, content_id: str, revision: int,
                          message_id: int | None = None, action: str = '新增') -> bool:
    """攻略劇情按角色記錄，供公告分角色計數。"""
    return record_content_release(
        guild_id, 'story', title, source_channel_id=source_channel_id,
        content_id=content_id, revision=revision, action=action,
        message_id=message_id, character_name=character_name, published=True,
    )


def record_released_change(guild_id: int, *, category: str, title: str,
                           source_channel_id: int, content_id: str, revision: int,
                           message_id: int | None = None, action: str = '新增') -> bool:
    """更新九大類中非攻略的正式公開內容。

    category：system/activity/character/shop/company/faction/world/other。
    content_id 用永久內容 ID，revision 每次修改後遞增。
    """
    if category == 'story':
        raise ValueError('攻略劇情請改用 record_released_story 並提供角色名稱')
    return record_content_release(
        guild_id, category, title, source_channel_id=source_channel_id,
        content_id=content_id, revision=revision, action=action,
        message_id=message_id, published=True,
    )
