"""Discord RP 官方商城：CP 同款購買、贈禮好感度與介面分流。

新增 modules/zz_shop_relationships.py 即可，依賴 modules.shop、
modules.shop_integration、modules.official_center、modules.character_view。
不另建頂層斜線指令，也不清除現有資料。
"""
from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

import discord
from discord.ext import commands

from core.database import transaction, get_connection
from core.permissions import Actor, InvalidState, PermissionDenied
from modules import shop, character_view, official_center


def init_tables():
    with transaction() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS rp_cp_pairs (
            guild_id TEXT NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,a,b), CHECK(a < b)
        );
        CREATE TABLE IF NOT EXISTS rp_relationship_scores (
            guild_id TEXT NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL,
            kind TEXT NOT NULL, points INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id,a,b,kind), CHECK(a < b), CHECK(points >= 0)
        );
        CREATE TABLE IF NOT EXISTS rp_relationship_events (
            guild_id TEXT NOT NULL, event_key TEXT NOT NULL,
            a INTEGER NOT NULL, b INTEGER NOT NULL, kind TEXT NOT NULL,
            product_id INTEGER NOT NULL, points INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,event_key)
        );
        """)


def pair_ids(a, b):
    a, b = int(a), int(b)
    if a <= 0 or b <= 0 or a == b:
        raise ValueError("請填寫兩個不同且有效的角色編號。")
    return min(a, b), max(a, b)


def is_owner(interaction):
    return bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)


def set_cp(guild_id, a, b, user_id, enabled):
    a, b = pair_ids(a, b)
    with transaction() as db:
        for character_id in (a, b):
            row = db.execute("SELECT status FROM characters WHERE guild_id=? AND id=?",
                             (str(guild_id), character_id)).fetchone()
            if not row or row["status"] != "active":
                raise InvalidState(f"角色 #{character_id} 不存在或尚未啟用。")
        if enabled:
            db.execute("""INSERT OR IGNORE INTO rp_cp_pairs(guild_id,a,b,created_by)
                          VALUES(?,?,?,?)""", (str(guild_id), a, b, str(user_id)))
        else:
            db.execute("DELETE FROM rp_cp_pairs WHERE guild_id=? AND a=? AND b=?",
                       (str(guild_id), a, b))
    return f"{'已登記' if enabled else '已解除'}正式 CP：#{a} ↔ #{b}。"


def add_score(db, guild, a, b, kind, points, event_key, product_id):
    a, b = pair_ids(a, b)
    result = db.execute("""INSERT OR IGNORE INTO rp_relationship_events
        (guild_id,event_key,a,b,kind,product_id,points)
        VALUES(?,?,?,?,?,?,?)""", (guild, event_key, a, b, kind, product_id, points))
    if not result.rowcount:
        return 0
    db.execute("""INSERT INTO rp_relationship_scores(guild_id,a,b,kind,points)
        VALUES(?,?,?,?,?) ON CONFLICT(guild_id,a,b,kind)
        DO UPDATE SET points=points+excluded.points""", (guild, a, b, kind, points))
    return points


def award_cp_for_purchase(actor, buyer_id, product_id):
    """必須兩人各自實際購買過相同官方商品；每對每件只獎勵一次。"""
    with transaction() as db:
        partners = db.execute("""SELECT a,b FROM rp_cp_pairs WHERE guild_id=?
                  AND (a=? OR b=?)""", (actor.guild_id, buyer_id, buyer_id)).fetchall()
        messages = []
        for pair in partners:
            a, b = pair["a"], pair["b"]
            found = []
            for member in (a, b):
                row = db.execute("""SELECT 1 FROM shop_events WHERE guild_id=?
                        AND character_id=? AND product_id=? AND kind='purchase' LIMIT 1""",
                        (actor.guild_id, member, product_id)).fetchone()
                found.append(bool(row))
            if not all(found):
                continue
            key = f"cp-purchase:{a}:{b}:{product_id}"
            points = random.SystemRandom().randint(1, 20)
            awarded = add_score(db, actor.guild_id, a, b, "cp", points, key, product_id)
            if awarded:
                messages.append(f"💕 與角色 #{b if buyer_id == a else a} 同款購買：共同 CP 好感度 +{awarded}！")
        return messages


def award_gift(actor, sender_id, recipient_id, product_id, quantity, request_key):
    """贈送成功後增加贈送者→收禮者的關係分數；500 元 1 點，不足 500 最少 1 點。"""
    with transaction() as db:
        ev = db.execute("""SELECT 1 FROM shop_events WHERE guild_id=? AND event_key=?
             AND kind='gift'""", (actor.guild_id, f"gift:{request_key}")).fetchone()
        if ev is None:
            return None
        product = db.execute("SELECT price FROM shop_products WHERE guild_id=? AND id=?",
                             (actor.guild_id, product_id)).fetchone()
        if not product:
            return None
        value = int(product["price"]) * int(quantity)
        points = max(1, value // 500)
        added = add_score(db, actor.guild_id, sender_id, recipient_id,
                          "gift", points, f"gift:{request_key}", product_id)
        return f"💝 贈禮關係好感度 +{added}（價值 {value:,} 金幣）。" if added else None


def relationship_text(actor, character_id):
    character_view.get_character(actor, character_id)
    with get_connection() as db:
        rows = db.execute("""SELECT a,b,kind,points FROM rp_relationship_scores
             WHERE guild_id=? AND (a=? OR b=?) ORDER BY points DESC LIMIT 20""",
             (actor.guild_id, character_id, character_id)).fetchall()
        pairs = db.execute("""SELECT a,b FROM rp_cp_pairs WHERE guild_id=? AND (a=? OR b=?)""",
             (actor.guild_id, character_id, character_id)).fetchall()
    names = [f"💕 正式 CP：#{r['b'] if r['a']==character_id else r['a']}" for r in pairs]
    names.extend(f"#{r['b'] if r['a']==character_id else r['a']}｜"
                 f"{'CP' if r['kind']=='cp' else '贈禮'} 好感度 {r['points']:,}" for r in rows)
    return "\n".join(names)[:1900] or "尚未建立正式 CP 或取得關係好感度。"


class CPModal(discord.ui.Modal, title="群主｜正式 CP 登記"):
    a = discord.ui.TextInput(label="第一名角色編號", placeholder="例如 12")
    b = discord.ui.TextInput(label="第二名角色編號", placeholder="例如 26")
    action = discord.ui.TextInput(label="操作（建立／解除）", default="建立")

    async def on_submit(self, interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("⛔ 只有群主可以登記正式 CP。", ephemeral=True)
            return
        try:
            command = str(self.action.value).strip()
            if command not in ("建立", "解除"):
                raise ValueError("操作只能填建立或解除。")
            result = await asyncio.to_thread(set_cp, interaction.guild_id,
                 int(self.a.value), int(self.b.value), interaction.user.id,
                 command == "建立")
        except (ValueError, InvalidState) as exc:
            result = f"⚠️ {exc}"
        await interaction.response.send_message(result, ephemeral=True)


class CPAdminButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💕 正式 CP 登記", style=discord.ButtonStyle.secondary, row=3)

    async def callback(self, interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("⛔ 群主專屬。", ephemeral=True)
            return
        await interaction.response.send_modal(CPModal())


class BagMenu(discord.ui.View):
    def __init__(self, actor, character_id):
        super().__init__(timeout=600)
        self.actor, self.character_id = actor, int(character_id)

    async def interaction_check(self, interaction):
        current = Actor.from_interaction(interaction)
        if current.user_id != self.actor.user_id or current.guild_id != self.actor.guild_id:
            await interaction.response.send_message("這不是你的背包。", ephemeral=True)
            return False
        with get_connection() as db:
            shop.require_character(db, current, self.character_id)
        return True

    @discord.ui.button(label="🎒 查看背包", style=discord.ButtonStyle.primary)
    async def inventory(self, interaction, button):
        try:
            from modules.shop_integration import combined_inventory
            msg = await asyncio.to_thread(combined_inventory,
                        Actor.from_interaction(interaction), self.character_id)
        except (InvalidState, PermissionDenied, ValueError) as exc:
            msg = f"⚠️ {exc}"
        await interaction.response.send_message(str(msg)[:1900], ephemeral=True)

    @discord.ui.button(label="🎁 贈送", style=discord.ButtonStyle.secondary)
    async def gift(self, interaction, button):
        context = SimpleNamespace(owner_id=interaction.user.id, character_id=self.character_id)
        await interaction.response.send_modal(shop.ShopActionModal(context, "gift"))

    @discord.ui.button(label="✨ 使用", style=discord.ButtonStyle.secondary)
    async def use(self, interaction, button):
        context = SimpleNamespace(owner_id=interaction.user.id, character_id=self.character_id)
        await interaction.response.send_modal(shop.ShopActionModal(context, "use"))

    @discord.ui.button(label="🧥 停用效果", style=discord.ButtonStyle.secondary)
    async def stop(self, interaction, button):
        context = SimpleNamespace(owner_id=interaction.user.id, character_id=self.character_id)
        await interaction.response.send_modal(shop.ShopActionModal(context, "stop"))


class BagButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎒 背包／使用／贈送", style=discord.ButtonStyle.primary, row=2)

    async def callback(self, interaction):
        actor = Actor.from_interaction(interaction)
        character_id = self.view.character_id
        try:
            with get_connection() as db:
                shop.require_character(db, actor, character_id)
            await interaction.response.send_message("🎒 背包管理", view=BagMenu(actor, character_id), ephemeral=True)
        except (InvalidState, PermissionDenied) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


class RelationshipButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💕 關係好感度", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction):
        actor = Actor.from_interaction(interaction)
        try:
            msg = relationship_text(actor, self.view.character_id)
        except (InvalidState, PermissionDenied) as exc:
            msg = f"⚠️ {exc}"
        await interaction.response.send_message(msg, ephemeral=True)


class ShopRelationships(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await asyncio.to_thread(init_tables)
    # 在既有金融交易成功後追記關係事件；不重複扣款或移轉商品。
    original_buy = shop.buy
    original_gift = shop.gift

    def buy_with_cp(actor, character_id, product_id, quantity, request_key):
        result = original_buy(actor, character_id, product_id, quantity, request_key)
        if "購買成功" in result or "這筆購買已完成" in result:
            try:
                bonus = award_cp_for_purchase(actor, character_id, product_id)
                if bonus:
                    result += "\n" + "\n".join(bonus)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("CP 購買加分失敗，原訂單已完成")
        return result

    def gift_with_points(actor, sender_id, recipient_id, product_id, quantity, request_key):
        result = original_gift(actor, sender_id, recipient_id, product_id, quantity, request_key)
        if "已贈送" in result or "這筆贈送已完成" in result:
            try:
                points = award_gift(actor, sender_id, recipient_id, product_id, quantity, request_key)
                if points:
                    result += "\n" + points
            except Exception:
                import logging
                logging.getLogger(__name__).exception("贈禮加分失敗，原轉移已完成")
        return result

    shop.buy = buy_with_cp
    shop.gift = gift_with_points

    # 原商城選單移除背包/贈送/使用/停用，移到 /查看。
    menu_class = shop.ShopMenu
    old_menu_init = menu_class.__init__

    def new_menu_init(self, *args, **kwargs):
        old_menu_init(self, *args, **kwargs)
        for child in list(self.children):
            if getattr(child, "label", None) in ("我的背包", "贈送物品", "使用物品", "停用效果"):
                self.remove_item(child)
        # 店主才看得見管理按鈕；資料庫中的店主角色須屬於操作者。
        # 驗證這個選單的 Discord 操作者確實擁有角色及營業中的個人商店。
        with get_connection() as db:
            has_store = db.execute("""
                SELECT 1 FROM personal_shops s
                JOIN characters c ON c.guild_id=s.guild_id AND c.id=s.character_id
                WHERE s.character_id=? AND c.owner_id=? AND s.status='open'
                LIMIT 1
            """, (self.character_id, str(self.owner_id))).fetchone()
        if not has_store:
            for child in list(self.children):
                if getattr(child, "label", None) in ("店主新增商品", "店主修改商品"):
                    self.remove_item(child)

    menu_class.__init__ = new_menu_init

    old_detail_init = character_view.CharacterDetailView.__init__
    def new_detail_init(self, *args, **kwargs):
        old_detail_init(self, *args, **kwargs)
        # 角色詳情已統一七鍵入口，避免重複增設背包及關係按鈕。
    character_view.CharacterDetailView.__init__ = new_detail_init

    # CP 登記屬於次級管理功能，不在 /官方 首頁擠出第六顆按鈕。
    old_tools_init = official_center.AdminToolsView.__init__
    if not getattr(official_center.AdminToolsView, '_rp_cp_patched', False):
        def new_tools_init(self, interaction, *args, **kwargs):
            old_tools_init(self, interaction, *args, **kwargs)
            if official_center.is_owner(interaction):
                button = CPAdminButton()
                button.row = 3
                self.add_item(button)
        official_center.AdminToolsView.__init__ = new_tools_init
        official_center.AdminToolsView._rp_cp_patched = True

    await bot.add_cog(ShopRelationships(bot))
