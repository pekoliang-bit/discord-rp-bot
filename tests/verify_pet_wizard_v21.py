"""V2.1 離線品種/分頁/資料庫結構驗證，不需要連線 Discord。"""
from pathlib import Path
import sys, sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.pet_catalog_v21 import BREEDS,PAGES,wizard_pages,item_templates
from core.pet_location_events_v21 import initialize

assert len(BREEDS)==24
assert len([b for b in BREEDS if b[1]=='貓'])==8
assert len([b for b in BREEDS if b[1]=='狗'])==10
assert len([b for b in BREEDS if b[1]=='鸚鵡'])==6
assert sum(b[4] for b in BREEDS)==21
assert all('米克斯' not in b[2] for b in BREEDS)
assert len(PAGES)==3
assert len(wizard_pages('貓','緬因貓'))==12
assert len(wizard_pages('狗','柴犬'))==11
assert all(x['price']>0 for x in item_templates())
db=sqlite3.connect(':memory:');initialize(db)
assert db.execute("SELECT name FROM sqlite_master WHERE name='rp_pet_location_events_v21'").fetchone()
print('✅ V2.1 候選品種、用品頁面與場所設定結構檢查通過')
