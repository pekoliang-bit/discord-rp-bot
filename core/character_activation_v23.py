"""角色啟用 V2.3：原創角色與玩家 NPC 共用的交易安全入口。

不發金幣、不改屬性、不代抽新人禮包；權益仍使用既有正式服務。
NPC 由其他模組核准的數值必須事先確實寫入 attributes 表。
"""
from __future__ import annotations

import json
import logging

from core.attribute_validation import read_schema, validate_initial_attributes
from core.character_creation_consent import persist_rumor_consent
from core.character_proxy_identity import activate as activate_proxy
from core.permissions import InvalidState, PermissionDenied
from modules.character_numbering import issue_in_transaction, normalize_birthday

LOG = logging.getLogger(__name__)


def _npc_attributes_present(db, guild_id: str, character_id: int) -> dict:
    """只核對 13 項「已核准並存在」的數值，不在本系統重新分配或修改。"""
    schema = read_schema(db, guild_id)
    required = set(schema['general']) | set(schema['talent'])
    rows = db.execute(
        'SELECT name,value FROM attributes WHERE guild_id=? AND character_id=?',
        (guild_id, character_id),
    ).fetchall()
    existing = {str(row['name']): row['value'] for row in rows}
    missing = sorted(required - set(existing))
    if missing:
        raise InvalidState('NPC 正式屬性尚未從核准版本匯入：' + '、'.join(missing))
    for name in required:
        value = existing[name]
        if type(value) is not int or value < 0:
            raise InvalidState(f'NPC 屬性「{name}」不合法，請檢查核准版本。')
    return {'schema_version': schema.get('version'), 'main_track': None, 'secondary_track': None}


def activate_in_transaction(db, guild_id, character_id, user_id, *, allowed_types=('player',)) -> dict:
    """呼叫者須持有 transaction()。一次成功，重複請求不再啟用。"""
    gid, cid, uid = str(guild_id), int(character_id), str(user_id)
    row = db.execute('SELECT * FROM characters WHERE guild_id=? AND id=?', (gid, cid)).fetchone()
    if not row:
        raise InvalidState('角色不存在。')
    if str(row['owner_id']) != uid:
        raise PermissionDenied('只有角色擁有者可以啟用。')
    if row['character_type'] not in allowed_types:
        raise PermissionDenied('此角色類型不能使用這個啟用入口。')
    if row['status'] != 'approved':
        raise InvalidState('角色必須先完成兩階段審核，且不可重複啟用。')
    try:
        settings = json.loads(row['settings_json'] or '{}')
        if not isinstance(settings, dict):settings = {}
    except (TypeError, ValueError):
        settings = {}
    if normalize_birthday(settings.get('birthday')) is None:
        raise InvalidState('生日未填或無效，請先設定月／日，才能啟用。')
    if row['character_type'] == 'player':
        # 玩家原創仍沿用舊有完整初始配點驗證。
        from modules.profile import missing_fields
        missing = missing_fields(row)
        if missing: raise InvalidState('尚有必填欄位：' + '、'.join(missing))
        validation = validate_initial_attributes(db, gid, cid)
    else:
        # NPC 初始屬性在另一個對話維護；不套用原創角色的 150 點上限。
        if row['age'] is None or not row['occupation']:
            raise InvalidState('NPC 年齡或職業尚未完成核准。')
        validation = _npc_attributes_present(db, gid, cid)
    changed = db.execute('''UPDATE characters SET status='active',version=version+1
        WHERE guild_id=? AND id=? AND status='approved' ''', (gid, cid))
    if changed.rowcount != 1:
        raise InvalidState('角色狀態已改變，未重複啟用。')
    if row['character_type'] in ('player_npc','official_npc'):
        from core.npc_initial_grants_v23 import award_in_transaction
        award_in_transaction(db,gid,cid)
    persist_rumor_consent(db, gid, cid, settings)
    activate_proxy(db, gid, cid)
    # 正式開群前不核發公開編號。
    issue_in_transaction(db, gid, cid)
    return validation


def history_notification(guild_id, character_id):
    """交易提交後執行；抽選失敗仍保留角色啟用狀態，並標記待補建。"""
    gid, cid = str(guild_id), int(character_id)
    try:
        from core.history_v23 import attempt_lock
        status = attempt_lock(gid, cid)
    except Exception:
        LOG.exception('角色 %s 的歷史履歷待管理員補建', cid)
        status = 'history_error'
    status_label = {
        'locked': '五筆歷史履歷已建立',
        'already': '歷史履歷已存在，未重抽',
        'pending_assets': '素材不足，歷史履歷待補建',
        'pending_seat': '作品席位不足，歷史履歷待補建',
        'history_error': '抽選處理異常，待管理員核對',
    }.get(status, str(status))
    try:
        from modules.notifications import queue_notification
        queue_notification(gid, 'private', 'role',
            f'🎉 角色 #{cid} 已正式啟用。{status_label}。\n'
            '新人禮包請依既有 V5.0 領取入口辦理；本通知不代表已領取或發放金錢。',
            character_id=cid, dedupe_key=f'v23:activation:{gid}:{cid}')
    except Exception:
        LOG.exception('角色 %s 私人通知排程失敗', cid)
    if status in ('locked','already'):
        try:
            from core.history_v23_delivery import queue_history_notice
            queue_history_notice(gid,cid)
        except Exception:
            LOG.exception('角色 %s 五筆歷史私人通知排程失敗', cid)
    return status
