"""隔離資料庫測試：商業短工初始化、規則、冷卻和正式代言隔離。"""
import os
import tempfile

fd, path = tempfile.mkstemp(prefix='commercial_tasks_', suffix='.sqlite3')
os.close(fd)
os.environ['DATABASE_PATH'] = path

from core.database import init_db, transaction, get_connection
from modules.task_commercial_seed import JOBS, seed_commercial_tasks
from modules.task_conditions import get_profile
from modules.task_service import eligible, accept
from modules.task_repository import get_definition
from modules.task_newbie import REQUIRED_COMMANDS
from modules.task_repository import record_event, initialize

try:
    init_db()
    initialize()
    gid='9917'
    with transaction() as db:
        db.execute('INSERT INTO guilds(guild_id,name) VALUES (?,?)',(gid,'測試'))
        db.execute('''INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,character_type)
                      VALUES (?,?,?,?,?,?,?)''',(gid,'42','測試模特',24,'模特兒','active','player'))
        cid=db.execute('SELECT id FROM characters WHERE guild_id=?',(gid,)).fetchone()['id']
        for name,val in [('容貌',300),('演繹',300),('公關',300),('主持',300),('歌唱',300),('舞蹈',300),('綜藝',300)]:
            db.execute('INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',(gid,cid,name,val))
    for command in REQUIRED_COMMANDS:
        record_event(gid,cid,'newbie:'+command,'command:'+command)
    assert seed_commercial_tasks(gid)==20
    assert seed_commercial_tasks(gid)==0
    assert get_profile(gid,'商業短工',1)=={}
    with get_connection() as db:
        rows=db.execute("SELECT * FROM rp_task_definitions WHERE guild_id=? AND code LIKE 'commercial_basic_%'",(gid,)).fetchall()
    assert len(rows)==20
    for row in rows:
        import json
        req=json.loads(row['requirements_json'])
        rew=json.loads(row['rewards_json'])
        assert len(req['min_attributes'])==1
        assert len(rew['attributes'])==1
        assert 100<=next(iter(req['min_attributes'].values()))<=300
        assert 5<=next(iter(rew['attributes'].values()))<=15
        assert '影響指數' not in rew['attributes']
        assert not any(k in rew for k in ('free_points','items'))
    task=next(dict(row) for row in rows if row['code']=='commercial_basic_exhibition_model')
    assert eligible(gid,task,{'occupation':'模特兒','attributes':{'容貌':300},'cafe_level':None})['eligible']
    assert accept(gid,cid,'42',task['id'])
    try:
        accept(gid,cid,'42',task['id'])
    except ValueError as e:
        assert '尚未完成' in str(e)
    else:
        raise AssertionError('未阻擋重複接取')
    with transaction() as db:
        db.execute("UPDATE rp_task_progress SET status='claimed' WHERE guild_id=? AND character_id=? AND task_id=?",(gid,cid,task['id']))
    try:
        accept(gid,cid,'42',task['id'])
    except ValueError as e:
        assert '間隔' in str(e)
    else:
        raise AssertionError('未阻擋冷卻期重複接取')
    with transaction() as db:
        db.execute("UPDATE rp_task_progress SET accepted_at='2020-01-01T00:00:00+00:00' WHERE guild_id=? AND character_id=? AND task_id=?",(gid,cid,task['id']))
    assert accept(gid,cid,'42',task['id'])
    assert get_definition(gid,task['id'])['subtype']=='商業短工'
    print('PASS: 20 gigs, idempotence, single-skill rules, no free points, cooldown, reaccept')
finally:
    os.unlink(path)
