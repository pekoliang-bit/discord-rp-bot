
"""V2.3 生日與共用啟用服務的隔離驗證；不寫正式資料庫。"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in (
    'modules/profile.py', 'modules/npc_history_v23.py',
    'core/character_activation_v23.py',
):
    ast.parse((ROOT / name).read_text(encoding='utf-8'))

# 原創欄位選單須能填生日，NPC 空白生日須列為自定義。
profile = (ROOT/'modules/profile.py').read_text(encoding='utf-8')
assert '"生日（月／日）": "birthday"' in profile
assert 'if field == "birthday":' in profile
npc = (ROOT/'modules/npc_history_v23.py').read_text(encoding='utf-8')
assert "custom['生日（月／日）']='（自定義）'" in npc
assert "'birthday':('生日（月／日）','生日')" in npc

# 以全新臨時資料庫驗證 NPC 未建立正式數值時不得啟用。
code = r'''
import sys, types, json
sys.modules['discord'] = types.ModuleType('discord')  # 測試只需權限例外類別
from core.database import init_db,transaction
from core.character_activation_v23 import activate_in_transaction
from core.permissions import InvalidState
init_db()
with transaction() as db:
    db.execute('INSERT INTO guilds(guild_id) VALUES (?)', ('123',))
    db.execute('INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,character_type,settings_json) VALUES (?,?,?,?,?,?,?,?)', ('123','42','測試NPC',21,'主持人','approved','player_npc',json.dumps({'birthday':'04/18'})))
    cid=db.execute('SELECT id FROM characters').fetchone()[0]
    schema={'version':1,'general':['流量熱度','商業價值','路人緣','公關','影響指數'],
            'talent':['容貌','才智','演繹','舞蹈','歌唱','綜藝','主持','個人才藝']}
    db.execute('INSERT INTO module_records(guild_id,module,owner_id,record_type,data_json) VALUES (?,?,?,?,?)',
        ('123','attributes','42','official_schema',json.dumps(schema,ensure_ascii=False)))
try:
    with transaction() as db:activate_in_transaction(db,'123',cid,'42',allowed_types=('player_npc',))
except InvalidState as e:
    assert 'NPC 正式屬性尚未' in str(e), str(e)
else:
    raise AssertionError('未匯入正式屬性的 NPC 不應被啟用')
with transaction() as db:
    assert db.execute('SELECT status FROM characters WHERE id=?',(cid,)).fetchone()[0]=='approved'
print('PASS: birthday field available and missing NPC attributes prevent activation')
'''
with tempfile.TemporaryDirectory() as temp:
    env = {**os.environ, 'DATABASE_PATH': str(Path(temp) / 'test.sqlite3')}
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                          env=env, capture_output=True, text=True)
    if done.returncode:
        print(done.stdout, done.stderr)
        raise SystemExit(done.returncode)
    print(done.stdout.strip())
