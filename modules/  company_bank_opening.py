"""公司銀行開戶交易：由 /申請 審核成功時呼叫，沿用正式帳本。"""
from __future__ import annotations
from core.database import get_or_create_account
from modules.company_bank_schema import ensure_company_bank_schema
from modules.company_bank_policy import check_opening_eligibility


def approve_bank_application(db, application):
    """須由外層審核 transaction 呼叫；如後續核准失敗，一起回滾。"""
    gid, cid = check_opening_eligibility(db, application)
    ensure_company_bank_schema(db)
    existing = db.execute(
        "SELECT account_id FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?",
        (gid, cid),
    ).fetchone()
    if existing:
        raise ValueError("這家公司已經開立銀行帳戶，不可重複開戶。")
    # 帳戶本身不帶金額；日後轉入金幣必須走交易帳本。
    aid = get_or_create_account(db, gid, "organization", cid, "bank")
    db.execute(
        "INSERT INTO rp_company_bank_accounts "
        "(guild_id,company_id,account_id,initial_deposit) VALUES (?,?,?,0)",
        (gid,cid,int(aid)),
    )
    db.execute(
        "INSERT INTO rp_company_bank_opening_log "
        "(guild_id,application_id,company_id,account_id) VALUES (?,?,?,?)",
        (gid,int(application["id"]),cid,int(aid)),
    )
    return aid
