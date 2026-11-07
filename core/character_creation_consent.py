"""創角授權的單一正式資料來源；未設定者採 2026-11-07 已核准預設值。"""
from __future__ import annotations
import json
import time

CATEGORIES = ("戀情", "商業", "工作", "人際", "私生活", "其他")
DEFAULTS = {
    "show_personality": False,
    "show_biography": False,
    "rumor_enabled": True,
    "network_opinion_enabled": True,
    "random_event_enabled": True,
    "official_story_enabled": True,
}
CATEGORY_KEY = "rumor_categories"


def normalize(settings):
    settings = dict(settings) if isinstance(settings, dict) else {}
    result = dict(DEFAULTS)
    for key in DEFAULTS:
        if type(settings.get(key)) is bool:
            result[key] = settings[key]
    cats = settings.get(CATEGORY_KEY)
    result[CATEGORY_KEY] = {cat: (cats.get(cat) if type(cats.get(cat)) is bool else True)
                            if isinstance(cats, dict) else True for cat in CATEGORIES}
    return result


def update(settings, key, enabled):
    if type(enabled) is not bool:
        raise ValueError("授權狀態必須是布林值")
    settings = dict(settings) if isinstance(settings, dict) else {}
    if key in DEFAULTS:
        settings[key] = enabled
    elif key in CATEGORIES:
        cats = normalize(settings)[CATEGORY_KEY]
        cats[key] = enabled
        settings[CATEGORY_KEY] = cats
    else:
        raise ValueError("未支援的授權項目")
    return settings


def permitted(settings, kind, category=None):
    values = normalize(settings)
    if kind == 'rumor':
        if category not in CATEGORIES:
            raise ValueError("未知的傳聞分類")
        return values['rumor_enabled'] and values[CATEGORY_KEY][category]
    if kind not in ('network_opinion_enabled', 'random_event_enabled', 'official_story_enabled'):
        raise ValueError("未知授權類型")
    return values[kind]


def persist_rumor_consent(db, guild_id, character_id, settings):
    """與角色啟用同一筆 SQL 交易中保存授權，不會先預寫到別人的角色。"""
    values = normalize(settings)
    gid, cid, now = str(guild_id), int(character_id), int(time.time())
    db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_optout (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL,
      PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_category_consent (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL, category TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL,
      PRIMARY KEY(guild_id,character_id,category))''')
    db.execute('''INSERT INTO rp_rumor_optout(guild_id,character_id,enabled,updated_at)
      VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET
      enabled=excluded.enabled,updated_at=excluded.updated_at''',
      (gid,cid,int(values['rumor_enabled']),now))
    for category, enabled in values[CATEGORY_KEY].items():
        db.execute('''INSERT INTO rp_rumor_category_consent
          (guild_id,character_id,category,enabled,updated_at)
          VALUES(?,?,?,?,?) ON CONFLICT(guild_id,character_id,category) DO UPDATE SET
          enabled=excluded.enabled,updated_at=excluded.updated_at''',
          (gid,cid,category,int(enabled),now))


def character_allowed(db, guild_id, character_id, kind, *, category=None):
    row = db.execute("SELECT status, settings_json FROM characters WHERE guild_id=? AND id=?",
                     (str(guild_id), int(character_id))).fetchone()
    if not row or row['status'] != 'active':
        return False
    try:
        settings = json.loads(row['settings_json'] or '{}')
    except (TypeError, ValueError):
        settings = {}
    return permitted(settings, kind, category)
