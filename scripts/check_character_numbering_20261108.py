"""角色公開編號隔離測試，不會觸碰正式資料庫。
在專案根目錄執行：python scripts/check_character_numbering_20261108.py
"""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

with tempfile.TemporaryDirectory() as dirname:
    from core import database
    database.DATABASE_PATH = str(Path(dirname) / 'isolated.sqlite3')
    database.init_db()
    from modules.character_numbering import (
        issue_if_launched, confirm_official_launch, number_status,
        normalize_birthday,
    )
    assert normalize_birthday('2/29') == '0229'
    assert normalize_birthday('02/30') is None
    for guild_id in ('G1', 'G2'):
        database.register_guild(guild_id)
    with database.transaction() as db:
        for guild_id, name, status, typ, birth in [
            ('G1', 'alpha', 'active', 'player', '04/18'),
            ('G1', 'beta', 'approved', 'player', '04/18'),
            ('G1', 'gamma', 'active', 'official_npc', '04/18'),
            ('G1', 'delta', 'active', 'player_npc', '02/29'),
            ('G1', 'missing', 'active', 'player', ''),
            ('G1', 'draft', 'draft', 'player', '04/18'),
            ('G2', 'other', 'active', 'player', '04/18'),
        ]:
            db.execute('''INSERT INTO characters(guild_id,owner_id,name,status,character_type,settings_json)
                VALUES(?,?,?,?,?,?)''', (guild_id,'owner',name,status,typ,json.dumps({'birthday':birth})))
    assert not number_status('G1')['launched']
    assert issue_if_launched('G1', 1) is None
    result = confirm_official_launch('G1', launched_at='2026-11-08T00:00:00+00:00')
    assert result['server_serial'] == 1, result
    assert [i['code'] for i in result['issued']] == [
        'P-01-0418-01', 'P-01-0418-02', 'O-01-0418-01', 'N-01-0229-01'
    ], result
    assert 5 in result['missing_birthday_or_type'], result
    assert 6 not in result['missing_birthday_or_type'], result
    again = confirm_official_launch('G1', launched_at='2099-01-01')
    assert again['already_launched'] and not again['issued'], again
    assert again['launched_at'] == '2026-11-08T00:00:00+00:00'
    other = confirm_official_launch('G2')
    assert other['server_serial'] == 2, other
    assert other['issued'][0]['code'] == 'P-02-0418-01', other
    with database.transaction() as db:
        db.execute("UPDATE characters SET settings_json=? WHERE guild_id='G1' AND id=5",
                   (json.dumps({'birthday':'01/01'}),))
    assert issue_if_launched('G1', 5) == 'P-01-0101-01'
    with database.transaction() as db:
        db.execute("UPDATE characters SET status='active' WHERE guild_id='G1' AND id=6")
    assert issue_if_launched('G1', 6) == 'P-01-0418-03'
    assert issue_if_launched('G1', 6) == 'P-01-0418-03'
    print('PASS: 開群前禁止核發／多服唯一／生日有效／同日流水／重試冪等／缺生日補建')
