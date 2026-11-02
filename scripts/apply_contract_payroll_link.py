
"""整合員工合約與7／14天薪資服務。

使用：
python scripts/apply_contract_payroll_link.py

不連接正式資料庫。
更新前保留原檔備份。
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PAYROLL = ROOT / "core/company_payroll_service.py"
CONTRACT = ROOT / "core/company_contract_service.py"


def replace_once(text, old, new, label):
    if new in text:
        return text

    count = text.count(old)

    if count != 1:
        raise RuntimeError(
            f"{label}：預期找到1處，實際{count}處。"
            "已停止修改。"
        )

    return text.replace(old, new, 1)


def patch_payroll(source):
    old = '''        amount = int(employee["salary"])
        if amount <= 0:
            raise ValueError("此職位未設定有效薪資")
'''

    new = '''        from core.company_contract_payroll_link import (
            resolve_payroll_terms,
        )

        terms = resolve_payroll_terms(
            db,
            gid,
            bid,
            cid,
            employee["salary"],
            policy["cycle_days"],
        )

        amount = terms.salary
'''

    return replace_once(
        source,
        old,
        new,
        "正式薪資金額",
    )


def patch_contract(source):
    old = '''        db.execute("""
            UPDATE rp_company_employee_contracts
            SET employee_accepted=1,
                status='active',
                activated_at=CURRENT_TIMESTAMP
'''

    new = '''        from core.company_contract_payroll_guard import (
            validate_contract_activation,
        )

        validate_contract_activation(
            db,
            gid,
            contract["company_id"],
            contract["cycle_days"],
        )

        db.execute("""
            UPDATE rp_company_employee_contracts
            SET employee_accepted=1,
                status='active',
                activated_at=CURRENT_TIMESTAMP
'''

    return replace_once(
        source,
        old,
        new,
        "合約啟用週期檢查",
    )


def main():
    required = (
        ROOT / "core/company_contract_payroll_link.py",
        ROOT / "core/company_contract_payroll_guard.py",
        PAYROLL,
        CONTRACT,
    )

    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)

    originals = {
        PAYROLL: PAYROLL.read_text(encoding="utf-8"),
        CONTRACT: CONTRACT.read_text(encoding="utf-8"),
    }

    updated = {
        PAYROLL: patch_payroll(originals[PAYROLL]),
        CONTRACT: patch_contract(originals[CONTRACT]),
    }

    # 先全部檢查，再修改任何檔案。
    for path, source in updated.items():
        ast.parse(source)
        compile(source, str(path), "exec")

    for path, source in updated.items():
        original = originals[path]

        if source == original:
            continue

        backup = path.with_suffix(
            path.suffix + ".salary_backup"
        )

        if not backup.exists():
            backup.write_text(
                original,
                encoding="utf-8",
            )

        path.write_text(
            source,
            encoding="utf-8",
        )

        print(
            "已更新：",
            path.relative_to(ROOT),
        )

    print("合約與發薪同步接線完成。")


if __name__ == "__main__":
    main()
