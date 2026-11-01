"""公司開戶核准前的共同檢查。"""
from __future__ import annotations


def check_opening_eligibility(db, application):
    if application["kind"] != "company_bank" or application["status"] != "pending":
        raise ValueError("只有待審中的公司銀行帳戶申請可開戶。")
    guild_id = str(application["guild_id"])
    company_id = str(application["title"]).strip()
    if not company_id:
        raise ValueError("公司編號不可為空。")
    from modules.application_extras import validate_company_bank_applicant
    validate_company_bank_applicant(db, guild_id, company_id, application["user_id"])
    # 只有公司實體存在，才能進一步開通金融功能；避免孤兒帳戶。
    from modules.company_treasury import _entity
    _entity(db, guild_id, company_id)
    return guild_id, company_id
