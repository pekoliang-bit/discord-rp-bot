"""第22檔：攻略劇情公開後登錄更新；『就緒』本身不觸發公告。"""
from __future__ import annotations
from urllib.parse import urlparse
import re
from modules.notification_update_records import record_published_update

_LINK = re.compile(r'^/channels/(\d+)/(\d+)(?:/(\d+))?/?$')


def parse_discord_source(url: str, guild_id: int) -> str:
    """只接受同伺服器 Discord 頻道、帖子或訊息連結。"""
    parsed = urlparse((url or '').strip())
    if parsed.scheme != 'https' or parsed.netloc not in ('discord.com', 'www.discord.com', 'discordapp.com'):
        raise ValueError('請提供 Discord 的原始內容連結')
    match = _LINK.fullmatch(parsed.path)
    if not match or int(match.group(1)) != int(guild_id):
        raise ValueError('連結必須指向目前伺服器的頻道、帖子或訊息')
    return f'https://discord.com{parsed.path.rstrip("/")}'


def record_story_publication(guild_id: int, *, story_id: int, character_name: str,
                             title: str, source_url: str, revision: int,
                             action: str = '新增') -> bool:
    """必須由已授權的業務發布流程於內容公開成功後呼叫。"""
    if int(revision) < 1:
        raise ValueError('版本號須大於零')
    source = parse_discord_source(source_url, guild_id)
    if not character_name.strip():
        raise ValueError('缺少攻略角色名稱')
    return record_published_update(
        guild_id, 'story', title, action=action,
        source_url=source, character_name=character_name,
        update_key=f'story:{int(story_id)}:rev:{int(revision)}', approved=True,
    )
