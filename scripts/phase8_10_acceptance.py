
"""第八～十部分離線驗收：不讀取正式資料庫，不接觸 Discord API。

用法：python scripts/phase8_10_acceptance.py
      python scripts/phase8_10_acceptance.py --skip-tests
測試時強制將 DATABASE_PATH 指向臨時 SQLite 位置。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "公司": "modules/company_portal.py",
    "陣營": "modules/faction_ui.py",
    "排行": "modules/z_system_hub.py",
    "通知": "modules/notifications.py",
    "抽獎": "modules/lottery_threads.py",
    "娛樂城": "modules/casino_commands.py",
    "銀行": "modules/z_bank_center.py",
}
CRITICAL_FILES = (
    "core/official_commands.py",
    "main.py",
    "modules/leaderboard_runtime.py",
    "modules/leaderboard_scheduler.py",
    "modules/leaderboard_settlement.py",
    "modules/leaderboard_delivery.py",
    "modules/notifications.py",
    "modules/lottery_threads.py",
    "modules/company_portal.py",
    "modules/faction_ui.py",
    "modules/zzz_legacy_cleanup.py",
    "core/asset_integrity.py",
    "core/general_case_executors.py",
    "core/application_capabilities.py",
)


def parse_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            if not (isinstance(d.func, ast.Attribute) and d.func.attr == "command"):
                continue
            for kw in d.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    names.add(kw.value.value)
    return names


def static_audit():
    errors, warnings = [], []
    parsed = {}
    all_files = sorted(path for path in ROOT.rglob("*.py")
                       if not any(part in {".git", ".venv", "venv", "__pycache__"}
                                  for part in path.relative_to(ROOT).parts))
    for path in all_files:
        if not path.exists():
            continue
        rel = str(path.relative_to(ROOT))
        source = path.read_text(encoding="utf-8-sig")
        if source.lstrip().startswith("```"):
            errors.append(f"Python 檔混入 Markdown 程式碼圍欄：{rel}")
        try:
            parsed[rel] = ast.parse(source, filename=rel)
        except (SyntaxError, ValueError) as exc:
            errors.append(f"Python 語法錯誤：{rel}: {exc}")
    for name in CRITICAL_FILES:
        if name not in parsed:
            errors.append(f"缺少必需模組：{name}")
    try:
        # 直接解析正式名單，避免匯入會有副作用的主程式。
        official_ast = parsed["core/official_commands.py"]
        official = None
        for node in official_ast.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "OFFICIAL_COMMANDS" for t in node.targets):
                official = ast.literal_eval(node.value)
                break
        if not isinstance(official, dict):
            raise ValueError("正式清單不是常數字典")
    except (KeyError, ValueError, TypeError) as exc:
        errors.append(f"正式指令名單無法讀取：{exc}")
        official = {}
    if len(official) != 24:
        errors.append(f"正式頂層指令應有 24 項，實際為 {len(official)} 項")
    for forbidden in ("稱號", "新手指引", "新人禮包管理"):
        if forbidden in official:
            errors.append(f"已取消頂層指令仍在正式名單：/{forbidden}")
    names = {}
    for path, tree in parsed.items():
        if path == "main.py" or path.startswith("modules/"):
            names[path] = parse_names(tree)
    declared = set().union(*names.values()) if names else set()
    missing_declarations = sorted(set(official) - declared)
    if missing_declarations:
        errors.append("缺少頂層指令宣告（靜態檢查）：" + "、".join(missing_declarations))
    for command, path in REQUIRED.items():
        if command not in names.get(path, set()):
            errors.append(f"第八／九部分入口不存在：/{command}（{path}）")
    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    if 'if self.command_report["missing"]:' not in main_text:
        errors.append("正式指令缺漏未阻止同步")
    notifications = (ROOT / "modules/notifications.py").read_text(encoding="utf-8")
    if "bulletin-[0-9]+" not in notifications:
        warnings.append("通知公告去重鍵格式沒有明確驗證")
    # 只查跨模組入口是否存在，不宣告實際收款、結算或 Discord 發送成功。
    bridges = {
        "排行榜排程": ("modules/leaderboard_runtime.py", "run_due"),
        "排行榜快照與獎勵": ("modules/leaderboard_settlement.py", "settle"),
        "通知排程": ("modules/notifications.py", "dispatch_digests"),
        "陣營介面": ("modules/faction_ui.py", "async def setup"),
        "公司介面": ("modules/company_portal.py", "async def setup"),
    }
    for label, (name, token) in bridges.items():
        if name in parsed and token not in (ROOT / name).read_text(encoding="utf-8"):
            errors.append(f"跨系統入口缺失：{label}（{name}）")
    return {"python_file_count": len(parsed), "declared_official_commands": len(official),
            "missing_declarations": missing_declarations, "errors": errors, "warnings": warnings}


def run_tests():
    with tempfile.TemporaryDirectory(prefix="discord_rp_final_preflight_") as directory:
        env = os.environ.copy()
        env["DATABASE_PATH"] = str(Path(directory) / "isolated_test.sqlite3")
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests", "--disable-warnings"],
            cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=180, check=False,
        )
        lines = (proc.stdout + "\n" + proc.stderr).splitlines()
        summary = next((line for line in reversed(lines) if re.search(r"\b(?:passed|failed|error|errors)\b", line)), "未取得測試摘要")
        return {"passed": proc.returncode == 0, "exit_code": proc.returncode,
                "summary": summary.strip(), "failure_tail": "\n".join(lines[-30:]) if proc.returncode else ""}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--json", help="另存報告至指定路徑")
    args = parser.parse_args()
    result = static_audit()
    result["unit_tests"] = {"skipped": True} if args.skip_tests else run_tests()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if result["errors"] or result["unit_tests"].get("passed") is False:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
