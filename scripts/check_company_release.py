"""公司／集團／薪資基礎回歸入口，不連接正式 Discord 或玩家資料庫。

用法：python scripts/check_company_release.py
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "tests.test_company_group_eligibility",
    "tests.test_company_group_membership_guard",
    "tests.test_company_payroll_ui_patch",
    "tests.test_company_payroll_guard",
    "tests.test_company_contract_payroll_integration",
    "tests.test_company_payroll_period",
    "tests.test_company_payroll_read",
    "tests.test_company_contract_payroll_guard",
    "tests.test_company_contract_payroll_link",
)


def main():
    print("檢查公司、集團、薪資：僅執行本地測試", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "-v", *TESTS],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        print("公司整合驗收未通過；禁止宣稱已完成正式驗證。")
    else:
        print("公司整合離線回歸通過；仍須 Discord / Railway 實測。")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
