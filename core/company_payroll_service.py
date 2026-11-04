
"""公司正式發薪服務：7／14天制。

沿用既有金流帳本，所有付款寫入同一個 SQLite 交易。
不建立新的餘額系統。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from core.database import (
    get_connection,
    get_or_create_account,
    transaction,
)
from core.economy import _payment
from core.company_payroll_period import (
    ALLOWED_CYCLES,
    payroll_period,
    payday_text,
)


def initialize_payroll():
    from modules.company_treasury import initialize_treasury

    initialize_treasury()

    with get_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_company_payroll_settings (
                guild_id TEXT NOT NULL,
                business_id TEXT NOT NULL,
                cycle_days INTEGER NOT NULL
                    CHECK(cycle_days IN (7,14)),
                updated_by TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(guild_id,business_id)
            )
        """)


def configure_payroll_cycle(
    guild_id,
    business_id,
    actor_id,
    cycle_days,
):
    """只有公司持有人能設定發薪週期。"""
    from modules.company_treasury import _is_owner

    if type(cycle_days) is not int:
        raise ValueError("週期必須是整數")

    if cycle_days not in ALLOWED_CYCLES:
        raise ValueError("僅支援7天或14天")

    initialize_payroll()

    gid = str(guild_id)
    bid = str(business_id)
    uid = str(actor_id)

    with transaction() as db:
        if not _is_owner(db, gid, bid, uid):
            raise PermissionError(
                "只有公司持有人能設定發薪週期"
            )

        old = db.execute("""
            SELECT cycle_days
            FROM rp_company_payroll_settings
            WHERE guild_id=? AND business_id=?
        """, (gid, bid)).fetchone()

        # 已有發薪紀錄後，不允許任意切換週期，
        # 避免重算已支付的薪資。
        if old and int(old["cycle_days"]) != cycle_days:
            paid = db.execute("""
                SELECT 1
                FROM rp_company_payroll
                WHERE guild_id=? AND business_id=?
                LIMIT 1
            """, (gid, bid)).fetchone()

            if paid:
                raise ValueError(
                    "公司已有薪資紀錄，"
                    "不可直接更改發薪週期；"
                    "需要先完成薪資遷移。"
                )

        db.execute("""
            INSERT INTO rp_company_payroll_settings
            (guild_id,business_id,cycle_days,updated_by,updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(guild_id,business_id)
            DO UPDATE SET
                cycle_days=excluded.cycle_days,
                updated_by=excluded.updated_by,
                updated_at=excluded.updated_at
        """, (
            gid, bid, cycle_days, uid, int(time.time()),
        ))

    return f"✅ 已設定公司每{cycle_days}天發薪一次。"


def pay_salary(
    guild_id,
    business_id,
    actor_id,
    character_id,
):
    """支付最新一期已到期薪資，禁止重複扣款。"""
    from modules.company_workforce import (
        initialize as initialize_workforce,
    )
    from modules.application_extras import (
        init_db as initialize_applications,
    )
    from modules.company_treasury import (
        _finance_permission,
        _is_owner,
        _manager_limit,
    )

    initialize_workforce()
    initialize_applications()
    initialize_payroll()

    gid = str(guild_id)
    bid = str(business_id)
    uid = str(actor_id)
    cid = int(character_id)

    if cid <= 0:
        raise ValueError("角色編號必須大於零")

    with transaction() as db:
        if not _finance_permission(db, gid, bid, uid):
            raise PermissionError("沒有公司發薪權限")

        policy = db.execute("""
            SELECT cycle_days
            FROM rp_company_payroll_settings
            WHERE guild_id=? AND business_id=?
        """, (gid, bid)).fetchone()

        if policy is None:
            raise ValueError(
                "公司尚未設定7天或14天發薪週期"
            )

        employee = db.execute("""
            SELECT
                p.salary,
                e.hired_at,
                c.owner_id
            FROM company_employees e
            JOIN company_job_positions p
              ON p.guild_id=e.guild_id
             AND p.company_id=e.company_id
             AND p.rank=e.rank
            JOIN characters c
              ON c.guild_id=e.guild_id
             AND c.id=e.character_id
            WHERE e.guild_id=?
              AND e.company_id=?
              AND e.character_id=?
              AND e.status='active'
        """, (gid, bid, cid)).fetchone()

        if employee is None:
            raise ValueError(
                "員工不存在、已離職或職位未設定"
            )

        from core.company_contract_store import initialize_contracts
        from core.company_contract_payroll_link import resolve_payroll_terms
        initialize_contracts(db)
        terms = resolve_payroll_terms(db, gid, bid, cid, employee["salary"], policy["cycle_days"])
        amount = terms.salary
        contractual_salary = int(amount)

        # 等級薪資倍率由正式地位規則統一計算。
        try:
            from modules import character_status
            character_status.initialize()
            state = db.execute("SELECT level FROM rp_character_status WHERE guild_id=? AND character_id=?", (gid, cid)).fetchone()
            level = max(1, min(20, int(state["level"]) if state else 1))
            amount = character_status.minimum_work_pay(amount, level)
        except (ImportError, AttributeError):
            level = 1

        period = payroll_period(
            employee["hired_at"],
            int(policy["cycle_days"]),
        )

        if period is None:
            raise ValueError(
                "尚未到達第一個發薪日"
            )

        # 舊版按月發薪紀錄不能無條件轉換，
        # 否則可能對同一段工作期間重複付款。
        legacy = db.execute("""
            SELECT 1
            FROM rp_company_payroll
            WHERE guild_id=? AND business_id=?
              AND character_id=?
              AND pay_period GLOB
                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
            LIMIT 1
        """, (gid, bid, cid)).fetchone()

        if legacy:
            raise ValueError(
                "員工有舊制月薪紀錄，"
                "請先核對並遷移薪資期，"
                "避免重複支付。"
            )

        # Select the oldest unpaid period so historical arrears can be repaid
        # without skipping earlier completed work cycles.
        from core.company_payroll_period import parse_time, PayrollPeriod
        hired = parse_time(employee["hired_at"])
        cycle = int(policy["cycle_days"])
        step = timedelta(days=cycle)
        completed = int((period.end - hired) // step)
        anchor = hired.strftime("%Y%m%dT%H%M%S%fZ")
        if completed > 520:
            raise ValueError("薪資歷史超過 520 期，請先人工核對歷史資料")
        for ix in range(1, completed + 1):
            key = f"{cycle}d:{anchor}:{ix}"
            paid = db.execute("SELECT 1 FROM rp_company_payroll WHERE guild_id=? AND business_id=? AND character_id=? AND pay_period=?", (gid,bid,cid,key)).fetchone()
            if not paid:
                end = hired + ix*step
                period = PayrollPeriod(key, end-step, end, cycle)
                break
        else:
            return f"ℹ️ 角色 #{cid} 所有已到期薪資均已發放。"

        existing = db.execute("""
            SELECT 1 FROM rp_company_payroll
            WHERE guild_id=? AND business_id=?
              AND character_id=? AND pay_period=?
        """, (
            gid, bid, cid, period.key,
        )).fetchone()

        if existing:
            return (
                f"ℹ️ 角色 #{cid} 本期薪資已發放，"
                "不會重複扣款。"
            )

        if not _is_owner(db, gid, bid, uid):
            limit = _manager_limit(db, gid, bid, uid)
            if limit <= 0 or amount > limit:
                raise PermissionError(
                    "薪資超出財務管理員單筆額度"
                )

        bank = db.execute("""
            SELECT account_id,status
            FROM rp_company_bank_accounts
            WHERE guild_id=? AND company_id=?
        """, (gid, bid)).fetchone()

        if bank is None:
            raise ValueError("公司尚未正式開立銀行帳戶")

        if bank["status"] != "active":
            raise PermissionError(
                "公司銀行帳戶已凍結，禁止發薪"
            )

        receiver = get_or_create_account(
            db, gid, "character", str(cid), "wallet"
        )

        reference = (
            f"company-payroll:{gid}:{bid}:"
            f"{cid}:{period.key}"
        )

        result = _payment(
            db,
            gid,
            reference,
            int(bank["account_id"]),
            receiver,
            amount,
            f"公司 {bid}｜{payday_text(period)}"
            f"｜角色 #{cid}",
        )

        if result.duplicated:
            raise RuntimeError(
                "帳本已存在同筆付款，"
                "但薪資紀錄缺失；請管理員核對。"
            )

        now_ts = int(time.time())

        db.execute("""
            INSERT INTO rp_company_payroll
            (
                guild_id,business_id,character_id,
                pay_period,amount,paid_by,
                reference_key,paid_at
            )
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            gid, bid, cid,
            period.key, amount, uid,
            reference, now_ts,
        ))

        # A salary snapshot is immutable evidence for this paid period.
        # It does not pretend to reconstruct older, unpaid salary revisions.
        from core.company_salary_snapshots import save_in_transaction
        save_in_transaction(db,gid,bid,cid,period.key,
                            employee["salary"],contractual_salary,
                            amount,level,int(policy["cycle_days"]),reference)

        db.execute("""
            INSERT INTO rp_company_finance_audit
            (
                guild_id,business_id,actor_id,
                action,reference_key,amount,
                target_type,target_id,note,created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            gid, bid, uid, "payroll", reference,
            amount, "character", str(cid),
            payday_text(period), now_ts,
        ))

    from core.company_finance_events import notify_payment
    notify_payment(gid,bid,cid,amount,"薪資入帳",reference)
    return (
        f"✅ 已支付角色 #{cid} 的薪資 "
        f"{amount:,} 金幣。\n"
        f"發薪期：{payday_text(period)}"
    )


def payroll_cycle_help():
    """提供 UI 的正式薪資規則摘要。"""
    return ("公司由持有人選擇每 7 或 14 天發薪；每期只可入帳一次。"
            "薪資由已開通的公司銀行帳戶扣除，支付至角色錢包。"
            "未開戶、餘額不足或舊月薪尚未遷移時停止支付，不會虛增金幣。")


def payroll_status(guild_id, business_id, actor_id):
    from core.database import get_connection
    from core.company_payroll_period import payroll_period, payday_text
    from modules.company_treasury import _finance_permission
    from modules.company_workforce import initialize as init_workforce
    init_workforce()
    initialize_payroll()
    gid, bid, uid = str(guild_id), str(business_id), str(actor_id)
    with get_connection() as db:
        if not _finance_permission(db, gid, bid, uid):
            raise PermissionError("沒有公司財務查閱權限。")
        settings = db.execute("SELECT cycle_days FROM rp_company_payroll_settings WHERE guild_id=? AND business_id=?",(gid,bid)).fetchone()
        if not settings:
            return "⚠️ 公司尚未設定 7／14 天發薪週期。請由持有人先設定。"
        cycle = int(settings['cycle_days'])
        rows = db.execute("""SELECT e.character_id,e.hired_at,p.salary FROM company_employees e
          JOIN company_job_positions p ON p.guild_id=e.guild_id AND p.company_id=e.company_id AND p.rank=e.rank
          WHERE e.guild_id=? AND e.company_id=? AND e.status='active' ORDER BY e.character_id LIMIT 40""",(gid,bid)).fetchall()
        result = [f"📅 **公司 {bid}｜薪資概況**", f"發薪週期：每 {cycle} 天", "（僅列前 40 名在職員工）"]
        for row in rows:
            cid = int(row['character_id'])
            period = payroll_period(row['hired_at'],cycle)
            if period is None:
                status = '尚未到期'
            else:
                existing = db.execute("SELECT 1 FROM rp_company_payroll WHERE guild_id=? AND business_id=? AND character_id=? AND pay_period=?",(gid,bid,cid,period.key)).fetchone()
                legacy = db.execute("SELECT 1 FROM rp_company_payroll WHERE guild_id=? AND business_id=? AND character_id=? AND pay_period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]' LIMIT 1",(gid,bid,cid)).fetchone()
                status = '⚠️ 舊月薪待核對' if legacy else ('本期已付（歷史期可能未付）' if existing else '待發放：'+payday_text(period))
            result.append(f"角色 #{cid}｜{int(row['salary']):,} 金幣｜{status}")
        return "\n".join(result)[:1900] if rows else "目前沒有在職且已設定薪資的員工。"
