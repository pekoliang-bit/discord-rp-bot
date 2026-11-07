"""場所行動安全交易離線驗收，不連接正式 Discord 或 Railway。"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tests = ('test_location_action_execution.py', 'test_action_medical_defaults.py', 'test_medical_service_rules.py')
for filename in tests:
    print(f'\n=== {filename} ===', flush=True)
    result=subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', filename, '-v'],cwd=ROOT, check=False)
    if result.returncode:
        sys.exit(result.returncode)
print('\n場所行動交易及醫院規則的指定離線測試全部通過。')
