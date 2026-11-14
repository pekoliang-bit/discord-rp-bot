
"""Check 45 recovered HB review-only entries can never import accidentally."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'assets/history_v23'
review=folder/'HB_0001_0045_recovered_review_only.json'
rows=json.loads(review.read_text(encoding='utf8'))
assert len(rows)==45 and [r['asset_id'] for r in rows]==[f'HB-{n:04d}' for n in range(1,46)]
assert all(r['approved'] is False and r['import_ready'] is False for r in rows)
catalog=json.loads((folder/'catalog_580_inventory.json').read_text(encoding='utf8'))
assert len(catalog)==580
assert sum(r['status']=='recovered_summary_needs_original_text_review' for r in catalog)==45
assert sum(r['import_ready'] is True for r in catalog)==10
with tempfile.TemporaryDirectory() as d:
    env={**os.environ,'DATABASE_PATH':str(Path(d)/'check.sqlite3')}
    subprocess.run([sys.executable,'-c','from core.database import init_db;init_db()'],cwd=ROOT,env=env,check=True)
    cmd=[sys.executable,str(ROOT/'tools/import_history_v23_assets.py'),'--guild','1544051203493601380','--file',str(review),'--apply']
    r=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True)
    assert r.returncode != 0 and 'not source-verified' in r.stderr,r.stderr
    # Simulate an attempted UI bulk import of the review list (must fail too).
    code='''from core.history_v23 import put_batch
import json
from pathlib import Path
rows=json.loads(Path(%r).read_text(encoding="utf8"))
try:
    put_batch("1544051203493601380",rows)
except ValueError as ex:
    assert "未確認" in str(ex)
else:raise AssertionError("Unsafe review assets were accepted")
'''%str(review)
    subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,check=True)
    import sqlite3
    con=sqlite3.connect(env['DATABASE_PATH'])
    assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rp_history_assets_v23'").fetchone() is None
    con.close()
print('PASS: 45 recovered brands are review-only; CLI and bulk core both refuse unapproved assets')
