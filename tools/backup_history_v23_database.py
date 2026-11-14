"""Read-only SQLite backup helper before a history asset import.

Example: python tools/backup_history_v23_database.py --output backups/pre_import.sqlite3
"""
from __future__ import annotations
import argparse,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.config import DATABASE_PATH

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    source=Path(DATABASE_PATH).resolve();output=args.output.resolve()
    if not source.is_file():raise SystemExit(f'Database not found: {source}')
    if source==output or output.exists():raise SystemExit('Refuse to overwrite original or existing backup.')
    output.parent.mkdir(parents=True,exist_ok=True)
    src=None;dst=None
    try:
        src=sqlite3.connect(f'file:{source.as_posix()}?mode=ro',uri=True)
        dst=sqlite3.connect(str(output))
        src.backup(dst)
        ok=dst.execute('PRAGMA integrity_check').fetchone()[0]
        if ok!='ok':raise RuntimeError('Backup integrity check failed: '+str(ok))
        print(f'SQLite backup completed and verified: {output}')
    except Exception:
        if dst:dst.close();dst=None
        output.unlink(missing_ok=True)
        raise
    finally:
        if src:src.close()
        if dst:dst.close()

if __name__=='__main__':main()
