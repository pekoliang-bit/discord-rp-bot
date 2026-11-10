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

# 直接執行 python scripts/audit_task_schedules.py 時，也能匯入專案 core。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def audit(db, guild_id):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'rp_task_definitions' not in tables:
        return {'error': '目前資料庫沒有通告定義表，無法盤點。'}
    has_day = 'rp_task_duration_definitions' in tables
    has_date = 'rp_work_task_schedule' in tables
    day_join = ('LEFT JOIN rp_task_duration_definitions w ON w.guild_id=d.guild_id AND w.task_id=d.id'
                if has_day else '')
    date_join = ('LEFT JOIN rp_work_task_schedule s ON s.guild_id=d.guild_id AND s.task_id=d.id'
                 if has_date else '')
    def_cols = {r[1] for r in db.execute('PRAGMA table_info(rp_task_definitions)')}
    subtype_col = 'd.subtype' if 'subtype' in def_cols else 'NULL'
    type_col = 'w.notice_type' if has_day else 'NULL'
    days_col = 'w.duration_days' if has_day else 'NULL'
    start_col = 's.starts_at' if has_date else 'NULL'
    end_col = 's.ends_at' if has_date else 'NULL'
    query = f"""SELECT d.id,d.code,d.kind,d.title,{subtype_col} AS subtype,d.status,
        {type_col} AS notice_type,{days_col} AS duration_days,
        {start_col} AS starts_at,{end_col} AS ends_at
        FROM rp_task_definitions d {day_join} {date_join}
        WHERE d.guild_id=? AND d.kind IN ('screen','variety','endorsement','limited','special')
        ORDER BY d.kind,d.id"""
    rows = db.execute(query,(str(guild_id),)).fetchall()
    from core.work_duration import BOUNDS
    result = {'guild_id':str(guild_id), 'day_based':[], 'legacy_dated':[],
              'missing_duration':[], 'exempt_templates':[], 'invalid_duration':[],
              'notice':'只讀；已有日期檔期視為舊格式，沒有設定天數的通告不能推測所需天數。'}
    for item in rows:
        entry={key:item[key] for key in item.keys()}
        if str(item['code'] or '').startswith(('screen_basic_','commercial_basic_')) and not item['duration_days']:
            result['exempt_templates'].append(entry)
        elif item['duration_days'] is not None:
            bounds=BOUNDS.get(item['notice_type'])
            if bounds is None or not bounds[0] <= item['duration_days'] <= bounds[1]:
                result['invalid_duration'].append(entry)
            else:
                result['day_based'].append(entry)
        elif item['starts_at'] and item['ends_at']:
            result['legacy_dated'].append(entry)
        else:
            result['missing_duration'].append(entry)
    result['scheduled'] = result['day_based'] + result['legacy_dated']
    result['unscheduled'] = result['missing_duration']
    result['totals']={k:len(result[k]) for k in
        ('day_based','legacy_dated','missing_duration','exempt_templates','invalid_duration','scheduled','unscheduled')}
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
