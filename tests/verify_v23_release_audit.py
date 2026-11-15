"""新版 V2.3 專案發行稽核的唯讀檢查；不能冒充 Discord 實機驗證。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / 'tools/audit_v23_release_readiness.py'
spec = importlib.util.spec_from_file_location('audit_v23_release_readiness', p)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
report = module.audit()
names = {x['name']:x for x in report['checks']}
assert report['project'] == '流光城'
assert names['總覽導覽完整且不重複']['ok']
assert names['現行580筆素材合法可解析']['ok']
assert names['NPC玩家/官方類型選單']['ok']
assert names['NPC已核准起始待遇及交易防重']['ok']
assert names['排行榜安全發獎政策']['ok']
assert not names['排行榜正式自動發獎可啟用']['ok']
assert not names['Discord論壇/通知/指令實機驗證']['ok']
assert not names['Railway資料庫與重啟驗證']['ok']
assert not report['release_ready']
assert module.main([]) == 0
assert module.main(['--strict']) == 1
print('PASS: newest audit reflects 580 current assets and does not claim live launch passed')
