"""統一 T4–T10 任命邀請；跨玩家任命必須由藝人持有人確認。
所有生效頭銜只寫 rp_company_endorsements；不直接處理金幣。
"""
from __future__ import annotations
import sqlite3
from core.database import transaction, get_connection
from core.company_endorsement_store import initialize_endorsements, active_endorsements
from core.company_endorsement_rules import validate_endorsement, competitor_risk, displayed_endorsement


def initialize(db):
    initialize_endorsements(db)
    db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_appointment_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL, brand_id TEXT NOT NULL,
        character_id INTEGER NOT NULL, tier INTEGER NOT NULL CHECK(tier BETWEEN 4 AND 10),
        scope TEXT NOT NULL, offered_by TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined','cancelled')),
        endorsement_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT, decided_by TEXT
    )""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_endorsement_pending_invite
        ON rp_endorsement_appointment_invites(guild_id,brand_id,character_id,tier,scope)
        WHERE status='pending'""")


def _brand(db,gid,bid,uid):
    row=db.execute("SELECT owner_id,industry FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='brand' AND status='active'",(gid,bid)).fetchone()
    if not row: raise ValueError('品牌不存在或未啟用')
    if str(row['owner_id'])!=str(uid): raise PermissionError('只有品牌持有人能提出任命')
    return row


def _artist(db,gid,cid):
    row=db.execute("SELECT owner_id FROM characters WHERE guild_id=? AND id=? AND status='active'",(gid,cid)).fetchone()
    if not row: raise ValueError('代言角色不存在或尚未啟用')
    return row


def _activate(db,gid,bid,cid,tier,scope,actor):
    brand=_brand(db,gid,bid,actor)
    _artist(db,gid,cid)
    existing=active_endorsements(db,gid,character_id=cid)
    competing=any(r['brand_id']!=bid and r['industry_key']==str(brand['industry']) for r in existing)
    risk=competitor_risk([r['tier'] for r in existing],tier,competing_brand=competing)
    try:
        result=db.execute("""INSERT INTO rp_company_endorsements
            (guild_id,brand_id,character_id,tier,scope,industry_key,appointed_by)
            VALUES(?,?,?,?,?,?,?)""",(gid,bid,cid,tier,scope,str(brand['industry']),actor))
    except sqlite3.IntegrityError as exc:
        raise ValueError('此代言範圍或同角色頭銜已有有效任命') from exc
    return {'id':int(result.lastrowid),'title':displayed_endorsement(tier,scope),'risk':risk}


def request(guild_id,brand_id,character_id,tier,scope,actor_id):
    gid,bid,uid=str(guild_id),str(brand_id),str(actor_id)
    cid,tier=int(character_id),int(tier)
    scope=validate_endorsement(tier,scope)
    with transaction() as db:
        initialize(db)
        _brand(db,gid,bid,uid)
        artist=_artist(db,gid,cid)
        if str(artist['owner_id'])==uid:
            return {'status':'active',**_activate(db,gid,bid,cid,tier,scope,uid)}
        if any(r['brand_id']==bid and r['tier']==tier and r['scope']==scope for r in active_endorsements(db,gid,character_id=cid)):
            raise ValueError('同角色已有此品牌的有效頭銜')
        try:
            cur=db.execute("""INSERT INTO rp_endorsement_appointment_invites
                 (guild_id,brand_id,character_id,tier,scope,offered_by) VALUES(?,?,?,?,?,?)""",
                 (gid,bid,cid,tier,scope,uid))
        except sqlite3.IntegrityError as exc:
            raise ValueError('已有相同條件的待回覆任命') from exc
        return {'status':'pending','id':int(cur.lastrowid),'title':displayed_endorsement(tier,scope)}


def pending(guild_id, actor_id, limit=20):
    gid,uid=str(guild_id),str(actor_id)
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute("""SELECT i.id,i.brand_id,i.character_id,i.tier,i.scope,i.created_at
            FROM rp_endorsement_appointment_invites i JOIN characters c
            ON c.guild_id=i.guild_id AND c.id=i.character_id
            WHERE i.guild_id=? AND c.owner_id=? AND i.status='pending'
            ORDER BY i.id DESC LIMIT ?""",(gid,uid,max(1,min(50,int(limit)))))]


def decide(guild_id, invite_id, actor_id, accept):
    gid,uid=str(guild_id),str(actor_id)
    with transaction() as db:
        initialize(db)
        row=db.execute("SELECT * FROM rp_endorsement_appointment_invites WHERE guild_id=? AND id=?",(gid,int(invite_id))).fetchone()
        if not row or row['status']!='pending': raise ValueError('邀約不存在或已處理')
        artist=_artist(db,gid,int(row['character_id']))
        if str(artist['owner_id'])!=uid: raise PermissionError('只有該角色持有人能回覆邀約')
        if not accept:
            db.execute("UPDATE rp_endorsement_appointment_invites SET status='declined',decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?",(uid,row['id']))
            return {'status':'declined','id':int(row['id'])}
        result=_activate(db,gid,row['brand_id'],int(row['character_id']),int(row['tier']),row['scope'],str(row['offered_by']))
        db.execute("""UPDATE rp_endorsement_appointment_invites
            SET status='accepted',decided_by=?,decided_at=CURRENT_TIMESTAMP,endorsement_id=? WHERE id=?""",
            (uid,result['id'],row['id']))
        return {'status':'accepted',**result}


def activate_legacy_accepted(db,gid,brand_id,character_id,tier,scope,brand_actor):
    """舊邀約同意時，同交易寫入唯一正式任命；失敗則舊邀約及金流一起回滾。"""
    initialize(db)
    gid,bid,uid=str(gid),str(brand_id),str(brand_actor)
    cid,tier=int(character_id),int(tier)
    return _activate(db,gid,bid,cid,tier,validate_endorsement(tier,scope),uid)
