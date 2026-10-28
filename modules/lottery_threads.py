
"""討論串／論壇帖子名稱對應獎單的抽獎模組。"""
from __future__ import annotations

import random
import sqlite3
import logging

import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection, transaction
from core.economy import issue_system_reward

LOG = logging.getLogger(__name__)


def init_lottery_db():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_lottery_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,name)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_lottery_prizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lottery_id INTEGER NOT NULL REFERENCES rp_lottery_lists(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            prize_type TEXT NOT NULL CHECK(prize_type IN ('money','gift')),
            amount INTEGER NOT NULL DEFAULT 0,
            remaining INTEGER NOT NULL DEFAULT 0,
            CHECK(amount >= 0), CHECK(remaining >= 0)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_lottery_draws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            lottery_id INTEGER NOT NULL REFERENCES rp_lottery_lists(id),
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            prize_id INTEGER NOT NULL REFERENCES rp_lottery_prizes(id),
            state TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,lottery_id,user_id)
        )""")


def is_owner(i: discord.Interaction) -> bool:
    return bool(i.guild and i.guild.owner_id == i.user.id)


async def tell(i: discord.Interaction, content: str):
    if i.response.is_done():
        await i.followup.send(content[:1900], ephemeral=True)
    else:
        await i.response.send_message(content[:1900], ephemeral=True)


def find_list(gid, name):
    with get_connection() as db:
        return db.execute(
            'SELECT * FROM rp_lottery_lists WHERE guild_id=? AND name=? AND enabled=1',
            (str(gid), name.strip()),
        ).fetchone()


def make_draw(gid, uid, thread_id, list_id):
    """原子化選取獎品／扣庫存／記錄。相同使用者不能重抽。"""
    with transaction() as db:
        existing = db.execute(
            'SELECT * FROM rp_lottery_draws WHERE guild_id=? AND lottery_id=? AND user_id=?',
            (str(gid), int(list_id), str(uid)),
        ).fetchone()
        if existing:
            return dict(existing), False

        prizes = db.execute(
            'SELECT id,remaining FROM rp_lottery_prizes WHERE lottery_id=? AND remaining>0 ORDER BY id',
            (int(list_id),),
        ).fetchall()

        total = sum(int(p['remaining']) for p in prizes)
        if not total:
            raise ValueError('這張獎單的獎品已經抽完了。')

        index = random.randrange(total)
        chosen = None

        for p in prizes:
            if index < p['remaining']:
                chosen = int(p['id'])
                break
            index -= p['remaining']

        cur = db.execute(
            'UPDATE rp_lottery_prizes SET remaining=remaining-1 WHERE id=? AND remaining>0',
            (chosen,),
        )
        if cur.rowcount != 1:
            raise ValueError('獎品庫存變動，請重新嘗試。')

        cur = db.execute(
            '''INSERT INTO rp_lottery_draws(guild_id,lottery_id,user_id,thread_id,prize_id)
               VALUES(?,?,?,?,?)''',
            (str(gid), int(list_id), str(uid), str(thread_id), chosen),
        )
        return {
            'id': cur.lastrowid,
            'guild_id': str(gid),
            'lottery_id': int(list_id),
            'user_id': str(uid),
            'thread_id': str(thread_id),
            'prize_id': chosen,
            'state': 'pending',
        }, True


def draw_detail(draw_id):
    with get_connection() as db:
        return db.execute(
            '''SELECT d.*,p.name AS prize_name,p.prize_type,p.amount
               FROM rp_lottery_draws d JOIN rp_lottery_prizes p ON p.id=d.prize_id
               WHERE d.id=?''', (int(draw_id),)
        ).fetchone()


def mark_done(draw_id):
    with transaction() as db:
        db.execute(
            "UPDATE rp_lottery_draws SET state='done' WHERE id=?",
            (int(draw_id),),
        )


class LotteryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        init_lottery_db()

    @app_commands.command(
        name='獎單建立',
        description='群主建立獎單，名稱須與討論串或論壇帖子相同',
    )
    @app_commands.describe(名稱='必須與討論串／帖子名稱完全相同')
    async def create_list(self, i: discord.Interaction, 名稱: str):
        if not is_owner(i):
            return await tell(i, '⛔ 只有群主能建立獎單。')

        name = 名稱.strip()
        if not 1 <= len(name) <= 100:
            return await tell(i, '⚠️ 獎單名稱須為 1～100 字。')

        try:
            with transaction() as db:
                db.execute(
                    'INSERT INTO rp_lottery_lists(guild_id,name,created_by) VALUES(?,?,?)',
                    (str(i.guild_id), name, str(i.user.id)),
                )
        except sqlite3.IntegrityError:
            return await tell(
                i, '⚠️ 同一伺服器已有同名獎單，請使用原獎單。'
            )

        await tell(
            i,
            f'✅ 已建立獎單「{name}」。接著使用 `/獎單獎品` 加入獎品。',
        )

    @app_commands.command(
        name='獎單獎品',
        description='群主為獎單新增獎品與數量',
    )
    @app_commands.describe(
        獎單名稱='對應討論串名稱',
        獎品名稱='中獎顯示名稱',
        類型='金幣會直接入帳；禮物會保存中獎領取紀錄',
        數量='可抽出的份數',
        金額='每份金幣獎勵；禮物填0',
    )
    @app_commands.choices(類型=[
        app_commands.Choice(name='金幣（立即發放）', value='money'),
        app_commands.Choice(name='禮物／物品（記錄待兌換）', value='gift'),
    ])
    async def add_prize(
        self,
        i: discord.Interaction,
        獎單名稱: str,
        獎品名稱: str,
        類型: app_commands.Choice[str],
        數量: app_commands.Range[int, 1, 100000],
        金額: app_commands.Range[int, 0, 100000000] = 0,
    ):
        if not is_owner(i):
            return await tell(i, '⛔ 只有群主能設定獎品。')

        lottery = find_list(i.guild_id, 獎單名稱)
        if lottery is None:
            return await tell(i, '此獎單尚未建立')

        if not 1 <= len(獎品名稱.strip()) <= 100:
            return await tell(i, '⚠️ 獎品名稱須為 1～100 字。')

        if 類型.value == 'money' and 金額 <= 0:
            return await tell(
                i, '⚠️ 金幣獎勵的金額必須大於零。'
            )

        if 類型.value == 'gift' and 金額 != 0:
            return await tell(
                i, '⚠️ 禮物／物品不使用金幣金額，請填 0。'
            )

        with transaction() as db:
            db.execute(
                '''INSERT INTO rp_lottery_prizes(lottery_id,name,prize_type,amount,remaining)
                   VALUES(?,?,?,?,?)''',
                (
                    int(lottery['id']),
                    獎品名稱.strip(),
                    類型.value,
                    金額,
                    數量,
                ),
            )

        await tell(i, f'✅ 已加入「{獎品名稱}」×{數量}。')

    @app_commands.command(
        name='獎單查看',
        description='查看指定獎單獎品及剩餘數量',
    )
    @app_commands.describe(獎單名稱='獎單名稱')
    async def show_list(
        self,
        i: discord.Interaction,
        獎單名稱: str,
    ):
        if i.guild_id is None:
            return await tell(i, '⚠️ 只能在伺服器使用。')

        lottery = find_list(i.guild_id, 獎單名稱)
        if lottery is None:
            return await tell(i, '此獎單尚未建立')

        with get_connection() as db:
            prizes = db.execute(
                'SELECT name,prize_type,amount,remaining FROM rp_lottery_prizes WHERE lottery_id=?',
                (int(lottery['id']),),
            ).fetchall()

        lines = [f'🎟️ 獎單：{lottery["name"]}']

        for p in prizes:
            suffix = (
                f'（每份 {p["amount"]} 金幣）'
                if p['prize_type'] == 'money'
                else '（待兌換禮物）'
            )
            lines.append(
                f'• {p["name"]} ×{p["remaining"]}{suffix}'
            )

        await tell(
            i,
            '\n'.join(lines)
            if prizes
            else '目前這張獎單尚未加入獎品。',
        )

    @app_commands.command(
        name='抽獎',
        description='在討論串或論壇帖子內，按名稱對應獎單抽獎',
    )
    async def draw(self, i: discord.Interaction):
        if (
            i.guild_id is None
            or not isinstance(i.channel, discord.Thread)
        ):
            return await tell(
                i,
                '⚠️ 請在討論串或論壇帖子內使用 `/抽獎`。',
            )

        lottery = find_list(i.guild_id, i.channel.name)

        if lottery is None:
            return await tell(i, '此獎單尚未建立')

        await i.response.defer(ephemeral=True, thinking=True)

        try:
            draw, new = make_draw(
                i.guild_id,
                i.user.id,
                i.channel.id,
                lottery['id'],
            )
            item = draw_detail(draw['id'])

            if item['state'] == 'done':
                return await tell(
                    i,
                    f'🎟️ 你已抽過此獎單，獎品是：{item["prize_name"]}。',
                )

            if item['prize_type'] == 'money':
                # unique_key 可防止重試時重複入帳。
                issue_system_reward(
                    str(i.guild_id),
                    f'lottery:{i.guild_id}:{item["id"]}',
                    'user',
                    str(i.user.id),
                    int(item['amount']),
                    (
                        f'抽獎獎勵：{lottery["name"]}'
                        f'／{item["prize_name"]}'
                    ),
                )

                mark_done(item['id'])

                return await tell(
                    i,
                    (
                        f'🎉 抽中「{item["prize_name"]}」！'
                        f'已發放 {item["amount"]} 金幣。'
                    ),
                )

            # 物品先記錄中獎，尚未寫入商城背包。
            mark_done(item['id'])
            return await tell(
                i,
                (
                    f'🎉 抽中「{item["prize_name"]}」！'
                    '已保存中獎紀錄，待官方兌換。'
                ),
            )

        except (ValueError, sqlite3.Error) as exc:
            LOG.exception('抽獎資料庫錯誤')
            await tell(i, f'⚠️ 抽獎失敗：{exc}')

        except Exception:
            LOG.exception(
                '抽獎發獎失敗；中獎紀錄保留 pending 供重試'
            )
            await tell(
                i,
                (
                    '⚠️ 獎勵暫時無法入帳。'
                    '中獎結果已保留，'
                    '請稍後在同一獎單重新使用 `/抽獎`。'
                ),
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(LotteryCog(bot))
