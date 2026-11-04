"""驗證任務回報用的 Discord 討論串留言；不能以 slash command 代替作品內容。"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta


def _date(value):
    if isinstance(value,datetime):
        parsed=value
    elif isinstance(value,str):
        try: parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as exc: raise ValueError('留言時間格式錯誤') from exc
    else:
        raise ValueError('留言時間格式錯誤')
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def validate_evidence(evidence, user_id, channel_id, accepted_at):
    if not isinstance(evidence,dict):raise ValueError('請先在指定討論串發表內容，再回報任務')
    if str(evidence.get('author_id'))!=str(user_id):raise ValueError('留言作者必須是目前玩家本人')
    if str(evidence.get('channel_id'))!=str(channel_id):raise ValueError('留言必須來自任務指定討論串')
    mid=str(evidence.get('message_id',''))
    if not mid.isdigit() or int(mid)<=0:raise ValueError('無效的留言 ID')
    body=str(evidence.get('content') or '').strip()
    if len(body)<10 or body.startswith('/'):
        raise ValueError('請先在討論串發表至少十個字的有效內容')
    when=_date(evidence.get('created_at'))
    accepted=_date(accepted_at)
    # SQLite CURRENT_TIMESTAMP uses second precision; allow small clock skew.
    if when + timedelta(seconds=3) < accepted:
        raise ValueError('該留言早於接取時間，請使用接取後發表的內容')
    if when > datetime.now(timezone.utc)+timedelta(minutes=5):
        raise ValueError('留言時間不正確')
    return {'message_id':mid,'excerpt':body[:300]}


async def find_thread_evidence(interaction, *, accepted_at, scan_limit=100):
    """Only inspect the current thread; the author must be the interaction user."""
    import discord
    channel=interaction.channel
    if not isinstance(channel,discord.Thread):
        raise ValueError('請在指定的討論串中回報，不能在一般文字頻道回報')
    try:
        async for message in channel.history(limit=scan_limit,oldest_first=False):
            if message.author.id!=interaction.user.id or message.author.bot: continue
            item={'message_id':str(message.id),'author_id':str(message.author.id),
                  'channel_id':str(message.channel.id),
                  'created_at':message.created_at,'content':message.content or ''}
            try:
                validate_evidence(item,interaction.user.id,interaction.channel_id,accepted_at)
            except ValueError:
                continue
            return item
    except (discord.Forbidden,discord.HTTPException) as exc:
        raise ValueError('無法讀取討論串留言，請檢查機器人的閱讀歷史訊息權限') from exc
    raise ValueError('未找到接取任務後由你發表的有效留言；請先發文再回報')
