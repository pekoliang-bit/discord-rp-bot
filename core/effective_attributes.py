"""角色玩法用的有效屬性（基礎值 + 當前陣營增益）。

只能用於明確允許臨時加成的玩法計算；不可用於永久發獎、
配點、影響指數或需要基礎值的硬性門檻。
同一連線查詢，避免交易中建立第二個寫入連線。
"""
from __future__ import annotations

# 此處只開放專業與輔助屬性，不含熱度、商業價值、影響指數。
EFFECTIVE_SKILLS = frozenset({
    "路人緣", "公關", "容貌", "才智", "歌唱", "舞蹈",
    "演繹", "主持", "綜藝", "個人才藝",
})


def active_faction_bonuses(db, guild_id, character_id):
    """讀取目前活躍角色的增益，資料表尚未初始化時安全回傳空值。"""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='faction_shop_buff_grants'").fetchone():
        return {}
    rows = db.execute("""SELECT g.effect_key, SUM(g.effect_value * g.quantity) AS bonus
        FROM faction_members AS m
        JOIN characters AS c ON c.guild_id=m.guild_id AND c.id=m.character_id
        JOIN faction_shop_buff_grants AS g
            ON g.guild_id=m.guild_id AND g.faction_id=m.faction_id
        WHERE m.guild_id=? AND m.character_id=?
          AND c.status='active' AND c.character_type='player'
          AND g.revoked_at IS NULL
          AND (g.expires_at IS NULL OR g.expires_at > CURRENT_TIMESTAMP)
        GROUP BY g.effect_key""", (str(guild_id), int(character_id))).fetchall()
    raw = {str(r['effect_key']): int(r['bonus']) for r in rows
           if r['effect_key'] in EFFECTIVE_SKILLS and int(r['bonus']) > 0}
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='faction_bonus_caps'").fetchone():
        return raw
    caps = {str(r[0]): int(r[1]) for r in db.execute(
        'SELECT effect_key,cap FROM faction_bonus_caps WHERE guild_id=?', (str(guild_id),)).fetchall()}
    return {k: min(v, caps[k]) if k in caps else v for k,v in raw.items()}


def effective_attributes(db, guild_id, character_id, base_attributes):
    """回傳新字典，不修改原始字典及資料庫永久屬性。"""
    result = {str(k): int(v) for k, v in base_attributes.items()}
    for key, bonus in active_faction_bonuses(db, guild_id, character_id).items():
        result[key] = result.get(key, 0) + bonus
    return result
