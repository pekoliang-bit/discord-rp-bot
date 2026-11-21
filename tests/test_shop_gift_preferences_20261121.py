"""贈禮計分的純 Python 測試；不需要登入 Discord。"""
import ast
import pathlib
import sqlite3
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'modules/zz_shop_relationships.py').read_text(encoding='utf-8'))
KEEP = {'parse_preference_codes', 'gift_base_points', 'calculate_gift_points',
        'pair_ids', 'add_score', 'award_gift_in_transaction'}
NODES = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in KEEP]


def funcs():
    ns = {'GIFT_STORES': {f'B{x:02}': str(x) for x in range(1, 10)}}
    exec(compile(ast.Module(body=NODES, type_ignores=[]), '<gift-functions>', 'exec'), ns)
    return ns


def test_price_tiers_and_preferences():
    f = funcs()
    assert f['gift_base_points'](1000) == 2
    assert f['gift_base_points'](5000) == 5
    assert f['gift_base_points'](10000) == 10
    assert f['gift_base_points'](30000) == 20
    assert f['gift_base_points'](60000) == 35
    assert f['gift_base_points'](120000) == 50
    assert f['calculate_gift_points'](10000, 2, 'B02', 'B02', '')[0] == 30
    assert f['calculate_gift_points'](10000, 2, 'B02', '', 'B02')[0] == 10
    assert f['calculate_gift_points'](10000, 2, 'B02', '', '')[0] == 20


def test_preferences_validation():
    f = funcs()
    assert f['parse_preference_codes']('B02，B03') == ['B02', 'B03']
    for bad in ('B01,B02,B03,B04', 'B02,B02', 'B50'):
        try:
            f['parse_preference_codes'](bad)
        except ValueError:
            pass
        else:
            assert False, bad


def test_atomic_score_idempotency():
    f = funcs()
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    con.executescript('''
      CREATE TABLE rp_relationship_events(guild_id TEXT,event_key TEXT PRIMARY KEY,
       a INTEGER,b INTEGER,kind TEXT,product_id INTEGER,points INTEGER);
      CREATE TABLE rp_relationship_scores(guild_id TEXT,a INTEGER,b INTEGER,
       kind TEXT,points INTEGER,PRIMARY KEY(guild_id,a,b,kind));
      CREATE TABLE rp_gift_preferences(guild_id TEXT,character_id INTEGER,
       liked TEXT,disliked TEXT,PRIMARY KEY(guild_id,character_id));
      CREATE TABLE shop_products(guild_id TEXT,id INTEGER,price INTEGER,category TEXT,scope TEXT);
    ''')
    con.execute("INSERT INTO shop_products VALUES('g',1,10000,'B02 美妝','public')")
    con.execute("INSERT INTO rp_gift_preferences VALUES('g',2,'B02','')")
    f['shop'] = SimpleNamespace(get_product_store_code=lambda row:'B02')
    f['InvalidState'] = ValueError
    actor = SimpleNamespace(guild_id='g')
    award = f['award_gift_in_transaction']
    assert '+15' in award(con,actor,1,2,1,1,'event1')
    assert award(con,actor,1,2,1,1,'event1') is None
    assert con.execute('SELECT points FROM rp_relationship_scores').fetchone()[0] == 15
