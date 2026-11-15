"""Unified per-guild Discord channel bindings, preserving existing subsystem storage.

No Discord API calls and no background services. Importing this module never mutates DB.
Always verify channel type, membership and permissions before calling save_binding.
A configured binding supersedes a built-in default. Empty optional slots are allowed.
"""
from __future__ import annotations
from core.database import get_connection,transaction

DEFAULT_GUILD = '1544051203493601380'
# key, label, group, Discord type, default channel ID (only for our server).
SLOT_ROWS = [('main_notice', '主通知', '通知', 'text', '1567786757020323960'),
 ('character_notice', '角色相關通知', '通知', 'text', '1569650209951195186'),
 ('activity_notice', '活動通知', '通知', 'text', '1567786824326189238'),
 ('rumor', '傳聞通知／發布', '通知', 'text', '1545703079691689994'),
 ('female_category', '女性角色（原創＋玩家NPC）', '角色論壇', 'category', '1567786100460625972'),
 ('male_category', '男性角色（原創＋玩家NPC）', '角色論壇', 'category', '1567786323773759588'),
 ('fallback_category', '無法歸類性別角色備援分類', '角色論壇', 'category', ''),
 ('overflow_categories', '角色論壇額外分類', '角色論壇', 'category', ''),
 ('post_forum', '網路貼文論壇', '網路', 'forum', '1568700458648870932'),
 ('group_category', '網路群組分類', '網路', 'category', '1545373646569340988'),
 ('global_group', '全員大群', '網路', 'text', '1567490340909351013'),
 ('network_faction_solo', '單打獨鬥', '網路陣營群', 'text', '1567491557807099934'),
 ('network_faction_alternative', '另闢蹊徑', '網路陣營群', 'text', '1567491656993738833'),
 ('network_faction_together', '抱團取暖', '網路陣營群', 'text', '1567491851588468796'),
 ('network_faction_agency', '經紀公司', '網路陣營群', 'text', '1567492007293886524'),
 ('network_faction_mcn', 'MCN', '網路陣營群', 'text', '1567492172469633134'),
 ('commerce_category', '商業經貿分類', '商業', 'category', '1548941879314489424'),
 ('brand_notice', '公司／品牌公告', '商業', 'text', '1567786757020323960'),
 ('event_category', '活動專用分類', '活動', 'category', '1571458077805256764'),
 ('event_registration_notice', '活動報名公告', '活動', 'text', '1567786824326189238'),
 ('application_inbox', '管理申請接收', '管理', 'text', '1571455121806590005'),
 ('review_workroom', '審核工作頻道', '管理', 'text', '1571455121806590005'),
 ('confidential_reports', '機密舉報頻道', '管理', 'text', '1571455467232821248'),
 ('admin_alerts', '管理員通知／系統錯誤', '管理', 'text', '1564266284114251907'),
 ('material_inbox', '官方素材收件', '管理', 'text', '1562396047098249227'),
 ('pet_missing', '寵物協尋公告', '寵物與傳聞', 'text', '1545703079691689994'),
 ('pet_rescue', '動物救援公告', '寵物與傳聞', 'text', '1545703079691689994'),
 ('rumor_investigation', '狗仔與傳聞調查公告', '寵物與傳聞', 'text', '1545703079691689994'),
 ('world_content', '世界觀資料', '世界觀', 'text', ''),
 ('world_backup', '世界觀備援', '世界觀', 'text', ''),
 ('world_rules', '世界規則', '世界觀', 'text', ''),
 ('world_regions', '地區資料', '世界觀', 'text', ''),
 ('world_story', '世界故事', '世界觀', 'text', ''),
 ('official_npc_solo', '單打獨鬥', '官方NPC角色單發布區', 'text_or_forum', '1545702664824823828'),
 ('official_npc_agency', '經紀公司', '官方NPC角色單發布區', 'text_or_forum', '1545702759322488832'),
 ('official_npc_mcn', 'MCN', '官方NPC角色單發布區', 'text_or_forum', '1545702859511697489'),
 ('official_npc_alternative', '另闢蹊徑', '官方NPC角色單發布區', 'text_or_forum', '1546815230175219822'),
 ('official_npc_together', '熟人抱團', '官方NPC角色單發布區', 'text_or_forum', '1547179593239105657')]
SLOTS = {row[0]: row for row in SLOT_ROWS}
SCHEMA = """CREATE TABLE IF NOT EXISTS rp_channel_bindings_v1 (
    guild_id TEXT NOT NULL, slot TEXT NOT NULL, channel_id TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(guild_id,slot))"""

def initialize(db):
    db.execute(SCHEMA)

def defaults_for(guild_id):
    return {key: str(cid) for key, label, group, kind, cid in SLOT_ROWS if cid and str(guild_id)==DEFAULT_GUILD}

def get_binding(guild_id, slot):
    if slot not in SLOTS: raise ValueError('未知的頻道綁定用途')
    with get_connection() as db:
        initialize(db)
        row=db.execute('SELECT channel_id FROM rp_channel_bindings_v1 WHERE guild_id=? AND slot=?',
            (str(guild_id),slot)).fetchone()
    if row: return str(row['channel_id']) or None
    return defaults_for(guild_id).get(slot)

def all_bindings(guild_id):
    values=defaults_for(guild_id)
    with get_connection() as db:
        initialize(db)
        for row in db.execute('SELECT slot,channel_id FROM rp_channel_bindings_v1 WHERE guild_id=?',(str(guild_id),)):
            values[row['slot']]=row['channel_id'] or None
    return values

def _sync_legacy(guild_id, slot, channel_id, actor_id):
    """Write to existing subsystem tables too; one owner-approved interaction only.
    Never create channels, edit permissions, send notifications or enable autoplay.
    """
    gid=str(guild_id); cid=str(channel_id)
    if slot in ('main_notice','character_notice','activity_notice','rumor'):
        from modules import notifications
        kind={'main_notice':'main','character_notice':'character','activity_notice':'event','rumor':'rumor'}[slot]
        notifications.initialize()
        with transaction() as db:
            db.execute('INSERT INTO notification_channels(guild_id,kind,channel_id) VALUES(?,?,?) '
                       'ON CONFLICT(guild_id,kind) DO UPDATE SET channel_id=excluded.channel_id',(gid,kind,cid))
    elif slot in ('female_category','male_category'):
        from modules.character_forums import initialize as forum_init
        forum_init()
        field='female_category_id' if slot=='female_category' else 'male_category_id'
        with transaction() as db:
            db.execute('INSERT OR IGNORE INTO character_forum_settings(guild_id) VALUES(?)',(gid,))
            db.execute(f'UPDATE character_forum_settings SET {field}=? WHERE guild_id=?',(cid,gid))
    elif slot=='post_forum':
        from core.network_social_posts import bind_forum
        bind_forum(gid,int(cid))
    elif slot in ('group_category','global_group'):
        from core.network_groups import set_settings
        if slot=='group_category':set_settings(gid,category_id=cid)
        else:set_settings(gid,global_channel_id=cid)
    elif slot.startswith('network_faction_'):
        from core.network_groups import set_settings
        key=slot.removeprefix('network_faction_')
        # use actual existing labels, without altering historical faction names
        names_map={'solo':'單打獨鬥','alternative':'另闢蹊徑','together':'抱團取暖','agency':'經紀公司','mcn':'MCN'}
        set_settings(gid,faction_name=names_map[key],faction_channel_id=cid)
    elif slot=='commerce_category':
        from modules.player_shop_channels import set_category
        set_category(gid,int(cid))
    elif slot=='rumor_investigation':
        pass # Only a route for future investigative announcements, not a rumor auto-post destination.
    elif slot.startswith('world_'):
        from modules.content_builder import world_get,world_save
        data=world_get(gid);data.setdefault('channels',{})[slot]=cid
        data.setdefault('overrides',{})[f'channels.{slot}']=True
        world_save(gid,data)
    # The five official NPC posting sources are never used as chat faction groups.
    # Other slots have no legacy destination; their consumers use get_binding().

def save_binding(guild_id,slot,channel_id,actor_id):
    if slot not in SLOTS:raise ValueError('未知的頻道綁定用途')
    if not str(channel_id).isdigit() or int(channel_id)<=0:raise ValueError('頻道 ID 不正確')
    _sync_legacy(guild_id,slot,channel_id,actor_id)
    with transaction() as db:
        initialize(db)
        db.execute('INSERT INTO rp_channel_bindings_v1(guild_id,slot,channel_id,updated_by) VALUES(?,?,?,?) '
                   'ON CONFLICT(guild_id,slot) DO UPDATE SET channel_id=excluded.channel_id,'
                   'updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP',
                   (str(guild_id),slot,str(channel_id),str(actor_id)))
    return str(channel_id)

def unbind(guild_id,slot,actor_id):
    """Clear the unified binding and reset relevant legacy binding; never delete Discord content."""
    if slot not in SLOTS:raise ValueError('未知用途')
    with transaction() as db:
        initialize(db)
        db.execute('INSERT INTO rp_channel_bindings_v1(guild_id,slot,channel_id,updated_by) VALUES(?,?,?,?) '
                   'ON CONFLICT(guild_id,slot) DO UPDATE SET channel_id=excluded.channel_id,'
                   'updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP',
                   (str(guild_id),slot,'',str(actor_id)))
    # Legacy settings may have fallbacks. Explicit disabled slots are honored by readers
    # that use this central registry. Other old modules retain their own existing config.

def get_official_npc_post_channel(guild_id,faction):
    names={'單打獨鬥':'solo','經紀公司':'agency','MCN':'mcn','另闢蹊徑':'alternative',
           '熟人抱團':'together','抱團取暖':'together'}
    key=names.get(str(faction),str(faction).lower())
    if key not in ('solo','agency','mcn','alternative','together'):return None
    return get_binding(guild_id,'official_npc_'+key)
