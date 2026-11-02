
"""集團制度整合腳本。

使用：
python scripts/apply_group_system.py

只修改原始碼，不連接資料庫。
修改前建立備份。
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUSINESS = ROOT / "modules/company_business.py"
PORTAL = ROOT / "modules/company_portal.py"


def replace_once(source, old, new, label):
    if new in source and old not in source:
        return source

    if source.count(old) != 1:
        raise RuntimeError(
            f"{label}：程式碼版本不同，"
            "已停止修改。"
        )

    return source.replace(old, new, 1)


def patch_business(source):
    # 舊的 _maybe_auto_group 可能仍被
    # 其他品牌模組呼叫，因此保留函式名稱，
    # 但停止以品牌數量建立集團。
    tree = ast.parse(source)

    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_maybe_auto_group"
    ]

    if len(nodes) != 1:
        raise RuntimeError(
            "找不到唯一的舊自動集團函式。"
        )

    node = nodes[0]
    lines = source.splitlines(keepends=True)

    replacement = '''def _maybe_auto_group(
    db, guild_id, user_id, character_id
):
    """舊品牌入口：不再以品牌數成立集團。"""
    return None

'''

    current = "".join(
        lines[node.lineno - 1:node.end_lineno]
    )

    if "舊品牌入口" not in current:
        source = (
            "".join(lines[:node.lineno - 1])
            + replacement
            + "".join(lines[node.end_lineno:])
        )

    # 公司正式核准後才檢查五間公司。
    old = '''        # 品牌數量達標時，免費成立集團。
        if application["kind"] == "brand":
            _maybe_auto_group(
                db,
                guild_id,
                application["applicant_id"],
                application["owner_character_id"],
            )
'''

    new = '''        # 第五間正式公司成立後，自動建立集團。
        if application["kind"] == "company":
            from core.company_group_auto import (
                maybe_create_group,
            )

            maybe_create_group(
                db,
                guild_id,
                application["applicant_id"],
                application["owner_character_id"],
            )
'''

    source = replace_once(
        source, old, new,
        "五間公司自動成立集團",
    )

    # 直接申請集團不要求先有五間公司，
    # 但禁止已有集團的持有人再次創立。
    anchor = '''        # 公司和品牌使用獨立帳戶。'''

    guard = '''        if application["kind"] == "group":
            from core.company_group_eligibility import (
                validate_group_application,
            )

            validate_group_application(
                db,
                guild_id,
                application["applicant_id"],
                method="direct",
            )

'''

    if guard not in source:
        source = replace_once(
            source,
            anchor,
            guard + anchor,
            "直接成立集團資格檢查",
        )

    # 直接核准集團成立後，自動納入
    # 尚未歸屬其他商業主體的自有公司。
    marker = '''        # 兼容舊有公司銀行、人事模組。'''

    attach = '''        if application["kind"] == "group":
            from core.company_group_membership import (
                attach_all_owned,
            )

            attach_all_owned(
                db,
                guild_id,
                business_id,
                application["applicant_id"],
            )

'''

    if attach not in source:
        source = replace_once(
            source, marker, attach + marker,
            "直接成立集團後的公司歸屬",
        )

    return source


def patch_portal(source):
    added = '''        from modules.company_group_management_ui import (
            CompanyGroupEntry,
        )
        self.add_item(CompanyGroupEntry())

'''

    if "self.add_item(CompanyGroupEntry())" in source:
        return source

    marker = '''        if is_owner or can_review:
            owner_actions = []
'''

    return replace_once(
        source,
        marker,
        added + marker,
        "公司面板集團入口",
    )


def main():
    files = {
        BUSINESS: patch_business,
        PORTAL: patch_portal,
    }

    required = (
        "core/company_group_rules.py",
        "core/company_group_eligibility.py",
        "core/company_group_membership.py",
        "core/company_group_auto.py",
        "core/company_group_governance.py",
        "core/company_group_overview.py",
        "modules/company_group_management_ui.py",
    )

    for relative in required:
        if not (ROOT / relative).is_file():
            raise FileNotFoundError(relative)

    outputs = {}

    for path, transform in files.items():
        original = path.read_text(
            encoding="utf-8"
        )

        updated = transform(original)

        ast.parse(updated)
        compile(
            updated,
            str(path),
            "exec",
        )

        outputs[path] = (original, updated)

    # 先確認兩個檔案都可修改，
    # 再一次寫入，避免只改到一半。
    for path, (original, updated) in outputs.items():
        if original == updated:
            continue

        backup = path.with_suffix(
            path.suffix + ".group_backup"
        )

        if not backup.exists():
            backup.write_text(
                original,
                encoding="utf-8",
            )

        path.write_text(
            updated,
            encoding="utf-8",
        )

        print(
            "✅ 已更新：",
            path.relative_to(ROOT),
        )

    print(
        "集團正式規則已完成原始碼接線。"
    )


if __name__ == "__main__":
    main()
