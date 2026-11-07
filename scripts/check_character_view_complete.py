"""/查看離線專項測試與整個專案語法檢查。"""
from pathlib import Path
import subprocess
import sys
import ast

ROOT = Path(__file__).resolve().parents[1]


def main():
    files = tuple(ROOT.rglob('*.py'))
    for p in files:
        try:
            ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p))
        except (SyntaxError, UnicodeError) as exc:
            print(f'ERROR {p.relative_to(ROOT)}: {exc}')
            return 1
    print(f'PASS: {len(files)} Python files parse successfully.', flush=True)
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                             '-p', 'test_character_view_complete_20261107.py', '-v'], cwd=ROOT)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
