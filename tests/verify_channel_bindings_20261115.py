"""Verify channel registry with isolated SQLite, without Discord API."""
import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for filename in ('core/channel_bindings_v1.py','modules/channel_binding_center.py',
                 'modules/official_center.py','modules/pet_center.py',
                 'modules/event_project_manager.py','modules/notifications.py'):
    ast.parse((ROOT/filename).read_text(encoding='utf-8'))
script = '''
from core.channel_bindings_v1 import SLOT_ROWS,SLOTS,get_binding,save_binding,unbind,get_official_npc_post_channel,all_bindings
G='1544051203493601380'; H='987654321123456789'
assert len(SLOT_ROWS)==38
assert get_binding(G,'event_category')=='1571458077805256764'
assert get_binding(G,'character_notice')=='1569650209951195186'
assert get_binding(G,'pet_missing')=='1545703079691689994'
assert get_binding(G,'world_story') is None
assert get_official_npc_post_channel(G,'MCN')=='1545702859511697489'
assert get_official_npc_post_channel(G,'çäººæ±å')=='1547179593239105657'
assert get_official_npc_post_channel(G,'æ±ååæ')=='1547179593239105657'
assert get_binding(H,'event_category') is None
save_binding(H,'event_category','123456789123456789','101')
assert get_binding(H,'event_category')=='123456789123456789'
save_binding(H,'network_faction_mcn','123456789123456790','101')
from core.network_groups import settings
assert settings(H)['factions']['MCN']==123456789123456790
save_binding(H,'post_forum','123456789123456791','101')
from core.network_social_posts import bound_forum
assert bound_forum(H)==123456789123456791
save_binding(H,'official_npc_solo','123456789123456792','101')
assert get_official_npc_post_channel(H,'å®æç¨é¬¥')=='123456789123456792'
assert len(all_bindings(H))==4
unbind(H,'event_category','101')
assert get_binding(H,'event_category') is None
assert get_binding(G,'event_category')=='1571458077805256764'
try:get_binding(G,'invalid')
except ValueError:pass
else:raise AssertionError('Unknown slot accepted')
print('PASS: 38 slots, event category, per-guild values, NPC sources, network sync')
'''
with tempfile.TemporaryDirectory(prefix='channel_verify_') as td:
    env=dict(os.environ,DATABASE_PATH=str(Path(td)/'test.sqlite3'))
    r=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=env,capture_output=True,text=True,timeout=45)
    print(r.stdout,end='')
    if r.returncode:
        print(r.stderr)
        raise SystemExit(r.returncode)
