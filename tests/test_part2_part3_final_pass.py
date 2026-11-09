
"""Second/third batch SQL transactions, static contract and idempotency."""
import ast
import sqlite3
import unittest
from pathlib import Path
from core.general_case_executors import execute_general_case, initialize_general_cases
from core.application_capabilities import general_can_approve

ROOT=Path(__file__).resolve().parents[1]

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE characters(id INTEGER,guild_id TEXT,owner_id TEXT,status TEXT)')
        self.db.execute('INSERT INTO characters VALUES(1,\'g\',\'owner\',\'active\')')
        self.db.execute('''CREATE TABLE shop_products(id INTEGER,guild_id TEXT,active INTEGER,
                        PRIMARY KEY(guild_id,id))''')
        self.db.execute('INSERT INTO shop_products VALUES(5,\'g\',1)')
        self.db.execute('''CREATE TABLE shop_inventory(guild_id TEXT,character_id INTEGER,
                        product_id INTEGER,quantity INTEGER,PRIMARY KEY(guild_id,character_id,product_id))''')
        self.db.execute('''CREATE TABLE rp_general_applications(id INTEGER PRIMARY KEY,guild_id TEXT,
                        user_id TEXT,character_id INTEGER,kind TEXT,details TEXT,status TEXT,
                        reviewer_id TEXT,reviewed_at TEXT)''')
        initialize_general_cases(self.db)

    def tearDown(self):
        self.db.close()

    def app(self,id,kind,details,uid='owner'):
        self.db.execute('''INSERT INTO rp_general_applications
            (id,guild_id,user_id,character_id,kind,details,status)
            VALUES (?,\'g\',?,1,?,?,\'pending\')''',(id,uid,kind,details))
        return self.db.execute(
            'SELECT * FROM rp_general_applications WHERE id=?',(id,)
        ).fetchone()

    def test_item_award_once(self):
        r=self.app(1,'item','【申請物品名稱及數量】商品#5 × 3')
        self.assertTrue(general_can_approve('item'))
        execute_general_case(self.db,r,'staff')
        execute_general_case(self.db,r,'staff')
        self.assertEqual(
            self.db.execute('SELECT quantity FROM shop_inventory').fetchone()[0],3
        )

    def test_wrong_owner_not_issued(self):
        r=self.app(1,'item','【申請物品名稱及數量】商品#5 × 3','stranger')
        with self.assertRaises(ValueError):
            execute_general_case(self.db,r,'staff')
        self.assertIsNone(
            self.db.execute('SELECT 1 FROM shop_inventory').fetchone()
        )

    def test_invalid_item_or_quantity_rejected(self):
        for i, label in enumerate(
            ('商品#999 × 1','商品#5 × 101','隨便給一個高價商品'),1
        ):
            r=self.app(i,'item','【申請物品名稱及數量】'+label)
            with self.assertRaises(ValueError):
                execute_general_case(self.db,r,'staff')

    def test_leave_overlap_rejected(self):
        a=self.app(
            1,'leave',
            '【開始與結束日期（YYYY-MM-DD 至 YYYY-MM-DD）】2026-11-01 至 2026-11-10\n'
            '【請假事由與需要暫停的義務】上課'
        )
        b=self.app(
            2,'leave',
            '【開始與結束日期（YYYY-MM-DD 至 YYYY-MM-DD）】2026-11-08 至 2026-11-12\n'
            '【請假事由與需要暫停的義務】上課'
        )
        execute_general_case(self.db,a,'staff')
        with self.assertRaises(ValueError):
            execute_general_case(self.db,b,'staff')

    def test_pending_cases_still_blocked(self):
        for kind in (
            'role_retire','npc_resign','npc_switch',
            'location_add','channel_add','item_create'
        ):
            self.assertFalse(general_can_approve(kind),kind)

    def test_character_view_contract(self):
        text=(ROOT/'modules/character_view.py').read_text()
        ast.parse(text)
        self.assertIn('rp_registered_partnerships',text)
        self.assertIn(
            '生日：{display_value(settings.get(\'birthday\')',text
        )
        self.assertIn(
            'representative(actor.guild_id, character_id)',text
        )
        self.assertIn('🏆 成就榮譽',text)

if __name__=='__main__':
    unittest.main()
