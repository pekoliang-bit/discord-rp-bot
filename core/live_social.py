"""Discord RP 直播互動：團播邀請、同意和真實禮物帳本。

不建立斜線指令；僅供 /網路 UI 呼叫。資料庫必須先執行 core.database.init_db。
每次贈禮以 Discord interaction id 做冪等鍵，扣款與直播禮物紀錄在同一交易內。
"""
from __future__ import annotations

import time
from core.database import transaction, get_connection, get_or_create_account, account_balance
from core import live_stream as live

GIFT_VALUES = {"花束": 10, "應援燈": 100, "星光": 1000, "皇冠": 10000, "銀河": 100000, "傳奇": 500000}

DDL = (
    """CREATE TABLE IF NOT EXISTS rp_live_invitations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL, session_id INTEGER NOT NULL,
        inviter_character_id INTEGER NOT NULL, invitee_character_id INTEGER NOT NULL,
        invitee_owner_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        decided_at INTEGER,
        UNIQUE(guild_id,session_id,invitee_character_id),
        CHECK(status IN ('pending','accepted','declined','expired'))
    )""",
    """CREATE TABLE IF NOT EXISTS rp_live_gifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        session_id INTEGER NOT NULL, sender_character_id INTEGER NOT NULL,
        receiver_character_id INTEGER NOT NULL,
        gift_name TEXT NOT NULL, gross_amount INTEGER NOT NULL,
        platform_fee INTEGER NOT NULL, received_amount INTEGER NOT NULL,
        anonymous INTEGER NOT NULL DEFAULT 0,
        ledger_key TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(guild_id,ledger_key)
    )""",
)


def _init(db):
    live._init(db)
    for sql in DDL:
        db.execute(sql)


def _active_char(db, gid, uid, cid):
    row = db.execute("SELECT id, owner_id FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'", (gid, int(cid), str(uid))).fetchone()
    if row is None:
        raise PermissionError("只能使用自己已啟用的角色")
    return row


def _session(db, gid, sid):
    row = db.execute("SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=? AND status='live'", (gid, int(sid))).fetchone()
    if row is None:
        raise ValueError("該場直播不存在或已經結束")
    return row


def invite(guild_id, session_id, host_user_id, target_character_id, *, now=None):
    gid, uid = str(guild_id), str(host_user_id)
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        session = _session(db, gid, session_id)
        if session['stream_type'] != 'group':
            raise ValueError("只有團播場次可以邀請其他角色")
        if session['owner_id'] != uid:
            raise PermissionError("只有主持人可以發送團播邀請")
        target = db.execute("SELECT id,owner_id,name,status FROM characters WHERE guild_id=? AND id=?", (gid, int(target_character_id))).fetchone()
        if not target or target['status'] != 'active':
            raise ValueError("目標不是已啟用的角色")
        if target['id'] == session['host_character_id']:
            raise ValueError("不能邀請自己的主持角色")
        active = db.execute("""SELECT 1 FROM rp_live_members m JOIN rp_live_sessions s ON s.id=m.session_id AND s.guild_id=m.guild_id
            WHERE m.guild_id=? AND m.character_id=? AND s.status='live'""", (gid, target['id'])).fetchone()
        if active:
            raise ValueError("目標角色已經參加其他直播")
        db.execute("""INSERT INTO rp_live_invitations(guild_id,session_id,inviter_character_id,invitee_character_id,invitee_owner_id,status,created_at,expires_at)
            VALUES(?,?,?,?,?,'pending',?,?)
            ON CONFLICT(guild_id,session_id,invitee_character_id) DO UPDATE SET
                invitee_owner_id=excluded.invitee_owner_id, status='pending',
                created_at=excluded.created_at, expires_at=excluded.expires_at, decided_at=NULL""",
            (gid,int(session_id),int(session['host_character_id']),int(target_character_id),str(target['owner_id']),ts,ts+3600))
    return str(target['name'])


def pending_for_character(guild_id, owner_user_id, character_id, *, now=None):
    gid, uid, cid = str(guild_id), str(owner_user_id), int(character_id)
    ts = int(time.time() if now is None else now)
    with get_connection() as db:
        _init(db)
        _active_char(db,gid,uid,cid)
        return [dict(r) for r in db.execute("""SELECT i.* FROM rp_live_invitations i JOIN rp_live_sessions s
            ON s.guild_id=i.guild_id AND s.id=i.session_id
            WHERE i.guild_id=? AND i.invitee_character_id=? AND i.invitee_owner_id=? AND i.status='pending'
            AND i.expires_at>? AND s.status='live' ORDER BY i.id DESC LIMIT 10""", (gid,cid,uid,ts)).fetchall()]


def respond(guild_id, invitation_id, user_id, character_id, accept: bool, *, now=None):
    gid, uid, cid = str(guild_id), str(user_id), int(character_id)
    ts = int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        _active_char(db,gid,uid,cid)
        row = db.execute("SELECT * FROM rp_live_invitations WHERE guild_id=? AND id=? AND invitee_character_id=? AND invitee_owner_id=?", (gid,int(invitation_id),cid,uid)).fetchone()
        if row is None or row['status'] != 'pending':
            raise ValueError("邀請不存在或已經處理")
        if row['expires_at'] <= ts:
            db.execute("UPDATE rp_live_invitations SET status='expired',decided_at=? WHERE id=?",(ts,row['id']))
            return 'expired'
        session = _session(db,gid,row['session_id'])
        if accept:
            if session['stream_type'] != 'group':
                raise ValueError("該直播不是團播")
            active = db.execute("""SELECT 1 FROM rp_live_members m JOIN rp_live_sessions s ON s.guild_id=m.guild_id AND s.id=m.session_id
                WHERE m.guild_id=? AND m.character_id=? AND s.status='live'""",(gid,cid)).fetchone()
            if active:
                raise ValueError("角色已經在另一個直播間")
            cap=live.settings(gid)['max_members']
            count=db.execute("SELECT COUNT(*) FROM rp_live_members WHERE guild_id=? AND session_id=?",(gid,row['session_id'])).fetchone()[0]
            if count>=cap:
                raise ValueError("團播已達人數上限")
            db.execute("INSERT INTO rp_live_members(guild_id,session_id,character_id,owner_id,share_bp) VALUES(?,?,?,?,0)", (gid,row['session_id'],cid,uid))
        state='accepted' if accept else 'declined'
        db.execute("UPDATE rp_live_invitations SET status=?,decided_at=? WHERE guild_id=? AND id=?",(state,ts,gid,row['id']))
        return state


def gift(guild_id, session_id, sender_user_id, sender_character_id, gift_name, interaction_id, anonymous=False, *, now=None):
    """真正扣除角色金幣；收款給主播角色，平台手續費進系統帳戶。不可對自己贈禮。"""
    gid, uid, cid = str(guild_id),str(sender_user_id),int(sender_character_id)
    if gift_name not in GIFT_VALUES:
        raise ValueError("不支援的禮物")
    key=f"rp-live-gift:{interaction_id}"
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        prior=db.execute("SELECT * FROM rp_live_gifts WHERE guild_id=? AND ledger_key=?",(gid,key)).fetchone()
        if prior:
            if prior['sender_character_id'] != cid or prior['session_id'] != int(session_id):
                raise ValueError("贈禮請求編號衝突")
            return dict(prior)
        _active_char(db,gid,uid,cid)
        session=_session(db,gid,session_id)
        if cid==session['host_character_id'] or uid==session['owner_id']:
            raise ValueError("不能替自己的直播贈禮")
        gross=GIFT_VALUES[gift_name]
        cfg=live.settings(gid)
        fee_pct=int(cfg['platform_fee_pct'])
        fee=(gross*fee_pct)//100
        net=gross-fee
        payer=get_or_create_account(db,gid,'character',cid,'wallet')
        receiver=get_or_create_account(db,gid,'character',session['host_character_id'],'wallet')
        system=get_or_create_account(db,gid,'system','live_platform','wallet') if fee else None
        if payer==receiver:
            raise ValueError("不能向自己的帳戶付款")
        if account_balance(db,gid,payer)<gross:
            raise ValueError("角色錢包金幣不足")
        tx=db.execute("INSERT INTO ledger_transactions(guild_id,unique_key,description) VALUES(?,?,?)",(gid,key,'直播禮物 '+gift_name)).lastrowid
        entries=[(gid,tx,payer,-gross)]
        if net: entries.append((gid,tx,receiver,net))
        if fee: entries.append((gid,tx,system,fee))
        db.executemany("INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount) VALUES(?,?,?,?)",entries)
        db.execute("""INSERT INTO rp_live_gifts(guild_id,session_id,sender_character_id,receiver_character_id,gift_name,gross_amount,platform_fee,received_amount,anonymous,ledger_key,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(gid,int(session_id),cid,int(session['host_character_id']),gift_name,gross,fee,net,int(bool(anonymous)),key,ts))
        return dict(db.execute("SELECT * FROM rp_live_gifts WHERE guild_id=? AND ledger_key=?",(gid,key)).fetchone())


def gift_totals(guild_id,session_id):
    with get_connection() as db:
        _init(db)
        row=db.execute("SELECT COUNT(*) AS gifts,COALESCE(SUM(gross_amount),0) AS gross,COALESCE(SUM(received_amount),0) AS net FROM rp_live_gifts WHERE guild_id=? AND session_id=?",(str(guild_id),int(session_id))).fetchone()
        return dict(row)
