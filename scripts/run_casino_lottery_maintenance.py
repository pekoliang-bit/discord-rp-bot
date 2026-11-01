"""Explicit lottery recovery entry point (not auto-started).

Usage: python scripts/run_casino_lottery_maintenance.py GUILD_ID
Must be run from repository root with correct DATABASE_PATH environment.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from modules.casino_maintenance import process_due_lotteries


def main() -> int:
    parser=argparse.ArgumentParser(description='結算已到期、已核准、非累積型彩票')
    parser.add_argument('guild_id',type=int)
    parser.add_argument('--max-draws',type=int,default=20)
    parser.add_argument('--batch-size',type=int,default=100)
    args=parser.parse_args()
    if args.guild_id<=0: parser.error('guild_id 必須大於零')
    outcome=process_due_lotteries(guild_id=args.guild_id,max_draws=args.max_draws,batch_size=args.batch_size)
    print(json.dumps(outcome,ensure_ascii=False,indent=2))
    return 1 if outcome['errors'] else 0

if __name__=='__main__':raise SystemExit(main())
