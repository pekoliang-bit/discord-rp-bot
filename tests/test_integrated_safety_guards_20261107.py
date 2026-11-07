"""Important previous regression checks; intentionally no discord.py dependency."""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]

class IntegratedSafetyGuards(unittest.TestCase):
    def test_contract_exit_ui_passes_only_required_parameters(self):
        tree = ast.parse((ROOT/'modules/company_contract_exit_ui.py').read_text(encoding='utf8'))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'to_thread']
        matches = [c for c in calls if c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == 'finalize_exit']
        self.assertTrue(matches, '解約 UI 必須有 finalize_exit 呼叫')
        self.assertTrue(all(len(c.args) == 4 for c in matches), 'finalize_exit 應僅有 guild、exit、actor 三個參數')
    def test_salary_initialize_not_inside_payment_transaction(self):
        tree=ast.parse((ROOT/'core/company_payroll_service.py').read_text(encoding='utf8'))
        fn=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='pay_salary')
        for node in ast.walk(fn):
            if isinstance(node,ast.With):
                self.assertFalse(any(isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute) and c.func.attr=='initialize' and isinstance(c.func.value,ast.Name) and c.func.value.id=='character_status' for c in ast.walk(node)), '發薪交易內禁止再次初始化角色地位 SQLite')
    def test_payout_remains_off_pending_approval(self):
        tree=ast.parse((ROOT/'modules/leaderboard_payout_policy.py').read_text(encoding='utf8'))
        assigns=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PAYOUT_ENABLED' for t in n.targets)]
        self.assertTrue(assigns)
        self.assertIs(assigns[-1].value.value,False)
if __name__=='__main__':unittest.main()
