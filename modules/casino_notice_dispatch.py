"""娛樂城通知派送橋接：只派送角色相關公開頭獎公告。

私人通知必須由角色專屬論壇的固定「通知」帖子處理，未安全整合前不會
透過此服務發到公開頻道或 Discord 私訊。失敗事件保持 pending 供重試。
呼叫端需使用既有角色通知設定提供正確頻道 ID，不會自行建立頻道。
"""
from __future__ import annotations
import json
from typing import Any
from core.database import get_connection
from modules.casino_effects import list_pending_events, mark_event_completed


def public_announcement(event: dict[str, Any]) -> str | None:
    """僅組裝去識別化的公開大獎訊息；絕不包含銀行或玩家 Discord ID。"""
    if event.get('event_kind') != 'major_win':
        return None
    payload = json.loads(event.get('payload_json') or '{}')
    if payload.get('category') != 'character_related':
        return None
    name = str(payload.get('game_name') or '娛樂城')[:80].replace('@', '＠')
    coins = int(payload.get('reward_coins') or 0)
    rarity = str(payload.get('rarity') or '')
    extra = '，抽中 UR 級大獎' if rarity == 'UR' else ''
    return f'🎉 **娛樂城恭喜公告**\n角色編號 **#{int(event["character_id"])}** 在 **{name}** 中獎{extra}！\n金幣獎金：**{coins:,}**'


async def dispatch_public_wins(bot: Any, guild_id: int | str, *, channel_id: int,
                               limit: int = 20) -> dict[str, int]:
    """只在指定的「角色相關」頻道通知。呼叫者必須先確認 channel_id 已獲群主配置。

    send 成功後才完成事件；Discord 發送成功、資料庫標記失敗時可能重送，
    因此此服務保證不重複發放獎勵，但不聲稱跨系統公告 exactly-once。
    """
    if type(channel_id) is not int or channel_id <= 0:
        raise ValueError('請先設定角色相關通知頻道')
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        raise ValueError('機器人未加入指定伺服器')
    channel = guild.get_channel(channel_id)
    if channel is None or not hasattr(channel, 'send'):
        raise ValueError('通知頻道不存在或不可發送')
    if channel.guild.id != guild.id:
        raise ValueError('不能跨伺服器發送娛樂城公告')
    events = list_pending_events(guild_id, limit=min(max(1,int(limit)),100))
    sent = 0
    skipped = 0
    for event in events:
        msg = public_announcement(event)
        if msg is None:
            skipped += 1
            continue
        # 先檢查原始結算依然有效。
        with get_connection() as conn:
            src = conn.execute('''SELECT status FROM casino_rounds WHERE guild_id=? AND id=?''',
                (str(guild_id), int(json.loads(event['payload_json'])['round_id']))).fetchone()
        if not src or src['status'] != 'settled':
            skipped += 1
            continue
        await channel.send(msg, allowed_mentions=__import__('discord').AllowedMentions.none())
        if mark_event_completed(guild_id, int(event['id'])):
            sent += 1
    return {'sent':sent, 'skipped':skipped, 'private_pending':skipped}
