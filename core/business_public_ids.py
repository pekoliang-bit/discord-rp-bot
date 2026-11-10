"""商業公開編碼：類型－伺服器－成立月日－同組序號。

C 公司、G 集團、B 品牌、S 一般店舖、H 已綁定頻道的店舖。
預設不核發；只有 character_number_launches 記錄存在才能執行核發。
保留所有業務原始 ID 與既有公司舊編號，不重寫既有關聯。
"""
from __future__ import annotations
from datetime import datetime
import re

BUSINESS_PREFIX={"company":"C","group":"G","brand":"B","shop":"S","channel_shop":"H"}
CODE_PATTERN=re.compile(r"^[CGBSH]-\d{2,}-\d{4}-\d{2,}$")


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_business_public_identity (
        public_code TEXT PRIMARY KEY,
        guild_id TEXT NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id,subject_type,subject_id)
    )""")


def _launched(db,gid):
    try:
        return db.execute("SELECT 1 FROM character_number_launches WHERE guild_id=?",(str(gid),)).fetchone() is not None
    except Exception:
        return False


def _serial(db,gid):
    row=db.execute("SELECT server_serial FROM character_number_servers WHERE guild_id=?",(str(gid),)).fetchone()
    if row is None:
        # 不私自註冊伺服器；須先完成正式開群流程。
        raise ValueError("伺服器尚未透過正式開群取得序號。")
    return int(row[0])


def _monthday(stamp):
    if stamp is None:
        raise ValueError("主體缺少成立日期，不能自動猜測。")
    try:
        return datetime.fromisoformat(str(stamp).replace("Z","+00:00")).strftime("%m%d")
    except ValueError as exc:
        raise ValueError("成立日期格式無法識別，待補資料。") from exc


def issue(db,gid,kind,subject_id):
    """需要外層原子交易；正式開群前一律不核發。"""
    gid,subject_id=str(gid),str(subject_id)
    if kind not in BUSINESS_PREFIX:
        raise ValueError("不支援的商業類別")
    setup(db)
    row=db.execute("SELECT public_code FROM rp_business_public_identity WHERE guild_id=? AND subject_type=? AND subject_id=?",(gid,kind,subject_id)).fetchone()
    if row:return str(row[0])
    if not _launched(db,gid):return None
    if kind in ("company","group","brand"):
        ent=db.execute("SELECT kind,status,founded_at FROM rp_business_entities WHERE guild_id=? AND business_id=?",(gid,subject_id)).fetchone()
        if not ent or ent["kind"]!=kind or ent["status"]!="active":return None
        date=ent["founded_at"]
    else:
        shop=db.execute("SELECT status,opened_at FROM personal_shops WHERE guild_id=? AND id=?",(gid,subject_id)).fetchone()
        if not shop or shop["status"]!="open":return None
        if kind=="channel_shop":
            if not _has_channel_table(db):return None
            binding=db.execute("SELECT 1 FROM rp_player_shop_channels WHERE guild_id=? AND shop_id=?",(gid,int(subject_id))).fetchone()
            if not binding:return None
        date=shop["opened_at"]
    stem=f"{BUSINESS_PREFIX[kind]}-{_serial(db,gid):02d}-{_monthday(date)}-"
    n=1
    while db.execute("SELECT 1 FROM rp_business_public_identity WHERE public_code=?",(f"{stem}{n:02d}",)).fetchone():n+=1
    code=f"{stem}{n:02d}"
    db.execute("INSERT INTO rp_business_public_identity(public_code,guild_id,subject_type,subject_id) VALUES(?,?,?,?)",(code,gid,kind,subject_id))
    return code


def resolve_business(db,gid,raw,*,allowed=("company","group","brand")):
    raw=str(raw).strip()
    setup(db)
    if CODE_PATTERN.fullmatch(raw):
        row=db.execute("SELECT subject_id,subject_type FROM rp_business_public_identity WHERE guild_id=? AND public_code=?",(str(gid),raw)).fetchone()
        if not row or row["subject_type"] not in allowed:raise ValueError("商業編碼不存在或類型不符。")
        return str(row["subject_id"])
    # 原始業務 ID 或舊版公開公司編號：不能拋棄既有資料。
    if "company" in allowed or "group" in allowed:
        # 只查舊碼，不呼叫會自動產生舊碼的 ensure_companies。
        try:
            old=db.execute("SELECT business_id FROM rp_company_public_codes WHERE guild_id=? AND public_code=?",(str(gid),raw)).fetchone()
            if old:return str(old[0])
        except Exception:
            pass
    return raw


def resolve_shop(db,gid,raw):
    raw=str(raw).strip()
    setup(db)
    if CODE_PATTERN.fullmatch(raw):
        row=db.execute("SELECT subject_id,subject_type FROM rp_business_public_identity WHERE guild_id=? AND public_code=?",(str(gid),raw)).fetchone()
        if not row or row["subject_type"] not in ("shop","channel_shop"):
            raise ValueError("店鋪編碼不存在。")
        raw=str(row["subject_id"])
    if not raw.isdecimal():raise ValueError("店鋪編碼無效。")
    return int(raw)


def _has_channel_table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_player_shop_channels'").fetchone() is not None


def backfill_after_launch(db,gid):
    """供未來正式開群確認按鈕呼叫，不會自行標記開群。"""
    result={"issued":[],"missing":[]}
    if not _launched(db,gid):return result
    for row in db.execute("SELECT kind,business_id FROM rp_business_entities WHERE guild_id=? AND status='active' ORDER BY founded_at,rowid",(str(gid),)).fetchall():
        try:
            code=issue(db,gid,row["kind"],row["business_id"])
            if code:result["issued"].append(code)
        except ValueError:result["missing"].append(str(row["business_id"]))
    for row in db.execute("SELECT id FROM personal_shops WHERE guild_id=? AND status='open' ORDER BY opened_at,id",(str(gid),)).fetchall():
        try:
            kind="channel_shop" if _has_channel_table(db) and db.execute("SELECT 1 FROM rp_player_shop_channels WHERE guild_id=? AND shop_id=?",(str(gid),row["id"])).fetchone() else "shop"
            code=issue(db,gid,kind,row["id"])
            if code:result["issued"].append(code)
        except ValueError:result["missing"].append(str(row["id"]))
    return result
