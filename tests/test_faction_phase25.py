"""Phase 25: offline regression tests. No real Discord or player balance mutations."""
import tempfile
import unittest
from unittest.mock import patch

from core import config
from modules import faction_action_transaction_lock as lock
from modules.faction_action_final_gate import inspect_final_gate

class Phase25Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.object(config,'DATABASE_PATH',self.tmp.name+'/test.sqlite3')
        self.patch.start()
        # core.database imports DATABASE_PATH by value
        from core import database
        self.db_patch=patch.object(database,'DATABASE_PATH',self.tmp.name+'/test.sqlite3')
        self.db_patch.start()
    def tearDown(self):
        self.db_patch.stop();self.patch.stop();self.tmp.cleanup()
    def test_one_lock_and_retries(self):
        a=lock.record_blocked_lock('g',1,'k',10000)
        self.assertTrue(a['created'])
        self.assertFalse(lock.record_blocked_lock('g',1,'k',10000)['created'])
        with self.assertRaises(ValueError): lock.record_blocked_lock('g',2,'k',10000)
        with self.assertRaises(ValueError): lock.record_blocked_lock('g',1,'other',10000)
        self.assertTrue(lock.cancel_blocked_lock('g',1))
        self.assertFalse(lock.cancel_blocked_lock('g',1))
    def test_bad_inputs(self):
        for n in (0,-10,True,1.5):
            with self.assertRaises(ValueError): lock.record_blocked_lock('g',1,'x',n)
    @patch('modules.faction_action_final_gate.read_policy',return_value={'values':{},'revision':2})
    @patch('modules.faction_action_final_gate.inspect_settlement',return_value={'issues':[], 'quoted_cost':10000})
    @patch('modules.faction_action_final_gate.inspect_proposal_treasury',return_value={'sufficient':True,'quoted_cost':10000})
    def test_gate_never_enables_execution(self,*_):
        result=inspect_final_gate('g',1)
        self.assertTrue(result['checks_passed'])
        self.assertFalse(result['execution_enabled'])

if __name__=='__main__': unittest.main()
