"""傳聞結算熱度快照：只接受持久化的可信任熱度來源。

安裝：core/rumor_heat_snapshot.py（非 Discord extension）。
不假造熱度、不自動連線網路系統、不修改傳聞狀態。
上游應先在同筆資料庫交易寫入來源表，再提交熱度快照。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from core.database import get_connection, transaction


@dataclass(frozen=True)
class HeatSnapshot:
    guild_id: str
    rumor_id: int
    heat: int
    source_event_id: str
    digest: str


def _digest(gid: str, rid: int, heat: int, source: str) -> str:
    payload = json.dumps([gid, rid, heat, source], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def initialize_heat_tables(db) -> None:
    db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_heat_events (
        guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
        source_event_id TEXT NOT NULL, heat INTEGER NOT NULL CHECK(heat>=0),
        recorded_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id,rumor_id,source_event_id)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_heat_snapshots (
        guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
        heat INTEGER NOT NULL CHECK(heat>=0), source_event_id TEXT NOT NULL,
        digest TEXT NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id,rumor_id)
    )''')


def record_heat_event_in_transaction(db, guild_id: str | int, rumor_id: int,
                                     source_event_id: str, heat: int, *, trusted: bool = False) -> None:
    """由系統網路／熱度引擎於既有交易內記錄一次觀測值。"""
    if not trusted:
        raise PermissionError('僅限已授權的系統熱度來源')
    if not db.in_transaction:
        raise RuntimeError('必須使用已有的資料庫交易')
    gid, source = str(guild_id).strip(), str(source_event_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0 or not source or len(source) > 160 or type(heat) is not int or not 0 <= heat <= 10**12:
        raise ValueError('熱度事件參數不合法')
    initialize_heat_tables(db)
    case = db.execute('SELECT 1 FROM rp_company_rumors WHERE guild_id=? AND id=?', (gid, rumor_id)).fetchone()
    if case is None:
        raise LookupError('案件不存在')
    db.execute('''INSERT OR IGNORE INTO rp_rumor_heat_events
        (guild_id,rumor_id,source_event_id,heat,recorded_at) VALUES(?,?,?,?,?)''',
        (gid, rumor_id, source, heat, int(time.time())))
    row = db.execute('''SELECT heat FROM rp_rumor_heat_events
        WHERE guild_id=? AND rumor_id=? AND source_event_id=?''',
        (gid, rumor_id, source)).fetchone()
    if int(row['heat']) != heat:
        raise ValueError('同一熱度來源事件不能改寫')


def lock_heat_snapshot(guild_id: str | int, rumor_id: int,
                       source_event_id: str, *, trusted: bool = False) -> HeatSnapshot:
    """從已記錄的熱度事件鎖定一次快照；缺少真實來源即拒絕。"""
    if not trusted:
        raise PermissionError('僅限可信任的案件結算器')
    gid, source = str(guild_id).strip(), str(source_event_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0 or not source:
        raise ValueError('案件或來源不合法')
    with transaction() as db:
        initialize_heat_tables(db)
        case = db.execute('SELECT status FROM rp_company_rumors WHERE guild_id=? AND id=?', (gid, rumor_id)).fetchone()
        if case is None or case['status'] not in ('active', 'settled'):
            raise ValueError('案件狀態不合法')
        event = db.execute('''SELECT heat FROM rp_rumor_heat_events
            WHERE guild_id=? AND rumor_id=? AND source_event_id=?''',
            (gid, rumor_id, source)).fetchone()
        if event is None:
            raise LookupError('熱度來源尚未記錄，禁止猜測熱度')
        heat = int(event['heat'])
        digest = _digest(gid, rumor_id, heat, source)
        db.execute('''INSERT OR IGNORE INTO rp_rumor_heat_snapshots
            (guild_id,rumor_id,heat,source_event_id,digest,created_at)
            VALUES(?,?,?,?,?,?)''', (gid, rumor_id, heat, source, digest, int(time.time())))
        snapshot = db.execute('''SELECT heat,source_event_id,digest FROM rp_rumor_heat_snapshots
            WHERE guild_id=? AND rumor_id=?''', (gid, rumor_id)).fetchone()
        if (int(snapshot['heat']), snapshot['source_event_id'], snapshot['digest']) != (heat, source, digest):
            raise ValueError('熱度快照已鎖定其他內容')
    return HeatSnapshot(gid, rumor_id, heat, source, digest)


def load_heat_snapshot(guild_id: str | int, rumor_id: int) -> HeatSnapshot:
    gid = str(guild_id).strip()
    with get_connection() as db:
        row = db.execute('''SELECT heat,source_event_id,digest FROM rp_rumor_heat_snapshots
            WHERE guild_id=? AND rumor_id=?''', (gid, rumor_id)).fetchone()
    if row is None:
        raise LookupError('熱度快照尚未建立')
    heat, source = int(row['heat']), row['source_event_id']
    digest = _digest(gid, rumor_id, heat, source)
    if digest != row['digest']:
        raise RuntimeError('熱度快照完整性驗證失敗')
    return HeatSnapshot(gid, rumor_id, heat, source, digest)
