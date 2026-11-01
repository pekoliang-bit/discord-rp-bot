"""直播團隊、成員同意、團播分潤與金庫（資料層，不新增 Discord 指令）。

所有金額以整數金幣和萬分比（10000=100%）計算。
隊伍建立／加入需角色本人操作；分潤方案必須由全部直播成員逐一同意。
正式贈禮結算由 core.live_social.gift 呼叫 resolve_gift_recipients_in_transaction。
"""
from __future__ import annotations

import time
from core.database import transaction, get_connection, get_or_create_account
from core import live_stream as live

DDL = (
    """CREATE TABLE IF NOT EXISTS rp_live_teams (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
      name TEXT NOT NULL, leader_character_id INTEGER NOT NULL,
      treasury_enabled INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
      UNIQUE(guild_id,name))""",
    """CREATE TABLE IF NOT EXISTS rp_live_team_members (
      guild_id TEXT NOT NULL, team_id INTEGER NOT NULL, character_id INTEGER NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('invited','active','declined')),
      invited_at INTEGER NOT NULL, accepted_at INTEGER,
      PRIMARY KEY(guild_id,team_id,character_id))""",
    """CREATE TABLE IF NOT EXISTS rp_live_split_plans (
      guild_id TEXT NOT NULL, session_id INTEGER NOT NULL, version INTEGER NOT NULL,
      allocations TEXT NOT NULL, created_by INTEGER NOT NULL,
      created_at INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','active','rejected')),
      PRIMARY KEY(guild_id,session_id,version))""",
    """CREATE TABLE IF NOT EXISTS rp_live_split_approvals (
      guild_id TEXT NOT NULL, session_id INTEGER NOT NULL, version INTEGER NOT NULL,
      character_id INTEGER NOT NULL, approved INTEGER NOT NULL,
      PRIMARY KEY(guild_id,session_id,version,character_id))""",
    """CREATE TABLE IF NOT EXISTS rp_live_gift_payouts (
      guild_id TEXT NOT NULL, gift_id INTEGER NOT NULL,
      recipient_type TEXT NOT NULL, recipient_id TEXT NOT NULL,
      amount INTEGER NOT NULL CHECK(amount > 0),
      PRIMARY KEY(guild_id,gift_id,recipient_type,recipient_id))""",
)


def _init(db):
    live._init(db)
    for query in DDL:
        db.execute(query)


def _owned(db, gid, user_id, cid):
    row = db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'", (gid, int(cid), str(user_id))).fetchone()
    if not row:
        raise PermissionError('只能操作自己已啟用的角色')


def create_team(guild_id, user_id, leader_character_id, name, *, now=None):
    gid, cid = str(guild_id), int(leader_character_id)
    name = str(name).strip()
    if not 2 <= len(name) <= 40:
        raise ValueError('團隊名稱必須為 2～40 字')
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        _owned(db, gid, user_id, cid)
        exists = db.execute("SELECT 1 FROM rp_live_team_members WHERE guild_id=? AND character_id=? AND state='active'", (gid,cid)).fetchone()
        if exists:
            raise ValueError('角色已經加入直播團隊')
        team_id = db.execute('INSERT INTO rp_live_teams(guild_id,name,leader_character_id,created_at) VALUES(?,?,?,?)', (gid,name,cid,ts)).lastrowid
        db.execute("INSERT INTO rp_live_team_members(guild_id,team_id,character_id,state,invited_at,accepted_at) VALUES(?,?,?,'active',?,?)", (gid,team_id,cid,ts,ts))
        return team_id


def invite_team_member(guild_id, leader_user_id, team_id, target_character_id, *, now=None):
    gid, tid, cid = str(guild_id), int(team_id), int(target_character_id)
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        team = db.execute('SELECT * FROM rp_live_teams WHERE guild_id=? AND id=?',(gid,tid)).fetchone()
        if not team:
            raise LookupError('找不到直播團隊')
        _owned(db,gid,leader_user_id,team['leader_character_id'])
        target = db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND status='active'",(gid,cid)).fetchone()
        if not target or cid == team['leader_character_id']:
            raise ValueError('受邀角色無效')
        active = db.execute("SELECT 1 FROM rp_live_team_members WHERE guild_id=? AND character_id=? AND state='active'",(gid,cid)).fetchone()
        if active:
            raise ValueError('該角色已在直播團隊')
        db.execute("INSERT INTO rp_live_team_members(guild_id,team_id,character_id,state,invited_at) VALUES(?,?,?,'invited',?) ON CONFLICT(guild_id,team_id,character_id) DO UPDATE SET state='invited',invited_at=excluded.invited_at,accepted_at=NULL", (gid,tid,cid,ts))


def respond_team_invite(guild_id, user_id, team_id, character_id, accept, *, now=None):
    gid, tid, cid = str(guild_id), int(team_id), int(character_id)
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        _owned(db,gid,user_id,cid)
        row = db.execute("SELECT state FROM rp_live_team_members WHERE guild_id=? AND team_id=? AND character_id=?",(gid,tid,cid)).fetchone()
        if not row or row['state'] != 'invited':
            raise ValueError('沒有待處理的團隊邀請')
        if accept and db.execute("SELECT 1 FROM rp_live_team_members WHERE guild_id=? AND character_id=? AND state='active'",(gid,cid)).fetchone():
            raise ValueError('角色已有直播團隊')
        state = 'active' if accept else 'declined'
        db.execute("UPDATE rp_live_team_members SET state=?,accepted_at=? WHERE guild_id=? AND team_id=? AND character_id=?", (state,ts if accept else None,gid,tid,cid))
        return state


def team_info(guild_id, team_id):
    with get_connection() as db:
        _init(db)
        row = db.execute('SELECT * FROM rp_live_teams WHERE guild_id=? AND id=?',(str(guild_id),int(team_id))).fetchone()
        if not row:
            raise LookupError('找不到直播團隊')
        members = [dict(r) for r in db.execute("SELECT * FROM rp_live_team_members WHERE guild_id=? AND team_id=? ORDER BY character_id",(str(guild_id),int(team_id))).fetchall()]
        return {'team':dict(row),'members':members}


def _member_ids(db, gid, session_id):
    return [int(r['character_id']) for r in db.execute('SELECT character_id FROM rp_live_members WHERE guild_id=? AND session_id=? ORDER BY character_id',(gid,int(session_id))).fetchall()]


def propose_split(guild_id, host_user_id, session_id, percentages, *, now=None):
    """主持人提出分潤百分比映射 {角色ID:整數基點}; 全員同意後才啟用。"""
    import json
    gid, sid = str(guild_id), int(session_id)
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        session = db.execute("SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=? AND status='live'",(gid,sid)).fetchone()
        if not session or session['stream_type'] != 'group' or session['owner_id'] != str(host_user_id):
            raise PermissionError('只有團播主持人可以提出分潤')
        members = _member_ids(db,gid,sid)
        p = {int(k):int(v) for k,v in percentages.items()}
        if not members or set(p) != set(members) or any(type(v) is not int or v < 0 for v in p.values()) or sum(p.values()) != 10000:
            raise ValueError('必須包含全部在場角色，分潤基點合計 10000')
        version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM rp_live_split_plans WHERE guild_id=? AND session_id=?',(gid,sid)).fetchone()[0]
        db.execute("INSERT INTO rp_live_split_plans(guild_id,session_id,version,allocations,created_by,created_at,state) VALUES(?,?,?,?,?,?,'pending')",(gid,sid,version,json.dumps(p),int(session['host_character_id']),ts))
        return version


def approve_split(guild_id, user_id, character_id, session_id, version, approve=True):
    gid,sid,cid=str(guild_id),int(session_id),int(character_id)
    with transaction() as db:
        _init(db)
        _owned(db,gid,user_id,cid)
        session=db.execute("SELECT stream_type,status FROM rp_live_sessions WHERE guild_id=? AND id=?",(gid,sid)).fetchone()
        if not session or session['status']!='live' or session['stream_type']!='group':
            raise ValueError('直播已結束或不是團播')
        members=_member_ids(db,gid,sid)
        if cid not in members:
            raise PermissionError('角色不是直播成員')
        plan=db.execute("SELECT * FROM rp_live_split_plans WHERE guild_id=? AND session_id=? AND version=?",(gid,sid,int(version))).fetchone()
        if not plan or plan['state']!='pending':
            raise ValueError('分潤方案不存在或已完成')
        if not approve:
            db.execute("UPDATE rp_live_split_plans SET state='rejected' WHERE guild_id=? AND session_id=? AND version=?",(gid,sid,int(version)))
            return 'rejected'
        db.execute('INSERT INTO rp_live_split_approvals(guild_id,session_id,version,character_id,approved) VALUES(?,?,?,?,1) ON CONFLICT(guild_id,session_id,version,character_id) DO UPDATE SET approved=1',(gid,sid,int(version),cid))
        approved={int(r[0]) for r in db.execute('SELECT character_id FROM rp_live_split_approvals WHERE guild_id=? AND session_id=? AND version=? AND approved=1',(gid,sid,int(version))).fetchall()}
        if approved==set(members):
            import json
            if set(map(int,json.loads(plan['allocations']))) != set(members):
                raise ValueError('成員名單已變動，請重新提出方案')
            db.execute("UPDATE rp_live_split_plans SET state='rejected' WHERE guild_id=? AND session_id=? AND state='active'",(gid,sid))
            db.execute("UPDATE rp_live_split_plans SET state='active' WHERE guild_id=? AND session_id=? AND version=?",(gid,sid,int(version)))
            return 'active'
        return 'pending'


def resolve_gift_recipients_in_transaction(db, gid, session, net):
    """在同一筆真實禮物交易中計算收款分配；不可在外部再次入帳。"""
    import json
    sid=int(session['id'])
    _init(db)
    if session['stream_type'] != 'group':
        return [('character',str(session['host_character_id']),net)] if net else []
    members=_member_ids(db,gid,sid)
    if not members:
        raise RuntimeError('團播沒有成員')
    plan=db.execute("SELECT allocations FROM rp_live_split_plans WHERE guild_id=? AND session_id=? AND state='active' ORDER BY version DESC LIMIT 1",(gid,sid)).fetchone()
    if plan:
        p={int(k):int(v) for k,v in json.loads(plan['allocations']).items()}
        if set(p)!=set(members):
            # 新成員加入後原方案失效；恢復平均分，杜絕分潤漏洞。
            p=None
    else:
        p=None
    if p is None:
        share,remainder=divmod(10000,len(members))
        p={cid:share+(1 if i<remainder else 0) for i,cid in enumerate(members)}
    allocation={cid:net*bp//10000 for cid,bp in p.items()}
    remainder=net-sum(allocation.values())
    # 最多幾枚的整數尾差一律給主持人（若主持人仍在團播）。
    receiver=int(session['host_character_id']) if int(session['host_character_id']) in allocation else members[0]
    allocation[receiver]+=remainder
    return [('character',str(cid),amt) for cid,amt in allocation.items() if amt>0]
