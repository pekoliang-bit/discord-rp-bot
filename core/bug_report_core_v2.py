"""問題通報正式獎勵帳本與待領補發；純資料層，不依賴 Discord。"""
from __future__ import annotations
from core.database import transaction, get_connection, account_balance, get_or_create_account
from core.economy import _payment
import logging
LOG=logging.getLogger(__name__)

def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_bug_report_snapshot_v2 (
        guild_id TEXT NOT NULL, message_id TEXT NOT NULL,
        author_id TEXT NOT NULL, is_beta INTEGER NOT NULL,
        posted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,message_id)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_bug_report_awards_v2 (
        guild_id TEXT NOT NULL, message_id TEXT NOT NULL,
        stage TEXT NOT NULL CHECK(stage IN ('ack','done')),
        author_id TEXT NOT NULL, amount INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','paid')),
        character_id INTEGER, operator_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        paid_at TEXT,
        PRIMARY KEY(guild_id,message_id,stage)
    )''')


def record_snapshot(guild_id, message_id, author_id, is_beta):
    with transaction() as db:
        setup(db)
        db.execute('''INSERT OR IGNORE INTO rp_bug_report_snapshot_v2
            (guild_id,message_id,author_id,is_beta) VALUES (?,?,?,?)''',
            (str(guild_id),str(message_id),str(author_id),int(bool(is_beta))))


def _choose_formal(db, guild_id, author_id):
    # 不把測試角或其已建立轉正草稿當作正式角色。
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_beta_characters'").fetchone():
        from core import beta_test_core as _beta
        # 初始化只在目前資料庫連線內進行，禁止巢狀 transaction()。
        db.execute("""CREATE TABLE IF NOT EXISTS rp_beta_characters (
           guild_id TEXT NOT NULL,owner_id TEXT NOT NULL,character_id INTEGER NOT NULL,
           status TEXT NOT NULL DEFAULT 'active',draft_id INTEGER,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
           updated_at TEXT,PRIMARY KEY(guild_id,owner_id),UNIQUE(guild_id,character_id))""")
    rows=db.execute('''SELECT c.id,c.name,a.id wallet_id
        FROM characters c LEFT JOIN accounts a ON a.guild_id=c.guild_id
          AND a.owner_type='character' AND a.owner_id=CAST(c.id AS TEXT)
          AND a.account_type='wallet'
        WHERE c.guild_id=? AND c.owner_id=? AND c.status='active'
          AND c.character_type='player'
          AND NOT EXISTS (SELECT 1 FROM rp_beta_characters b
              WHERE b.guild_id=c.guild_id AND b.character_id=c.id)
        ORDER BY c.id''',(str(guild_id),str(author_id))).fetchall()
    if not rows:return None
    return min(rows,key=lambda r:(account_balance(db,guild_id,r['wallet_id']) if r['wallet_id'] else 0,int(r['id'])))


def _pay(db, guild_id, message_id, stage, author_id, amount):
    target=_choose_formal(db,guild_id,author_id)
    if target is None:return None
    wallet=target['wallet_id'] or get_or_create_account(db,guild_id,'character',str(target['id']),'wallet')
    treasury=get_or_create_account(db,guild_id,'system','bug_report_rewards','treasury')
    _payment(db,guild_id,f'bug-report-v2:{message_id}:{stage}',treasury,wallet,int(amount),
             description=f'問題通報 {stage} | {message_id}')
    return dict(id=int(target['id']),name=target['name'])


def reward_once(guild_id,message_id,author_id,operator_id,stage,is_beta=None):
    """一次性建獎；有正式角色立即結清，否則 pending。"""
    if stage not in ('ack','done'):raise ValueError('未知通報處理階段')
    gid,mid,uid=str(guild_id),str(message_id),str(author_id)
    with transaction() as db:
        setup(db)
        snapshot=db.execute('''SELECT author_id,is_beta FROM rp_bug_report_snapshot_v2
            WHERE guild_id=? AND message_id=?''',(gid,mid)).fetchone()
        if snapshot is None:
            if is_beta is None:raise ValueError('缺少通報時身分紀錄')
            db.execute('''INSERT INTO rp_bug_report_snapshot_v2
                (guild_id,message_id,author_id,is_beta) VALUES (?,?,?,?)''',
                (gid,mid,uid,int(bool(is_beta))))
            snapshot={'author_id':uid,'is_beta':int(bool(is_beta))}
        if str(snapshot['author_id'])!=uid:raise PermissionError('通報作者與原始紀錄不符')
        existing=db.execute('''SELECT 1 FROM rp_bug_report_awards_v2
            WHERE guild_id=? AND message_id=? AND stage=?''',(gid,mid,stage)).fetchone()
        if existing:return None
        # 相容原版已付獎表：升級時不能把同一條 ‼️／🆗 再發一次。
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_bug_report_rewards'").fetchone():
            legacy=db.execute('''SELECT 1 FROM rp_bug_report_rewards
                WHERE guild_id=? AND message_id=? AND stage=?''',(gid,mid,stage)).fetchone()
            if legacy:return None
        if stage=='done':
            acknowledged=db.execute("""SELECT 1 FROM rp_bug_report_awards_v2
                WHERE guild_id=? AND message_id=? AND stage='ack'""",(gid,mid)).fetchone()
            if not acknowledged and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_bug_report_rewards'").fetchone():
                acknowledged=db.execute("SELECT 1 FROM rp_bug_report_rewards WHERE guild_id=? AND message_id=? AND stage='ack'",(gid,mid)).fetchone()
            if not acknowledged:
                raise ValueError('必須先以 ‼️ 標記收到，再以 🆗 結案')
        amount=1000 if stage=='done' or snapshot['is_beta'] else 500
        recipient=_pay(db,gid,mid,stage,uid,amount)
        state='paid' if recipient else 'pending'
        db.execute('''INSERT INTO rp_bug_report_awards_v2
            (guild_id,message_id,stage,author_id,amount,state,character_id,operator_id,paid_at)
            VALUES (?,?,?,?,?,?,?,?,CASE WHEN ?='paid' THEN CURRENT_TIMESTAMP ELSE NULL END)''',
            (gid,mid,stage,uid,amount,state,recipient['id'] if recipient else None,str(operator_id),state))
        return dict(amount=amount,state=state,recipient=recipient)


def settle_pending_for_owner(guild_id,owner_id):
    """角色正式啟用後呼叫，也可供排程補償；不可發到測試角。"""
    gid,uid=str(guild_id),str(owner_id)
    with transaction() as db:
        setup(db)
        if not _choose_formal(db,gid,uid):return []
        pending=db.execute('''SELECT message_id,stage,amount FROM rp_bug_report_awards_v2
            WHERE guild_id=? AND author_id=? AND state='pending'
            ORDER BY created_at,message_id,stage''',(gid,uid)).fetchall()
        results=[]
        for entry in pending:
            recipient=_pay(db,gid,entry['message_id'],entry['stage'],uid,entry['amount'])
            if recipient is None:break
            db.execute('''UPDATE rp_bug_report_awards_v2 SET state='paid',character_id=?,
                paid_at=CURRENT_TIMESTAMP WHERE guild_id=? AND message_id=? AND stage=? AND state='pending' ''',
                (recipient['id'],gid,entry['message_id'],entry['stage']))
            results.append(dict(message_id=entry['message_id'],stage=entry['stage'],
                                amount=entry['amount'],recipient=recipient))
        return results


def settle_all_pending():
    with get_connection() as db:
        try:
            rows=db.execute("SELECT DISTINCT guild_id,author_id FROM rp_bug_report_awards_v2 WHERE state='pending'").fetchall()
        except Exception:return 0
    total=0
    for row in rows:
        try:total+=len(settle_pending_for_owner(row['guild_id'],row['author_id']))
        except Exception:LOG.exception('待領通報獎勵補發失敗')
    return total

