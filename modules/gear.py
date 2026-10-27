"""Discord RP equipment slots and auto-reconciled possession bonuses.

Install as modules/zzzzzz_gear_bonus.py (after shop and multiuse bridge).
Uses existing shop tables; all added tables are additive, never DROP.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from core.permissions import Actor, InvalidState, PermissionDenied
from modules import shop

LOG = logging.getLogger(__name__)
SLOT_NAMES = {
    'top': '上衣', 'bottom': '下裝', 'dress': '洋裝／套裝',
    'outer': '外套／披肩', 'shoes': '鞋子', 'head': '頭飾',
    'neck': '項鍊／頸飾', 'ear': '耳飾', 'wrist': '手錶／手環',
    'hand': '戒指', 'other': '其他飾品',
}

_original_adjust_inventory = None
_original_use_item = None
_original_unequip = None
_installed = False


def _table(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def init_db():
    with transaction() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS rp_gear_slots (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL, slot TEXT NOT NULL,
            PRIMARY KEY(guild_id,character_id,product_id)
        )''')
        conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS rp_one_gear_per_slot
            ON rp_gear_slots(guild_id,character_id,slot)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS rp_passive_bonus_applied (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            stat TEXT NOT NULL, amount INTEGER NOT NULL,
            PRIMARY KEY(guild_id,character_id,stat)
        )''')


def _slot(product):
    name = str(product['name']).lower()
    cat = str(product['category']).lower()
    if 'b03' in cat or '珠寶' in cat or '鐘錶' in cat:
        for term, slot in [
            ('戒指','hand'), ('指環','hand'), ('耳環','ear'), ('耳釘','ear'),
            ('耳墜','ear'), ('項鍊','neck'), ('項圈','neck'), ('頸鍊','neck'),
            ('腕錶','wrist'), ('手錶','wrist'), ('手環','wrist'),
            ('手鐲','wrist'), ('髮飾','head'), ('髮夾','head'),
            ('皇冠','head'), ('髮冠','head'),
        ]:
            if term in name:
                return slot
        return 'other'
    for term, slot in [
        ('套裝','dress'), ('洋裝','dress'), ('禮服','dress'),
        ('連身裙','dress'), ('長裙','bottom'), ('短裙','bottom'),
        ('短褲','bottom'), ('長褲','bottom'), ('牛仔褲','bottom'),
        ('西裝褲','bottom'), ('工裝褲','bottom'), ('闊腿褲','bottom'),
        ('靴','shoes'), ('鞋','shoes'), ('外套','outer'),
        ('風衣','outer'), ('披肩','outer'), ('開襟衫','outer'),
        ('大衣','outer'), ('圍巾','neck'), ('帽','head'),
        ('襯衫','top'), ('短袖','top'), ('t恤','top'), ('上衣','top'),
        ('針織衫','top'), ('長衫','top'), ('長袍','dress'),
    ]:
        if term in name:
            return slot
    return 'top'


def _eligible_gear(product):
    category = str(product['category']).lower()
    return (product['effect_kind'] == 'context'
            and any(t in category for t in ('b01', '服飾', 'b03', '珠寶', '鐘錶')))


def _bonus_lookup(conn, guild_id, product_id):
    for table in ('b02_catalog', 'b04_catalog'):
        if not _table(conn, table):
            continue
        row = conn.execute(
            f'''SELECT bonus_stat, bonus_value FROM {table}
                WHERE guild_id=? AND product_id=? AND item_type='持有' LIMIT 1''',
            (str(guild_id), product_id)
        ).fetchone()
        if row and row['bonus_stat'] and int(row['bonus_value']) > 0:
            return str(row['bonus_stat']), int(row['bonus_value'])
    return None


def reconcile(conn, guild_id, character_id):
    """Idempotent reconciliation inside the caller's transaction.

    Persistent attributes are adjusted only by the difference from previously
    applied passive bonuses; repeated calls do not stack duplicate bonuses.
    """
    guild_id, character_id = str(guild_id), int(character_id)
    if not _table(conn, 'rp_passive_bonus_applied'):
        return

    desired = {}
    rows = conn.execute('''SELECT p.id,p.category,p.effect_kind,p.effect_stat,
                  p.effect_value,i.quantity
        FROM shop_inventory i JOIN shop_products p
        ON p.guild_id=i.guild_id AND p.id=i.product_id
        WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0''',
        (guild_id, character_id)).fetchall()
    equipped = {int(r['product_id']) for r in conn.execute(
        '''SELECT product_id FROM shop_context_equipment
           WHERE guild_id=? AND character_id=?''', (guild_id, character_id))}
    for r in rows:
        pid = int(r['id'])
        if pid in equipped and _eligible_gear(r):
            stat, val = r['effect_stat'], int(r['effect_value'] or 0)
            if stat and val > 0:
                desired[stat] = desired.get(stat, 0) + val
        bonus = _bonus_lookup(conn, guild_id, pid)
        if bonus:
            stat, val = bonus
            desired[stat] = desired.get(stat, 0) + val  # once per SKU, not qty

    applied = {r['stat']: int(r['amount']) for r in conn.execute(
        '''SELECT stat,amount FROM rp_passive_bonus_applied
           WHERE guild_id=? AND character_id=?''', (guild_id, character_id))}
    for stat in set(applied) | set(desired):
        delta = desired.get(stat, 0) - applied.get(stat, 0)
        if delta:
            old = conn.execute('''SELECT value FROM attributes
              WHERE guild_id=? AND character_id=? AND name=?''',
              (guild_id, character_id, stat)).fetchone()
            current = int(old['value']) if old else 0
            if current + delta < 0:
                raise InvalidState(
                    f'屬性「{stat}」低於已登記加成，無法安全重新計算；請管理員檢查。')
            conn.execute('''INSERT INTO attributes(guild_id,character_id,name,value)
               VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,name)
               DO UPDATE SET value=value+excluded.value,version=version+1''',
               (guild_id, character_id, stat, delta))
        if desired.get(stat, 0):
            conn.execute('''INSERT INTO rp_passive_bonus_applied
               (guild_id,character_id,stat,amount) VALUES(?,?,?,?)
               ON CONFLICT(guild_id,character_id,stat)
               DO UPDATE SET amount=excluded.amount''',
               (guild_id,character_id,stat,desired[stat]))
        else:
            conn.execute('''DELETE FROM rp_passive_bonus_applied
                WHERE guild_id=? AND character_id=? AND stat=?''',
                (guild_id,character_id,stat))


def sync_existing():
    '''Adopt existing gear and calculate passive bonuses on first install.'''
    with transaction() as conn:
        if not _table(conn, 'shop_inventory'):
            return
        if _table(conn, 'shop_context_equipment'):
            rows = conn.execute('''SELECT e.guild_id,e.character_id,e.product_id,
                       p.name,p.category,p.effect_kind,p.effect_stat,p.effect_value
                FROM shop_context_equipment e
                JOIN shop_products p ON p.guild_id=e.guild_id AND p.id=e.product_id
                ORDER BY e.equipped_at DESC, e.product_id DESC''').fetchall()
            for r in rows:
                if not _eligible_gear(r):
                    continue
                slot = _slot(r)
                old = conn.execute('''SELECT product_id FROM rp_gear_slots
                     WHERE guild_id=? AND character_id=? AND slot=?''',
                     (r['guild_id'],r['character_id'],slot)).fetchone()
                if old and old['product_id'] != r['product_id']:
                    conn.execute('''DELETE FROM shop_context_equipment
                        WHERE guild_id=? AND character_id=? AND product_id=?''',
                        (r['guild_id'],r['character_id'],r['product_id']))
                    continue
                conn.execute('''INSERT OR IGNORE INTO rp_gear_slots
                    (guild_id,character_id,product_id,slot) VALUES(?,?,?,?)''',
                    (r['guild_id'],r['character_id'],r['product_id'],slot))
        characters = conn.execute('''SELECT DISTINCT guild_id,character_id
              FROM shop_inventory WHERE quantity>0
              UNION SELECT DISTINCT guild_id,character_id FROM rp_passive_bonus_applied''').fetchall()
        for char in characters:
            reconcile(conn,char['guild_id'],char['character_id'])


def _adjust_inventory(conn,guild_id,character_id,product_id,delta):
    result = _original_adjust_inventory(
        conn,guild_id,character_id,product_id,delta)
    # Automatic removal of equipped items when inventory reaches zero.
    remaining = conn.execute('''SELECT quantity FROM shop_inventory
        WHERE guild_id=? AND character_id=? AND product_id=?''',
        (guild_id,character_id,product_id)).fetchone()
    if not remaining or int(remaining['quantity']) <= 0:
        conn.execute('''DELETE FROM shop_context_equipment
            WHERE guild_id=? AND character_id=? AND product_id=?''',
            (guild_id,character_id,product_id))
        conn.execute('''DELETE FROM rp_gear_slots
            WHERE guild_id=? AND character_id=? AND product_id=?''',
            (str(guild_id),character_id,product_id))
    reconcile(conn,guild_id,character_id)
    return result


def equip(actor, character_id, product_id):
    with transaction() as conn:
        shop.require_character(conn,actor,character_id)
        item = shop.get_product(conn,actor.guild_id,product_id)
        if not _eligible_gear(item):
            raise InvalidState('此商品不是可穿戴的服裝／珠寶。')
        owned = conn.execute('''SELECT quantity FROM shop_inventory
            WHERE guild_id=? AND character_id=? AND product_id=?''',
            (str(actor.guild_id),character_id,product_id)).fetchone()
        if not owned or int(owned['quantity']) <= 0:
            raise InvalidState('角色沒有這件商品。')
        slot = _slot(item)
        occupied = conn.execute('''SELECT product_id FROM rp_gear_slots
            WHERE guild_id=? AND character_id=? AND slot=?''',
            (str(actor.guild_id),character_id,slot)).fetchone()
        if occupied and int(occupied['product_id']) != product_id:
            conn.execute('''DELETE FROM shop_context_equipment
               WHERE guild_id=? AND character_id=? AND product_id=?''',
               (str(actor.guild_id),character_id,int(occupied['product_id'])))
            conn.execute('''DELETE FROM rp_gear_slots
               WHERE guild_id=? AND character_id=? AND slot=?''',
               (str(actor.guild_id),character_id,slot))
        conn.execute('''INSERT OR IGNORE INTO rp_gear_slots
           (guild_id,character_id,product_id,slot) VALUES(?,?,?,?)''',
           (str(actor.guild_id),character_id,product_id,slot))
        conn.execute('''INSERT OR IGNORE INTO shop_context_equipment
           (guild_id,character_id,product_id,equipped_at) VALUES(?,?,?,?)''',
           (str(actor.guild_id),character_id,product_id,
            datetime.now(timezone.utc).isoformat()))
        reconcile(conn,actor.guild_id,character_id)
        return f'✅ 已裝備「{item["name"]}」｜欄位：{SLOT_NAMES[slot]}。屬性已自動更新。'


def remove(actor,character_id,product_id):
    with transaction() as conn:
        shop.require_character(conn,actor,character_id)
        existing=conn.execute('''SELECT 1 FROM shop_context_equipment
           WHERE guild_id=? AND character_id=? AND product_id=?''',
           (str(actor.guild_id),character_id,product_id)).fetchone()
        if not existing:
            return '此商品目前沒有裝備。'
        conn.execute('''DELETE FROM shop_context_equipment
           WHERE guild_id=? AND character_id=? AND product_id=?''',
           (str(actor.guild_id),character_id,product_id))
        conn.execute('''DELETE FROM rp_gear_slots
           WHERE guild_id=? AND character_id=? AND product_id=?''',
           (str(actor.guild_id),character_id,product_id))
        reconcile(conn,actor.guild_id,character_id)
        return '✅ 已卸下商品，裝備加成已自動更新。'


def _use_item(actor,character_id,product_id,request_key):
    with get_connection() as conn:
        product=shop.get_product(conn,actor.guild_id,product_id)
    if _eligible_gear(product):
        return equip(actor,character_id,product_id)
    return _original_use_item(actor,character_id,product_id,request_key)


def _unequip(actor,character_id,product_id):
    with get_connection() as conn:
        product=shop.get_product(conn,actor.guild_id,product_id)
    if _eligible_gear(product):
        return remove(actor,character_id,product_id)
    return _original_unequip(actor,character_id,product_id)


def current_equipment(actor,character_id):
    with get_connection() as conn:
        shop.require_character(conn,actor,character_id)
        rows=conn.execute('''SELECT g.slot,p.id,p.name,p.effect_stat,p.effect_value
           FROM rp_gear_slots g JOIN shop_products p
             ON p.guild_id=g.guild_id AND p.id=g.product_id
           WHERE g.guild_id=? AND g.character_id=? ORDER BY g.slot''',
           (str(actor.guild_id),character_id)).fetchall()
        passive=conn.execute('''SELECT stat,amount FROM rp_passive_bonus_applied
           WHERE guild_id=? AND character_id=? ORDER BY stat''',
           (str(actor.guild_id),character_id)).fetchall()
    lines=[f'**{SLOT_NAMES.get(r["slot"],r["slot"])}**：#{r["id"]} {r["name"]} '
           f'({r["effect_stat"]}+{r["effect_value"]})' for r in rows]
    lines.append('\n**目前裝備與持有的總加成**')
    lines.extend(f'{r["stat"]} +{r["amount"]}' for r in passive)
    return '\n'.join(lines) if lines else '目前沒有穿戴裝備。'


class Equipment(commands.Cog):
    def __init__(self,bot):
        self.bot=bot

    group=app_commands.Group(name='裝備',description='穿戴、卸下與查看裝備欄位',guild_only=True)

    @group.command(name='穿戴',description='穿戴物品；同欄位原裝備自動卸下')
    async def wear(self,interaction: discord.Interaction,角色編號:int,商品編號:int):
        await interaction.response.defer(ephemeral=True)
        try:
            actor=Actor.from_interaction(interaction)
            result=await asyncio.to_thread(equip,actor,角色編號,商品編號)
            await interaction.followup.send(result,ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied,sqlite3.Error) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)

    @group.command(name='卸下',description='卸下指定裝備並取消加成')
    async def unwear(self,interaction: discord.Interaction,角色編號:int,商品編號:int):
        await interaction.response.defer(ephemeral=True)
        try:
            actor=Actor.from_interaction(interaction)
            result=await asyncio.to_thread(remove,actor,角色編號,商品編號)
            await interaction.followup.send(result,ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied,sqlite3.Error) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)

    @group.command(name='查看',description='檢視已穿戴裝備及所有被動加成')
    async def view(self,interaction: discord.Interaction,角色編號:int):
        await interaction.response.defer(ephemeral=True)
        try:
            actor=Actor.from_interaction(interaction)
            result=await asyncio.to_thread(current_equipment,actor,角色編號)
            await interaction.followup.send(result[:1900],ephemeral=True)
        except (ValueError,InvalidState,PermissionDenied,sqlite3.Error) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)


async def setup(bot:commands.Bot):
    global _original_adjust_inventory, _original_use_item, _original_unequip, _installed
    await asyncio.to_thread(shop.init_shop_db)
    await asyncio.to_thread(init_db)
    await asyncio.to_thread(sync_existing)
    if not _installed:
        _original_adjust_inventory=shop.adjust_inventory
        _original_use_item=shop.use_item
        _original_unequip=shop.unequip
        shop.adjust_inventory=_adjust_inventory
        shop.use_item=_use_item
        shop.unequip=_unequip
        _installed=True
    await bot.add_cog(Equipment(bot))
    LOG.info('裝備欄位與持有型加成同步已載入')
