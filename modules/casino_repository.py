"""娛樂城資料層（非 Discord Cog）。

只新增 casino_* 表，不改動既有角色、金流及商城資料。
資料庫連線與事務使用 core.database，避免建立第二套金幣帳本。
"""
from __future__ import annotations

import json
from typing import Any

from core.database import get_connection, transaction

GAME_KINDS = frozenset({'scratch', 'lottery', 'gacha', 'blackjack', 'roulette', 'sicbo', 'slots', 'bingo', 'pvp'})
FUNDING_KINDS = frozenset({'direct', 'reserve', 'sales_pool', 'mixed'})

DDL = (
    """CREATE TABLE IF NOT EXISTS casino_games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        ticket_price INTEGER NOT NULL CHECK(ticket_price > 0),
        funding_mode TEXT NOT NULL DEFAULT 'direct',
        settings_json TEXT NOT NULL DEFAULT '{}',
        config_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id, code),
        UNIQUE(guild_id, id),
        FOREIGN KEY(guild_id) REFERENCES guilds(guild_id)
    )""",
    """CREATE TABLE IF NOT EXISTS casino_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        game_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        request_key TEXT NOT NULL,
        game_kind TEXT NOT NULL,
        config_version INTEGER NOT NULL,
        ticket_price INTEGER NOT NULL CHECK(ticket_price > 0),
        payment_source TEXT NOT NULL CHECK(payment_source IN ('wallet','bank')),
        funding_mode TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','settled','cancelled')),
        outcome_json TEXT,
        reward_coins INTEGER NOT NULL DEFAULT 0 CHECK(reward_coins >= 0),
        pay_tx_id INTEGER,
        reward_tx_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        settled_at TEXT,
        UNIQUE(guild_id, request_key),
        UNIQUE(guild_id, id),
        FOREIGN KEY(guild_id,game_id) REFERENCES casino_games(guild_id,id),
        FOREIGN KEY(guild_id,character_id) REFERENCES characters(guild_id,id)
    )""",
    """CREATE TABLE IF NOT EXISTS casino_rewards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        round_id INTEGER NOT NULL,
        reward_key TEXT NOT NULL,
        reward_type TEXT NOT NULL CHECK(reward_type IN ('coins','item','collectible','title')),
        reward_ref TEXT NOT NULL DEFAULT '',
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','delivered','failed')),
        delivered_at TEXT,
        UNIQUE(guild_id,round_id,reward_key),
        FOREIGN KEY(guild_id,round_id) REFERENCES casino_rounds(guild_id,id)
    )""",
    """CREATE TABLE IF NOT EXISTS casino_pool_balances (
        guild_id TEXT NOT NULL,
        pool_code TEXT NOT NULL,
        game_id INTEGER,
        balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,pool_code),
        FOREIGN KEY(guild_id) REFERENCES guilds(guild_id)
    )""",
    """CREATE TABLE IF NOT EXISTS casino_pity (
        guild_id TEXT NOT NULL,
        character_id INTEGER NOT NULL,
        pool_family TEXT NOT NULL,
        since_ssr INTEGER NOT NULL DEFAULT 0 CHECK(since_ssr>=0),
        since_ur INTEGER NOT NULL DEFAULT 0 CHECK(since_ur>=0),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,character_id,pool_family),
        FOREIGN KEY(guild_id,character_id) REFERENCES characters(guild_id,id)
    )""",
    """CREATE TABLE IF NOT EXISTS casino_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        ref_key TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_casino_rounds_character ON casino_rounds(guild_id,character_id,created_at)",
    "CREATE INDEX IF NOT EXISTS idx_casino_rounds_status ON casino_rounds(guild_id,status)",
    "CREATE INDEX IF NOT EXISTS idx_casino_game_kind ON casino_games(guild_id,kind,enabled)",
)


def init_casino_schema() -> None:
    """可重複執行；需先由主程式初始化原有 core.database。"""
    with transaction() as conn:
        for ddl in DDL:
            conn.execute(ddl)


def get_game(guild_id: str | int, code: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            'SELECT * FROM casino_games WHERE guild_id=? AND code=?',
            (str(guild_id), str(code)),
        ).fetchone()
        return dict(row) if row else None


def list_games(guild_id: str | int, kind: str | None = None, enabled_only: bool = True) -> list[dict[str, Any]]:
    query = 'SELECT * FROM casino_games WHERE guild_id=?'
    params: list[Any] = [str(guild_id)]
    if kind is not None:
        if kind not in GAME_KINDS:
            raise ValueError('未知遊戲種類')
        query += ' AND kind=?'
        params.append(kind)
    if enabled_only:
        query += ' AND enabled=1'
    query += ' ORDER BY id'
    with get_connection() as conn:
        return [dict(x) for x in conn.execute(query, params).fetchall()]


def upsert_game(*, guild_id: str | int, code: str, name: str, kind: str,
                ticket_price: int, funding_mode: str = 'direct',
                settings: dict[str, Any] | None = None, enabled: bool = False,
                actor_id: str | int = 'system') -> dict[str, Any]:
    """**僅供已授權後台調用**。不得直接暴露為玩家自填參數的指令。"""
    if kind not in GAME_KINDS or funding_mode not in FUNDING_KINDS:
        raise ValueError('不支援的遊戲或獎金來源')
    if not code or len(code) > 80 or not name or len(name) > 100:
        raise ValueError('遊戲代碼或名稱不合法')
    if type(ticket_price) is not int or ticket_price <= 0 or ticket_price > 10**12:
        raise ValueError('票價不合法')
    settings = settings or {}
    if not isinstance(settings, dict):
        raise ValueError('設定必須是物件')
    encoded = json.dumps(settings, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 30000:
        raise ValueError('設定內容過大')
    gid = str(guild_id)
    with transaction() as conn:
        conn.execute('INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)', (gid,))
        previous = conn.execute('SELECT id FROM casino_games WHERE guild_id=? AND code=?',(gid,code)).fetchone()
        conn.execute('''INSERT INTO casino_games(guild_id,code,name,kind,enabled,ticket_price,funding_mode,settings_json)
                        VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(guild_id,code) DO UPDATE SET
                          name=excluded.name, kind=excluded.kind, enabled=excluded.enabled,
                          ticket_price=excluded.ticket_price, funding_mode=excluded.funding_mode,
                          settings_json=excluded.settings_json, config_version=casino_games.config_version+1''',
                     (gid,code,name,kind,int(enabled),ticket_price,funding_mode,encoded))
        row = conn.execute('SELECT * FROM casino_games WHERE guild_id=? AND code=?',(gid,code)).fetchone()
        conn.execute('INSERT INTO casino_audit(guild_id,actor_id,action,ref_key,details_json) VALUES(?,?,?,?,?)',
                     (gid,str(actor_id),'game_update' if previous else 'game_create',code,
                      json.dumps({'version':row['config_version'],'enabled':bool(enabled)},ensure_ascii=False)))
        return dict(row)


def get_round(guild_id: str | int, round_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND id=?',(str(guild_id),int(round_id))).fetchone()
        return dict(row) if row else None


def list_character_rounds(guild_id: str | int, character_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT * FROM casino_rounds WHERE guild_id=? AND character_id=? ORDER BY id DESC LIMIT ?',
            (str(guild_id),int(character_id),max(1,min(100,int(limit))))) .fetchall()]
