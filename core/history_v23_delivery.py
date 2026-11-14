"""歷史履歷 V2.3 五筆結果通知：只在完整鎖定後入列、不得重送或重抽。

私人通知使用既有 notification_outbox -> character_forum_links.notice_id。
此模組不改動角色屬性、獎勵、歷史內容或額外加成。
"""
from __future__ import annotations

import json
import logging

from core.database import get_connection

LOG = logging.getLogger(__name__)
LABELS = {
    'work': '代表／歷史作品',
    'endorsement': '歷史代言',
    'achievement': '歷史成就',
    'honor': '歷史榮譽',
    'event': '歷史事件',
}


def _safe(value, limit=115):
    """內容來自管理員素材庫；防止 Discord 提及濫用及訊息超長。"""
    text = str(value or '').replace('@', '@\u200b').replace('`', 'ˋ')
    text = ' '.join(text.split())
    return text[:limit] + ('…' if len(text) > limit else '')


def read_locked_history(guild_id, character_id):
    """傳回五筆完整鎖定快照，資料不足時傳回 None。"""
    gid, cid = str(guild_id), int(character_id)
    with get_connection() as db:
        exists = db.execute('''SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='rp_history_results_v23' ''').fetchone()
        if not exists:
            return None
        character = db.execute('''SELECT name,status FROM characters
            WHERE guild_id=? AND id=?''', (gid,cid)).fetchone()
        if not character or character['status'] != 'active':
            return None
        rows = db.execute('''SELECT slot,kind,asset_id,snapshot_json
            FROM rp_history_results_v23 WHERE guild_id=? AND character_id=?
            ORDER BY slot''', (gid,cid)).fetchall()
        if [r['slot'] for r in rows] != [1,2,3,4,5]:
            return None
        out = []
        for row in rows:
            try:
                snapshot = json.loads(row['snapshot_json'])
            except (ValueError, TypeError):
                LOG.warning('歷史快照 JSON 無效：guild=%s character=%s slot=%s', gid,cid,row['slot'])
                return None
            if not isinstance(snapshot, dict) or not str(snapshot.get('title','')).strip():
                return None
            out.append((int(row['slot']),str(row['kind']),str(row['asset_id']),snapshot))
        return _safe(character['name'],60), out


def build_history_notice(guild_id, character_id):
    locked = read_locked_history(guild_id, character_id)
    if locked is None:
        return None
    name, records = locked
    lines = [f'📜 **{name}｜歷史履歷 V2.3**', '五筆過往經歷已建立並永久鎖定：']
    for slot,kind,asset_id,info in records:
        title = _safe(info.get('title'))
        suffix = []
        if kind == 'work':
            for key,label in (('participation_role','身分'),('character_name','劇中角色')):
                if info.get(key):
                    suffix.append(f'{label}：{_safe(info[key],35)}')
            hot = info.get('paired_hot_search')
            if isinstance(hot,dict) and hot.get('title'):
                suffix.append(f'出圈熱搜：{_safe(hot["title"],85)}')
        if info.get('rarity'):
            suffix.append(_safe(info['rarity'],5))
        if kind == 'endorsement' and info.get('tier'):
            suffix.append(_safe(info['tier'],5))
        extra = ('（'+'；'.join(suffix)+'）') if suffix else ''
        lines.append(f'{slot}. **{LABELS.get(kind,"過往經歷")}**：{title} [{_safe(asset_id,12)}]{extra}')
    lines.extend(('', '可使用 `/查看` 閱讀履歷。以上是過往經歷，並非現役合約，也不會自動發放金錢或數值。'))
    result = '\n'.join(lines)
    # Notification API 強制 1800 字；本模組保留足夠餘量。
    return result[:1760]


def queue_history_notice(guild_id, character_id):
    """同一角色最多入列一次，重試或舊角色補建不重複通知。"""
    body = build_history_notice(guild_id,character_id)
    if body is None:
        return False
    from modules.notifications import queue_notification
    return bool(queue_notification(
        str(guild_id),'private','role',body,
        character_id=int(character_id),priority='normal',
        dedupe_key=f'v23:history:five:{guild_id}:{character_id}',
    ))
