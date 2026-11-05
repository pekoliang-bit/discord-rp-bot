"""綜藝常駐席位官方審核與單期播出獎勵（冪等）。"""
from __future__ import annotations
from core.database import transaction,get_connection
from modules.task_variety_seed import initialize_variety,SHOW_BY_KEY,EXPOSURE


def _task_cast_key(code):
    if not code.startswith('variety_') or '_apply_' not in code:return None
    key,seat=code[len('variety_'):].split('_apply_',1)
    if key not in SHOW_BY_KEY or seat not in ('host','resident'):return None
    return key,seat


def apply(guild_id,character_id,task):
    """資格已由 task_service 驗證，交易內建立待審申請。不鎖定名額。"""
    cast=_task_cast_key(str(task['code']))
    if not cast:raise ValueError('不是常駐選角申請')
    initialize_variety();gid=str(guild_id);key,seat=cast
    with transaction() as db:
        seat_row=db.execute('SELECT capacity,reserved_count FROM rp_variety_seats WHERE guild_id=? AND show_key=? AND seat=?',(gid,key,seat)).fetchone()
        if not seat_row:raise ValueError('席位不存在')
        existing=db.execute('SELECT 1 FROM rp_variety_cast_roster WHERE guild_id=? AND show_key=? AND character_id=?',(gid,key,character_id)).fetchone()
        if existing:raise ValueError('已是這檔節目的常駐出演者')
        active=db.execute('''SELECT 1 FROM rp_variety_cast_applications
            WHERE guild_id=? AND show_key=? AND character_id=? AND status='pending' ''',(gid,key,int(character_id))).fetchone()
        if active:raise ValueError('已有待審常駐申請')
        taken=db.execute('SELECT COUNT(*) c FROM rp_variety_cast_roster WHERE guild_id=? AND show_key=? AND seat=?',(gid,key,seat)).fetchone()['c']
        if int(taken)+seat_row['reserved_count']>=seat_row['capacity']:raise ValueError('常駐席位已滿')
        prior=db.execute('''SELECT id,status FROM rp_variety_cast_applications
            WHERE guild_id=? AND show_key=? AND character_id=? AND seat=?''',(gid,key,int(character_id),seat)).fetchone()
        if prior:
            db.execute("UPDATE rp_variety_cast_applications SET status='pending',reviewer_id=NULL,reviewed_at=NULL,created_at=CURRENT_TIMESTAMP WHERE id=?",(prior['id'],))
            return int(prior['id'])
        cur=db.execute('INSERT INTO rp_variety_cast_applications(guild_id,show_key,character_id,seat) VALUES (?,?,?,?)',
                       (gid,key,int(character_id),seat))
        return int(cur.lastrowid)


def pending(guild_id):
    initialize_variety()
    with get_connection() as db:
        return [dict(x) for x in db.execute('''SELECT a.*,s.title FROM rp_variety_cast_applications a
            JOIN rp_variety_shows s ON s.guild_id=a.guild_id AND s.show_key=a.show_key
            WHERE a.guild_id=? AND a.status='pending' ORDER BY a.created_at LIMIT 40''',(str(guild_id),))]


def decide(guild_id,application_id,reviewer_id,owner_id,approved):
    from modules.task_permissions import has_permission
    if not has_permission(guild_id,reviewer_id,owner_id):raise PermissionError('沒有任務審核權限')
    initialize_variety();gid=str(guild_id)
    with transaction() as db:
        a=db.execute('SELECT * FROM rp_variety_cast_applications WHERE guild_id=? AND id=?',(gid,int(application_id))).fetchone()
        if not a or a['status']!='pending':raise ValueError('找不到待審常駐申請')
        # 一般管理員不能審核自己角色，群主可例外。
        owner=db.execute('SELECT owner_id FROM characters WHERE guild_id=? AND id=?',(gid,a['character_id'])).fetchone()
        if not owner:raise ValueError('申請角色不存在')
        if str(reviewer_id)!=str(owner_id) and str(owner['owner_id'])==str(reviewer_id):
            raise PermissionError('管理員不得審核自己的常駐申請')
        if approved:
            s=db.execute('SELECT * FROM rp_variety_seats WHERE guild_id=? AND show_key=? AND seat=?',
                         (gid,a['show_key'],a['seat'])).fetchone()
            if not s:raise ValueError('職位不存在')
            n=db.execute('SELECT COUNT(*) c FROM rp_variety_cast_roster WHERE guild_id=? AND show_key=? AND seat=?',
                         (gid,a['show_key'],a['seat'])).fetchone()['c']
            if n+s['reserved_count']>=s['capacity']:raise ValueError('常駐職位已滿')
            exists=db.execute('SELECT 1 FROM rp_variety_cast_roster WHERE guild_id=? AND show_key=? AND character_id=?',
                              (gid,a['show_key'],a['character_id'])).fetchone()
            if exists:raise ValueError('此角色已佔同節目另一常駐席位')
            db.execute('''INSERT INTO rp_variety_cast_roster(guild_id,show_key,character_id,seat,approved_by)
                VALUES (?,?,?,?,?)''',(gid,a['show_key'],a['character_id'],a['seat'],str(reviewer_id)))
        db.execute('''UPDATE rp_variety_cast_applications SET status=?,reviewer_id=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?''',
                   ('approved' if approved else 'rejected',str(reviewer_id),int(application_id)))
    return True


def check_episode_access(guild_id,character_id,task):
    """常駐必須入選 roster；每期每人一個演出席位，交給 cast_slots 原子鎖定。"""
    code=str(task.get('code',''))
    if not code.startswith('variety_') or '_apply_' in code:return
    import re
    m=re.fullmatch(r'variety_([a-z]+)_e(\d+)_(host|resident|flight)',code)
    if not m:return
    key,episode,seat=m.group(1),int(m.group(2)),m.group(3)
    if seat in ('host','resident'):
        with get_connection() as db:
            assigned=db.execute('''SELECT seat FROM rp_variety_cast_roster WHERE guild_id=? AND show_key=? AND character_id=?''',
                                (str(guild_id),key,int(character_id))).fetchone()
        if not assigned or assigned['seat']!=seat:raise ValueError('此常駐席位需先通過官方選角審核')
    else:
        with get_connection() as db:
            assigned=db.execute('''SELECT 1 FROM rp_variety_cast_roster WHERE guild_id=? AND show_key=? AND character_id=?''',
                                (str(guild_id),key,int(character_id))).fetchone()
        if assigned:raise ValueError('本節目常駐成員不可重複接飛行席位')


def broadcast(guild_id,show_key,episode,actor_id,owner_id,performance=1.0):
    """播出後對已核准完成錄製的該期參演者發放流量與認知度；重試不重複。"""
    from modules.task_permissions import has_permission
    from core.stat_rewards import grant_stats
    if not has_permission(guild_id,actor_id,owner_id):raise PermissionError('沒有播出權限')
    initialize_variety();gid=str(guild_id);show=SHOW_BY_KEY.get(show_key)
    if show is None:raise ValueError('未知節目')
    episode=int(episode)
    if not 1<=episode<=show[8]:raise ValueError('期數超出上限')
    perf=float(performance)
    if perf not in (0.5,1.0,1.5,2.0):raise ValueError('作品表現倍數僅允許 0.5/1/1.5/2')
    with transaction() as db:
        db.execute('''INSERT OR IGNORE INTO rp_variety_airings(guild_id,show_key,episode,performance,published_by)
            VALUES (?,?,?,?,?)''',(gid,show_key,episode,perf,str(actor_id)))
        air=db.execute('''SELECT performance FROM rp_variety_airings WHERE guild_id=? AND show_key=? AND episode=?''',
                       (gid,show_key,episode)).fetchone()
        perf=float(air['performance'])
        rows=db.execute('''SELECT p.character_id,e.seat FROM rp_variety_episode_tasks e
            JOIN rp_task_progress p ON p.guild_id=e.guild_id AND p.task_id=e.task_id
            WHERE e.guild_id=? AND e.show_key=? AND e.episode=? AND p.status IN ('completed','claimed')''',
            (gid,show_key,episode)).fetchall()
    base_flight,flight,awareness=EXPOSURE[show[3]]
    processed=0
    for row in rows:
        seat=row['seat'];traffic=flight if seat=='flight' else base_flight
        a=round(awareness*(0.6 if seat=='flight' else 1.0)*perf)
        t=round(traffic*perf)
        source_id=f'variety:air:{gid}:{show_key}:e{episode}:character:{row["character_id"]}'
        grant_stats(gid,int(row['character_id']),{'流量熱度':t,'大眾認知度':a},
                    source='mission_reward',source_id=source_id,reason=f'《{show[1]}》第{episode}期播出')
        processed+=1
    return processed
