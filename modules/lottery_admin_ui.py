"""官方抽獎獎單按鈕管理 UI。沿用 modules.lottery_threads 資料表。"""
from __future__ import annotations

import sqlite3
import discord
from discord.ext import commands
from core.database import get_connection, transaction
from modules.lottery_threads import init_lottery_db


def allowed(i: discord.Interaction) -> bool:
    return bool(i.guild and i.guild.owner_id == i.user.id)


async def reply(i: discord.Interaction, message: str):
    if i.response.is_done():
        await i.followup.send(message[:1900], ephemeral=True)
    else:
        await i.response.send_message(message[:1900], ephemeral=True)


def get_list(gid: int, name: str):
    with get_connection() as db:
        return db.execute('SELECT * FROM rp_lottery_lists WHERE guild_id=? AND name=?', (str(gid), name.strip())).fetchone()


def list_text(gid: int):
    with get_connection() as db:
        rows = db.execute('''SELECT l.id,l.name,l.enabled,COUNT(p.id) AS prize_types,
          COALESCE(SUM(p.remaining),0) AS remaining
          FROM rp_lottery_lists l LEFT JOIN rp_lottery_prizes p ON p.lottery_id=l.id
          WHERE l.guild_id=? GROUP BY l.id ORDER BY l.id DESC LIMIT 25''', (str(gid),)).fetchall()
    if not rows:
        return '目前尚無抽獎獎單。'
    return '\n'.join(f"#{r['id']}｜{r['name']}｜{'啟用' if r['enabled'] else '停用'}｜獎品種類 {r['prize_types']}｜剩餘 {r['remaining']} 份" for r in rows)


class CreateLottery(discord.ui.Modal, title='🎟️ 建立抽獎獎單'):
    name = discord.ui.TextInput(label='獎單名稱（需與討論串／帖子名稱相同）', max_length=100)

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, '⛔ 只有群主能建立獎單。')
        name = str(self.name.value).strip()
        if not name:
            return await reply(i, '⚠️ 獎單名稱不可留空。')
        try:
            init_lottery_db()
            with transaction() as db:
                db.execute('INSERT INTO rp_lottery_lists(guild_id,name,created_by) VALUES(?,?,?)', (str(i.guild_id), name, str(i.user.id)))
        except sqlite3.IntegrityError:
            return await reply(i, '⚠️ 已有同名獎單，請改用獎品設定。')
        await reply(i, f'✅ 已建立「{discord.utils.escape_mentions(name)}」。請繼續新增獎品。')


class AddLotteryPrize(discord.ui.Modal, title='🎁 新增獎單獎品'):
    list_name = discord.ui.TextInput(label='獎單名稱', max_length=100)
    prize = discord.ui.TextInput(label='獎品名稱', max_length=100)
    kind = discord.ui.TextInput(label='獎品類型（money 或 gift）', max_length=5, placeholder='money')
    quantity = discord.ui.TextInput(label='獎品份數（正整數）', max_length=7, placeholder='10')
    amount = discord.ui.TextInput(label='每份金幣金額（禮物填 0）', max_length=10, default='0')

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, '⛔ 只有群主能管理獎品。')
        try:
            name = str(self.list_name.value).strip()
            title = str(self.prize.value).strip()
            kind = str(self.kind.value).strip().lower()
            qty = int(str(self.quantity.value).strip())
            amount = int(str(self.amount.value).strip())
            if kind not in ('money', 'gift'):
                raise ValueError('類型只能填 money（金幣）或 gift（待兌換禮物）。')
            if not (1 <= qty <= 100000):
                raise ValueError('份數必須為 1 至 100000。')
            if not title:
                raise ValueError('獎品名稱不可空白。')
            if (kind == 'money' and not 1 <= amount <= 100000000) or (kind == 'gift' and amount != 0):
                raise ValueError('金幣類型須設定正金額；禮物類型的金額須為 0。')
            with transaction() as db:
                lottery = db.execute('SELECT id FROM rp_lottery_lists WHERE guild_id=? AND name=?', (str(i.guild_id), name)).fetchone()
                if lottery is None:
                    raise ValueError('此獎單尚未建立')
                db.execute('INSERT INTO rp_lottery_prizes(lottery_id,name,prize_type,amount,remaining) VALUES(?,?,?,?,?)', (lottery['id'], title, kind, amount, qty))
            await reply(i, f'✅ 「{discord.utils.escape_mentions(name)}」新增獎品 {discord.utils.escape_mentions(title)} ×{qty}。')
        except (ValueError, sqlite3.Error) as e:
            await reply(i, f'⚠️ {e}')


class LotteryDetail(discord.ui.Modal, title='🔎 查看獎單獎品'):
    name = discord.ui.TextInput(label='獎單名稱', max_length=100)

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, '⛔ 僅群主可使用此面板。')
        row = get_list(i.guild_id, str(self.name.value))
        if row is None:
            return await reply(i, '此獎單尚未建立')
        with get_connection() as db:
            prizes = db.execute('SELECT name,prize_type,amount,remaining FROM rp_lottery_prizes WHERE lottery_id=? ORDER BY id', (row['id'],)).fetchall()
        lines = [f"🎟️ {discord.utils.escape_mentions(row['name'])}｜{'啟用' if row['enabled'] else '停用'}"]
        lines.extend(f"• {discord.utils.escape_mentions(p['name'])}｜{'金幣 '+str(p['amount']) if p['prize_type']=='money' else '禮物待兌換'}｜剩餘 {p['remaining']}" for p in prizes)
        await reply(i, '\n'.join(lines) if prizes else '此獎單尚未設定獎品。')


class ToggleLottery(discord.ui.Modal, title='⚙️ 開啟／關閉獎單'):
    name = discord.ui.TextInput(label='獎單名稱', max_length=100)
    state = discord.ui.TextInput(label='操作（啟用 或 停用）', max_length=2)

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, '⛔ 僅群主可管理。')
        name = str(self.name.value).strip()
        state = str(self.state.value).strip()
        if state not in ('啟用', '停用'):
            return await reply(i, '⚠️ 操作只能填「啟用」或「停用」。')
        with transaction() as db:
            cur = db.execute('UPDATE rp_lottery_lists SET enabled=? WHERE guild_id=? AND name=?', (1 if state == '啟用' else 0, str(i.guild_id), name))
        await reply(i, f'✅ 「{discord.utils.escape_mentions(name)}」已{state}。' if cur.rowcount else '此獎單尚未建立')


class LotteryAdminView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.owner_id = i.user.id
        self.guild_id = i.guild_id

    async def interaction_check(self, i):
        if i.user.id != self.owner_id or i.guild_id != self.guild_id or not allowed(i):
            await reply(i, '⛔ 這不是你的管理面板。')
            return False
        return True

    @discord.ui.button(label='🎟️ 建立獎單', style=discord.ButtonStyle.primary, row=0)
    async def create(self, i, b):
        await i.response.send_modal(CreateLottery())

    @discord.ui.button(label='🎁 新增獎品', style=discord.ButtonStyle.success, row=0)
    async def prize(self, i, b):
        await i.response.send_modal(AddLotteryPrize())

    @discord.ui.button(label='📋 獎單清單', style=discord.ButtonStyle.secondary, row=1)
    async def listing(self, i, b):
        await reply(i, list_text(i.guild_id))

    @discord.ui.button(label='🔎 查看獎品', style=discord.ButtonStyle.secondary, row=1)
    async def details(self, i, b):
        await i.response.send_modal(LotteryDetail())

    @discord.ui.button(label='⚙️ 啟用／停用', style=discord.ButtonStyle.secondary, row=1)
    async def toggle(self, i, b):
        await i.response.send_modal(ToggleLottery())

    @discord.ui.button(label='↩️ 返回內容建立', style=discord.ButtonStyle.secondary, row=2)
    async def back(self, i, b):
        from modules.content_builder import ContentView
        await i.response.edit_message(content='✍️ **內容建立中心**', view=ContentView(i))


class LotteryAdminButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='🎟️ 抽獎管理', style=discord.ButtonStyle.primary, row=2)

    async def callback(self, i):
        if not allowed(i):
            return await reply(i, '⛔ 只有群主可以管理抽獎。')
        init_lottery_db()
        await i.response.edit_message(content='🎟️ **抽獎獎單管理**\n獎單名稱必須與討論串或論壇帖子名稱一致。\n玩家只可在討論串／帖子內使用 `/抽獎`。', view=LotteryAdminView(i))


async def setup(bot: commands.Bot):
    # 此模組只有 UI，提供標準擴充模組載入入口。
    init_lottery_db()
