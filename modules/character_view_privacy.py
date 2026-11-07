"""角色生平公開控制：獨立隱私選項，預設不公開。"""
from __future__ import annotations
import json
from core.database import transaction

FLAGS = {"personality": "show_personality", "biography": "show_biography"}


def visibility_state(settings):
    return {key: settings.get(column) is True for key, column in FLAGS.items()}


def visible_biography(settings, authorized=False):
    """回傳允許呈現的性格與身世；對未授權者永不回退私人欄位。"""
    flags = visibility_state(settings)
    personality = str(settings.get("private_personality") or "").strip() if authorized or flags['personality'] else ""
    biography = str(settings.get("biography") or settings.get("public_biography") or "").strip() if authorized or flags['biography'] else ""
    return personality, biography


def update_visibility(guild_id, character_id, owner_id, field, enabled):
    if field not in FLAGS or not isinstance(enabled, bool):
        raise ValueError("公開項目無效。")
    with transaction() as db:
        row = db.execute("SELECT owner_id,status,settings_json FROM characters WHERE guild_id=? AND id=?",
                         (str(guild_id), int(character_id))).fetchone()
        if row is None or str(row['owner_id']) != str(owner_id):
            raise PermissionError("只有角色持有人能修改公開設定。")
        if row['status'] != 'active':
            raise ValueError("只有已啟用角色可以修改公開設定。")
        try:
            settings = json.loads(row['settings_json'] or '{}')
        except (ValueError, TypeError):
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        settings[FLAGS[field]] = enabled
        db.execute("UPDATE characters SET settings_json=?,version=version+1 WHERE guild_id=? AND id=?",
                   (json.dumps(settings,ensure_ascii=False),str(guild_id),int(character_id)))
    return enabled
