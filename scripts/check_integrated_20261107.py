"""一次整合離線驗收：不能替代 Discord / Railway 實機。"""
from __future__ import annotations
import py_compile
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TESTS=[
 'tests/test_integrated_safety_guards_20261107.py',
 'tests/test_leaderboard_batch05_audit.py',
 'tests/test_location_action_execution.py',
 'tests/test_action_medical_defaults.py',
 'tests/test_medical_service_rules.py',
 'tests/test_company_group_eligibility.py',
 'tests/test_company_payroll_ui_patch.py',
 'tests/test_company_group_membership_guard.py',
 'tests/test_rumor_auto_delivery.py',
 'tests/test_rumor_auto_linked_cases.py',
 'tests/test_rumor_due_preview.py',
 'tests/test_rumor_reward_sqlite.py',
]
def main():
    count=0
    for p in ROOT.rglob('*.py'):
        if '.git' in p.parts: continue
        py_compile.compile(str(p),doraise=True)
        count+=1
    print(f'語法檢查：{count} 個 Python 檔案通過')
    args=[sys.executable,'-m','pytest','-q',*TESTS,'--disable-warnings']
    result = subprocess.call(args,cwd=ROOT)
    if result:
        return result
    for check in ('scripts/check_comment_pipeline.py', 'scripts/check_live_release.py'):
        result = subprocess.call([sys.executable, check], cwd=ROOT)
        if result:
            return result
    return 0
if __name__=='__main__':sys.exit(main())
