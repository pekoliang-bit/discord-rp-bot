"""娛樂城部署前靜態檢查。

安裝位置：scripts/casino_integration_check.py
執行：python scripts/casino_integration_check.py
不登入 Discord、不連結正式資料庫、不修改任何玩家資料。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"
EXPECTED = (
    "casino_repository.py", "casino_service.py", "casino_scratch.py",
    "casino_lottery.py", "casino_gacha.py", "casino_games.py",
    "casino_effects.py", "casino_commands.py",
)


def inspect_project() -> list[str]:
    problems = []
    main = ROOT / "main.py"
    if not main.is_file():
        return [f"缺少主程式：{main}"]
    try:
        main_text = main.read_text(encoding="utf-8")
        ast.parse(main_text, filename=str(main))
    except (OSError, SyntaxError, UnicodeError) as exc:
        problems.append(f"main.py 讀取或語法錯誤：{exc}")
        main_text = ""
    if 'CASINO_EXTENSION = "modules.casino_commands"' not in main_text:
        problems.append("main.py 尚未加入娛樂城載入排除規則；請使用本批修正版 main.py")
    if "path.stem.startswith(CASINO_HELPER_PREFIX)" not in main_text:
        problems.append("main.py 可能誤將娛樂城服務檔當作 Extension")
    for name in EXPECTED:
        path = MODULES / name
        if not path.is_file():
            problems.append(f"缺少檔案：modules/{name}")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            problems.append(f"modules/{name} 無法解析：{exc}")
            continue
        setups = [x for x in tree.body if isinstance(x, ast.AsyncFunctionDef) and x.name == "setup"]
        if name == "casino_commands.py" and not setups:
            problems.append("casino_commands.py 沒有 async setup(bot)")
        if name != "casino_commands.py" and setups:
            problems.append(f"注意：{name} 存在 setup()，仍需確認不重複載入")
    # 偵測文字上重複宣告頂層娛樂城指令（不需匯入 discord.py）。
    claimants = []
    if MODULES.exists():
        for path in MODULES.glob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    func = dec.func
                    if not (isinstance(func, ast.Attribute) and func.attr == "command"):
                        continue
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and kw.value.value == "娛樂城":
                            claimants.append(path.name)
    if len(claimants) != 1:
        problems.append(f"頂層 /娛樂城 指令宣告數量應為 1，目前為 {len(claimants)}：{claimants}")
    return problems


def main() -> int:
    problems = inspect_project()
    if problems:
        print("[FAIL] 娛樂城部署前檢查未通過：")
        for problem in problems:
            print(" -", problem)
        return 1
    print("[PASS] 八個娛樂城檔案、主程式載入規則與指令宣告靜態檢查通過。")
    print("[NOTICE] 尚不代表金流、商城商品、保底與 Discord 互動已完成測試。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
