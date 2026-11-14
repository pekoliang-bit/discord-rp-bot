"""Regression: NPC select + /總覽 24 commands + read-only tags + SQLite event DDL.
Run in project root: python tests/verify_overview_npc_import_20261114.py
No discord.py, bot startup, guild access or live DB is required.
"""
from __future__ import annotations
import ast
import contextlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules.overview_catalog import SYSTEMS
names=[name for group in SYSTEMS.values() for name in group]
EXPECTED={"查看","抽獎","傳聞","創角","登記","公司","官方","劇情","群主指引","商城","申請","審核","娛樂城","惡意","任務","總覽","排行","系統狀態","銀行","陣營","網路","行動","通知","資產"}
assert len(names)==len(EXPECTED)==24 and set(names)==EXPECTED
assert "角色切換" not in names
for name in ('modules/npc_history_v23.py','modules/overview_catalog.py','modules/overview_data.py','modules/event_project_manager.py'):
    ast.parse((ROOT/name).read_text(encoding='utf-8'),filename=name)
npc=(ROOT/'modules/npc_history_v23.py').read_text(encoding='utf-8')
assert "value='player_npc'" in npc and "value='official_npc'" in npc
assert "ImportNPCModal(kind)" in npc and "self.kind.value" not in npc
assert "NPCImportKindView(i.guild_id, i.user.id)" in npc
assert "send_modal(ImportNPCModal())" not in npc
src=(ROOT/'modules/overview_data.py').read_text(encoding='utf-8')
assert 'seed(gid)' not in src
# Isolate event schema creation without importing discord.py or touching actual DB.
source=ast.parse((ROOT/'modules/event_project_manager.py').read_text(encoding='utf-8'))
fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='init_db')
fn.decorator_list=[]
namespace={}
class TempTransaction:
    def __init__(self,db):self.db=db
    def __enter__(self):return self.db
    def __exit__(self,typ,exc,tb):
        if exc is None:self.db.commit()
with sqlite3.connect(':memory:') as db:
    namespace['transaction']=lambda:TempTransaction(db)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<isolated-event-ddl>','exec'),namespace)
    namespace['init_db']()
    cols=[row[1] for row in db.execute('PRAGMA table_info(rp_event_project_audit)')]
    assert cols.count('detail')==1,cols
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'rp_event_%'").fetchone()[0]>=6
print('PASS: 24 system commands, NPC 2-option selector, read-only overview, event schema')
