"""飼主須知 2.3：逐頁閱讀、角色姓名與身分證字號雙重驗證。"""
from __future__ import annotations
from datetime import datetime,timezone
from core.database import transaction
from core import pets_v1 as pets
VERSION='2.3'
PAGES=(
('適用對象','本須知適用於購買、正式領養或接收寵物的角色。每隻寵物擁有獨立性格，飼主須依物種需求提供妥善照護。'),
('所有權與晶片','購買或領養完成時，系統登記主要飼主及永久晶片。共同飼養、轉讓及身分變更應依正式規則辦理。'),
('健康與生活照護','寵物健康上限100，每日自然減少2；生病可減少20～40，受傷可減少15～30，未治療每24小時額外減少5。飼主須提供飲食與合適環境。'),
('依賴與情緒','寵物依賴上限200，每日自然減少5，長時間不陪伴可能另有損失。寵物可能因疲勞、疼痛或壓力出現防衛反應，甚至造成實際互動者健康下降。'),
('用品與維護','耐久型用品最高100點，降至30點或以下將通知，歸零後永久損壞且不可維修；尚未歸零的用品可依品質及損耗程度付費維修。'),
('訓練與疲勞','可自行訓練或參加專業課程。居家訓練次數不限，但會累積疲勞，達到限制時必須休息；訓練不能完全消除寵物原本天性。'),
('醫療費與傷病','寵物可能產生高額檢查、診斷、治療、手術、住院及急救費。收費依付款時公布的價目表及個別確認帳單；不保證所有傷病立即康復。日後保險保障依實際契約，不保證全部理賠。'),
('首次複檢義務','完成購買或正式領養滿7天（168小時）後，必須於接下來5天（120小時）內完成首次適應期複檢。首次複檢實收0金幣，其他疾病治療另外報價；逾期仍需補檢。'),
('走失與特殊規則','寵物可能走失，應遵守協尋及救援規則。保育類或未核准交易動物不得擅自販售或收養。住院與走失造成的複檢困難可依正式延期流程處理。'),
('簽署與確認','本人已依序閱讀以上九頁，理解飼主責任、醫療費用與首次複檢義務，同意遵守相關規則。請填寫角色正式姓名及「身分證字號」（原對外角色代碼）；不得使用玩家現實身分證字號。'),
)

def setup(db):
    pets.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_read_session_v23 (guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,user_id TEXT NOT NULL,version TEXT NOT NULL,max_page INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(guild_id,character_id,user_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_signed_terms_v23 (
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,version TEXT NOT NULL,
      official_name TEXT NOT NULL,identity_code TEXT NOT NULL,user_id TEXT NOT NULL,
      signed_at TEXT NOT NULL,read_pages INTEGER NOT NULL,
      PRIMARY KEY(guild_id,character_id,version))''')

def has_signed(db,gid,cid):
    setup(db)
    return bool(db.execute('SELECT 1 FROM rp_pet_signed_terms_v23 WHERE guild_id=? AND character_id=? AND version=?',(str(gid),int(cid),VERSION)).fetchone())

def start(guild_id,character_id,user_id):
    gid=str(guild_id);cid=int(character_id)
    with transaction() as db:
        setup(db);pets._actor(db,gid,cid,user_id)
        db.execute('''INSERT INTO rp_pet_read_session_v23 VALUES (?,?,?,?,?,?)
          ON CONFLICT(guild_id,character_id,user_id) DO UPDATE SET version=excluded.version,max_page=0,updated_at=excluded.updated_at''',
          (gid,cid,str(user_id),VERSION,0,datetime.now(timezone.utc).isoformat()))

def advance(guild_id,character_id,user_id,current_page):
    gid=str(guild_id);cid=int(character_id);index=int(current_page)
    with transaction() as db:
        setup(db);pets._actor(db,gid,cid,user_id)
        row=db.execute('SELECT * FROM rp_pet_read_session_v23 WHERE guild_id=? AND character_id=? AND user_id=?',(gid,cid,str(user_id))).fetchone()
        if not row or row['version']!=VERSION or index<0 or index>=len(PAGES)-1 or index>int(row['max_page']):
            raise PermissionError('請從第一頁依序閱讀飼主須知')
        db.execute('UPDATE rp_pet_read_session_v23 SET max_page=?,updated_at=? WHERE guild_id=? AND character_id=? AND user_id=?',
          (max(index+1,int(row['max_page'])),datetime.now(timezone.utc).isoformat(),gid,cid,str(user_id)))
        return index+1

def sign(guild_id,character_id,user_id,official_name,identity_code,read_pages):
    gid=str(guild_id);cid=int(character_id)
    if int(read_pages)!=len(PAGES):raise PermissionError('尚未完成全部十頁閱讀')
    with transaction() as db:
        setup(db);pets._actor(db,gid,cid,user_id)
        session=db.execute('SELECT max_page,version FROM rp_pet_read_session_v23 WHERE guild_id=? AND character_id=? AND user_id=?',(gid,cid,str(user_id))).fetchone()
        if not session or session['version']!=VERSION or int(session['max_page'])!=len(PAGES)-1:
            raise PermissionError('請從第一頁依序閱讀所有條款後再簽署')
        row=db.execute("SELECT name,public_code FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'",(gid,cid,str(user_id))).fetchone()
        if not row:raise PermissionError('找不到本人已啟用角色')
        if not row['public_code']:
            raise ValueError('角色尚未取得身分證字號，請先確認角色正式編號已核發')
        if str(official_name).strip()!=str(row['name']).strip():raise PermissionError('簽名必須符合正式角色姓名')
        if str(identity_code).strip()!=str(row['public_code']).strip():raise PermissionError('身分證字號不符合角色資料')
        when=datetime.now(timezone.utc).isoformat()
        db.execute('''INSERT OR IGNORE INTO rp_pet_signed_terms_v23 VALUES (?,?,?,?,?,?,?,?)''',
          (gid,cid,VERSION,row['name'],row['public_code'],str(user_id),when,len(PAGES)))
        db.execute('''INSERT INTO rp_pet_consent_v1 (guild_id,character_id,accepted_at,terms_version)
          VALUES (?,?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET accepted_at=excluded.accepted_at,terms_version=excluded.terms_version''',
          (gid,cid,when,VERSION))
        return {'name':row['name'],'identity_code':row['public_code'],'version':VERSION,'signed_at':when}
