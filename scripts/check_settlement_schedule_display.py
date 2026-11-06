"""本批離線驗收。執行：python scripts/check_settlement_schedule_display.py"""
from pathlib import Path
import os,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
env=dict(os.environ);env['PYTHONPATH']=str(ROOT)+os.pathsep+env.get('PYTHONPATH','')
args=[sys.executable,'-m','unittest','discover','-s','tests','-p','test_settlement_countdown_display.py','-v']
a=subprocess.run(args,cwd=ROOT,env=env)
if a.returncode:sys.exit(a.returncode)
args[7]='test_schedule_reminder_contract.py'
sys.exit(subprocess.run(args,cwd=ROOT,env=env).returncode)
