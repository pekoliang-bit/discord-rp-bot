"""V2.2 smoke tests with isolated in-memory-style temporary SQLite file."""
import os,sys,tempfile,random
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
fd,path=tempfile.mkstemp(suffix='.sqlite3');os.close(fd)
try:
    import core.config as config
    config.DATABASE_PATH=path
    from core.database import init_db,transaction
    from core import pets_v1 as pets,pet_market_v2 as market,pet_traits_v22 as traits
    init_db()
    with transaction() as db:
        market.setup(db,'100')
        db.execute("INSERT INTO guilds(guild_id,name) VALUES ('100','test')")
        cur=db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES ('100','200','Tester','active')")
        cid=cur.lastrowid
        db.execute('INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',('100',cid,'健康值',100))
        db.execute("INSERT INTO rp_pets_v1(guild_id,main_character_id,name,species,rarity,chip,health,bond,last_play,last_tick) VALUES ('100',?,'布丁','狗','v21:dog:06','TEST-CHIP',100,200,'2026-11-15T00:00:00+00:00','2026-11-15T00:00:00+00:00')",(cid,))
        pid=db.execute('SELECT id FROM rp_pets_v1').fetchone()[0]
        db.execute('INSERT INTO rp_pet_owners_v1 VALUES (?,?,?,1)',('100',pid,cid))
        traits.bootstrap(db,'100',pid,random.Random(7))
        assert traits.bootstrap(db,'100',pid,random.Random(9))['breed']=='哈士奇'
        db.execute("INSERT INTO rp_pet_items_v22(guild_id,pet_id,owner_character_id,code,created_at) VALUES (?,?,?,?,?)",('100',pid,cid,'v21:dog:toy:basic','2026-11-15T00:00:00+00:00'))
        db.execute("INSERT INTO rp_pet_items_v22(guild_id,pet_id,owner_character_id,code,created_at) VALUES (?,?,?,?,?)",('100',pid,cid,'v21:dog:bed:basic','2026-11-15T00:00:00+00:00'))
    seen=traits.state('100',pid,cid,'200')
    assert seen['traits_hidden'] and seen['breed']=='哈士奇' and len(seen['equipment'])==2
    trial=traits.training('100',pid,cid,'200','耐心培養',rng=random.Random(0))
    assert trial['fatigue']>=10
    interaction=traits.interaction('100',pid,cid,'200','play',rng=random.Random(4))
    assert isinstance(interaction['injury'],int)
    updated=traits.state('100',pid,cid,'200')
    assert any(x['durability']<100 for x in updated['equipment'])
    a=traits.daily_equipment('100')
    b=traits.daily_equipment('100')
    assert a==1 and b==0,(a,b)
    with transaction() as db:
        r=db.execute("SELECT traits_json FROM rp_pet_traits_v22 WHERE pet_id=?",(pid,)).fetchone()
        assert r and '破壞力' in r['traits_json']
    print('PASS: trait generation, no reroll, training, event wear, daily wear idempotency')
finally:
    try:os.unlink(path)
    except OSError:pass
