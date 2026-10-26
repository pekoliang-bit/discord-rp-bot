
"""Official shop, per-character inventory, gifting, consumables, and bonuses.

Uses the shared SQLite ledger and permissions modules.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction, get_connection, get_or_create_account
from core.economy import _payment
from core.permissions import Actor, require_admin, PermissionDenied, InvalidState


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_shop_db():
    with transaction() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shop_config (
                guild_id TEXT PRIMARY KEY,
                capacity INTEGER,
                CHECK (capacity IS NULL OR capacity >= 1)
            );
            CREATE TABLE IF NOT EXISTS shop_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL CHECK(price >= 0),
                stock INTEGER,
                scope TEXT NOT NULL DEFAULT 'public'
                    CHECK(scope IN ('public','black')),
                effect_kind TEXT NOT NULL DEFAULT 'none'
                    CHECK(effect_kind IN ('none','instant','weekly','context')),
                effect_stat TEXT,
                effect_value INTEGER NOT NULL DEFAULT 0,
                effect_context TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(stock IS NULL OR stock >= 0),
                CHECK(length(name) BETWEEN 1 AND 100),
                CHECK(length(category) BETWEEN 1 AND 60),
                UNIQUE(guild_id, id)
            );
            CREATE INDEX IF NOT EXISTS idx_shop_catalog
                ON shop_products(guild_id, scope, active, category);
            CREATE TABLE IF NOT EXISTS shop_inventory (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity >= 0),
                PRIMARY KEY(guild_id, character_id, product_id)
            );
            CREATE TABLE IF NOT EXISTS shop_events (
                guild_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, event_key)
            );
            CREATE TABLE IF NOT EXISTS shop_active_bonuses (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                stat TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, character_id, stat)
            );
            CREATE TABLE IF NOT EXISTS shop_instant_effects (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                stat TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(guild_id, character_id, stat)
            );
            CREATE TABLE IF NOT EXISTS shop_context_equipment (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                equipped_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, character_id, product_id)
            );
        """)


def require_character(conn, actor, character_id):
    if not actor.guild_id:
        raise InvalidState('請在伺服器內使用指令。')
    row = conn.execute(
        'SELECT * FROM characters WHERE guild_id=? AND id=?',
        (actor.guild_id, character_id),
    ).fetchone()
    if not row or row['status'] != 'active':
        raise InvalidState('找不到已啟用的角色。')
    if row['owner_id'] != actor.user_id and not actor.is_admin:
        raise PermissionDenied('你沒有此角色的操作權限。')
    return row


def get_product(conn, guild_id, product_id):
    row = conn.execute(
        'SELECT * FROM shop_products WHERE guild_id=? AND id=?',
        (guild_id, product_id),
    ).fetchone()
    if row is None:
        raise InvalidState('商品不存在。')
    return row


def has_black_access(conn, guild_id, character_id):
    # Defer to the black-card module for membership and deposit checks.
    from modules.black_card import is_black_card_active
    return is_black_card_active(conn, guild_id, character_id)


def validate_effect(kind, stat, value, context):
    if kind not in ('none', 'instant', 'weekly', 'context'):
        raise ValueError('商品效果類型不支援。')
    stat = stat.strip() if stat else ''
    context = context.strip() if context else ''
    if kind == 'none':
        return None, 0, None
    if not stat or len(stat) > 60 or not (0 < value <= 1_000_000):
        raise ValueError('效果商品需要屬性名稱與正整數加成值。')
    if kind == 'context' and not context:
        raise ValueError('情境加成必須填寫適用場合。')
    return stat, value, context or None


def add_product(actor, name, category, price, stock, scope,
                effect_kind, effect_stat, effect_value, effect_context, description):
    require_admin(actor)
    name = name.strip()
    category = category.strip()
    if not (1 <= len(name) <= 100 and 1 <= len(category) <= 60):
        raise ValueError('商品名稱與分類不得空白且不能過長。')
    if price < 0 or price > 10**15:
        raise ValueError('價格必須介於0至10^15。')
    if stock is not None and stock < 0:
        raise ValueError('庫存不能是負數。')
    if scope not in ('public', 'black'):
        raise ValueError('商品商城分類不合法。')
    if len(description) > 500:
        raise ValueError('商品介紹不能超過500字。')
    stat, amount, context = validate_effect(
        effect_kind, effect_stat, effect_value, effect_context
    )
    with transaction() as conn:
        cursor = conn.execute('''
            INSERT INTO shop_products (
                guild_id,name,category,description,price,stock,scope,
                effect_kind,effect_stat,effect_value,effect_context,
                created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (actor.guild_id, name, category, description, price, stock,
              scope, effect_kind, stat, amount, context, utc_now(), utc_now()))
        return cursor.lastrowid


def edit_product(actor, product_id, price, stock, active):
    require_admin(actor)
    if price is not None and not (0 <= price <= 10**15):
        raise ValueError('商品價格不合法。')
    if stock is not None and stock < 0:
        raise ValueError('庫存不可為負數。')
    with transaction() as conn:
        get_product(conn, actor.guild_id, product_id)
        if price is not None:
            conn.execute('UPDATE shop_products SET price=?,updated_at=? WHERE guild_id=? AND id=?',
                         (price, utc_now(), actor.guild_id, product_id))
        if stock is not None:
            conn.execute('UPDATE shop_products SET stock=?,updated_at=? WHERE guild_id=? AND id=?',
                         (stock, utc_now(), actor.guild_id, product_id))
        if active is not None:
            conn.execute('UPDATE shop_products SET active=?,updated_at=? WHERE guild_id=? AND id=?',
                         (int(active), utc_now(), actor.guild_id, product_id))
    return '商品資料已更新。'


def adjust_inventory(conn, guild_id, character_id, product_id, delta):
    if not delta:
        return
    before = conn.execute('''
        SELECT quantity FROM shop_inventory
        WHERE guild_id=? AND character_id=? AND product_id=?
    ''', (guild_id, character_id, product_id)).fetchone()
    qty = (before['quantity'] if before else 0) + delta
    if qty < 0:
        raise InvalidState('背包內的商品數量不足。')
    if qty and delta > 0:
        settings = conn.execute('SELECT capacity FROM shop_config WHERE guild_id=?',
                                (guild_id,)).fetchone()
        if settings and settings['capacity'] is not None and not before:
            used = conn.execute('''
                SELECT COUNT(*) FROM shop_inventory
                WHERE guild_id=? AND character_id=? AND quantity>0
            ''', (guild_id, character_id)).fetchone()[0]
            if used >= settings['capacity']:
                raise InvalidState('背包已達伺服器設定的種類容量上限。')
    if qty:
        conn.execute('''
            INSERT INTO shop_inventory(guild_id,character_id,product_id,quantity)
            VALUES (?,?,?,?)
            ON CONFLICT(guild_id,character_id,product_id)
            DO UPDATE SET quantity=excluded.quantity
        ''', (guild_id, character_id, product_id, qty))
    else:
        conn.execute('DELETE FROM shop_inventory WHERE guild_id=? AND character_id=? AND product_id=?',
                     (guild_id, character_id, product_id))


def recorded(conn, guild_id, key):
    return conn.execute('SELECT 1 FROM shop_events WHERE guild_id=? AND event_key=?',
                        (guild_id, key)).fetchone() is not None


def log_event(conn, guild_id, key, kind, character_id, product_id, quantity):
    conn.execute('''
        INSERT INTO shop_events(guild_id,event_key,kind,character_id,product_id,quantity,created_at)
        VALUES (?,?,?,?,?,?,?)
    ''', (guild_id, key, kind, character_id, product_id, quantity, utc_now()))


def buy(actor, character_id, product_id, quantity, request_key):
    if not 1 <= quantity <= 10000:
        raise ValueError('每次購買數量必須介於1至10000。')
    with transaction() as conn:
        require_character(conn, actor, character_id)
        key = f'buy:{request_key}'
        if recorded(conn, actor.guild_id, key):
            return '這筆購買已完成，未再次扣款。'
        item = get_product(conn, actor.guild_id, product_id)
        if not item['active']:
            raise InvalidState('商品已下架。')
        if item['scope'] == 'black' and not has_black_access(conn, actor.guild_id, character_id):
            raise PermissionDenied('此商品僅限有效黑卡會員購買。')
        if item['stock'] is not None and item['stock'] < quantity:
            raise InvalidState('商品庫存不足。')
        total = item['price'] * quantity
        wallet = get_or_create_account(conn, actor.guild_id, 'character', str(character_id), 'wallet')
        merchant = get_or_create_account(conn, actor.guild_id, 'system', 'official_shop', 'treasury')
        # Atomic deduction and fulfillment; _payment checks insufficient funds.
        if total:
            _payment(conn, actor.guild_id, f'shop:{key}', wallet, merchant, total,
                     f'官方商城購買：{item["name"]} x{quantity}')
        adjust_inventory(conn, actor.guild_id, character_id, product_id, quantity)
        if item['stock'] is not None:
            conn.execute('''
                UPDATE shop_products SET stock=stock-?,updated_at=?
                WHERE guild_id=? AND id=?
            ''', (quantity, utc_now(), actor.guild_id, product_id))
        log_event(conn, actor.guild_id, key, 'purchase', character_id, product_id, quantity)
        return f'購買成功：{item["name"]} ×{quantity}，支付 {total:,} 金幣。'


def gift(actor, sender_id, recipient_id, product_id, quantity, request_key):
    if sender_id == recipient_id:
        raise ValueError('不能贈送給同一個角色。')
    if not 1 <= quantity <= 10000:
        raise ValueError('轉讓數量必須介於1至10000。')
    with transaction() as conn:
        require_character(conn, actor, sender_id)
        target = conn.execute('SELECT status FROM characters WHERE guild_id=? AND id=?',
                              (actor.guild_id, recipient_id)).fetchone()
        if not target or target['status'] != 'active':
            raise InvalidState('接收角色不存在或尚未啟用。')
        item = get_product(conn, actor.guild_id, product_id)
        key = f'gift:{request_key}'
        if recorded(conn, actor.guild_id, key):
            return '這筆贈送已完成，未再次轉移物品。'
        # An equipped item cannot be gifted unless first unequipped.
        equipped = conn.execute('''
            SELECT 1 FROM shop_context_equipment
            WHERE guild_id=? AND character_id=? AND product_id=?
        ''', (actor.guild_id, sender_id, product_id)).fetchone()
        if equipped:
            raise InvalidState('請先卸下此商品後再贈送。')
        active = conn.execute('''
            SELECT 1 FROM shop_active_bonuses
            WHERE guild_id=? AND character_id=? AND product_id=?
        ''', (actor.guild_id, sender_id, product_id)).fetchone()
        if active:
            raise InvalidState('請先停用此商品的週期加成再贈送。')
        adjust_inventory(conn, actor.guild_id, sender_id, product_id, -quantity)
        adjust_inventory(conn, actor.guild_id, recipient_id, product_id, quantity)
        log_event(conn, actor.guild_id, key, 'gift', sender_id, product_id, quantity)
        return f'已贈送 {item["name"]} ×{quantity} 給角色 #{recipient_id}。'


def use_item(actor, character_id, product_id, request_key):
    with transaction() as conn:
        require_character(conn, actor, character_id)
        key = f'use:{request_key}'
        if recorded(conn, actor.guild_id, key):
            return '這次使用已經記錄，不會重複生效。'
        item = get_product(conn, actor.guild_id, product_id)
        held = conn.execute('''
            SELECT quantity FROM shop_inventory
            WHERE guild_id=? AND character_id=? AND product_id=?
        ''', (actor.guild_id, character_id, product_id)).fetchone()
        if not held or held['quantity'] <= 0:
            raise InvalidState('背包中沒有這件商品。')
        kind = item['effect_kind']
        if kind == 'none':
            raise InvalidState('這件商品沒有可使用的數值效果。')
        stat = item['effect_stat']
        value = item['effect_value']
        if kind == 'instant':
            adjust_inventory(conn, actor.guild_id, character_id, product_id, -1)
            # Effect recorded independently until integrated with the attributes API.
            conn.execute('''
                INSERT INTO shop_instant_effects(guild_id,character_id,stat,amount)
                VALUES (?,?,?,?)
                ON CONFLICT(guild_id,character_id,stat)
                DO UPDATE SET amount=amount+excluded.amount
            ''', (actor.guild_id, character_id, stat, value))
            msg = f'已使用 {item["name"]}，記錄一次性 {stat} +{value}。'
        elif kind == 'weekly':
            conn.execute('''
                INSERT INTO shop_active_bonuses
                (guild_id,character_id,stat,product_id,amount,activated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(guild_id,character_id,stat)
                DO UPDATE SET product_id=excluded.product_id,
                    amount=excluded.amount,activated_at=excluded.activated_at
            ''', (actor.guild_id, character_id, stat, product_id, value, utc_now()))
            msg = f'已啟用每週 {stat} +{value}；同屬性舊加成已被替換。'
        else:
            conn.execute('''
                INSERT INTO shop_context_equipment(guild_id,character_id,product_id,equipped_at)
                VALUES (?,?,?,?)
                ON CONFLICT(guild_id,character_id,product_id)
                DO UPDATE SET equipped_at=excluded.equipped_at
            ''', (actor.guild_id, character_id, product_id, utc_now()))
            msg = f'已裝備 {item["name"]}。適用場合：{item["effect_context"]}；僅於符合場合時加成。'
        log_event(conn, actor.guild_id, key, 'use', character_id, product_id, 1)
        return msg


def unequip(actor, character_id, product_id):
    with transaction() as conn:
        require_character(conn, actor, character_id)
        product = get_product(conn, actor.guild_id, product_id)
        if product['effect_kind'] == 'weekly':
            count = conn.execute('''
                DELETE FROM shop_active_bonuses
                WHERE guild_id=? AND character_id=? AND product_id=?
            ''', (actor.guild_id, character_id, product_id)).rowcount
        elif product['effect_kind'] == 'context':
            count = conn.execute('''
                DELETE FROM shop_context_equipment
                WHERE guild_id=? AND character_id=? AND product_id=?
            ''', (actor.guild_id, character_id, product_id)).rowcount
        else:
            raise InvalidState('這件商品沒有可停用的效果。')
        return '已停用商品效果。' if count else '此商品目前未啟用。'


def catalog(actor, character_id, black, category):
    with transaction() as conn:
        require_character(conn, actor, character_id)
        scope = 'black' if black else 'public'
        if black and not has_black_access(conn, actor.guild_id, character_id):
            raise PermissionDenied('僅有效黑卡會員可進入隱藏商城。')
        params = [actor.guild_id, scope]
        sql = '''SELECT id,name,category,price,stock FROM shop_products
                 WHERE guild_id=? AND scope=? AND active=1'''
        if category:
            sql += ' AND category=?'
            params.append(category.strip())
        sql += ' ORDER BY id LIMIT 25'
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            return '此商城目前沒有上架商品。'
        return '\n'.join(
            f'#{r["id"]}｜{r["name"]}｜{r["category"]}｜{r["price"]:,}金幣｜'
            f'庫存：{"不限" if r["stock"] is None else r["stock"]}' for r in rows
        ) + '\n（每次最多顯示前25項，之後會加入分頁。）'


def inventory(actor, character_id):
    with get_connection() as conn:
        require_character(conn, actor, character_id)
        rows = conn.execute('''
            SELECT p.id,p.name,i.quantity
            FROM shop_inventory i
            JOIN shop_products p ON p.guild_id=i.guild_id AND p.id=i.product_id
            WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0
            ORDER BY p.id LIMIT 40
        ''', (actor.guild_id, character_id)).fetchall()
        return '\n'.join(f'#{r["id"]}｜{r["name"]} × {r["quantity"]}' for r in rows) or '背包目前是空的。'


class ShopActionModal(discord.ui.Modal):
    """以商品編號操作，不要求玩家記住多個斜線指令。"""

    def __init__(self, menu: "ShopMenu", kind: str):
        titles = {
            "buy": "購買商品", "gift": "贈送物品",
            "use": "使用物品", "stop": "停用物品效果",
        }
        super().__init__(title=titles[kind], timeout=300)
        self.menu = menu
        self.kind = kind
        self.product = discord.ui.TextInput(
            label="商品編號", placeholder="例如：12", max_length=12
        )
        self.add_item(self.product)
        if kind in ("buy", "gift"):
            self.quantity = discord.ui.TextInput(
                label="數量", default="1", max_length=8
            )
            self.add_item(self.quantity)
        if kind == "gift":
            self.recipient = discord.ui.TextInput(
                label="收禮角色編號", placeholder="例如：8", max_length=12
            )
            self.add_item(self.recipient)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.menu.owner_id:
            await interaction.response.send_message("這不是你的商城選單。", ephemeral=True)
            return
        try:
            product_id = int(str(self.product.value))
            if product_id <= 0:
                raise ValueError()
            if self.kind in ("buy", "gift"):
                quantity = int(str(self.quantity.value))
                if not 1 <= quantity <= 10000:
                    raise ValueError()
            if self.kind == "gift":
                recipient_id = int(str(self.recipient.value))
                if recipient_id <= 0:
                    raise ValueError()
        except ValueError:
            await interaction.response.send_message(
                "請輸入正確的商品、角色編號及數量。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        actor = Actor.from_interaction(interaction)
        try:
            if self.kind == "buy":
                result = await asyncio.to_thread(
                    buy, actor, self.menu.character_id, product_id,
                    quantity, str(interaction.id)
                )
            elif self.kind == "gift":
                result = await asyncio.to_thread(
                    gift, actor, self.menu.character_id, recipient_id,
                    product_id, quantity, str(interaction.id)
                )
            elif self.kind == "use":
                result = await asyncio.to_thread(
                    use_item, actor, self.menu.character_id,
                    product_id, str(interaction.id)
                )
            else:
                result = await asyncio.to_thread(
                    unequip, actor, self.menu.character_id, product_id
                )
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            result = f"⚠️ {exc}"
        await interaction.followup.send(str(result)[:1900], ephemeral=True)


class ShopMenu(discord.ui.View):
    def __init__(self, owner_id: int, character_id: int, black: bool, category: str):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.character_id = character_id
        self.black = black
        self.category = category

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("請使用自己的 /商城 選單。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="查看商品", style=discord.ButtonStyle.primary, row=0)
    async def browse(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(
                catalog, Actor.from_interaction(interaction),
                self.character_id, self.black, self.category
            )
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            result = f"⚠️ {exc}"
        await interaction.followup.send(str(result)[:1900], ephemeral=True)

    @discord.ui.button(label="購買商品", style=discord.ButtonStyle.success, row=0)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ShopActionModal(self, "buy"))

    @discord.ui.button(label="我的背包", style=discord.ButtonStyle.secondary, row=0)
    async def bag_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(
                inventory, Actor.from_interaction(interaction), self.character_id
            )
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            result = f"⚠️ {exc}"
        await interaction.followup.send(str(result)[:1900], ephemeral=True)

    @discord.ui.button(label="贈送物品", style=discord.ButtonStyle.secondary, row=1)
    async def gift_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ShopActionModal(self, "gift"))

    @discord.ui.button(label="使用物品", style=discord.ButtonStyle.secondary, row=1)
    async def use_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ShopActionModal(self, "use"))

    @discord.ui.button(label="停用效果", style=discord.ButtonStyle.secondary, row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ShopActionModal(self, "stop"))


class Shop(commands.Cog):
    # 管理指令歸到 /管理 下；玩家只需 /商城。
    # 注意：若專案其他 Cog 已註冊 /管理，須在主程式統一合併管理群組。
    management = app_commands.Group(name="管理", description="伺服器管理員操作")

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(init_shop_db)

    async def reply(self, interaction, work):
        if interaction.guild_id is None:
            await interaction.response.send_message('請在伺服器內使用此指令。', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(work)
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            result = f'⚠️ {exc}'
        await interaction.followup.send(str(result)[:1900], ephemeral=True)

    @app_commands.command(name='商城', description='開啟購物、背包與物品操作選單')
    async def shop(self, interaction: discord.Interaction, 角色編號: int,
                   黑卡商城: bool = False, 商品分類: str = ''):
        if interaction.guild_id is None:
            await interaction.response.send_message('請在伺服器內使用。', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            actor = Actor.from_interaction(interaction)
            products = await asyncio.to_thread(
                catalog, actor, 角色編號, 黑卡商城, 商品分類
            )
        except (ValueError, InvalidState, PermissionDenied, sqlite3.Error) as exc:
            await interaction.followup.send(f'⚠️ {exc}', ephemeral=True)
            return
        title = '🖤 黑卡隱藏商城' if 黑卡商城 else '🛍️ 官方商城'
        view = ShopMenu(interaction.user.id, 角色編號, 黑卡商城, 商品分類)
        await interaction.followup.send(
            f'{title}｜角色 #{角色編號}\n{products[:1600]}\n\n請使用下方按鈕操作。',
            ephemeral=True, view=view
        )

    @management.command(name='商城新增', description='管理員新增官方商城商品')
    @app_commands.default_permissions(manage_guild=True)
    async def create(self, interaction: discord.Interaction,
                     名稱: str, 分類: str, 價格: app_commands.Range[int, 0, 10**15],
                     庫存: app_commands.Range[int, 0, 100000000] = None,
                     黑卡專屬: bool = False,
                     效果類型: str = 'none', 效果屬性: str = '',
                     效果數值: int = 0, 適用場合: str = '', 商品介紹: str = ''):
        await self.reply(interaction, lambda:
            f'✅ 商品編號 #{add_product(Actor.from_interaction(interaction), 名稱, 分類, 價格, 庫存, "black" if 黑卡專屬 else "public", 效果類型, 效果屬性, 效果數值, 適用場合, 商品介紹)} 已建立。')

    @management.command(name='商城修改', description='管理員修改商城商品')
    @app_commands.default_permissions(manage_guild=True)
    async def edit(self, interaction: discord.Interaction, 商品編號: int,
                   新價格: app_commands.Range[int, 0, 10**15] = None,
                   新庫存: app_commands.Range[int, 0, 100000000] = None,
                   是否上架: bool = None):
        await self.reply(interaction, lambda: edit_product(
            Actor.from_interaction(interaction), 商品編號, 新價格, 新庫存, 是否上架))

    @management.command(name='商城下架', description='管理員停用商品販售')
    @app_commands.default_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction, 商品編號: int):
        await self.reply(interaction, lambda: edit_product(
            Actor.from_interaction(interaction), 商品編號, None, None, False))


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
