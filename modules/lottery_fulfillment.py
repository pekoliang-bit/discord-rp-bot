"""抽獎商品領獎模組。

與 modules.lottery_threads 共用 rp_lottery_* 資料表。
不覆蓋原本 /抽獎，避免破壞現有金幣流程。
"""
from __future__ import annotations

import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from modules.lottery_threads import init_lottery_db

LOG = logging.getLogger(__name__)


def init_fulfillment_db():
    init_lottery_db()
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_lottery_product_bindings (
            guild_id TEXT NOT NULL,
            prize_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            updated_by TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, prize_id),
            FOREIGN KEY(prize_id) REFERENCES rp_lottery_prizes(id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_lottery_deliveries (
            guild_id TEXT NOT NULL,
            draw_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            delivered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,draw_id),
            FOREIGN KEY(draw_id) REFERENCES rp_lottery_draws(id)
        )""")


def is_owner(i):
    return bool(i.guild and i.guild.owner_id == i.user.id)


async def answer(i, message):
    message = discord.utils.escape_mentions(str(message))[:1900]
    if i.response.is_done():
        await i.followup.send(message, ephemeral=True)
    else:
        await i.response.send_message(message, ephemeral=True)


def bind_prize(gid, uid, prize_id, product_id):
    gid = str(gid)
    with transaction() as db:
        prize = db.execute("""SELECT p.id, p.name, p.prize_type
            FROM rp_lottery_prizes p
            JOIN rp_lottery_lists l ON l.id=p.lottery_id
            WHERE l.guild_id=? AND p.id=?""", (gid, prize_id)).fetchone()
        if not prize:
            raise ValueError('找不到此伺服器的獎品。')
        if prize['prize_type'] != 'gift':
            raise ValueError('只有 gift 類型獎品能綁定商城商品。')
        product = db.execute("""SELECT id,name FROM shop_products
            WHERE guild_id=? AND id=? AND active=1""", (gid, product_id)).fetchone()
        if not product:
            raise ValueError('商城商品不存在或已下架。')
        # 已有領獎紀錄時不可改綁，避免新舊得獎人收到不同商品。
        existing = db.execute("""SELECT 1 FROM rp_lottery_deliveries d
            JOIN rp_lottery_draws r ON r.id=d.draw_id
            WHERE r.guild_id=? AND r.prize_id=? LIMIT 1""", (gid, prize_id)).fetchone()
        if existing:
            raise ValueError('此獎品已有成功領取紀錄，禁止重新綁定。')
        db.execute("""INSERT INTO rp_lottery_product_bindings
            (guild_id,prize_id,product_id,updated_by)
            VALUES (?,?,?,?) ON CONFLICT(guild_id,prize_id)
            DO UPDATE SET product_id=excluded.product_id,
            updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
            (gid, prize_id, product_id, str(uid)))
        return prize['name'], product['name']


def deliver(gid, uid, draw_id, character_id):
    gid, uid = str(gid), str(uid)
    with transaction() as db:
        draw = db.execute("""SELECT d.id, d.prize_id, d.state,
                    p.name AS prize_name, p.prize_type, l.name AS list_name,
                    b.product_id
            FROM rp_lottery_draws d
            JOIN rp_lottery_prizes p ON p.id=d.prize_id
            JOIN rp_lottery_lists l ON l.id=d.lottery_id
            LEFT JOIN rp_lottery_product_bindings b
              ON b.guild_id=d.guild_id AND b.prize_id=d.prize_id
            WHERE d.guild_id=? AND d.user_id=? AND d.id=?""",
            (gid,uid,draw_id)).fetchone()
        if draw is None:
            raise ValueError('找不到你名下的這筆中獎紀錄。')
        if draw['prize_type'] != 'gift':
            raise ValueError('此紀錄是金幣獎勵，無需領取商品。')
        if draw['product_id'] is None:
            raise ValueError('官方尚未為此獎品綁定商城商品，暫時無法領取。')
        previous = db.execute("""SELECT character_id,product_id FROM rp_lottery_deliveries
            WHERE guild_id=? AND draw_id=?""", (gid,draw_id)).fetchone()
        if previous:
            return False, draw['prize_name'], previous['character_id']
        character = db.execute("""SELECT id FROM characters
            WHERE guild_id=? AND id=? AND owner_id=?
              AND status IN ('approved','active')""", (gid,character_id,uid)).fetchone()
        if not character:
            raise ValueError('角色不存在、不屬於你，或尚未正式啟用。')
        product = db.execute("""SELECT id,name FROM shop_products
            WHERE guild_id=? AND id=? AND active=1""", (gid,draw['product_id'])).fetchone()
        if not product:
            raise ValueError('獎品對應商品已下架，請先聯絡官方。')
        # 沿用商城自身的庫存/背包容量規則，所有變更皆在同一交易內。
        from modules.shop import adjust_inventory
        adjust_inventory(db, gid, int(character_id), int(product['id']), 1)
        db.execute("""INSERT INTO rp_lottery_deliveries
            (guild_id,draw_id,user_id,character_id,product_id,quantity)
            VALUES (?,?,?,?,?,1)""", (gid,draw_id,uid,character_id,product['id']))
        return True, product['name'], character_id


class LotteryFulfillment(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        init_fulfillment_db()

    @app_commands.command(name='抽獎商品綁定', description='群主將抽獎禮物綁定至已上架的商城商品')
    @app_commands.describe(獎品編號='rp_lottery_prizes 的獎品編號', 商品編號='商城商品編號')
    async def bind(self, i: discord.Interaction, 獎品編號: int, 商品編號: int):
        if not is_owner(i):
            return await answer(i, '⛔ 僅群主可以綁定獎品。')
        if 獎品編號 < 1 or 商品編號 < 1:
            return await answer(i, '⚠️ 編號必須是正整數。')
        try:
            a, b = bind_prize(i.guild_id, i.user.id, 獎品編號, 商品編號)
            await answer(i, f'✅ 已將獎品「{a}」綁定商品「{b}」。')
        except (ValueError, sqlite3.Error) as exc:
            await answer(i, f'⚠️ {exc}')

    @app_commands.command(name='抽獎紀錄', description='查看自己的中獎紀錄及商品領取編號')
    async def history(self, i: discord.Interaction):
        if i.guild_id is None:
            return await answer(i, '⚠️ 請在伺服器內使用。')
        with get_connection() as db:
            rows = db.execute("""SELECT d.id,l.name AS list_name,p.name AS prize_name,
                p.prize_type,p.amount, delivery.character_id,b.product_id
                FROM rp_lottery_draws d
                JOIN rp_lottery_lists l ON l.id=d.lottery_id
                JOIN rp_lottery_prizes p ON p.id=d.prize_id
                LEFT JOIN rp_lottery_deliveries delivery
                  ON delivery.guild_id=d.guild_id AND delivery.draw_id=d.id
                LEFT JOIN rp_lottery_product_bindings b
                  ON b.guild_id=d.guild_id AND b.prize_id=d.prize_id
                WHERE d.guild_id=? AND d.user_id=?
                ORDER BY d.id DESC LIMIT 20""",
                (str(i.guild_id),str(i.user.id))).fetchall()
        if not rows:
            return await answer(i, '目前沒有中獎紀錄。')
        lines = ['🎟️ **你的抽獎紀錄**']
        for r in rows:
            if r['prize_type']=='money':
                suffix=f"金幣 {r['amount']}（請以銀行入帳紀錄確認）"
            elif r['character_id'] is not None:
                suffix=f"已入角色 #{r['character_id']} 的背包"
            elif r['product_id'] is not None:
                suffix='可用 /抽獎商品領取 指定角色領取'
            else:
                suffix='待官方綁定商品／安排兌換'
            lines.append(f"#{r['id']}｜{r['list_name']}｜{r['prize_name']}｜{suffix}")
        await answer(i, '\n'.join(lines))

    @app_commands.command(name='抽獎商品領取', description='把自己抽中的商城商品領取到指定角色背包')
    @app_commands.describe(中獎編號='使用 /抽獎紀錄 查看', 角色編號='只能指定本人角色')
    async def claim(self, i: discord.Interaction, 中獎編號: int, 角色編號: int):
        if i.guild_id is None:
            return await answer(i, '⚠️ 請在伺服器內使用。')
        if 中獎編號 < 1 or 角色編號 < 1:
            return await answer(i, '⚠️ 編號必須是正整數。')
        await i.response.defer(ephemeral=True)
        try:
            fresh, name, cid = deliver(i.guild_id,i.user.id,中獎編號,角色編號)
            if fresh:
                await answer(i, f'🎁 已將「{name}」×1 放入角色 #{cid} 的背包。')
            else:
                await answer(i, f'ℹ️ 此筆獎勵已領取到角色 #{cid}，不能重複領取。')
        except (ValueError,sqlite3.Error) as exc:
            await answer(i, f'⚠️ {exc}')
        except Exception:
            LOG.exception('抽獎商品領取失敗')
            await answer(i, '⚠️ 領取失敗，資料未完成寫入，請聯絡官方。')


async def setup(bot: commands.Bot):
    await bot.add_cog(LotteryFulfillment(bot))
