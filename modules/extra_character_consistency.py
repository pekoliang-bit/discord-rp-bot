
"""路人角色資料檢查，供最後集中驗收用。不修改角色。"""
from __future__ import annotations
import json
from core.database import get_connection
from modules.extra_character_store import initialize
from modules.attributes import default_schema

def audit(guild_id):
    initialize(guild_id)
    expected=set(default_schema()['general'] + default_schema()['talent'])
    findings=[]
    with get_connection() as db:
        rows=db.execute("""SELECT id,name,age,occupation,settings_json FROM characters
            WHERE guild_id=? AND json_extract(settings_json,'$.character_category')='extra'""", (str(guild_id),)).fetchall()
        for row in rows:
            settings=json.loads(row['settings_json'] or '{}')
            attrs={r['name']:r['value'] for r in db.execute(
                'SELECT name,value FROM attributes WHERE guild_id=? AND character_id=?',
                (str(guild_id),row['id'])).fetchall()}
            problems=[]
            if set(attrs) != expected: problems.append('屬性項目與正式十三項不一致')
            if attrs.get('影響指數') not in (20,): problems.append('初始影響指數異常，請人工確認後續獎勵')
            if not settings.get('portrait_url'): problems.append('缺少照片')
            if not settings.get('nationality'): problems.append('缺少國籍')
            if not settings.get('main_track'): problems.append('缺少主攻')
            if problems: findings.append({'id':row['id'],'name':row['name'],'problems':problems})
        stock=db.execute("""SELECT COUNT(*) FROM extra_photos
            WHERE guild_id=? AND used_character_id IS NULL""",(str(guild_id),)).fetchone()[0]
    return {'characters':len(rows),'remaining_photos':stock,'issues':findings}
