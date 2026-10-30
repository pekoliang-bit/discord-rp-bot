"""傳聞公關評級門檻版本庫。安裝：core/rumor_grade_policy.py。

純後端模組；必須由已驗證群主權限的官方介面呼叫寫入函式。
只保存經核定的門檻，不自行推定 S～D 分數。
"""
from __future__ import annotations
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Mapping
from core.database import get_connection, transaction

ORDER = ("S", "A", "B", "C")


def _check_thresholds(values: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(values, Mapping) or set(values) != set(ORDER):
        raise ValueError("必須完整提供 S、A、B、C 四級門檻")
    result = {key: values[key] for key in ORDER}
    if any(type(value) is not int or not -10**9 <= value <= 10**9 for value in result.values()):
        raise ValueError("評級門檻必須為安全範圍內的整數")
    if not all(result[ORDER[i]] > result[ORDER[i + 1]] for i in range(3)):
        raise ValueError("S>A>B>C 門檻必須嚴格遞減")
    return result


def _serialized(values: dict[str, int]) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _gid(value: str | int) -> str:
    gid = str(value).strip()
    if not gid:
        raise ValueError("伺服器編號不可空白")
    return gid


def _version(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64 or not all(c.isascii() and (c.isalnum() or c in "._-") for c in value):
        raise ValueError("門檻版本名稱不合法")
    return value


def initialize(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_grade_policies (
        guild_id TEXT NOT NULL, version TEXT NOT NULL,
        thresholds_json TEXT NOT NULL, digest TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id,version))""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_grade_policy_active (
        guild_id TEXT PRIMARY KEY, version TEXT NOT NULL,
        updated_at INTEGER NOT NULL)""")


@dataclass(frozen=True)
class GradePolicy:
    guild_id: str
    version: str
    thresholds: dict[str, int]
    digest: str


def register_grade_policy(guild_id: str | int, version: str, thresholds: Mapping[str, int], *, trusted: bool = False) -> GradePolicy:
    if not trusted:
        raise PermissionError("必須由已授權的官方設定流程呼叫")
    gid, ver = _gid(guild_id), _version(version)
    data = _check_thresholds(thresholds)
    blob = _serialized(data)
    digest = _hash(blob)
    with transaction() as db:
        initialize(db)
        db.execute("""INSERT OR IGNORE INTO rp_rumor_grade_policies
            (guild_id,version,thresholds_json,digest,created_at)
            VALUES(?,?,?,?,?)""", (gid, ver, blob, digest, int(time.time())))
        row = db.execute("SELECT thresholds_json,digest FROM rp_rumor_grade_policies WHERE guild_id=? AND version=?", (gid,ver)).fetchone()
        if row["thresholds_json"] != blob or row["digest"] != digest:
            raise ValueError("同名門檻版本已有不同設定；請使用新版本名稱")
    return GradePolicy(gid,ver,data,digest)


def _decode(gid: str, version: str, row) -> GradePolicy:
    if row is None:
        raise LookupError("找不到指定的評級門檻版本")
    blob = row["thresholds_json"]
    if _hash(blob) != row["digest"]:
        raise RuntimeError("評級門檻版本摘要不符")
    values = _check_thresholds(json.loads(blob))
    if _serialized(values) != blob:
        raise RuntimeError("評級門檻資料格式異常")
    return GradePolicy(gid, version, values, row["digest"])


def load_grade_policy(guild_id: str | int, version: str) -> GradePolicy:
    gid,ver = _gid(guild_id),_version(version)
    with get_connection() as db:
        row = db.execute("SELECT thresholds_json,digest FROM rp_rumor_grade_policies WHERE guild_id=? AND version=?",(gid,ver)).fetchone()
    return _decode(gid,ver,row)


def activate_grade_policy(guild_id: str | int, version: str, *, trusted: bool = False) -> None:
    if not trusted:
        raise PermissionError("必須由已授權的官方設定流程呼叫")
    gid,ver = _gid(guild_id),_version(version)
    with transaction() as db:
        initialize(db)
        row = db.execute("SELECT thresholds_json,digest FROM rp_rumor_grade_policies WHERE guild_id=? AND version=?",(gid,ver)).fetchone()
        _decode(gid,ver,row)
        db.execute("""INSERT INTO rp_rumor_grade_policy_active (guild_id,version,updated_at)
            VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET
            version=excluded.version,updated_at=excluded.updated_at""",(gid,ver,int(time.time())))


def get_active_grade_policy(guild_id: str | int) -> GradePolicy:
    gid = _gid(guild_id)
    with get_connection() as db:
        row = db.execute("SELECT version FROM rp_rumor_grade_policy_active WHERE guild_id=?",(gid,)).fetchone()
    if row is None:
        raise LookupError("尚未啟用正式公關評級門檻")
    return load_grade_policy(gid,row["version"])


def lock_case_grade_policy_in_transaction(db, guild_id: str | int, rumor_id: int, *, trusted: bool = False) -> str:
    """建立案件時，與案件 INSERT 共用同一交易；只鎖定當前啟用版本。"""
    if not trusted:
        raise PermissionError("只有可信任的案件建立流程可以鎖定門檻")
    if not db.in_transaction:
        raise RuntimeError("必須在案件建立的同一筆交易中呼叫")
    gid = _gid(guild_id)
    if type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件編號無效")
    initialize(db)
    db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_case_grade_policy (
        guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
        version TEXT NOT NULL, created_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id,rumor_id))""")
    case = db.execute("SELECT 1 FROM rp_company_rumors WHERE guild_id=? AND id=?", (gid,rumor_id)).fetchone()
    if case is None:
        raise LookupError("案件不存在")
    active = db.execute("SELECT version FROM rp_rumor_grade_policy_active WHERE guild_id=?", (gid,)).fetchone()
    if active is None:
        raise LookupError("伺服器尚未啟用公關評級門檻")
    ver = active["version"]
    policy_row = db.execute("SELECT thresholds_json,digest FROM rp_rumor_grade_policies WHERE guild_id=? AND version=?", (gid,ver)).fetchone()
    _decode(gid,ver,policy_row)
    db.execute("INSERT OR IGNORE INTO rp_rumor_case_grade_policy (guild_id,rumor_id,version,created_at) VALUES(?,?,?,?)", (gid,rumor_id,ver,int(time.time())))
    existing = db.execute("SELECT version FROM rp_rumor_case_grade_policy WHERE guild_id=? AND rumor_id=?", (gid,rumor_id)).fetchone()
    if existing["version"] != ver:
        raise ValueError("此案件已鎖定不同的評級門檻版本")
    return ver
