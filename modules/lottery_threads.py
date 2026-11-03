"""討論串／論壇帖子名稱對應獎單的抽獎模組。"""
from __future__ import annotations

import random
import sqlite3
import logging
import re
import json

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
        list_cols = {r["name"] for r in db.execute("PRAGMA table_info(rp_lottery_lists)")}
        if "allowed_roles_json" not in list_cols:
            db.execute("ALTER TABLE rp_lottery_lists ADD COLUMN allowed_roles_json TEXT NOT NULL DEFAULT '[]'")
        cols = {r["name"] for r in db.execute("PRAGMA table_info(rp_lottery_prizes)")}
        if "unlimited" not in cols:
            db.execute("ALTER TABLE rp_lottery_prizes ADD COLUMN unlimited INTEGER NOT NULL DEFAULT 0")



def parse_prize_lines(raw: str):
    """每行：獎品名稱 [限量數字]；未寫數字為不限量。

    金幣範例：金幣500 10 / 500金幣 10，表示每份500金幣、限量10份。
    """
    result = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"(.+?)(?:[ \t]+([0-9]+))?", line)
        if not match:
            raise ValueError(f"第 {line_no} 行格式錯誤。")
        name = match.group(1).strip()
        count_text = match.group(2)
        if not 1 <= len(name) <= 100:
            raise ValueError(f"第 {line_no} 行獎品名稱需為 1～100 字。")
        unlimited = count_text is None
        count = 0 if unlimited else int(count_text)
        if not unlimited and not 1 <= count <= 100000:
            raise ValueError(f"第 {line_no} 行限量須介於 1～100000。")
        coin = re.fullmatch(r"金幣[：: ]?([0-9]+)|([0-9]+)[ ]?金幣", name)
        amount = int(coin.group(1) or coin.group(2)) if coin else 0
        if coin and not 1 <= amount <= 100000000:
            raise ValueError(f"第 {line_no} 行金幣額度無效。")
        result.append((name, "money" if coin else "gift", amount, count, int(unlimited)))
    if not result:
        raise ValueError("請至少填寫一種獎品。")
    if len(result) > 100:
        raise ValueError("單張獎單最多100種獎品。")
    return result


def create_list_with_prizes(gid, uid, name, raw, role_ids=None):
    init_lottery_db()
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("獎單名稱須為 1～100 字。")
    prizes = parse_prize_lines(raw)
    role_ids = sorted({int(x) for x in (role_ids or [])})
    with transaction() as db:
        cur = db.execute(
            "INSERT INTO rp_lottery_lists(guild_id,name,created_by,allowed_roles_json) VALUES(?,?,?,?)",
            (str(gid), name, str(uid), json.dumps(role_ids)),
        )
        for title, kind, amount, count, unlimited in prizes:
            db.execute(
                """INSERT INTO rp_lottery_prizes
                (lottery_id,name,prize_type,amount,remaining,unlimited)
                VALUES(?,?,?,?,?,?)""",
                (cur.lastrowid,title,kind,amount,count,unlimited),
            )
    return len(prizes)


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


def permitted_role_ids(lottery):
    try:
        values = json.loads(lottery["allowed_roles_json"] or "[]")
        if not isinstance(values, list):
            raise ValueError
        return {int(x) for x in values}
    except (KeyError, ValueError, TypeError):
        raise ValueError("獎單身分組設定異常，請通知群主。")


def set_lottery_roles(gid, lottery_name, role_ids):
    roles = sorted({int(x) for x in role_ids})
    with transaction() as db:
        changed = db.execute(
            "UPDATE rp_lottery_lists SET allowed_roles_json=? WHERE guild_id=? AND name=?",
            (json.dumps(roles), str(gid), lottery_name.strip()),
        )
        if changed.rowcount != 1:
            raise ValueError("此獎單尚未建立")


def make_draw(gid, uid, thread_id, list_id):
    """原子化選取獎品／扣庫存／記錄。相同使用者不能重抽。"""
    with transaction() as db:
        existing = db.execute(
            'SELECT * FROM rp_lottery_draws WHERE guild_id=? AND lottery_id=? AND user_id=?',
            (str(gid), int(list_id), str(uid)),
        ).fetchone()
        if existing:
            return dict(existing), False

        active = db.execute(
            "SELECT 1 FROM rp_lottery_lists WHERE id=? AND guild_id=? AND enabled=1",
            (int(list_id), str(gid)),
        ).fetchone()
        if active is None:
            raise ValueError('此獎單尚未建立')

        prizes = db.execute(
            """SELECT id,remaining,unlimited FROM rp_lottery_prizes
            WHERE lottery_id=? AND (remaining>0 OR unlimited=1) ORDER BY id""",
            (int(list_id),),
        ).fetchall()
        # 每種獎品有相同中獎機率，不限量品不扣庫存。
        if not prizes:
            raise ValueError('這張獎單的獎品已經抽完了。')
        pick = random.choice(prizes)
        chosen = int(pick['id'])
        if not pick['unlimited']:
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

    @app_commands.command(name='獎單建立', description='同時建立獎單及多行獎品')
    @app_commands.describe(名稱='與討論串／帖子名稱相同', 獎品清單='每行一種，例如：香水 10 或 懷錶（不限量）')
    async def create_list(self, i: discord.Interaction, 名稱: str, 獎品清單: str):
        if not is_owner(i):
            return await tell(i, '⛔ 只有群主能建立獎單。')
        try:
            n = create_list_with_prizes(i.guild_id, i.user.id, 名稱, 獎品清單)
        except sqlite3.IntegrityError:
            return await tell(i, '⚠️ 已有同名獎單。')
        except (ValueError, sqlite3.Error) as exc:
            return await tell(i, f'⚠️ {exc}')
        await tell(i, f'✅ 已建立獎單「{名稱.strip()}」，共 {n} 種獎品。')

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
                'SELECT name,prize_type,amount,remaining,unlimited FROM rp_lottery_prizes WHERE lottery_id=?',
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
                f'• {p["name"]} ×{"不限量" if p["unlimited"] else p["remaining"]}{suffix}'
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
        from modules.task_event_bridge import require_tutorial_channel, mark_tutorial_after_reply
        allowed, tutorial_character = await require_tutorial_channel(i, '抽獎')
        if not allowed:
            return
        if tutorial_character is not None:
            await i.response.send_message(
                '🎟️ 抽獎教學：正式抽獎必須到指定的獎單討論串使用 `/抽獎`。'
                '這次僅瀏覽說明，不抽取獎品，也不變更抽獎資格。', ephemeral=True,
            )
            mark_tutorial_after_reply(i, '抽獎', tutorial_character)
            return
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

        try:
            allowed_roles = permitted_role_ids(lottery)
            member = i.user
            if allowed_roles:
                # 權限由 Discord 即時身分組決定；擁有任一指定組即可抽。
                if not isinstance(member, discord.Member):
                    member = await i.guild.fetch_member(i.user.id)
                member_roles = {role.id for role in member.roles}
                if not (allowed_roles & member_roles):
                    return await tell(i, '⛔ 你沒有這張獎單允許的抽獎身分組。')
        except discord.HTTPException:
            return await tell(i, '⚠️ 無法確認你的身分組，請稍後再試。')
        except ValueError as exc:
            return await tell(i, f'⚠️ {exc}')

        await i.response.defer(ephemeral=True, thinking=True)

        try:
            draw, new = make_draw(
                i.guild_id,
                i.user.id,
                i.channel.id,
                lottery['id'],
            )
            item = draw_detail(draw['id'])

            if item is None:
                raise ValueError('找不到中獎紀錄，請聯絡官方。')

            if item['state'] == 'done':
                return await tell(
                    i,
                    f'🎟️ 你已抽過此獎單，獎品是：{discord.utils.escape_mentions(item["prize_name"])}。',
                )

            if item['prize_type'] == 'money':
                # unique_key 可防止重試時重複入帳。
                issue_system_reward(
                    str(i.guild_id),
                    f'lottery:{i.guild_id}:{item["id"]}',
                    'player',
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
                    '已保存中獎紀錄；請使用 `/抽獎紀錄` 查詢領取狀態。'
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
