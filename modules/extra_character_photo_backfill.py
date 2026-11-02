
"""將已綁定的四類照片頻道既有附件掃描入庫（不註冊新 Slash 指令）。"""
from __future__ import annotations
from modules import extra_character_store as store

async def import_history(guild, *, per_channel=1000):
    """由群主授權的管理介面呼叫；回傳逐頻道數量與錯誤。"""
    if not 1 <= per_channel <= 10000:
        raise ValueError("每頻道掃描上限必須為 1～10000")
    store.initialize(guild.id)
    from core.database import get_connection
    with get_connection() as db:
        bindings = db.execute(
            "SELECT channel_id,gender,age_group FROM extra_photo_channels WHERE guild_id=?",
            (str(guild.id),)
        ).fetchall()
    result=[]
    for entry in bindings:
        cid=int(entry['channel_id']); channel=guild.get_channel(cid)
        if channel is None or not hasattr(channel, 'history'):
            result.append({'channel_id':cid,'added':0,'error':'無法存取頻道'}); continue
        added=0
        try:
            async for message in channel.history(limit=per_channel,oldest_first=True):
                if message.author.id != guild.owner_id: continue
                urls=[a.url for a in message.attachments if
                      (a.content_type or '').startswith('image/') or
                      a.filename.lower().endswith(('.png','.jpg','.jpeg','.webp'))]
                if urls:
                    added += store.add_photos(guild.id,urls,
                        gender=entry['gender'],age_group=entry['age_group'],
                        channel_id=cid,message_id=message.id)
            result.append({'channel_id':cid,'added':added,'error':None})
        except Exception as exc:
            result.append({'channel_id':cid,'added':added,'error':type(exc).__name__})
    return result
