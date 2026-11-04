
"""Immutable salary evidence captured on each new payroll payment.
Historical unpaid periods are not retrospectively re-priced from current salaries.
"""
from __future__ import annotations
import time
from core.database import get_connection


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_company_salary_snapshots (
        guild_id TEXT NOT NULL, business_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        period_key TEXT NOT NULL, base_salary INTEGER NOT NULL,
        contractual_salary INTEGER NOT NULL, paid_amount INTEGER NOT NULL,
        status_level INTEGER NOT NULL, cycle_days INTEGER NOT NULL,
        reference_key TEXT NOT NULL, captured_at INTEGER NOT NULL,
        PRIMARY KEY (guild_id,business_id,character_id,period_key))''')


def save_in_transaction(db,gid,bid,cid,period_key,base_salary,contractual_salary,paid_amount,level,cycle_days,reference):
    if not db.in_transaction: raise RuntimeError('薪資快照必須隨發薪交易寫入')
    initialize(db)
    values=(str(gid),str(bid),int(cid),str(period_key),int(base_salary),int(contractual_salary),int(paid_amount),int(level),int(cycle_days),str(reference))
    old=db.execute('''SELECT base_salary,contractual_salary,paid_amount,status_level,cycle_days,reference_key
        FROM rp_company_salary_snapshots WHERE guild_id=? AND business_id=? AND character_id=? AND period_key=?''',values[:4]).fetchone()
    expected=values[4:]
    if old:
        if tuple(old)!=expected: raise RuntimeError('薪資快照與既有資料衝突，禁止覆蓋')
        return False
    db.execute('''INSERT INTO rp_company_salary_snapshots
        (guild_id,business_id,character_id,period_key,base_salary,contractual_salary,
        paid_amount,status_level,cycle_days,reference_key,captured_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',values+(int(time.time()),))
    return True


def salary_history(gid,bid,cid,actor_id):
    from modules.company_treasury import _finance_permission, initialize_treasury
    initialize_treasury()
    with get_connection() as db:
        initialize(db)
        character=db.execute('SELECT owner_id FROM characters WHERE guild_id=? AND id=?',(str(gid),int(cid))).fetchone()
        if not character: raise LookupError('角色不存在')
        if str(character['owner_id'])!=str(actor_id) and not _finance_permission(db,str(gid),str(bid),str(actor_id)):
            raise PermissionError('沒有查閱薪資紀錄的權限')
        return [dict(r) for r in db.execute('''SELECT period_key,base_salary,contractual_salary,paid_amount,status_level,cycle_days,captured_at
            FROM rp_company_salary_snapshots WHERE guild_id=? AND business_id=? AND character_id=? ORDER BY captured_at DESC LIMIT 50''',(str(gid),str(bid),int(cid)))]


def capture_position_revision(db,gid,bid,rank,new_salary,actor_id):
    """Store future position salary edits, without rewriting old payroll history."""
    initialize(db)
    db.execute("""CREATE TABLE IF NOT EXISTS rp_company_position_salary_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
        business_id TEXT NOT NULL,position_rank INTEGER NOT NULL,
        previous_salary INTEGER,new_salary INTEGER NOT NULL,
        actor_id TEXT NOT NULL,changed_at INTEGER NOT NULL)""")
    previous=db.execute('SELECT salary FROM company_job_positions WHERE guild_id=? AND company_id=? AND rank=?',
                        (str(gid),str(bid),int(rank))).fetchone()
    if previous is None or int(previous['salary'])!=int(new_salary):
        db.execute("""INSERT INTO rp_company_position_salary_history
            (guild_id,business_id,position_rank,previous_salary,new_salary,actor_id,changed_at)
            VALUES (?,?,?,?,?,?,?)""",
            (str(gid),str(bid),int(rank),int(previous['salary']) if previous else None,
             int(new_salary),str(actor_id),int(time.time())))
