import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core.config import DATABASE_PATH


def get_connection():
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def transaction():
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 每一份 DDL 獨立執行，避免 sqlite3.executescript() 隱含 COMMIT
# 破壞外層 transaction() 的交易語意。
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS guilds (
        guild_id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL,
        age INTEGER,
        occupation TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        character_type TEXT NOT NULL DEFAULT 'player',
        stamina INTEGER NOT NULL DEFAULT 100,
        max_stamina INTEGER NOT NULL DEFAULT 100,
        public_code TEXT,
        settings_json TEXT NOT NULL DEFAULT '{}',
        version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (guild_id, id),
        UNIQUE (guild_id, public_code),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
        CHECK (length(trim(name)) > 0),
        CHECK (age IS NULL OR age BETWEEN 0 AND 200),
        CHECK (status IN ('draft','review_1','review_2','approved','active','retired','deleted')),
        CHECK (character_type IN ('player','player_npc','official_npc')),
        CHECK (stamina >= 0 AND stamina <= max_stamina),
        CHECK (max_stamina > 0)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_characters_owner ON characters(guild_id, owner_id)",
    """CREATE TABLE IF NOT EXISTS attributes (
        guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        value INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, character_id, name),
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id),
        CHECK (value >= 0)
    )""",
    """CREATE TABLE IF NOT EXISTS character_skills (
        guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        skill_name TEXT NOT NULL,
        skill_value INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, character_id, skill_name),
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id),
        CHECK (skill_value >= 0)
    )""",
    """CREATE TABLE IF NOT EXISTS organizations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'company',
        owner_character_id INTEGER,
        status TEXT NOT NULL DEFAULT 'active',
        settings_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
        FOREIGN KEY (guild_id, owner_character_id) REFERENCES characters(guild_id, id)
    )""",
    """CREATE TABLE IF NOT EXISTS organization_members (
        guild_id TEXT NOT NULL,
        organization_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, organization_id, character_id),
        FOREIGN KEY (guild_id, organization_id) REFERENCES organizations(guild_id, id),
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id)
    )""",
    """CREATE TABLE IF NOT EXISTS productions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        scale TEXT NOT NULL DEFAULT 'S1',
        owner_character_id INTEGER,
        organization_id INTEGER,
        status TEXT NOT NULL DEFAULT 'draft',
        template_version TEXT NOT NULL DEFAULT '1',
        baseline INTEGER NOT NULL DEFAULT 0,
        quality_score INTEGER,
        publicity_score INTEGER,
        result_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
        FOREIGN KEY (guild_id, owner_character_id) REFERENCES characters(guild_id, id),
        FOREIGN KEY (guild_id, organization_id) REFERENCES organizations(guild_id, id),
        CHECK (baseline >= 0),
        CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 100),
        CHECK (publicity_score IS NULL OR publicity_score BETWEEN 0 AND 100)
    )""",
    """CREATE TABLE IF NOT EXISTS production_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        production_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        capacity INTEGER NOT NULL DEFAULT 1,
        terms_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id, production_id) REFERENCES productions(guild_id, id),
        CHECK (capacity > 0)
    )""",
    """CREATE TABLE IF NOT EXISTS contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        production_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        position_id INTEGER,
        status TEXT NOT NULL DEFAULT 'draft',
        version INTEGER NOT NULL DEFAULT 1,
        fixed_salary INTEGER NOT NULL DEFAULT 0,
        terms_json TEXT NOT NULL DEFAULT '{}',
        qualification_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id, production_id) REFERENCES productions(guild_id, id),
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id),
        FOREIGN KEY (guild_id, position_id) REFERENCES production_positions(guild_id, id),
        CHECK (fixed_salary >= 0)
    )""",
    """CREATE TABLE IF NOT EXISTS contract_versions (
        guild_id TEXT NOT NULL,
        contract_id INTEGER NOT NULL,
        version INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, contract_id, version),
        FOREIGN KEY (guild_id, contract_id) REFERENCES contracts(guild_id, id)
    )""",
    """CREATE TABLE IF NOT EXISTS work_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        production_id INTEGER NOT NULL,
        contract_id INTEGER,
        character_id INTEGER NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'submitted',
        reviewer_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id, production_id) REFERENCES productions(guild_id, id),
        FOREIGN KEY (guild_id, contract_id) REFERENCES contracts(guild_id, id),
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id)
    )""",
    """CREATE TABLE IF NOT EXISTS schools (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        name TEXT NOT NULL,
        organization_id INTEGER,
        owner_character_id INTEGER,
        settings_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
        FOREIGN KEY (guild_id, organization_id) REFERENCES organizations(guild_id, id),
        FOREIGN KEY (guild_id, owner_character_id) REFERENCES characters(guild_id, id)
    )""",
    """CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        school_id INTEGER,
        title TEXT NOT NULL,
        subject TEXT NOT NULL,
        teacher_character_id INTEGER,
        capacity INTEGER,
        fee INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
        settings_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
        FOREIGN KEY (guild_id, school_id) REFERENCES schools(guild_id, id),
        FOREIGN KEY (guild_id, teacher_character_id) REFERENCES characters(guild_id, id),
        CHECK (fee >= 0),
        CHECK (capacity IS NULL OR capacity > 0)
    )""",
    """CREATE TABLE IF NOT EXISTS enrollments (
        guild_id TEXT NOT NULL,
        course_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'enrolled',
        progress INTEGER NOT NULL DEFAULT 0,
        enrolled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, course_id, character_id),
        FOREIGN KEY (guild_id, course_id) REFERENCES courses(guild_id, id),
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id),
        CHECK (progress BETWEEN 0 AND 100)
    )""",
    """CREATE TABLE IF NOT EXISTS training_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        skill_name TEXT NOT NULL,
        duration TEXT NOT NULL,
        stamina_cost INTEGER NOT NULL,
        skill_gain INTEGER NOT NULL,
        finished_at TEXT NOT NULL,
        next_available_at TEXT NOT NULL,
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id)
    )""",
    """CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        owner_type TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        account_type TEXT NOT NULL DEFAULT 'wallet',
        UNIQUE (guild_id, owner_type, owner_id, account_type),
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ledger_transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        unique_key TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (guild_id, unique_key),
        UNIQUE (guild_id, id),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ledger_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        transaction_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        FOREIGN KEY (guild_id, transaction_id) REFERENCES ledger_transactions(guild_id, id),
        FOREIGN KEY (guild_id, account_id) REFERENCES accounts(guild_id, id),
        CHECK (amount <> 0)
    )""",
    """CREATE TABLE IF NOT EXISTS receivables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        source_key TEXT NOT NULL,
        production_id INTEGER,
        beneficiary_type TEXT NOT NULL,
        beneficiary_id TEXT NOT NULL,
        total_amount INTEGER NOT NULL,
        remaining_amount INTEGER NOT NULL,
        budget_type TEXT NOT NULL DEFAULT 'market',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (guild_id, source_key, beneficiary_type, beneficiary_id, budget_type),
        FOREIGN KEY (guild_id, production_id) REFERENCES productions(guild_id, id),
        CHECK (total_amount >= 0),
        CHECK (remaining_amount >= 0 AND remaining_amount <= total_amount)
    )""",
    """CREATE TABLE IF NOT EXISTS reward_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        production_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        source_key TEXT NOT NULL,
        normal_json TEXT NOT NULL DEFAULT '{}',
        highlight_json TEXT NOT NULL DEFAULT '{}',
        edit_count INTEGER NOT NULL DEFAULT 0,
        locked_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (guild_id, source_key),
        FOREIGN KEY (guild_id, production_id) REFERENCES productions(guild_id, id),
        FOREIGN KEY (guild_id, character_id) REFERENCES characters(guild_id, id),
        CHECK (edit_count BETWEEN 0 AND 3)
    )""",
    """CREATE TABLE IF NOT EXISTS module_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        module TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        record_type TEXT NOT NULL,
        data_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_module_records ON module_records(guild_id, module, owner_id)",
    """CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target_key TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
    )""",
    """CREATE TABLE IF NOT EXISTS scheduled_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        job_key TEXT NOT NULL,
        job_type TEXT NOT NULL,
        run_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        payload_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE (guild_id, job_key),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
    )""",
]


def init_db():
    with transaction() as conn:
        for statement in SCHEMA:
            conn.execute(statement)


def register_guild(guild_id, name=""):
    with transaction() as conn:
        conn.execute(
            "INSERT INTO guilds (guild_id, name) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET name=excluded.name",
            (str(guild_id), str(name)),
        )


def create_character(guild_id, owner_id, name, age=None, occupation=""):
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("角色姓名須為 1 至 100 字")
    if age is not None and not 0 <= age <= 200:
        raise ValueError("角色年齡不合法")
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)", (str(guild_id),))
        cursor = conn.execute(
            "INSERT INTO characters(guild_id,owner_id,name,age,occupation) "
            "VALUES (?,?,?,?,?)",
            (str(guild_id), str(owner_id), name, age, str(occupation)),
        )
        return cursor.lastrowid


def get_characters(guild_id, owner_id):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM characters WHERE guild_id=? AND owner_id=? ORDER BY id",
            (str(guild_id), str(owner_id)),
        ).fetchall()
        return [dict(row) for row in rows]


def get_attribute(guild_id, character_id, name):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM attributes WHERE guild_id=? AND character_id=? AND name=?",
            (str(guild_id), int(character_id), name),
        ).fetchone()
        return None if row is None else row["value"]


def get_skill(guild_id, character_id, skill_name):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT skill_value FROM character_skills "
            "WHERE guild_id=? AND character_id=? AND skill_name=?",
            (str(guild_id), int(character_id), skill_name),
        ).fetchone()
        return 0 if row is None else row["skill_value"]


def add_record(guild_id, module, owner_id, record_type, data):
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)", (str(guild_id),))
        cur = conn.execute(
            "INSERT INTO module_records(guild_id,module,owner_id,record_type,data_json) "
            "VALUES (?,?,?,?,?)",
            (str(guild_id), module, str(owner_id), record_type,
             json.dumps(data, ensure_ascii=False, sort_keys=True)),
        )
        return cur.lastrowid


def get_records(guild_id, module, owner_id, limit=30):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM module_records WHERE guild_id=? AND module=? AND owner_id=? "
            "ORDER BY id DESC LIMIT ?",
            (str(guild_id), module, str(owner_id), max(1, min(int(limit), 100))),
        ).fetchall()
        return [dict(row) for row in rows]


def get_or_create_account(conn, guild_id, owner_type, owner_id, account_type="wallet"):
    guild_id = str(guild_id)
    conn.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)", (guild_id,))
    conn.execute(
        "INSERT OR IGNORE INTO accounts(guild_id,owner_type,owner_id,account_type) "
        "VALUES (?,?,?,?)",
        (guild_id, owner_type, str(owner_id), account_type),
    )
    row = conn.execute(
        "SELECT id FROM accounts WHERE guild_id=? AND owner_type=? "
        "AND owner_id=? AND account_type=?",
        (guild_id, owner_type, str(owner_id), account_type),
    ).fetchone()
    return row["id"]


def account_balance(conn, guild_id, account_id):
    row = conn.execute(
        "SELECT COALESCE(SUM(e.amount),0) AS balance FROM ledger_entries e "
        "WHERE e.guild_id=? AND e.account_id=?",
        (str(guild_id), int(account_id)),
    ).fetchone()
    return row["balance"]


def transfer_coins(guild_id, unique_key, sender_account_id, receiver_account_id,
                   amount, description=""):
    """同群雙向分錄轉帳。首次執行回 True；同 key 重試回 False。"""
    amount = int(amount)
    if amount <= 0:
        raise ValueError("轉帳金額必須是正整數")
    if sender_account_id == receiver_account_id:
        raise ValueError("不可轉帳給同一帳戶")
    guild_id = str(guild_id)
    with transaction() as conn:
        previous = conn.execute(
            "SELECT id FROM ledger_transactions WHERE guild_id=? AND unique_key=?",
            (guild_id, unique_key),
        ).fetchone()
        if previous:
            return False
        rows = conn.execute(
            "SELECT id FROM accounts WHERE guild_id=? AND id IN (?,?)",
            (guild_id, int(sender_account_id), int(receiver_account_id)),
        ).fetchall()
        if len(rows) != 2:
            raise ValueError("找不到此伺服器內的合法付款帳戶")
        if account_balance(conn, guild_id, sender_account_id) < amount:
            raise ValueError("付款帳戶餘額不足")
        cursor = conn.execute(
            "INSERT INTO ledger_transactions(guild_id,unique_key,description) "
            "VALUES (?,?,?)", (guild_id, unique_key, description),
        )
        conn.executemany(
            "INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount) "
            "VALUES (?,?,?,?)",
            [(guild_id, cursor.lastrowid, sender_account_id, -amount),
             (guild_id, cursor.lastrowid, receiver_account_id, amount)],
        )
        return True


if __name__ == "__main__":
    init_db()
    print("Discord RP 資料庫初始化成功")
