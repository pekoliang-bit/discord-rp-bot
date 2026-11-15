"""第五區/第六區發行前測試：僅讀檔與驗證資料，絕不修改正式資料庫。"""
from __future__ import annotations
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
A=ROOT/'assets/history_v23'
materials=json.loads((A/'current_adopted_580.json').read_text(encoding='utf-8'))
fill=json.loads((A/'WN_0193_0200_current_fill.json').read_text(encoding='utf-8'))
assert len(materials)==580
assert len({x['asset_id'] for x in materials})==580
assert {x['asset_id'] for x in materials if x['asset_id'].startswith('WN-')}=={f'WN-{i:04d}' for i in range(1,201)}
assert next(x for x in materials if x['asset_id']=='WN-0200')==next(x for x in fill if x['asset_id']=='WN-0200')
assert all(x.get('approved') is True and x.get('import_ready') is True for x in materials)
for f in ('tools/audit_v23_release_readiness.py','tests/verify_v23_release_audit.py',
          'core/character_activation_v23.py','modules/notifications.py','modules/leaderboard_payout_policy.py'):
    ast.parse((ROOT/f).read_text(encoding='utf-8'),filename=f)
# 僅執行不連線、且明確以臨時資料庫隔離的已知測試。
for script in ('tests/verify_v23_release_audit.py',
               'tests/verify_history_v23_materials_one_pass.py',
               'tests/verify_current_materials_npc_grants_20261114.py',
               'tests/verify_phase34_forum_leaderboard_20261115.py'):
    proc=subprocess.run([sys.executable,str(ROOT/script)],cwd=ROOT,
                        text=True,capture_output=True)
    if proc.returncode:
        print('FAIL',script,proc.stdout,proc.stderr)
        raise SystemExit(1)
    print('PASS',script)
print('PASS: Phase 5+6 preflight; no live Discord or Railway access was attempted')
