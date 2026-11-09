"""第七部分：商城付費行動值交易重試安全、行動值扣除安全。"""
import ast
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import database
from core.action_execution import _spend_paid, ActionExecutionError

ROOT = Path(__file__).resolve().parents[1]


class ShopActionSafetyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(database, 'DATABASE_PATH', Path(tmp.name)/'test.sqlite')
        p.start()
        self.addCleanup(p.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('g','測試')")
            db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES "
                       "(10,'g','u','甲','active'),(11,'g','u','乙','active')")
            from core.economy import _payment
            wallet = database.get_or_create_account(db,'g','character',10,'wallet')
            source = database.get_or_create_account(db,'g','system','seed','treasury')
            _payment(db,'g','seed-10',source,wallet,10000,'測試初始化')
        self.actor=SimpleNamespace(guild_id='g', user_id='u', is_admin=False)

        # Production module is Discord UI; extract its actual standalone business function
        # for offline testing without importing discord.py.
        src = (ROOT/'modules/zzz_shop_home.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='buy_actions')
        from core.database import transaction, get_or_create_account
        from core.economy import _payment
        self.calls = {'count': 0}
        def payment(*args, **kwargs):
            self.calls['count'] += 1
            return _payment(*args, **kwargs)
        namespace = {
            'transaction':transaction, 'shop':SimpleNamespace(require_character=self.require_character),
            'datetime':datetime, 'timedelta':timedelta, 'timezone':timezone,
            'ACTION_PRICE':500, 'PERIOD_DAYS':30,
            'get_or_create_account':get_or_create_account, '_payment':payment,
        }
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(ROOT/'modules/zzz_shop_home.py'),'exec'),namespace)
        self.buy=namespace['buy_actions']

    def require_character(self, db,actor,character_id):
        row = db.execute("SELECT 1 FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'",
                         (str(actor.guild_id), int(character_id), str(actor.user_id))).fetchone()
        if not row:
            raise PermissionError('角色無權購買')

    def test_duplicate_same_purchase_does_not_pay_twice(self):
        self.buy(self.actor,10,2,'unique-1')
        self.buy(self.actor,10,2,'unique-1')
        self.assertEqual(self.calls['count'],1)
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM paid_action_packs').fetchone()[0],1)

    def test_duplicate_key_different_character_or_quantity_fails(self):
        self.buy(self.actor,10,2,'unique-1')
        with self.assertRaises(ValueError):
            self.buy(self.actor,11,2,'unique-1')
        with self.assertRaises(ValueError):
            self.buy(self.actor,10,3,'unique-1')
        self.assertEqual(self.calls['count'],1)

    def test_invalid_transaction_key_and_bool_quantity_fail(self):
        with self.assertRaises(ValueError):
            self.buy(self.actor,10,1,'')
        with self.assertRaises(ValueError):
            self.buy(self.actor,10,True,'unique-2')

    def test_paid_debit_requires_matching_character_and_unexpired_package(self):
        now = datetime.now(timezone.utc)
        with database.transaction() as db:
            db.execute('''CREATE TABLE paid_action_packs (
              id INTEGER PRIMARY KEY, guild_id TEXT, character_id INTEGER,
              remaining INTEGER, expires_at TEXT)''')
            db.execute('INSERT INTO paid_action_packs VALUES (1,?,?,?,?)',
                       ('g',10,2,(now+timedelta(days=1)).isoformat()))
            _spend_paid(db,'g',10,1,now.isoformat())
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT remaining FROM paid_action_packs WHERE id=1').fetchone()[0],1)
        with self.assertRaises(ActionExecutionError):
            with database.transaction() as db:
                _spend_paid(db,'g',11,1,now.isoformat())

if __name__=='__main__':
    unittest.main()
