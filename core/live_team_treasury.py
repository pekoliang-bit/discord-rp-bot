"""直播團隊金庫：角色存款、成員雙人同意提款、同一帳本原子交易。

僅提供 /網路 面板呼叫，不新增任何斜線指令。
一筆提款由隊長提出、另一名已啟用團隊成員同意；收款人須為團隊成員。
沒有第二名成員時，提款申請不能執行，以保障玩家資產。
"""
from __future__ import annotations
import time
from core.database import transaction, get_connection, get_or_create_account, account_balance
from core import live_teams as teams

DDL = (
    """CREATE TABLE IF NOT EXISTS rp_live_treasury_requests (
       id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
       team_id INTEGER NOT NULL, requester_character_id INTEGER NOT NULL,
       recipient_character_id INTEGER NOT NULL, amount INTEGER NOT NULL CHECK(amount>0),
       status TEXT NOT NULL CHECK(status IN ('pending','approved','cancelled','expired')),
       created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
       approver_character_id INTEGER, approved_at INTEGER,
       UNIQUE(guild_id,id))""",
)

def _init(db):
    teams._init(db)
    for ddl in DDL: db.execute(ddl)

def _owned(db, gid, uid, cid):
    row=db.execute("SELECT 1 FROM characters WHERE guild_id=? AND owner_id=? AND id=? AND status='active'",(gid,str(uid),int(cid))).fetchone()
    if not row: raise PermissionError('只能操作自己的已啟用角色')

def _member(db, gid, tid, cid):
    row=db.execute("SELECT 1 FROM rp_live_team_members WHERE guild_id=? AND team_id=? AND character_id=? AND state='active'",(gid,int(tid),int(cid))).fetchone()
    if not row: raise PermissionError('角色不是該團隊正式成員')

def _team(db,gid,tid):
    row=db.execute('SELECT * FROM rp_live_teams WHERE guild_id=? AND id=?',(gid,int(tid))).fetchone()
    if not row: raise LookupError('找不到直播團隊')
    return row

def _balance(db,gid,tid):
    account=get_or_create_account(db,gid,'live_team',str(tid),'treasury')
    return account,account_balance(db,gid,account)

def _transfer(db,gid,from_ac,to_ac,amount,key,description):
    if amount<=0 or from_ac==to_ac: raise ValueError('轉帳金額或帳戶無效')
    prior=db.execute('SELECT id FROM ledger_transactions WHERE guild_id=? AND unique_key=?',(gid,key)).fetchone()
    if prior: return False
    if account_balance(db,gid,from_ac)<amount: raise ValueError('金幣不足')
    tx=db.execute('INSERT INTO ledger_transactions(guild_id,unique_key,description) VALUES(?,?,?)',(gid,key,description)).lastrowid
    db.executemany('INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount) VALUES(?,?,?,?)',[(gid,tx,from_ac,-amount),(gid,tx,to_ac,amount)])
    return True

def find_team(guild_id,user_id,character_id):
    gid,cid=str(guild_id),int(character_id)
    with get_connection() as db:
        _init(db); _owned(db,gid,user_id,cid)
        row=db.execute("""SELECT t.* FROM rp_live_teams t JOIN rp_live_team_members m ON m.team_id=t.id AND m.guild_id=t.guild_id WHERE t.guild_id=? AND m.character_id=? AND m.state='active' LIMIT 1""",(gid,cid)).fetchone()
        return dict(row) if row else None

def pending_invites(guild_id,user_id,character_id):
    gid,cid=str(guild_id),int(character_id)
    with get_connection() as db:
        _init(db); _owned(db,gid,user_id,cid)
        return [dict(r) for r in db.execute("""SELECT t.id,t.name FROM rp_live_team_members m JOIN rp_live_teams t ON t.id=m.team_id AND t.guild_id=m.guild_id WHERE m.guild_id=? AND m.character_id=? AND m.state='invited' ORDER BY m.invited_at DESC""",(gid,cid)).fetchall()]

def balance(guild_id,user_id,character_id,team_id):
    gid=str(guild_id)
    with transaction() as db:
        _init(db);_owned(db,gid,user_id,character_id);_member(db,gid,team_id,character_id)
        _team(db,gid,team_id)
        return _balance(db,gid,team_id)[1]

def deposit(guild_id,user_id,character_id,team_id,amount,request_key):
    gid,cid,tid=str(guild_id),int(character_id),int(team_id)
    if type(amount) is not int or amount<=0: raise ValueError('必須存入正整數金幣')
    with transaction() as db:
        _init(db);_owned(db,gid,user_id,cid);_member(db,gid,tid,cid)
        treasury,_=_balance(db,gid,tid)
        wallet=get_or_create_account(db,gid,'character',cid,'wallet')
        return _transfer(db,gid,wallet,treasury,amount,f'live-team-deposit:{request_key}',f'直播團隊 #{tid} 金庫存入')

def request_withdrawal(guild_id,user_id,leader_character_id,team_id,recipient_character_id,amount,*,now=None):
    gid,tid,cid,rid=str(guild_id),int(team_id),int(leader_character_id),int(recipient_character_id)
    if type(amount) is not int or amount<=0: raise ValueError('金額必須是正整數')
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db);_owned(db,gid,user_id,cid);_member(db,gid,tid,cid)
        team=_team(db,gid,tid)
        if int(team['leader_character_id'])!=cid: raise PermissionError('只有團隊隊長可提出提款')
        _member(db,gid,tid,rid)
        count=db.execute("SELECT COUNT(*) FROM rp_live_team_members WHERE guild_id=? AND team_id=? AND state='active'",(gid,tid)).fetchone()[0]
        if count<2: raise ValueError('至少需要兩名正式成員才可提出提款')
        _,available=_balance(db,gid,tid)
        if available<amount: raise ValueError('團隊金庫餘額不足')
        reserved=db.execute("SELECT COALESCE(SUM(amount),0) FROM rp_live_treasury_requests WHERE guild_id=? AND team_id=? AND status='pending' AND expires_at>?",(gid,tid,ts)).fetchone()[0]
        if available-reserved<amount: raise ValueError('扣除待審提款後金庫餘額不足')
        return db.execute("INSERT INTO rp_live_treasury_requests(guild_id,team_id,requester_character_id,recipient_character_id,amount,status,created_at,expires_at) VALUES(?,?,?,?,?,'pending',?,?)",(gid,tid,cid,rid,amount,ts,ts+86400)).lastrowid

def pending_withdrawals(guild_id,user_id,character_id):
    gid,cid=str(guild_id),int(character_id)
    with get_connection() as db:
        _init(db);_owned(db,gid,user_id,cid)
        return [dict(r) for r in db.execute("""SELECT r.* FROM rp_live_treasury_requests r JOIN rp_live_team_members m ON m.guild_id=r.guild_id AND m.team_id=r.team_id WHERE r.guild_id=? AND m.character_id=? AND m.state='active' AND r.status='pending' AND r.expires_at>? ORDER BY r.id DESC LIMIT 20""",(gid,cid,int(time.time()))).fetchall()]

def decide_withdrawal(guild_id,user_id,approver_character_id,request_id,approve=True,*,now=None):
    gid,cid=str(guild_id),int(approver_character_id)
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db);_owned(db,gid,user_id,cid)
        req=db.execute('SELECT * FROM rp_live_treasury_requests WHERE guild_id=? AND id=?',(gid,int(request_id))).fetchone()
        if not req or req['status']!='pending': raise ValueError('提款申請不存在或已經處理')
        _member(db,gid,req['team_id'],cid)
        if cid==req['requester_character_id']: raise PermissionError('申請人不可自行審核')
        if ts>=req['expires_at']:
            db.execute("UPDATE rp_live_treasury_requests SET status='expired' WHERE id=? AND guild_id=?",(int(request_id),gid))
            return 'expired'
        if not approve:
            db.execute("UPDATE rp_live_treasury_requests SET status='cancelled',approver_character_id=?,approved_at=? WHERE guild_id=? AND id=?",(cid,ts,gid,int(request_id)))
            return 'cancelled'
        _member(db,gid,req['team_id'],req['recipient_character_id'])
        treasury,_=_balance(db,gid,req['team_id'])
        recipient=get_or_create_account(db,gid,'character',req['recipient_character_id'],'wallet')
        _transfer(db,gid,treasury,recipient,int(req['amount']),f"live-team-withdraw:{req['id']}",f"直播團隊 #{req['team_id']} 雙人審核提款")
        db.execute("UPDATE rp_live_treasury_requests SET status='approved',approver_character_id=?,approved_at=? WHERE guild_id=? AND id=?",(cid,ts,gid,int(request_id)))
        return 'approved'
