"""第二批 3/3：業務模組使用的更新記錄入口（暫不直接改動原有模組）。

例：publish_story_change(123, '角色名', '第一章', source_url='https://discord.com/channels/...',
                         stable_id='story:18:chapter:1:rev:2')
只在內容正式發布/通過審核後呼叫。此檔沒有 Discord 指令。
"""
from __future__ import annotations

from modules.notification_update_records import record_published_update


def publish_story_change(guild_id: int, character_name: str, title: str, *,
                         source_url: str | None, stable_id: str,
                         action: str = "新增") -> bool:
    """攻略劇情：每角色分開計數，stable_id 必須包含內容版本。"""
    if not character_name.strip() or not stable_id.strip():
        raise ValueError("角色名稱與更新識別碼不能空白")
    return record_published_update(
        guild_id, "story", title, action=action,
        character_name=character_name, source_url=source_url,
        update_key=stable_id, approved=True,
    )


def publish_content_change(guild_id: int, category: str, title: str, *,
                           source_url: str | None, stable_id: str,
                           action: str = "新增") -> bool:
    """系統、活動、NPC、商城、公司、陣營、世界觀與其他已公開更新。"""
    if category == "story":
        raise ValueError("攻略劇情請使用 publish_story_change")
    if not stable_id.strip():
        raise ValueError("更新識別碼不能空白")
    return record_published_update(
        guild_id, category, title, action=action, source_url=source_url,
        update_key=stable_id, approved=True,
    )
