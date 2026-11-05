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


# 顯示用純函式：排行榜14/30天各自倒數，不等同拍攝工作日曆。
def remaining_text(target, now):
    seconds=max(0,int((target-now).total_seconds()))
    days,seconds=divmod(seconds,86400)
    hours,seconds=divmod(seconds,3600)
    minutes=seconds//60
    return f'{days}天{hours}小時{minutes}分鐘'


def next_period(anchor, now, days):
    """返回下一個尚未到期的結算索引與UTC時間；邊界到期時顯示0。"""
    if anchor.tzinfo is None or now.tzinfo is None:
        raise ValueError('結算錨點與現在時間必須含時區')
    anchor=anchor.astimezone(timezone.utc); now=now.astimezone(timezone.utc)
    if now<anchor: return 1,cycle_end(anchor,days,1)
    index=max(1,int((now-anchor).total_seconds()//(days*86400))+1)
    return index,cycle_end(anchor,days,index)


def settlement_countdown(anchor_utc, now=None):
    anchor=parse(anchor_utc) if isinstance(anchor_utc,str) else anchor_utc
    now=now or utc_now()
    return {f'{d}d':{'index':idx,'end_utc':iso(end),'remaining':remaining_text(end,now)}
            for d in (14,30) for idx,end in [next_period(anchor,now,d)]}
