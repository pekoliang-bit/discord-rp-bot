"""公關策略設定：只由已驗證的官方管理後端呼叫，不註冊新斜線指令。"""
from __future__ import annotations

from dataclasses import dataclass
from core.database import transaction, get_connection
from core.rumor_pr_paid_flow import initialize_pr_strategy_config


@dataclass(frozen=True)
class StrategySetting:
    guild_id: str
    strategy: str
    base_score: int
    enabled: bool
    version: int


def initialize(db):
    """相容既有 config 表，額外建立不可覆蓋的修改歷程。"""
    initialize_pr_strategy_config(db)
    db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_pr_strategy_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        strategy TEXT NOT NULL,
        version INTEGER NOT NULL,
        base_score INTEGER NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        operator_id TEXT NOT NULL,
        changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id, strategy, version)
    )""")


def update_strategy(guild_id, strategy, base_score, enabled, operator_id, *, trusted=False):
    """原子更新設定及歷程；呼叫者必須先驗證群主／官方管理權限。"""
    if not trusted:
        raise PermissionError("未通過官方管理權限驗證")
    gid, name, actor = str(guild_id).strip(), str(strategy).strip(), str(operator_id).strip()
    if not gid or not actor or not name or len(name) > 80:
        raise ValueError("伺服器、操作者或策略名稱無效")
    if type(base_score) is not int or not 0 <= base_score <= 10000:
        raise ValueError("分數必須是 0～10000 的整數")
    if type(enabled) is not bool:
        raise ValueError("enabled 必須為布林值")
    with transaction() as db:
        initialize(db)
        old = db.execute("SELECT base_score, enabled FROM rp_rumor_pr_strategy_config "
                         "WHERE guild_id=? AND strategy=?", (gid, name)).fetchone()
        version_row = db.execute("SELECT COALESCE(MAX(version),0) FROM rp_rumor_pr_strategy_audit "
                                 "WHERE guild_id=? AND strategy=?", (gid, name)).fetchone()
        version = int(version_row[0])
        if old is not None and int(old['base_score']) == base_score and bool(old['enabled']) == enabled:
            return StrategySetting(gid, name, base_score, enabled, version)
        db.execute("""INSERT INTO rp_rumor_pr_strategy_config(guild_id,strategy,base_score,enabled)
                    VALUES(?,?,?,?) ON CONFLICT(guild_id,strategy) DO UPDATE SET
                    base_score=excluded.base_score, enabled=excluded.enabled""",
                   (gid, name, base_score, int(enabled)))
        version += 1
        db.execute("""INSERT INTO rp_rumor_pr_strategy_audit
                    (guild_id,strategy,version,base_score,enabled,operator_id)
                    VALUES(?,?,?,?,?,?)""", (gid, name, version, base_score, int(enabled), actor))
    return StrategySetting(gid, name, base_score, enabled, version)


def list_strategies(guild_id):
    gid = str(guild_id).strip()
    if not gid:
        raise ValueError("伺服器 ID 無效")
    with get_connection() as db:
        rows = db.execute("""SELECT c.strategy,c.base_score,c.enabled,
            COALESCE((SELECT MAX(a.version) FROM rp_rumor_pr_strategy_audit a
            WHERE a.guild_id=c.guild_id AND a.strategy=c.strategy),0) version
            FROM rp_rumor_pr_strategy_config c WHERE c.guild_id=? ORDER BY c.strategy""",
            (gid,)).fetchall()
    return [StrategySetting(gid, r['strategy'], int(r['base_score']),
                            bool(r['enabled']), int(r['version'])) for r in rows]
