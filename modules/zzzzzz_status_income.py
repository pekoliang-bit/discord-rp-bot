"""地位與公司每月薪資串接；保留公司銀行付款、權限與防重複付款。"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from core.database import get_or_create_account, transaction
from core.economy import _payment
from modules import character_status, company_treasury, company_treasury_ui

LOG = logging.getLogger(__name__)


def status_salary_payment(guild_id, business_id, actor_id, character_id):
    """將職位基本月薪乘地位倍率，單一月份原子入帳一次。"""
    from modules.company_workforce import initialize as initialize_workforce
    from modules.application_extras import init_db as initialize_applications

    initialize_workforce()
    initialize_applications()
    company_treasury.initialize_treasury()
    character_status.initialize()
    gid, bid, uid, cid = str(guild_id), str(business_id), str(actor_id), int(character_id)
    period = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m")
    reference = f"company-payroll:{gid}:{bid}:{cid}:{period}"

    with transaction() as db:
        if not company_treasury._finance_permission(db, gid, bid, uid):
            raise PermissionError("你沒有公司財務付款權限。")
        employee = db.execute("""
            SELECT p.salary FROM company_employees e
            JOIN company_job_positions p
              ON p.guild_id=e.guild_id AND p.company_id=e.company_id AND p.rank=e.rank
            JOIN characters c ON c.guild_id=e.guild_id AND c.id=e.character_id
            WHERE e.guild_id=? AND e.company_id=? AND e.character_id=?
              AND e.status='active' AND c.status='active'
        """, (gid, bid, cid)).fetchone()
        if not employee:
            raise ValueError("角色非在職員工，或職位設定不存在。")
        base = int(employee["salary"])
        if base <= 0:
            raise ValueError("職位的基本月薪必須大於零。")
        already = db.execute("""
            SELECT 1 FROM rp_company_payroll
            WHERE guild_id=? AND business_id=? AND character_id=? AND pay_period=?
        """, (gid, bid, cid, period)).fetchone()
        if already:
            return f"ℹ️ 角色 #{cid} 的 {period} 薪資已發放，不會重複扣款。"

        state = db.execute("""
            SELECT level FROM rp_character_status WHERE guild_id=? AND character_id=?
        """, (gid, cid)).fetchone()
        level = int(state["level"]) if state else 1
        level = max(1, min(20, level))
        amount = character_status.minimum_work_pay(base, level)
        if not company_treasury._is_owner(db, gid, bid, uid):
            limit = company_treasury._manager_limit(db, gid, bid, uid)
            if limit <= 0 or amount > limit:
                raise PermissionError("薪資（含地位倍率）超過管理者單筆支出上限。")
        bank = db.execute("""
            SELECT account_id FROM rp_company_bank_accounts
            WHERE guild_id=? AND company_id=?
        """, (gid, bid)).fetchone()
        if not bank:
            raise ValueError("公司尚未開戶，無法支付薪資。")
        receiver = get_or_create_account(db, gid, "character", str(cid), "wallet")
        result = _payment(
            db, gid, reference, int(bank["account_id"]), receiver, amount,
            f"公司 {bid}｜{period} 薪資 LV{level}｜角色 #{cid}",
        )
        if result.duplicated:
            return "ℹ️ 這筆薪資已經入帳，不會重複支付。"
        now = int(time.time())
        db.execute("""
            INSERT INTO rp_company_payroll
            (guild_id,business_id,character_id,pay_period,amount,paid_by,reference_key,paid_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (gid, bid, cid, period, amount, uid, reference, now))
        db.execute("""
            INSERT INTO rp_company_finance_audit
            (guild_id,business_id,actor_id,action,reference_key,amount,
             target_type,target_id,note,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (gid, bid, uid, "payroll", reference, amount, "character", str(cid),
              f"{period} 月薪：基本 {base:,} × LV{level} 倍率 {character_status.WORK_MULTIPLIERS[level-1]:g}", now))
    return f"✅ 已核發 {period} 月薪 {amount:,} 金幣（基本 {base:,}，LV{level} 地位倍率），角色 #{cid} 已入帳。"


def endorsement_quote(tier: int, offered: int, *, honorary_origin: bool = False):
    """供未來代言合約使用；此函式不轉帳、不乘地位倍率。"""
    tier = int(tier)
    offered = int(offered)
    minimum = character_status.minimum_endorsement_fee(tier)
    if honorary_origin and tier == 10:
        return 0
    if offered < minimum:
        raise ValueError(f"T{tier} 代言最低費用為 {minimum:,} 金幣。")
    return offered


async def setup(bot):
    company_treasury.pay_employee_salary = status_salary_payment
    # 財務 UI 在載入時以 from ... import ... 綁定舊函式，需一併替換。
    company_treasury_ui.pay_employee_salary = status_salary_payment
    LOG.info("每月公司薪資已套用角色地位倍率；代言最低價驗證函式已備妥。")
