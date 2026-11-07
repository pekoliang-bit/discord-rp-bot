"""點數分配確認識別碼。

使用記憶體暫存；重新啟動後未確認請求失效。
正式扣點仍由 points_service 的交易處理。
"""

import secrets
import time

from modules.leaderboard_points_preview import preview
from modules.leaderboard_points_service import allocate

_PENDING = {}
TTL_SECONDS = 180


def create_request(
    guild_id, character_id, user_id,
    pool, category, target, amount,
):
    if pool == 'professional':
        from modules.action_professional_points import get_balance, verify_owner
        verify_owner(guild_id, character_id, user_id)
        from modules.leaderboard_points_catalog import validate_target
        validate_target('skill', category, target)
        if type(amount) is not int or amount <= 0:
            raise ValueError('專業點數必須是正整數')
        balance = get_balance(guild_id, character_id)
        if balance < amount:
            raise ValueError('專業點數餘額不足')
        details = {'pool': 'professional', 'balance': balance, 'amount': amount,
                   'category': category, 'target': target}
    else:
        details = preview(
            guild_id, character_id, user_id,
            pool, category, target, amount,
        )

    token = secrets.token_urlsafe(24)
    _PENDING[token] = {
        "guild_id": str(guild_id),
        "character_id": int(character_id),
        "user_id": str(user_id),
        "pool": pool,
        "category": category,
        "target": target,
        "amount": int(amount),
        "expires": time.time() + TTL_SECONDS,
    }

    return {
        "token": token,
        "preview": details,
        "expires_in": TTL_SECONDS,
    }


def confirm_request(token, user_id):
    request = _PENDING.get(token)

    if request is None:
        raise ValueError("確認請求不存在或已使用")

    if str(user_id) != request["user_id"]:
        raise PermissionError("無權確認此請求")

    if time.time() > request["expires"]:
        _PENDING.pop(token, None)
        raise ValueError("確認請求已過期")

    # 先刪除暫存，防止同一進程重複確認。
    _PENDING.pop(token, None)

    if request['pool'] == 'professional':
        from modules.action_professional_points import allocate as spend_professional
        return spend_professional(request['guild_id'],request['character_id'],
            request['user_id'],request['target'],request['amount'],
            'action-professional:' + token)

    return allocate(
        request["guild_id"],
        request["character_id"],
        request["user_id"],
        request["pool"],
        request["category"],
        request["target"],
        request["amount"],
        "lb-allocation:" + token,
    )


def cancel_request(token, user_id):
    request = _PENDING.get(token)
    if request is None:
        return False
    if str(user_id) != request["user_id"]:
        raise PermissionError("無權取消此請求")
    _PENDING.pop(token, None)
    return True
