
"""官方抽獎獎單按鈕管理 UI。沿用 modules.lottery_threads 資料表。"""
from __future__ import annotations

import sqlite3
import discord
from discord.ext import commands
from core.database import get_connection, transaction
from modules.lottery_threads import init_lottery_db, create_list_with_prizes


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
          COALESCE(SUM(p.remaining),0) AS remaining,
          COALESCE(SUM(p.unlimited),0) AS unlimited_count
          FROM rp_lottery_lists l LEFT JOIN rp_lottery_prizes p ON p.lottery_id=l.id
          WHERE l.guild_id=? GROUP BY l.id ORDER BY l.id DESC LIMIT 25''', (str(gid),)).fetchall()
    if not rows:
        return '目前尚無抽獎獎單。'
    return '\n'.join(f"#{r['id']}｜{r['name']}｜{'啟用' if r['enabled'] else '停用'}｜獎品種類 {r['prize_types']}｜剩餘 {r['remaining']} 份＋不限量 {r['unlimited_count']} 種" for r in rows)


class CreateLottery(discord.ui.Modal, title='🎟️ 建立獎單與獎品'):
    name = discord.ui.TextInput(label='獎單名稱（對應討論串名稱）', max_length=100)
    prizes = discord.ui.TextInput(
        label='獎品清單：每行一種，數字為限量',
        style=discord.TextStyle.paragraph,
        max_length=4000,
        placeholder='香水 10\n限定懷錶\n金幣500 5',
    )

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, '⛔ 只有群主能建立獎單。')
        try:
            count = create_list_with_prizes(
                i.guild_id, i.user.id, str(self.name.value), str(self.prizes.value)
            )
        except sqlite3.IntegrityError:
            return await reply(i, '⚠️ 已有同名獎單。')
        except (ValueError, sqlite3.Error) as exc:
            return await reply(i, f'⚠️ {exc}')
        await reply(i, f'✅ 已建立「{discord.utils.escape_mentions(str(self.name.value).strip())}」，包含 {count} 種獎品。')


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
            prizes = db.execute('SELECT name,prize_type,amount,remaining,unlimited FROM rp_lottery_prizes WHERE lottery_id=? ORDER BY id', (row['id'],)).fetchall()
        lines = [f"🎟️ {discord.utils.escape_mentions(row['name'])}｜{'啟用' if row['enabled'] else '停用'}"]
        lines.extend(f"• {discord.utils.escape_mentions(p['name'])}｜{'金幣 '+str(p['amount']) if p['prize_type']=='money' else '禮物待兌換'}｜剩餘 {'不限量' if p['unlimited'] else p['remaining']}" for p in prizes)
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


# ===== 商城商品綁定與中獎查帳 =====

def recent_products(gid):
    with get_connection() as db:
        return db.execute(
            """SELECT id,name FROM shop_products
               WHERE guild_id=? AND active=1
               ORDER BY id DESC LIMIT 25""",
            (str(gid),),
        ).fetchall()


def lottery_gift_prizes(gid):
    with get_connection() as db:
        return db.execute(
            """SELECT p.id, p.name, l.name AS lottery_name
               FROM rp_lottery_prizes p
               JOIN rp_lottery_lists l ON l.id=p.lottery_id
               WHERE l.guild_id=? AND p.prize_type='gift'
               ORDER BY p.id DESC LIMIT 25""",
            (str(gid),),
        ).fetchall()


def draw_report(gid, only_unclaimed=False):
    from modules.lottery_fulfillment import init_fulfillment_db
    init_fulfillment_db()
    with get_connection() as db:
        rows = db.execute(
            """SELECT d.id,d.user_id,d.state,l.name AS lottery_name,
                      p.name AS prize_name,p.prize_type,
                      b.product_id,deliv.character_id
               FROM rp_lottery_draws d
               JOIN rp_lottery_lists l ON l.id=d.lottery_id
               JOIN rp_lottery_prizes p ON p.id=d.prize_id
               LEFT JOIN rp_lottery_product_bindings b
                 ON b.guild_id=d.guild_id AND b.prize_id=d.prize_id
               LEFT JOIN rp_lottery_deliveries deliv
                 ON deliv.guild_id=d.guild_id AND deliv.draw_id=d.id
               WHERE d.guild_id=?
                 AND (?=0 OR (p.prize_type='gift' AND deliv.draw_id IS NULL))
               ORDER BY d.id DESC LIMIT 25""",
            (str(gid), int(bool(only_unclaimed))),
        ).fetchall()
    if not rows:
        return '沒有符合條件的中獎紀錄。'
    lines = ['🎟️ '+('**待領取獎品（最近 25 筆）**' if only_unclaimed else '**中獎紀錄（最近 25 筆）**')]
    for r in rows:
        if r['prize_type']=='money':
            status='已入帳' if r['state']=='done' else '待發放／待重試'
        elif r['character_id'] is not None:
            status=f"已領取至角色 #{r['character_id']}"
        elif r['product_id'] is not None:
            status='待玩家領取'
        else:
            status='待官方綁定商品／兌換'
        lines.append(f"#{r['id']}｜<@{r['user_id']}>｜{discord.utils.escape_mentions(r['lottery_name'])}｜{discord.utils.escape_mentions(r['prize_name'])}｜{status}")
    return '\n'.join(lines)[:1850]


class BindProductSelect(discord.ui.Select):
    def __init__(self, prize_id, gid):
        self.prize_id=int(prize_id)
        products=recent_products(gid)
        choices=[discord.SelectOption(label=f"#{p['id']} {p['name']}"[:100],value=str(p['id'])) for p in products]
        if not choices:
            choices=[discord.SelectOption(label='沒有已上架的商城商品',value='none')]
        super().__init__(placeholder='選擇要綁定的商城商品（最近 25 項）',options=choices,disabled=not bool(products))

    async def callback(self,i):
        if not allowed(i):
            return await reply(i,'⛔ 僅群主可以綁定商品。')
        from modules.lottery_fulfillment import bind_prize
        try:
            a,b=bind_prize(i.guild_id,i.user.id,self.prize_id,int(self.values[0]))
        except (ValueError,sqlite3.Error) as exc:
            return await reply(i,f'⚠️ {exc}')
        await i.response.edit_message(content=f'✅ 獎品「{discord.utils.escape_mentions(a)}」已綁定商城商品「{discord.utils.escape_mentions(b)}」。',view=None)


class BindProductView(discord.ui.View):
    def __init__(self, i, prize_id):
        super().__init__(timeout=300)
        self.user_id=i.user.id
        self.guild_id=i.guild_id
        self.add_item(BindProductSelect(prize_id,i.guild_id))

    async def interaction_check(self,i):
        if i.user.id!=self.user_id or i.guild_id!=self.guild_id or not allowed(i):
            await reply(i,'⛔ 此操作不屬於你。')
            return False
        return True


class GiftPrizePicker(discord.ui.Select):
    def __init__(self,gid):
        prizes=lottery_gift_prizes(gid)
        choices=[discord.SelectOption(label=f"#{p['id']} {p['name']}"[:100],description=p['lottery_name'][:100],value=str(p['id'])) for p in prizes]
        if not choices:
            choices=[discord.SelectOption(label='目前沒有禮物類型獎品',value='none')]
        super().__init__(placeholder='先選要綁定的抽獎禮物（最近 25 項）',options=choices,disabled=not bool(prizes))

    async def callback(self,i):
        if not allowed(i):
            return await reply(i,'⛔ 僅群主可以綁定商品。')
        await i.response.edit_message(content='🛍️ 請選擇對應的商城商品：',view=BindProductView(i,int(self.values[0])))


class GiftPrizePickerView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=300)
        self.user_id=i.user.id
        self.guild_id=i.guild_id
        self.add_item(GiftPrizePicker(i.guild_id))

    async def interaction_check(self,i):
        if i.user.id!=self.user_id or i.guild_id!=self.guild_id or not allowed(i):
            await reply(i,'⛔ 此操作不屬於你。')
            return False
        return True


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

    @discord.ui.button(label='🎟️ 建立獎單＋獎品', style=discord.ButtonStyle.primary, row=0)
    async def create(self, i, b):
        await i.response.send_modal(CreateLottery())

    @discord.ui.button(label='📋 獎單清單', style=discord.ButtonStyle.secondary, row=1)
    async def listing(self, i, b):
        await reply(i, list_text(i.guild_id))

    @discord.ui.button(label='🔎 查看獎品', style=discord.ButtonStyle.secondary, row=1)
    async def details(self, i, b):
        await i.response.send_modal(LotteryDetail())

    @discord.ui.button(label='⚙️ 啟用／停用', style=discord.ButtonStyle.secondary, row=1)
    async def toggle(self, i, b):
        await i.response.send_modal(ToggleLottery())

    @discord.ui.button(label='🛍️ 綁定商城商品', style=discord.ButtonStyle.primary, row=2)
    async def bind_product(self, i, b):
        from modules.lottery_fulfillment import init_fulfillment_db
        init_fulfillment_db()
        await i.response.send_message('🎁 選擇要綁定的禮物獎品：',view=GiftPrizePickerView(i),ephemeral=True)

    @discord.ui.button(label='📜 中獎名單', style=discord.ButtonStyle.secondary, row=2)
    async def winners(self, i, b):
        await reply(i,draw_report(i.guild_id))

    @discord.ui.button(label='📦 未領獎清單', style=discord.ButtonStyle.secondary, row=2)
    async def unclaimed(self, i, b):
        await reply(i,draw_report(i.guild_id,True))

    @discord.ui.button(label='↩️ 返回內容建立', style=discord.ButtonStyle.secondary, row=3)
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
