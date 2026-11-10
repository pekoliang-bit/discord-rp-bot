"""官方抽獎管理介面：獎單、獎品、身分組、商品與查帳。"""
from __future__ import annotations

import json
import sqlite3
import discord
from discord.ext import commands

from core.database import get_connection, transaction
from modules.lottery_threads import (
    init_lottery_db,
    create_list_with_prizes,
    set_lottery_roles,
)


def allowed(i):
    return bool(i.guild and i.guild.owner_id == i.user.id)


def clean(text):
    return discord.utils.escape_mentions(str(text))


async def reply(i, message):
    if i.response.is_done():
        await i.followup.send(message[:1900], ephemeral=True)
    else:
        await i.response.send_message(message[:1900], ephemeral=True)


def get_list(gid, name):
    with get_connection() as db:
        return db.execute(
            """SELECT * FROM rp_lottery_lists
               WHERE guild_id=? AND name=?""",
            (str(gid), name.strip()),
        ).fetchone()


def list_text(gid):
    with get_connection() as db:
        rows = db.execute(
            """SELECT l.id,l.name,l.enabled,
                      COUNT(p.id) AS kinds,
                      COALESCE(SUM(p.remaining),0) AS remaining,
                      COALESCE(SUM(p.unlimited),0) AS unlimited
               FROM rp_lottery_lists l
               LEFT JOIN rp_lottery_prizes p
                 ON p.lottery_id=l.id
               WHERE l.guild_id=?
               GROUP BY l.id
               ORDER BY l.id DESC LIMIT 25""",
            (str(gid),),
        ).fetchall()

    if not rows:
        return "目前尚無抽獎獎單。"

    return "\n".join(
        f"#{r['id']}｜{clean(r['name'])}｜"
        f"{'啟用' if r['enabled'] else '停用'}｜"
        f"獎品 {r['kinds']} 種｜"
        f"限量剩餘 {r['remaining']}｜"
        f"不限量 {r['unlimited']} 種"
        for r in rows
    )


# ======================================
# 身分組選單
# ======================================

class RoleChoice(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(
            placeholder="選擇可抽獎身分組（不選＝所有人）",
            min_values=0,
            max_values=25,
        )

    async def callback(self, i):
        self.view.selected_roles = [
            role.id for role in self.values
        ]

        names = [
            role.name for role in self.values
        ]

        text = (
            "、".join(clean(name) for name in names)
            if names else "所有人"
        )

        await reply(
            i,
            f"✅ 已選擇：{text}\n"
            "接著按下方按鈕繼續。",
        )


class RoleCreateView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.owner_id = i.user.id
        self.guild_id = i.guild_id
        self.selected_roles = []
        self.add_item(RoleChoice())

    async def interaction_check(self, i):
        if (
            i.user.id != self.owner_id
            or i.guild_id != self.guild_id
            or not allowed(i)
        ):
            await reply(i, "⛔ 這不是你的操作面板。")
            return False
        return True

    @discord.ui.button(
        label="📝 填寫獎單與獎品",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def enter(self, i, button):
        await i.response.send_modal(
            CreateLottery(self.selected_roles)
        )


class CreateLottery(
    discord.ui.Modal,
    title="🎟️ 建立獎單與獎品",
):
    name = discord.ui.TextInput(
        label="獎單名稱（對應討論串／帖子名稱）",
        max_length=100,
    )

    prizes = discord.ui.TextInput(
        label="獎品清單（一行一種，數字為限量）",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        placeholder=(
            "限定香水 10\n"
            "紀念懷錶\n"
            "金幣500 5"
        ),
    )

    def __init__(self, role_ids=None):
        super().__init__()
        self.role_ids = list(role_ids or [])

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(
                i, "⛔ 只有群主能建立獎單。"
            )

        try:
            count = create_list_with_prizes(
                i.guild_id,
                i.user.id,
                str(self.name.value),
                str(self.prizes.value),
                self.role_ids,
            )
        except sqlite3.IntegrityError:
            return await reply(
                i, "⚠️ 已有同名獎單。"
            )
        except (ValueError, sqlite3.Error) as exc:
            return await reply(i, f"⚠️ {exc}")

        rule = (
            f"{len(self.role_ids)} 個指定身分組"
            if self.role_ids else "所有人"
        )

        await reply(
            i,
            f"✅ 已建立「{clean(self.name.value)}」\n"
            f"獎品種類：{count}\n"
            f"可抽獎資格：{rule}",
        )


# ======================================
# 修改既有獎單身分組
# ======================================

class EditRoleModal(
    discord.ui.Modal,
    title="設定獎單抽獎資格",
):
    name = discord.ui.TextInput(
        label="要修改的獎單名稱",
        max_length=100,
    )

    def __init__(self, role_ids):
        super().__init__()
        self.role_ids = list(role_ids)

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, "⛔ 只有群主可修改。")

        try:
            set_lottery_roles(
                i.guild_id,
                str(self.name.value),
                self.role_ids,
            )
        except (ValueError, sqlite3.Error) as exc:
            return await reply(i, f"⚠️ {exc}")

        result = (
            f"已限制為 {len(self.role_ids)} 個身分組"
            if self.role_ids else "已開放所有人抽獎"
        )

        await reply(i, f"✅ {result}。")


class EditRoleView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.owner_id = i.user.id
        self.guild_id = i.guild_id
        self.selected_roles = []
        self.add_item(RoleChoice())

    async def interaction_check(self, i):
        if (
            i.user.id != self.owner_id
            or i.guild_id != self.guild_id
            or not allowed(i)
        ):
            await reply(i, "⛔ 這不是你的管理面板。")
            return False
        return True

    @discord.ui.button(
        label="📝 輸入要修改的獎單",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def save(self, i, button):
        await i.response.send_modal(
            EditRoleModal(self.selected_roles)
        )


# ======================================
# 獎單查詢
# ======================================

class LotteryDetail(
    discord.ui.Modal,
    title="🔎 查看獎單",
):
    name = discord.ui.TextInput(
        label="獎單名稱",
        max_length=100,
    )

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, "⛔ 群主專用。")

        row = get_list(i.guild_id, str(self.name.value))
        if row is None:
            return await reply(i, "此獎單尚未建立")

        with get_connection() as db:
            prizes = db.execute(
                """SELECT id,name,prize_type,amount,
                          remaining,unlimited
                   FROM rp_lottery_prizes
                   WHERE lottery_id=?
                   ORDER BY id""",
                (row["id"],),
            ).fetchall()

        roles = json.loads(
            row["allowed_roles_json"] or "[]"
        )

        role_text = (
            "、".join(f"<@&{rid}>" for rid in roles)
            if roles else "所有人"
        )

        lines = [
            f"🎟️ **{clean(row['name'])}**",
            f"狀態：{'啟用' if row['enabled'] else '停用'}",
            f"可抽獎身分組：{role_text}",
            "",
        ]

        for p in prizes:
            amount_text = (
                "不限量"
                if p["unlimited"]
                else f"剩餘 {p['remaining']} 份"
            )
            kind_text = (
                f"金幣 {p['amount']}"
                if p["prize_type"] == "money"
                else "商品／禮物"
            )

            lines.append(
                f"#{p['id']} {clean(p['name'])}"
                f"｜{kind_text}｜{amount_text}"
            )

        if not prizes:
            lines.append("尚未設定獎品。")

        await reply(i, "\n".join(lines))


class ToggleLottery(
    discord.ui.Modal,
    title="⚙️ 啟用／停用獎單",
):
    name = discord.ui.TextInput(
        label="獎單名稱",
        max_length=100,
    )
    state = discord.ui.TextInput(
        label="操作（啟用／停用）",
        max_length=2,
    )

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, "⛔ 群主專用。")

        state = str(self.state.value).strip()
        name = str(self.name.value).strip()

        if state not in ("啟用", "停用"):
            return await reply(
                i, "⚠️ 請輸入「啟用」或「停用」。"
            )

        with transaction() as db:
            result = db.execute(
                """UPDATE rp_lottery_lists
                   SET enabled=?
                   WHERE guild_id=? AND name=?""",
                (
                    1 if state == "啟用" else 0,
                    str(i.guild_id),
                    name,
                ),
            )

        await reply(
            i,
            f"✅ 獎單已{state}。"
            if result.rowcount
            else "此獎單尚未建立",
        )


# ======================================
# 商城商品綁定
# ======================================

def recent_products(gid):
    with get_connection() as db:
        return db.execute(
            """SELECT id,name FROM shop_products
               WHERE guild_id=? AND active=1
               ORDER BY id DESC LIMIT 25""",
            (str(gid),),
        ).fetchall()


def gift_prizes(gid):
    with get_connection() as db:
        return db.execute(
            """SELECT p.id,p.name,l.name AS lottery_name
               FROM rp_lottery_prizes p
               JOIN rp_lottery_lists l
                 ON l.id=p.lottery_id
               WHERE l.guild_id=?
                 AND p.prize_type='gift'
               ORDER BY p.id DESC LIMIT 25""",
            (str(gid),),
        ).fetchall()


class ProductSelect(discord.ui.Select):
    def __init__(self, prize_id, gid):
        self.prize_id = int(prize_id)
        products = recent_products(gid)

        options = [
            discord.SelectOption(
                label=f"#{p['id']} {p['name']}"[:100],
                value=str(p["id"]),
            )
            for p in products
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="沒有上架商品",
                    value="none",
                )
            ]

        super().__init__(
            placeholder="選擇商城商品",
            options=options,
            disabled=not bool(products),
        )

    async def callback(self, i):
        if not allowed(i):
            return await reply(i, "⛔ 群主專用。")

        from modules.lottery_fulfillment import bind_prize

        try:
            prize_name, product_name = bind_prize(
                i.guild_id,
                i.user.id,
                self.prize_id,
                int(self.values[0]),
            )
        except (ValueError, sqlite3.Error) as exc:
            return await reply(i, f"⚠️ {exc}")

        await i.response.edit_message(
            content=(
                f"✅ 獎品「{clean(prize_name)}」"
                f"已綁定商品「{clean(product_name)}」。"
            ),
            view=None,
        )


class ProductView(discord.ui.View):
    def __init__(self, i, prize_id):
        super().__init__(timeout=300)
        self.owner_id = i.user.id
        self.guild_id = i.guild_id
        self.add_item(ProductSelect(prize_id, i.guild_id))

    async def interaction_check(self, i):
        if (
            i.user.id != self.owner_id
            or i.guild_id != self.guild_id
            or not allowed(i)
        ):
            await reply(i, "⛔ 這不是你的面板。")
            return False
        return True


class PrizeSelect(discord.ui.Select):
    def __init__(self, gid):
        prizes = gift_prizes(gid)

        options = [
            discord.SelectOption(
                label=f"#{p['id']} {p['name']}"[:100],
                description=p["lottery_name"][:100],
                value=str(p["id"]),
            )
            for p in prizes
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="目前沒有禮物獎品",
                    value="none",
                )
            ]

        super().__init__(
            placeholder="選擇要綁定的禮物獎品",
            options=options,
            disabled=not bool(prizes),
        )

    async def callback(self, i):
        if not allowed(i):
            return await reply(i, "⛔ 群主專用。")

        await i.response.edit_message(
            content="🛍️ 請選擇對應商城商品：",
            view=ProductView(i, int(self.values[0])),
        )


class PrizeView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=300)
        self.owner_id = i.user.id
        self.guild_id = i.guild_id
        self.add_item(PrizeSelect(i.guild_id))

    async def interaction_check(self, i):
        if (
            i.user.id != self.owner_id
            or i.guild_id != self.guild_id
            or not allowed(i)
        ):
            await reply(i, "⛔ 這不是你的面板。")
            return False
        return True


# ======================================
# 中獎查帳
# ======================================

def draw_report(gid, only_unclaimed=False):
    from modules.lottery_fulfillment import init_fulfillment_db
    init_fulfillment_db()

    with get_connection() as db:
        rows = db.execute(
            """SELECT d.id,d.user_id,d.state,
                      l.name AS lottery_name,
                      p.name AS prize_name,
                      p.prize_type,
                      b.product_id,
                      deliv.character_id
               FROM rp_lottery_draws d
               JOIN rp_lottery_lists l
                 ON l.id=d.lottery_id
               JOIN rp_lottery_prizes p
                 ON p.id=d.prize_id
               LEFT JOIN rp_lottery_product_bindings b
                 ON b.guild_id=d.guild_id
                AND b.prize_id=d.prize_id
               LEFT JOIN rp_lottery_deliveries deliv
                 ON deliv.guild_id=d.guild_id
                AND deliv.draw_id=d.id
               WHERE d.guild_id=?
                 AND (
                     ?=0
                     OR (
                         p.prize_type='gift'
                         AND deliv.draw_id IS NULL
                     )
                 )
               ORDER BY d.id DESC LIMIT 25""",
            (str(gid), int(bool(only_unclaimed))),
        ).fetchall()

    if not rows:
        return "沒有符合條件的紀錄。"

    lines = [
        "📦 **未領獎清單**"
        if only_unclaimed
        else "📜 **中獎紀錄**"
    ]

    for r in rows:
        if r["prize_type"] == "money":
            status = (
                "已入帳"
                if r["state"] == "done"
                else "待發放／待重試"
            )
        elif r["character_id"] is not None:
            status = f"已領取至角色 #{r['character_id']}"
        elif r["product_id"] is not None:
            status = "待玩家領取"
        else:
            status = "待官方綁定商品／兌換"

        lines.append(
            f"#{r['id']}｜<@{r['user_id']}>｜"
            f"{clean(r['lottery_name'])}｜"
            f"{clean(r['prize_name'])}｜{status}"
        )

    return "\n".join(lines)[:1850]


# ======================================
# 抽獎管理中心
# ======================================

# ======================================
# 現有獎單管理：下拉選擇、修改名稱與內容、修改未派發獎品
# ======================================

class EditLotteryListModal(discord.ui.Modal, title='📝 修改現有獎單'):
    def __init__(self, lottery_id, name, description):
        super().__init__(timeout=300)
        self.list_id = int(lottery_id)
        self.new_name = discord.ui.TextInput(label='獎單名稱', default=str(name)[:100], max_length=100)
        self.description = discord.ui.TextInput(label='獎單內容／說明', default=str(description or '')[:500],
            required=False, max_length=500, style=discord.TextStyle.paragraph)
        self.add_item(self.new_name)
        self.add_item(self.description)

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, '群主專用。')
        from core.lottery_admin_editor import edit_list
        try:
            changed = edit_list(i.guild_id, i.user.id, self.list_id,
                name=str(self.new_name.value), description=str(self.description.value))
        except (ValueError, LookupError, sqlite3.Error) as exc:
            return await reply(i, f'⚠️ {exc}')
        await reply(i, f'✅ 獎單已更新為「{clean(changed)}」，原有抽獎紀錄與商品綁定保留。')


class EditLotteryPrizeModal(discord.ui.Modal, title='🎁 修改獎品名稱與內容'):
    def __init__(self, prize):
        super().__init__(timeout=300)
        self.prize_id = int(prize['id'])
        self.new_name = discord.ui.TextInput(label='獎品名稱', default=str(prize['name'])[:100],max_length=100)
        self.description = discord.ui.TextInput(label='獎品說明',default=str(prize['description'] or '')[:500],
            max_length=500,required=False,style=discord.TextStyle.paragraph)
        self.amount = discord.ui.TextInput(label='金幣獎額（禮物填0）',default=str(prize['amount']),
            max_length=12,required=True)
        self.stock = discord.ui.TextInput(label='剩餘數量（或填「不限量」）',
            default='不限量' if prize['unlimited'] else str(prize['remaining']),max_length=15)
        self.add_item(self.new_name)
        self.add_item(self.description)
        self.add_item(self.amount)
        self.add_item(self.stock)

    async def on_submit(self, i):
        if not allowed(i):
            return await reply(i, '群主專用。')
        from core.lottery_admin_editor import edit_prize
        try:
            title = edit_prize(i.guild_id,i.user.id,self.prize_id,
                name=str(self.new_name.value),description=str(self.description.value),
                amount=str(self.amount.value),stock=str(self.stock.value))
        except (ValueError, LookupError, sqlite3.Error) as exc:
            return await reply(i,f'⚠️ {exc}')
        await reply(i,f'✅ 已更新「{clean(title)}」；已派發的中獎資料不會被改寫。')


class LotteryListEditorView(discord.ui.View):
    def __init__(self, interaction, page=0):
        super().__init__(timeout=600)
        self.uid,self.gid=interaction.user.id,interaction.guild_id
        self.page=max(0,int(page))
        self.rebuild()

    def rebuild(self):
        from core.lottery_admin_editor import list_current
        self.clear_items()
        lists=list_current(self.gid)
        self.page=min(self.page,max(0,(len(lists)-1)//25))
        chunk=lists[self.page*25:self.page*25+25]
        if chunk:
            select=discord.ui.Select(placeholder='選擇目前要修改的獎單',row=0,
                options=[discord.SelectOption(label=str(r['name'])[:100],value=str(r['id']),
                    description='啟用' if r['enabled'] else '停用') for r in chunk])
            async def pick(i):
                list_id=int(select.values[0])
                await i.response.edit_message(content='📝 選擇這張獎單要修改的項目：',
                    view=LotterySelectedEditor(i,list_id))
            select.callback=pick
            self.add_item(select)
        self.add_item(self.back)
        if len(lists)>25:
            for title,delta in [('◀ 上一頁',-1),('下一頁 ▶',1)]:
                button=discord.ui.Button(label=title,style=discord.ButtonStyle.secondary,
                    row=1,disabled=(self.page+delta<0 or (self.page+delta)*25>=len(lists)))
                async def move(i,step=delta):
                    self.page+=step
                    self.rebuild()
                    await i.response.edit_message(view=self)
                button.callback=move
                self.add_item(button)

    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not allowed(i):
            await reply(i,'這不是你的獎單編輯面板。')
            return False
        return True

    @discord.ui.button(label='⬅️ 返回抽獎管理',style=discord.ButtonStyle.secondary,row=3)
    async def back(self,i,button):
        await i.response.edit_message(content='🎟️ 抽獎獎單管理',view=LotteryAdminView(i))


class LotterySelectedEditor(discord.ui.View):
    def __init__(self, i, lottery_id):
        super().__init__(timeout=600)
        self.gid,self.uid,self.lid=i.guild_id,i.user.id,int(lottery_id)

    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.uid or not allowed(i):
            await reply(i,'這不是你的獎單面板。')
            return False
        return True

    @discord.ui.button(label='📝 修改獎單名稱／內容',style=discord.ButtonStyle.primary,row=0)
    async def list_data(self,i,button):
        from core.lottery_admin_editor import initialize
        with transaction() as db:
            initialize(db)
            row=db.execute('SELECT name,description FROM rp_lottery_lists WHERE guild_id=? AND id=?',
                (str(self.gid),self.lid)).fetchone()
        if row is None:
            return await reply(i,'此獎單不存在。')
        await i.response.send_modal(EditLotteryListModal(self.lid,row['name'],row['description']))

    @discord.ui.button(label='🎁 修改獎品',style=discord.ButtonStyle.primary,row=0)
    async def prizes(self,i,button):
        await i.response.edit_message(content='🎁 從下拉選單挑選要修改的獎品：',
            view=LotteryPrizeEditorView(i,self.lid))

    @discord.ui.button(label='⬅️ 返回獎單清單',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,button):
        await i.response.edit_message(content='📝 選擇要修改的獎單：',view=LotteryListEditorView(i))


class LotteryPrizeEditorView(discord.ui.View):
    def __init__(self,i,lottery_id,page=0):
        super().__init__(timeout=600)
        self.uid,self.gid,self.lid=i.user.id,i.guild_id,int(lottery_id)
        self.page=int(page)
        self.rebuild()

    def rebuild(self):
        from core.lottery_admin_editor import list_prizes
        self.clear_items()
        prizes=list_prizes(self.gid,self.lid)
        self.page=max(0,min(self.page,max(0,(len(prizes)-1)//25)))
        chunk=prizes[self.page*25:self.page*25+25]
        if chunk:
            select=discord.ui.Select(placeholder='選擇獎品',row=0,
                options=[discord.SelectOption(label=str(r['name'])[:100],value=str(r['id'])) for r in chunk])
            async def pick(i):
                prize=next((r for r in list_prizes(self.gid,self.lid)
                    if int(r['id'])==int(select.values[0])),None)
                if not prize:
                    return await reply(i,'獎品已不存在。')
                await i.response.send_modal(EditLotteryPrizeModal(prize))
            select.callback=pick
            self.add_item(select)
        self.add_item(self.back)
        if len(prizes)>25:
            for title,delta in [('◀ 上一頁',-1),('下一頁 ▶',1)]:
                b=discord.ui.Button(label=title,row=1,style=discord.ButtonStyle.secondary,
                    disabled=self.page+delta<0 or (self.page+delta)*25>=len(prizes))
                async def move(i,step=delta):
                    self.page+=step
                    self.rebuild()
                    await i.response.edit_message(view=self)
                b.callback=move
                self.add_item(b)

    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not allowed(i):
            await reply(i,'這不是你的獎品編輯面板。')
            return False
        return True

    @discord.ui.button(label='⬅️ 返回獎單',style=discord.ButtonStyle.secondary,row=3)
    async def back(self,i,button):
        await i.response.edit_message(content='📝 選擇這張獎單要修改的項目：',
            view=LotterySelectedEditor(i,self.lid))


class OrdinaryTicketSetupView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=180)
        self.uid,self.gid=i.user.id,i.guild_id

    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not allowed(i):
            await reply(i,'群主專用。')
            return False
        return True

    @discord.ui.button(label='✅ 上架普通抽獎券',style=discord.ButtonStyle.success,row=0)
    async def publish(self,i,button):
        from core.ordinary_lottery_tickets import configure
        configure(self.gid,i.user.id)
        await i.response.edit_message(content='✅ 普通抽獎券已上架特殊道具店：每張800金幣／每週每人限五張。獎池使用「普通獎池」帖子。',view=None)

    @discord.ui.button(label='⬅️ 返回抽獎管理',style=discord.ButtonStyle.secondary,row=0)
    async def back(self,i,button):
        await i.response.edit_message(content='🎟️ 抽獎獎單管理',view=LotteryAdminView(i))


class LotteryAdminView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.owner_id = i.user.id
        self.guild_id = i.guild_id

    async def interaction_check(self, i):
        if (
            i.user.id != self.owner_id
            or i.guild_id != self.guild_id
            or not allowed(i)
        ):
            await reply(i, "⛔ 這不是你的管理面板。")
            return False
        return True

    @discord.ui.button(
        label="🎟️ 建立獎單",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def create(self, i, button):
        await i.response.send_message(
            "🎟️ 先選可抽獎身分組。\n"
            "不選直接按「填寫獎單」＝所有人可抽。",
            view=RoleCreateView(i),
            ephemeral=True,
        )

    @discord.ui.button(
        label="📋 清單",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def listing(self, i, button):
        await reply(i, list_text(i.guild_id))

    @discord.ui.button(
        label="🔎 獎品",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def details(self, i, button):
        await i.response.send_modal(LotteryDetail())

    @discord.ui.button(
        label="⚙️ 啟用/停用",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def toggle(self, i, button):
        await i.response.send_modal(ToggleLottery())

    @discord.ui.button(
        label="🛍️ 綁定商城",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def bind_product(self, i, button):
        from modules.lottery_fulfillment import (
            init_fulfillment_db,
        )
        init_fulfillment_db()

        await i.response.send_message(
            "🎁 選擇要綁定的禮物獎品：",
            view=PrizeView(i),
            ephemeral=True,
        )

    @discord.ui.button(
        label="📜 中獎",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def winners(self, i, button):
        await reply(i, draw_report(i.guild_id))

    @discord.ui.button(
        label="📦 未領獎",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def unclaimed(self, i, button):
        await reply(
            i,
            draw_report(i.guild_id, True),
        )

    @discord.ui.button(
        label="👥 設定身分",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def roles(self, i, button):
        await i.response.send_message(
            "👥 選擇身分組後，"
            "按「輸入要修改的獎單」。\n"
            "不選表示開放所有人。",
            view=EditRoleView(i),
            ephemeral=True,
        )

    @discord.ui.button(label='📝 修改目前獎單',style=discord.ButtonStyle.primary,row=3)
    async def edit_current(self,i,button):
        await i.response.send_message('📝 選擇要修改的獎單：',
            view=LotteryListEditorView(i),ephemeral=True)

    @discord.ui.button(label='🎟️ 新增抽獎券',style=discord.ButtonStyle.success,row=3)
    async def setup_ticket(self,i,button):
        await i.response.send_message(
            '🎟️ 普通抽獎券\n定價：800金幣／張\n每名玩家每週最多購買5張。\n'
            '綁定「普通獎池」，每張票可抽一次；符合條件的商城商品及正式課程自動入池。\n'
            '確認後將自動開放特殊道具店的購買入口。',
            view=OrdinaryTicketSetupView(i),ephemeral=True)

    @discord.ui.button(
        label="↩️ 返回",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def back(self, i, button):
        from modules.content_builder import ContentView

        await i.response.edit_message(
            content="✍️ **內容建立中心**",
            view=ContentView(i),
        )


class LotteryAdminButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🎟️ 抽獎管理",
            style=discord.ButtonStyle.primary,
            row=2,
        )

    async def callback(self, i):
        if not allowed(i):
            return await reply(
                i,
                "⛔ 只有群主可以管理抽獎。",
            )

        init_lottery_db()

        await i.response.edit_message(
            content=(
                "🎟️ **抽獎獎單管理**\n"
                "獎單名稱對應討論串／帖子名稱。\n"
                "建立獎單時可選擇抽獎身分組，"
                "不選代表所有人。\n"
                "玩家只可在討論串或帖子內使用 `/抽獎`。"
            ),
            view=LotteryAdminView(i),
        )


async def setup(bot: commands.Bot):
    init_lottery_db()
