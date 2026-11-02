"""V11 14/30 日獨立週期，從正式啟用時刻起算（UTC）。"""
from datetime import datetime,timezone,timedelta

def utc_now():return datetime.now(timezone.utc)
def iso(dt):return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
def parse(value):
    dt=datetime.fromisoformat(value)
    if dt.tzinfo is None:raise ValueError('時間必須有時區')
    return dt.astimezone(timezone.utc)
def due_indices(anchor,now,days):
    if now<anchor:return []
    count=int((now-anchor).total_seconds()//(days*86400))
    return list(range(1,count+1))
def cycle_end(anchor,days,index):return anchor+timedelta(days=days*index)
