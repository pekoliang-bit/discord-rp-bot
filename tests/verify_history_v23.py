"""可離線執行，不觸碰正式 SQLite 或 Discord；靜態與資料庫隔離檢查。"""
from pathlib import Path
import ast,os,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
FILES=['core/history_v23.py','modules/npc_history_v23.py','modules/creation_wizard.py','modules/registration.py','modules/content_builder.py','modules/review_center.py','modules/character_sheet_registration.py','modules/character_view.py','modules/profile.py']
for name in FILES:
    ast.parse((ROOT/name).read_text(encoding='utf-8'))
print('PASS: 9個整合檔 Python 語法有效')
code=r'''
from core.database import init_db,transaction
from core.history_v23 import put_batch,stats,attempt_lock
init_db()
with transaction() as db:
    db.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES ('123')")
    db.execute("INSERT INTO characters(guild_id,owner_id,name,status,character_type) VALUES ('123','42','測試角色','active','player')")
assert put_batch('123',[{'asset_id':'HB-0046','title':'GO WANDER 漫遊GO','tier':'B1'},
    {'asset_id':'HE-0151','title':'GO WANDER 漫遊GO｜旅遊推薦官','tier':'B1','rarity':'R1','meta':{'hb_id':'HB-0046'}}])==2
assert sum(x['total'] for x in stats('123','general'))==2
assert attempt_lock('123',1)=='pending_assets'
with transaction() as db:
    assert db.execute('SELECT COUNT(*) FROM rp_history_results_v23').fetchone()[0]==0
print('PASS: 核准素材匯入、缺素材不生成半套履歷')
'''
with tempfile.TemporaryDirectory() as td:
    env={**os.environ,'DATABASE_PATH':str(Path(td)/'test.sqlite3')}
    proc=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,text=True,capture_output=True)
    if proc.returncode:
        print(proc.stdout,proc.stderr);raise SystemExit(proc.returncode)
    print(proc.stdout.strip())
print('INFO: Discord UI 與 Railway 實機測試需要部署後另外執行。')
