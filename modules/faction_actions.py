"""陣營暗害／防護提案紀錄（第十二階段基礎）。

僅建立待審提案與狀態紀錄；絕不扣款、施加效果或自動核准。
正式費用、成功率、持續時間及反制規則未核定前禁止執行。
"""
from __future__ import annotations

from core.database import transaction

ACTION_TYPES = frozenset({'sabotage', 'defense'})
STATUSES = frozenset({'draft', 'cancelled', 'rejected'})


def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            faction_id INTEGER NOT NULL,
            applicant_character_id INTEGER NOT NULL,
            request_key TEXT NOT NULL,
            action_type TEXT NOT NULL CHECK(action_type IN ('sabotage','defense')),
            target_text TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','cancelled','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id, request_key),
            FOREIGN KEY(guild_id,faction_id) REFERENCES faction_groups(guild_id,id)
        )''')
        db.execute('''CREATE INDEX IF NOT EXISTS faction_action_proposals_lookup
            ON faction_action_proposals(guild_id,faction_id,status,id)''')


def create_proposal(guild_id, faction_id, applicant_character_id, owner_id,
                    action_type, target_text, request_key, reason=''):
    """寫入未執行的草稿提案，重送相同 request_key 回傳同一筆 ID。"""
    if action_type not in ACTION_TYPES:
        raise ValueError('不支援的行動類型。')
    target_text = str(target_text).strip()
    reason = str(reason).strip()
    request_key = str(request_key).strip()
    if not 1 <= len(target_text) <= 150 or len(reason) > 500:
        raise ValueError('目標或原因長度不合法。')
    if not 1 <= len(request_key) <= 120:
        raise ValueError('請求編號不合法。')
    gid = str(guild_id)
    initialize()
    with transaction() as db:
        row = db.execute('''SELECT id, faction_id, applicant_character_id,
            action_type, target_text, reason FROM faction_action_proposals
            WHERE guild_id=? AND request_key=?''', (gid, request_key)).fetchone()
        if row:
            if (int(row['faction_id']), int(row['applicant_character_id']), row['action_type'],
                    row['target_text'], row['reason']) != (int(faction_id), int(applicant_character_id),
                       action_type, target_text, reason):
                raise ValueError('相同請求編號已被其他提案使用。')
            return int(row['id'])
        member = db.execute('''SELECT 1 FROM faction_members AS m
            JOIN characters AS c ON c.guild_id=m.guild_id AND c.id=m.character_id
            WHERE m.guild_id=? AND m.faction_id=? AND m.character_id=?
              AND c.owner_id=? AND c.status='active' AND c.character_type='player' ''',
            (gid, int(faction_id), int(applicant_character_id), str(owner_id))).fetchone()
        if not member:
            raise ValueError('只有陣營內已啟用角色的持有人可以提出提案。')
        cursor = db.execute('''INSERT INTO faction_action_proposals
            (guild_id,faction_id,applicant_character_id,request_key,action_type,target_text,reason)
            VALUES (?,?,?,?,?,?,?)''',
            (gid, int(faction_id), int(applicant_character_id), request_key, action_type, target_text, reason))
        return int(cursor.lastrowid)


def _assert_active_member(db, guild_id, faction_id, character_id, owner_id):
    member = db.execute('''SELECT 1 FROM faction_members AS m
        JOIN characters AS c ON c.guild_id=m.guild_id AND c.id=m.character_id
        WHERE m.guild_id=? AND m.faction_id=? AND m.character_id=?
          AND c.owner_id=? AND c.status='active' AND c.character_type='player' ''',
        (str(guild_id), int(faction_id), int(character_id), str(owner_id))).fetchone()
    if not member:
        raise ValueError('角色不再屬於此陣營，或沒有查看權限。')


def count_proposals(guild_id, faction_id, viewer_character_id, owner_id):
    """只允許目前陣營中的合法角色持有人查看提案數量。"""
    initialize()
    with transaction() as db:
        _assert_active_member(db, guild_id, faction_id, viewer_character_id, owner_id)
        return int(db.execute('''SELECT COUNT(*) FROM faction_action_proposals
            WHERE guild_id=? AND faction_id=?''',
            (str(guild_id), int(faction_id))).fetchone()[0])


def list_proposals(guild_id, faction_id, viewer_character_id, owner_id, *, limit=20, offset=0):
    """查看所屬陣營草稿（尚不執行），每次查詢都重新驗證權限。"""
    if not 1 <= int(limit) <= 50 or int(offset) < 0:
        raise ValueError('分頁範圍不合法。')
    initialize()
    with transaction() as db:
        _assert_active_member(db, guild_id, faction_id, viewer_character_id, owner_id)
        return [dict(row) for row in db.execute('''SELECT id, applicant_character_id,
            action_type, target_text, reason, status, created_at FROM faction_action_proposals
            WHERE guild_id=? AND faction_id=? ORDER BY id DESC LIMIT ? OFFSET ?''',
            (str(guild_id), int(faction_id), int(limit), int(offset))).fetchall()]


def cancel_proposal(guild_id, proposal_id, applicant_character_id, owner_id):
    """只能取消本人尚未處理的提案。"""
    initialize()
    with transaction() as db:
        cursor = db.execute('''UPDATE faction_action_proposals SET
            status='cancelled', updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND id=? AND applicant_character_id=? AND status='draft'
              AND EXISTS(SELECT 1 FROM characters c WHERE c.guild_id=?
                AND c.id=? AND c.owner_id=?)''',
            (str(guild_id), int(proposal_id), int(applicant_character_id),
             str(guild_id), int(applicant_character_id), str(owner_id)))
        if cursor.rowcount != 1:
            raise ValueError('提案無法取消，可能已取消或並非本人提出。')
        return True
