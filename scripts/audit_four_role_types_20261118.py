"""Read-only integrity audit for original / player NPC / official NPC / extras.

Usage: python scripts/audit_four_role_types_20261118.py
       DATABASE_PATH=/path/to/bot.sqlite3 python scripts/audit_four_role_types_20261118.py
No writes, migrations, or Discord API calls.
"""
from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path

REQUIRED = {'流量熱度','商業價值','路人緣','公關','影響指數','容貌','才智','歌唱','舞蹈','演繹','主持','綜藝','個人才藝'}


def run(path: str) -> int:
    p = Path(path).resolve()
    if not p.is_file():
        print(f'[NOT FOUND] SQLite database: {p}')
        print('Render free Web Service local files might not be persistent; check DATABASE_PATH before interpreting results.')
        return 2
    uri = p.as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'characters' not in tables:
            print('[ERROR] No characters table found; this may be a different or uninitialized database.')
            return 2
        roles = db.execute("SELECT id,guild_id,name,owner_id,status,character_type,settings_json FROM characters WHERE status!='deleted' ORDER BY id").fetchall()
        print(f'[INFO] Read-only audit: {p} / characters={len(roles)}')
        summary = {'original':0,'player_npc':0,'official_npc':0,'extra':0,'unknown':0}
        issues = 0
        attrs_available = 'attributes' in tables
        links_available = 'character_forum_links' in tables
        sheet_available = 'character_sheet_links' in tables
        for role in roles:
            try:
                settings = json.loads(role['settings_json'] or '{}')
                if not isinstance(settings,dict): settings={}
            except (ValueError,TypeError):
                settings={}
                print(f'[WARN] #{role["id"]} invalid settings_json')
                issues += 1
            extra = settings.get('character_category') in ('extra','路人') or settings.get('public_category') in ('extra','路人') or settings.get('is_extra') is True or settings.get('is_bystander') is True
            category = 'extra' if extra else {'player':'original','player_npc':'player_npc','official_npc':'official_npc'}.get(role['character_type'],'unknown')
            summary[category] += 1
            label=f'#{role["id"]} {role["name"]} ({category}, {role["status"]})'
            if category == 'extra' and role['character_type'] != 'official_npc':
                print(f'[WARN] {label}: unexpected database type for extra'); issues += 1
            if category == 'extra' and not 1 <= int(settings.get('status_level',settings.get('level',0)) or 0) <= 20:
                print(f'[WARN] {label}: invalid level'); issues += 1
            if attrs_available:
                present = {r[0] for r in db.execute('SELECT name FROM attributes WHERE guild_id=? AND character_id=?',(role['guild_id'],role['id']))}
                missing = sorted(REQUIRED - present)
                if missing and role['status'] in ('active','approved'):
                    print(f'[WARN] {label}: missing {len(missing)} stats: {", ".join(missing)}'); issues += 1
            else:
                print('[WARN] attributes table missing; cannot check stats')
                issues += 1
                attrs_available = False
            if category in ('original','player_npc','official_npc') and role['status']=='active' and links_available:
                link=db.execute('SELECT forum_id,notice_id,question_id,story_id,bot_id FROM character_forum_links WHERE guild_id=? AND character_id=?',(role['guild_id'],role['id'])).fetchone()
                if not link or any(not val for val in link):
                    print(f'[WARN] {label}: forum or fixed thread link missing'); issues += 1
            if category == 'official_npc' and sheet_available:
                sheet=db.execute('SELECT 1 FROM character_sheet_links WHERE guild_id=? AND character_id=?',(role['guild_id'],role['id'])).fetchone()
                if not sheet:
                    print(f'[WARN] {label}: official NPC has no linked source sheet'); issues += 1
        print('[SUMMARY] '+', '.join(f'{k}={v}' for k,v in summary.items()))
        print(f'[SUMMARY] findings={issues}; no records modified')
        return 0
    finally:
        db.close()

if __name__ == '__main__':
    raise SystemExit(run(os.getenv('DATABASE_PATH','data/bot.sqlite3')))
