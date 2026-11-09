"""個人店鋪銀行帳戶：由一般申請核准交易原子開立。"""
from __future__ import annotations

from core.database import get_or_create_account


def initialize_shop_bank(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_personal_shop_bank_accounts (
            guild_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            applicant_user_id TEXT NOT NULL,
            application_id INTEGER NOT NULL,
            opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','frozen')),
            PRIMARY KEY (guild_id, shop_id),
            UNIQUE (guild_id, account_id),
            UNIQUE (guild_id, application_id)
        )
    """)


def approve_shop_bank(db, guild_id, shop_id, user_id, character_id, application_id):
    """只替既有、啟用且屬於申請角色的個人店舖開戶；重試具有冪等性。"""
    guild_id, user_id = str(guild_id), str(user_id)
    try:
        shop_id, character_id, application_id = int(shop_id), int(character_id), int(application_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('店鋪、角色及申請編號必須是有效整數。') from exc
    if min(shop_id, character_id, application_id) <= 0:
        raise ValueError('店鋪、角色及申請編號必須大於 0。')
    initialize_shop_bank(db)
    shop = db.execute("""
        SELECT id, character_id, status FROM personal_shops
        WHERE guild_id=? AND id=?
    """, (guild_id, shop_id)).fetchone()
    if shop is None or shop['status'] != 'open' or int(shop['character_id']) != character_id:
        raise ValueError('店鋪不存在、未營業或不屬於申請角色。')
    owner = db.execute("""
        SELECT owner_id FROM characters
        WHERE guild_id=? AND id=? AND status='active'
    """, (guild_id, character_id)).fetchone()
    if owner is None or str(owner['owner_id']) != user_id:
        raise PermissionError('必須由店鋪持有人所屬的已啟用角色申請。')
    prev = db.execute("""
        SELECT account_id, application_id FROM rp_personal_shop_bank_accounts
        WHERE guild_id=? AND shop_id=?
    """, (guild_id, shop_id)).fetchone()
    if prev is not None:
        if int(prev['application_id']) == application_id:
            return int(prev['account_id'])
        raise ValueError('此店鋪已經開立銀行帳戶。')
    account_id = get_or_create_account(db, guild_id, 'personal_shop', shop_id, 'bank')
    db.execute("""
        INSERT INTO rp_personal_shop_bank_accounts
        (guild_id,shop_id,account_id,applicant_user_id,application_id)
        VALUES (?,?,?,?,?)
    """, (guild_id,shop_id,account_id,user_id,application_id))
    return int(account_id)
