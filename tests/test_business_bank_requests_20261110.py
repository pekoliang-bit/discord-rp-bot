"""銀行申請與編碼隔離單測，無 Discord 實機／正式 DB 寫入。"""
import sqlite3
import unittest

from core import business_public_ids as ids
import ast
from pathlib import Path

root=Path(__file__).resolve().parents[1]
# 現在的測試環境沒有 discord.py；只隔離測試資料庫邏輯，不冒充 Discord UI 驗收。
source=ast.parse((root/'modules/business_bank_request_ui.py').read_text(encoding='utf-8'))
funcs=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('resolve_target','validate_operator')]
code=compile(ast.Module(body=funcs,type_ignores=[]),'<bank-logic>','exec')
ns={}
exec(code,ns)
resolve_target=ns['resolve_target']
validate_operator=ns['validate_operator']
reason_src=ast.parse((root/'modules/report_modals.py').read_text(encoding='utf-8'))
REASONS=next(ast.literal_eval(n.value) for n in reason_src.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='REASONS' for t in n.targets))


class BankChangeTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE rp_business_entities(guild_id TEXT,business_id TEXT,kind TEXT,owner_id TEXT,status TEXT,founded_at TEXT)')
        self.db.execute('CREATE TABLE personal_shops(guild_id TEXT,id INTEGER,character_id INTEGER,status TEXT,opened_at TEXT)')
        self.db.execute('CREATE TABLE characters(guild_id TEXT,id INTEGER,name TEXT,owner_id TEXT,status TEXT,public_code TEXT)')
        self.db.execute('CREATE TABLE character_number_launches(guild_id TEXT,launched_at TEXT)')
        self.db.execute('CREATE TABLE character_number_servers(guild_id TEXT,server_serial INTEGER)')
        self.db.execute('CREATE TABLE rp_player_shop_channels(guild_id TEXT,shop_id INTEGER,channel_id TEXT)')
        self.db.executemany('INSERT INTO rp_business_entities VALUES (?,?,?,?,?,?)',[
            ('1','c1','company','owner','active','2026-11-10'),
            ('1','g1','group','owner','active','2026-11-10'),
            ('1','b1','brand','owner','active','2026-11-10'),
        ])
        self.db.execute("INSERT INTO characters VALUES ('1',17,'角色甲','owner','active',NULL)")
        self.db.execute("INSERT INTO personal_shops VALUES ('1',41,17,'open','2026-11-10')")
        self.db.commit()

    def tearDown(self):self.db.close()

    def test_owner_only_and_legacy_ids(self):
        self.assertEqual(resolve_target(self.db,'1','company_bank','c1','owner'),('c1','company'))
        self.assertEqual(resolve_target(self.db,'1','company_bank','g1','owner'),('g1','group'))
        self.assertEqual(resolve_target(self.db,'1','shop_bank','brand:b1','owner'),('brand:b1','brand'))
        self.assertEqual(resolve_target(self.db,'1','shop_bank','41','owner'),('41','shop'))
        for raw,kind in [('c1','company_bank'),('g1','company_bank'),('41','shop_bank'),('brand:b1','shop_bank')]:
            with self.assertRaises(PermissionError):resolve_target(self.db,'1',kind,raw,'not_owner')

    def test_character_operator(self):
        self.assertEqual(validate_operator(self.db,'1','17')[0],17)
        with self.assertRaises(ValueError):validate_operator(self.db,'1','99')

    def test_numbering_gated(self):
        self.assertIsNone(ids.issue(self.db,'1','company','c1'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rp_business_public_identity').fetchone()[0],0)
        self.db.execute("INSERT INTO character_number_servers VALUES ('1',1)")
        self.db.execute("INSERT INTO character_number_launches VALUES ('1','2026-11-10')")
        c=ids.issue(self.db,'1','company','c1')
        g=ids.issue(self.db,'1','group','g1')
        b=ids.issue(self.db,'1','brand','b1')
        s=ids.issue(self.db,'1','shop','41')
        self.assertEqual((c,g,b,s),('C-01-1110-01','G-01-1110-01','B-01-1110-01','S-01-1110-01'))
        self.assertEqual(ids.issue(self.db,'1','company','c1'),c)
        self.assertEqual(ids.resolve_business(self.db,'1',c,allowed=('company',)), 'c1')
        self.assertEqual(ids.resolve_shop(self.db,'1',s),41)

    def test_reason_select_under_limit(self):
        self.assertGreaterEqual(len(REASONS),15)
        self.assertLessEqual(len(REASONS),25)

if __name__=='__main__':unittest.main()
