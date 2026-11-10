"""唯讀盤點本伺服器影視、綜藝、代言及特殊通告檔期。

用法：python scripts/audit_task_schedules.py <伺服器ID> [--database PATH]
只讀 SQLite，不會建立或修改表格，也不會自行推斷工作日期。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


def audit(db, guild_id):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'rp_task_definitions' not in tables:
        return {'error': '目前資料庫沒有通告定義表，無法盤點。'}
    with_slots = 'rp_work_task_schedule' in tables
    if with_slots:
        rows = db.execute('''SELECT d.id,d.code,d.kind,d.title,d.status,
            s.starts_at,s.ends_at
            FROM rp_task_definitions d
            LEFT JOIN rp_work_task_schedule s ON s.guild_id=d.guild_id AND s.task_id=d.id
            WHERE d.guild_id=? AND d.kind IN ('screen','variety','endorsement','limited','special')
            ORDER BY d.kind,d.id''', (str(guild_id),)).fetchall()
    else:
        rows = db.execute('''SELECT id,code,kind,title,status,NULL AS starts_at,NULL AS ends_at
            FROM rp_task_definitions WHERE guild_id=?
            AND kind IN ('screen','variety','endorsement','limited','special')
            ORDER BY kind,id''', (str(guild_id),)).fetchall()
    result = {'guild_id':str(guild_id), 'scheduled':[], 'unscheduled':[], 'exempt_templates':[],
              'notice':'僅檢查已儲存的正式檔期；沒有日期不等於工作未成立，也不能視為已預約。'}
    for item in rows:
        entry = {key:item[key] for key in item.keys()}
        if str(item['code'] or '').startswith(('screen_basic_','commercial_basic_')):
            result['exempt_templates'].append(entry)
        elif item['starts_at'] and item['ends_at']:
            result['scheduled'].append(entry)
        else:
            result['unscheduled'].append(entry)
    result['totals']={k:len(result[k]) for k in ('scheduled','unscheduled','exempt_templates')}
    return result


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('guild_id',help='Discord 伺服器 ID')
    parser.add_argument('--database',help='指定已有資料庫檔案位置；不建立新資料庫')
    args=parser.parse_args(argv)
    from core.config import DATABASE_PATH
    path=Path(args.database or DATABASE_PATH).resolve()
    if not path.is_file():
        print(json.dumps({'error':'找不到實際資料庫；不能只靠程式碼推定通告是否有檔期',
                          'path':str(path)},ensure_ascii=False,indent=2))
        return 2
    try:
        uri='file:'+str(path)+'?mode=ro'
        with sqlite3.connect(uri,uri=True) as db:
            db.row_factory=sqlite3.Row
            result=audit(db,args.guild_id)
    except sqlite3.Error as exc:
        result={'error':f'資料庫只讀盤點失敗：{exc}'}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result.get('error') else 0


if __name__=='__main__':
    sys.exit(main())
