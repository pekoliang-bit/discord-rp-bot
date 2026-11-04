"""排行榜角色獎勵原子入帳服務（預設保持安全鎖）。

僅支援已正式核准的角色型獎勵。非角色獎勵、未核准版本、
未完成映射的粉絲類獎勵一律拒絕；不修改現有排行榜的派發開關。
"""
from __future__ import annotations

import json
from core.database import transaction, get_or_create_account
from core.economy import _payment
from modules.leaderboard_payout_storage import get_job, initialize as init_jobs
from modules.leaderboard_payout_policy import require_payout_permission
from modules.leaderboard_payout_identity import validate_identity, CHARACTER_BOARDS
from modules.leaderboard_reward_validation import validate_reward
from modules.leaderboard_points_storage import initialize as init_points


class PayoutNotReadyError(RuntimeError):
    """獎勵未核准或正式入帳映射尚未完成。"""


ATTRIBUTES = {
    'heat': '流量熱度', 'commercial': '商業價值',
    'public_favor': '路人緣', 'pr': '公關',
    'appearance': '容貌', 'intellect': '才智',
    'acting': '演繹', 'dance': '舞蹈', 'sing': '歌唱',
    'hosting': '主持', 'variety': '綜藝',
    'talent': '個人才藝',
}
OTHER_SUPPORTED = {'free_points', 'skill_points', 'awareness'}


def _approved(job):
    try:
        reward = json.loads(job['reward_json'])
    except (TypeError, ValueError) as exc:
        raise PayoutNotReadyError('獎勵 JSON 無效') from exc
    validate_reward(reward)
    if reward.get('reward_version') != 'v11-approved':
        raise PayoutNotReadyError('僅支援經正式核准的 v11-approved 獎勵')
    board = reward['board']
    if board not in CHARACTER_BOARDS:
        raise PayoutNotReadyError('非角色榜的派發適配尚未完成')
    identity = validate_identity(board, job['entity_key'])
    if identity['entity_type'] != 'character' or not str(identity['entity_id']).isdecimal():
        raise PayoutNotReadyError('必須使用含角色類型的正式身分鍵')
    values = reward.get('values', {})
    unsupported = set(values) - set(ATTRIBUTES) - OTHER_SUPPORTED
    if unsupported:
        raise PayoutNotReadyError('尚無正式入帳映射：' + ', '.join(sorted(unsupported)))
    # 部分獎勵（如 fans）需要先定義總粉絲與忠實粉絲歸屬，故不做假設。
    return reward, int(identity['entity_id'])


def _eligible_character(db, gid, cid):
    row = db.execute(
        'SELECT status, settings_json FROM characters WHERE guild_id=? AND id=?',
        (gid, cid),
    ).fetchone()
    if row is None or row['status'] != 'active':
        raise PermissionError('獲獎角色不存在或未啟用')
    try:
        settings = json.loads(row['settings_json'] or '{}')
    except (TypeError, ValueError) as exc:
        raise PayoutNotReadyError('角色設定 JSON 無效') from exc
    if not isinstance(settings, dict):
        raise PayoutNotReadyError('角色設定格式錯誤')
    if settings.get('character_category') != 'extra':
        return
    if settings.get('ranking_eligible', True) is False:
        raise PermissionError('路人角色的個別排行資格已停用')
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='extra_settings'"
    ).fetchone()
    if exists:
        switch = db.execute(
            'SELECT allow_ranking FROM extra_settings WHERE guild_id=?',
            (gid,),
        ).fetchone()
        if switch is not None and not bool(switch['allow_ranking']):
            raise PermissionError('路人排行榜總開關已關閉')


def _grant_points(db, gid, cid, pool, amount, source_key):
    if amount == 0:
        return
    cursor = db.execute(
        """INSERT OR IGNORE INTO lb_point_history
           (guild_id,character_id,pool,amount,category,target,source_key)
           VALUES(?,?,?,?,'grant','',?)""",
        (gid, cid, pool, amount, source_key),
    )
    if cursor.rowcount != 1:
        raise PayoutNotReadyError('獎勵點數來源已存在，停止重複派發')
    db.execute(
        """INSERT INTO lb_point_balances(guild_id,character_id,pool,balance)
           VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,pool)
           DO UPDATE SET balance=balance+excluded.balance""",
        (gid, cid, pool, amount),
    )


def inspect_payout(payout_key):
    job = get_job(str(payout_key))
    if job is None:
        return {'status': 'missing', 'payout_key': str(payout_key), 'issues': ['找不到派發紀錄']}
    if job['state'] == 'paid':
        return {'status': 'already_paid', 'payout_key': str(payout_key), 'issues': []}
    issues = []
    try:
        reward, _ = _approved(job)
        require_payout_permission(reward['board'], job['entity_key'], reward)
        if job['state'] != 'ready':
            raise PayoutNotReadyError('派發工作尚未進入 ready 狀態')
    except (ValueError, TypeError, KeyError, PermissionError, PayoutNotReadyError) as exc:
        issues.append(str(exc))
    return {
        'status': 'blocked' if issues else 'ready',
        'payout_key': str(payout_key),
        'guild_id': str(job['guild_id']),
        'entity_key': str(job['entity_key']),
        'issues': issues,
    }


def payout(payout_key):
    """同一 SQLite 交易完成全部獎勵和 paid 狀態。"""
    key = str(payout_key)
    with transaction() as db:
        init_jobs(db)
        row = db.execute(
            'SELECT * FROM lb_payout_jobs WHERE payout_key=?', (key,)
        ).fetchone()
        if row is None:
            raise ValueError('找不到派發紀錄')
        job = dict(row)
        if job['state'] == 'paid':
            return {'status': 'already_paid', 'payout_key': key}
        if job['state'] != 'ready':
            raise PermissionError('派發工作必須先核准為 ready')
        reward, cid = _approved(job)
        require_payout_permission(reward['board'], job['entity_key'], reward)
        gid = str(job['guild_id'])
        _eligible_character(db, gid, cid)
        init_points(db)

        for field, amount in reward.get('values', {}).items():
            if amount == 0:
                continue
            if field in OTHER_SUPPORTED:
                if field in ('free_points', 'skill_points'):
                    pool = 'free' if field == 'free_points' else 'skill'
                    _grant_points(db, gid, cid, pool, amount, f'lb:{key}:{field}')
                else:
                    changed = db.execute(
                        """UPDATE character_system_tracking
                           SET public_awareness=public_awareness+?, updated_at=CURRENT_TIMESTAMP
                           WHERE guild_id=? AND character_id=?""",
                        (amount, gid, cid),
                    )
                    if changed.rowcount != 1:
                        raise PayoutNotReadyError('正式知名度紀錄不存在，已回滾全部獎勵')
            else:
                attr_name = ATTRIBUTES[field]
                changed = db.execute(
                    """UPDATE attributes SET value=value+?, version=version+1
                       WHERE guild_id=? AND character_id=? AND name=?""",
                    (amount, gid, cid, attr_name),
                )
                if changed.rowcount != 1:
                    raise PayoutNotReadyError(f'正式屬性「{attr_name}」不存在，已回滾全部獎勵')

        coins = reward.get('coins', 0)
        if coins > 0:
            sender = get_or_create_account(
                db, gid, 'system', 'leaderboard_rewards', 'treasury'
            )
            receiver = get_or_create_account(
                db, gid, 'character', str(cid), 'wallet'
            )
            result = _payment(db, gid, 'lb:payout:' + key, sender, receiver,
                              coins, '排行榜正式獎勵')
            if result.duplicated:
                raise PayoutNotReadyError('款項已經入帳但工作狀態不一致，拒絕重複操作')

        changed = db.execute(
            """UPDATE lb_payout_jobs
               SET state='paid', reason='', updated_at=CURRENT_TIMESTAMP
               WHERE payout_key=? AND state='ready'""",
            (key,),
        )
        if changed.rowcount != 1:
            raise RuntimeError('派發工作狀態變更失敗，整筆回滾')
    return {'status': 'paid', 'payout_key': key, 'character_id': cid}
