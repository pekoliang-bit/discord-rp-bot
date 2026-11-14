"""Read-only readiness audit regression checks; no Discord or database required."""
from __future__ import annotations
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'tools/audit_v23_release_readiness.py'
spec=importlib.util.spec_from_file_location('audit_v23_release_readiness',path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
report=module.audit()
assert report['project']=='流光城'
assert report['materials']['approved_slots']==580
assert report['materials']['import_ready']==55
assert report['materials']['source_missing']==525
names={check['name']:check for check in report['checks']}
assert names['總覽24指令']['ok']
assert names['NPC玩家/官方選單']['ok']
assert names['角色啟用安全防護']['ok']
assert names['五筆歷史通知服務']['ok']
assert not names['NPC初始資源完整接入']['ok']
assert not names['啟用時建立角色專屬論壇']['ok']
assert not names['排行榜正式發獎開關']['ok']
assert module.main([])==0
assert module.main(['--strict'])==1
assert not (ROOT/'project.db').exists(), '測試不應建立正式資料庫'
print('PASS: audit reflects verified readiness and never touches Discord or database')
