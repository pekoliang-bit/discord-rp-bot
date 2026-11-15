
"""NPC V2.4 role ownership, delegated claiming and old table migration.

Fully isolated SQLite. Does not import discord.py, contact Discord, or touch live data.
"""
from __future__ import annotations

import ast
import contextlib
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.npc_owner_access_v24 import (
    is_server_admin, member_is_admin, ensure_npc_owner, can_operate_npc,
)


def member(uid, guild, admin=False, bot=False):
    return SimpleNamespace(id=uid, guild=guild, bot=bot,
                           guild_permissions=SimpleNamespace(administrator=admin))


guild = SimpleNamespace(id=123, owner_id=1)
owner = member(1, guild)
admin = member(2, guild, admin=True)
player = member(3, guild)
other = SimpleNamespace(id=456, owner_id=9)
outsider = member(4, other, admin=True)
assert is_server_admin(SimpleNamespace(guild=guild, user=owner))
assert is_server_admin(SimpleNamespace(guild=guild, user=admin))
assert not is_server_admin(SimpleNamespace(guild=guild, user=player))
assert ensure_npc_owner(guild, admin, 'official_npc') == 2
assert ensure_npc_owner(guild, player, 'player_npc') == 3
for invalid_member in (player, outsider):
    try:
        ensure_npc_owner(guild, invalid_member, 'official_npc')
    except PermissionError:
        pass
    else:
        raise AssertionError('Official NPC assigned to unauthorized member')

actor_admin = SimpleNamespace(user_id='2', is_admin=True, is_discord_administrator=True)
actor_player = SimpleNamespace(user_id='3', is_admin=False, is_discord_administrator=False)
actor_manage_guild = SimpleNamespace(user_id='4', is_admin=True, is_discord_administrator=False)
assert can_operate_npc(actor_admin, {'character_type':'official_npc','owner_id':'1'})
assert not can_operate_npc(actor_manage_guild, {'character_type':'official_npc','owner_id':'1'})
assert not can_operate_npc(actor_player, {'character_type':'official_npc','owner_id':'1'})
assert can_operate_npc(actor_player, {'character_type':'player_npc','owner_id':'3'})
assert not can_operate_npc(actor_admin, {'character_type':'player_npc','owner_id':'3'})

source = (ROOT/'modules/npc_history_v23.py').read_text(encoding='utf-8')
tree = ast.parse(source)
functions = {node.name:node for node in tree.body if isinstance(node, ast.FunctionDef)}
selected = ('init','parse_text','import_template','available','claim')
module = ast.Module(body=[functions[k] for k in selected], type_ignores=[])
ast.fix_missing_locations(module)

with tempfile.TemporaryDirectory(prefix='npc_owner_v24_') as temp:
    path = Path(temp)/'test.sqlite'

    @contextlib.contextmanager
    def transaction():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextlib.contextmanager
    def get_connection():
        with transaction() as db:
            yield db

    with transaction() as db:
        db.execute('CREATE TABLE characters(guild_id TEXT,owner_id TEXT,character_type TEXT,status TEXT)')
        # The old v2.3 table does not have preferred_owner or registered_by.
        db.execute('''CREATE TABLE rp_npc_templates_v23 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            source_message TEXT NOT NULL, channel_id TEXT NOT NULL,
            name TEXT NOT NULL,kind TEXT NOT NULL,raw_text TEXT NOT NULL,
            fields_json TEXT NOT NULL,custom_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'available',claimed_by TEXT,
            revision INTEGER NOT NULL DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,source_message))''')

    namespace = {'transaction':transaction,'get_connection':get_connection,
                 'history_init':lambda db:None,'json':json,'re':re,
                 'CUSTOM':re.compile('（自定義）'),
                 'FIELD':re.compile(r'^\s*([^：:\n]{1,38})\s*[：:]\s*(.*?)\s*$'),
                 'PROTECTED':('健康值','持有金錢')}
    exec(compile(module,str(ROOT/'modules/npc_history_v23.py'),'exec'),namespace)
    text='本名／藝名：／測試玩家NPC\n生日：04/18\n國籍：（自定義）'
    tid,name,count=namespace['import_template'](123,1001,2001,text,'player_npc',preferred_owner=3,registered_by=2)
    assert name=='測試玩家NPC' and count==1
    with get_connection() as db:
        cols={r['name'] for r in db.execute('PRAGMA table_info(rp_npc_templates_v23)')}
        assert {'preferred_owner','registered_by'} <= cols
        row=db.execute('SELECT preferred_owner,registered_by FROM rp_npc_templates_v23 WHERE id=?',(tid,)).fetchone()
        assert tuple(row)==('3','2')
    assert len(namespace['available'](123,uid=3))==1
    assert namespace['available'](123,uid=4)==[]
    try:
        namespace['claim'](123,4,tid,{'國籍':'台灣'})
    except PermissionError:
        pass
    else:
        raise AssertionError('Unauthorized player claimed reserved NPC')
    claim_id=namespace['claim'](123,3,tid,{'國籍':'台灣'})
    assert claim_id>0
    assert namespace['available'](123,uid=3)==[]

for relative in ('modules/character_sheet_registration.py',
                 'modules/character_view.py','core/permissions.py'):
    ast.parse((ROOT/relative).read_text(encoding='utf-8'))
assert 'NPCOwnerMemberSelect(discord.ui.UserSelect)' in source
assert 'NPCImportOwnerModeView' in source
assert 'authorized_admin=is_admin' in (ROOT/'modules/character_sheet_registration.py').read_text(encoding='utf-8')
print('PASS: administrator/owner roles; delegated template visibility, claim restriction, old DB migration; code syntax')
