"""已核准一般申請的具體業務執行器：請假及雙方同意的關係登記。

必須由 application_extras 的單一 SQLite transaction 呼叫。不得直接修改
application 的 status；審核中心在執行成功後才會提交 approved。
"""
from __future__ import annotations

import datetime as dt
import re


def initialize_general_cases(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_character_leaves (
        guild_id TEXT NOT NULL, application_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        reason TEXT NOT NULL, reviewer_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id, application_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_registered_partnerships (
        guild_id TEXT NOT NULL, character_a INTEGER NOT NULL, character_b INTEGER NOT NULL,
        relationship_kind TEXT NOT NULL,
        application_a INTEGER NOT NULL, application_b INTEGER NOT NULL,
        reviewer_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, character_a, character_b),
        CHECK (character_a < character_b))""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_rp_partnership_character_b
        ON rp_registered_partnerships(guild_id, character_b)""")


def _character(db, guild_id, cid, uid):
    r = db.execute("SELECT id, owner_id, status FROM characters "
                   "WHERE guild_id=? AND id=?", (str(guild_id), int(cid))).fetchone()
    if r is None or r['status'] != 'active' or str(r['owner_id']) != str(uid):
        raise ValueError('角色已停用，或申請人不是其擁有者。')
    return r


def _field(details, label):
    match = re.search(r'【'+re.escape(label)+r'】([^\n]*)', details or '')
    return match.group(1).strip() if match else ''


def execute_general_case(db, application, reviewer_id, *, owner_mode=False):
    kind = application['kind']
    if kind == 'leave':
        return _execute_leave(db, application, reviewer_id)
    if kind == 'cp_register':
        return _execute_partnership(db, application, reviewer_id, owner_mode)
    raise ValueError('此種類不屬於已接通的案件執行器。')


def _execute_leave(db, r, reviewer_id):
    if r['character_id'] is None:
        raise ValueError('請假必須指定自己的已啟用角色。')
    _character(db, r['guild_id'], r['character_id'], r['user_id'])
    raw = _field(r['details'], '開始與結束日期（YYYY-MM-DD 至 YYYY-MM-DD）')
    m = re.fullmatch(r'(\d{4}-\d{2}-\d{2})\s*(?:至|~|—|～)\s*(\d{4}-\d{2}-\d{2})', raw)
    if not m:
        raise ValueError('請假日期格式必須是 2026-11-09 至 2026-11-16。')
    try:
        start, end = [dt.date.fromisoformat(x) for x in m.groups()]
    except ValueError as exc:
        raise ValueError('請假日期無效。') from exc
    if end < start or (end-start).days > 365:
        raise ValueError('請假結束不能早於開始，且單次不得超過 366 天。')
    reason = _field(r['details'], '請假事由與需要暫停的義務')
    if not reason:
        raise ValueError('請假理由不能為空。')
    db.execute("""INSERT OR IGNORE INTO rp_character_leaves
       (guild_id,application_id,character_id,start_date,end_date,reason,reviewer_id)
       VALUES(?,?,?,?,?,?,?)""",
       (str(r['guild_id']),int(r['id']),int(r['character_id']),start.isoformat(),end.isoformat(),reason,str(reviewer_id)))
    return 'leave'


def _execute_partnership(db, r, reviewer_id, owner_mode):
    if r['character_id'] is None:
        raise ValueError('關係登記必須指定自己的角色。')
    gid = str(r['guild_id'])
    a = int(r['character_id'])
    _character(db, gid, a, r['user_id'])
    raw_target = _field(r['details'], '對方角色編號')
    if not raw_target.isdecimal():
        raise ValueError('請填寫有效的對方角色編號。')
    b = int(raw_target)
    if a == b:
        raise ValueError('不可與自己登記。')
    target = db.execute("SELECT id,owner_id,status FROM characters WHERE guild_id=? AND id=?",(gid,b)).fetchone()
    if target is None or target['status']!='active':
        raise ValueError('對方角色不存在或未啟用。')
    rel_kind = _field(r['details'],'關係類型（伴侶／婚姻）')
    if rel_kind not in {'伴侶','婚姻'}:
        raise ValueError('關係類型必須是「伴侶」或「婚姻」。')
    if not owner_mode and str(target['owner_id']) == str(reviewer_id):
        raise PermissionError('審核員不得間接核准自己角色的關係登記。')
    other = db.execute("""SELECT id,details FROM rp_general_applications
       WHERE guild_id=? AND kind='cp_register' AND character_id=?
         AND user_id=? AND status='pending' AND id<>?
       ORDER BY id DESC""",(gid,b,str(target['owner_id']),int(r['id']))).fetchall()
    other_id = None
    for candidate in other:
        if (_field(candidate['details'],'對方角色編號') == str(a)
                and _field(candidate['details'],'關係類型（伴侶／婚姻）')==rel_kind):
            other_id=int(candidate['id']); break
    if other_id is None:
        raise ValueError('對方尚未提交相互對應的關係申請，暫不能核准。')
    left,right=sorted([a,b])
    for cid in (left,right):
        exists=db.execute("""SELECT 1 FROM rp_registered_partnerships
            WHERE guild_id=? AND (character_a=? OR character_b=?)""",(gid,cid,cid)).fetchone()
        if exists:
            raise ValueError('其中一方已有有效登記關係，請先處理原有關係。')
    db.execute("""INSERT INTO rp_registered_partnerships
      (guild_id,character_a,character_b,relationship_kind,application_a,application_b,reviewer_id)
      VALUES(?,?,?,?,?,?,?)""",(gid,left,right,rel_kind,int(r['id']),other_id,str(reviewer_id)))
    # 相互送件代表兩位角色擁有者皆同意，雙件作為一組共同結案。
    updated=db.execute("""UPDATE rp_general_applications SET status='approved',
      reviewer_id=?,reviewed_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=? AND status='pending'""",
      (str(reviewer_id),gid,other_id))
    if updated.rowcount!=1:
        raise ValueError('對方的同意申請狀態已改變，請重試。')
    return 'cp_register'
