"""寵物中心：購買、照顧、醫療、住院及晶片管理。"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import pets_v1 as pet
from core import pet_market_v2 as market
from core import pet_traits_v22 as behaviour
from core import pet_medical_v23 as medical
from core import pet_agreement_v23 as agreement
from core import pet_clinical_v23 as clinical
from core import pet_professional_v3 as professional
from core.world_interaction_rules import pet_rules
from core.database import get_connection
from core.prelaunch_gate import is_open

LOG = logging.getLogger(__name__)
PAGE_SIZE = 20
PRODUCTS_PER_PAGE = 8


def mine(gid, uid):
    with get_connection() as db:
        return [
            dict(r) for r in db.execute(
                """SELECT id,name FROM characters
                WHERE guild_id=? AND owner_id=? AND status='active'
                ORDER BY id LIMIT 25""",
                (str(gid), str(uid))
            )
        ]


def staff(i):
    return bool(
        i.guild and (
            i.user.id == i.guild.owner_id
            or i.user.guild_permissions.administrator
        )
    )


def terms(gid):
    r = pet_rules(gid)
    return (
        f'📜 **寵物飼主須知／晶片登記**\n'
        f'健康上限 {r["health_max"]}；每天 -{r["health_daily_loss"]}。'
        f'餵錯食物 -{r["wrong_food_health_loss"]}；'
        f'生病 -{r["sick_health_loss_min"]}～{r["sick_health_loss_max"]}；'
        f'受傷 -{r["injured_health_loss_min"]}～{r["injured_health_loss_max"]}。'
        f'未治療每 24 小時額外 -{r["untreated_24h_health_loss"]}；'
        f'健康歸零後可能死亡，請盡速救治。\n'
        f'依賴上限 {r["bond_max"]}；每天 -{r["bond_daily_loss"]}，'
        f'每 12 小時未玩樂再 -{r["bond_inactive_12h_loss"]}；'
        f'患病或受傷期間每天額外 -{r["sick_daily_bond_loss"]}。'
        f'陪玩 +{r["play_bond_gain"]}；'
        f'合適用品 +{r["gift_bond_min"]}～{r["gift_bond_max"]}。\n'
        '購買前須完成十頁飼主須知並正式簽署。'
        '結帳後植入永久晶片。'
        '疾病、外傷及住院依醫療價目表計費。'
        '保育類動物不可私養。'
    )


def cart_text(data):
    lines = ['🧺 **寵物與商品選購清單**']
    for x in data['items']:
        prefix = x['species'] + '／' if x['kind'] == 'breed' else ''
        lines.append(
            f'• {prefix}{x["label"]} ×{x["quantity"]}：'
            f'{x["price"] * x["quantity"]:,} 金幣'
        )
    lines.append(f'**合計：{data["total"]:,} 金幣**')
    if not data['pet_selected']:
        lines.append('ℹ️ 尚未選擇寵物，也可以只購買用品。')
    if data['missing_basics']:
        lines.append(
            '⚠️ 尚未選齊：' + ', '.join(data['missing_basics'])
        )
    lines.append('選購不立即扣款；最後統一結帳。')
    return '\n'.join(lines)


async def update_flow(interaction, content, view):
    """Modal 送出後直接換成下一步頁面，無來源訊息才另開私人頁。"""
    if interaction.message is not None:
        return await interaction.response.edit_message(content=content,view=view)
    return await interaction.response.send_message(content,view=view,ephemeral=True)


class Guard(discord.ui.View):
    def __init__(self, i, cid):
        super().__init__(timeout=600)
        self.uid = i.user.id
        self.gid = i.guild_id
        self.cid = int(cid)

    async def interaction_check(self, i):
        if i.guild_id != self.gid or i.user.id != self.uid:
            await i.response.send_message(
                '請使用自己的角色介面。',
                ephemeral=True
            )
            return False
        return True


class PageView(Guard):
    def __init__(self, i, cid, kind, species='', page=0):
        super().__init__(i, cid)
        self.kind = kind
        self.species = species
        self.page = max(0, page)

        if kind == 'species':
            values = [(s, s) for s in market.species_list(i.guild_id)]
        else:
            rows = market.catalog(
                i.guild_id,
                'breed' if kind == 'breed' else 'item',
                species if kind == 'breed' else None
            )
            values = [
                (
                    r['code'],
                    f'{r["label"]}｜{r["price"]:,} 金幣'
                )
                for r in rows
            ]

        self.values = values
        maximum = max(0, (len(values) - 1) // PAGE_SIZE)
        self.page = min(self.page, maximum)
        current = values[
            self.page * PAGE_SIZE:
            (self.page + 1) * PAGE_SIZE
        ]

        if current:
            select = discord.ui.Select(
                placeholder={
                    'species': '選擇物種',
                    'breed': '選擇品種',
                    'item': '選擇用品'
                }[kind],
                options=[
                    discord.SelectOption(
                        label=label[:100],
                        value=code
                    )
                    for code, label in current
                ],
                row=0
            )

            async def selected(x):
                code = select.values[0]
                if kind == 'species':
                    return await x.response.edit_message(
                        content=f'🐾 已選擇 {code}，請選擇品種。',
                        view=PageView(x, self.cid, 'breed', code)
                    )

                try:
                    cart = await asyncio.to_thread(
                        market.select,
                        x.guild_id,
                        self.cid,
                        x.user.id,
                        'breed' if kind == 'breed' else 'item',
                        code
                    )
                except (ValueError, LookupError, PermissionError) as exc:
                    return await x.response.send_message(
                        f'⚠️ {exc}', ephemeral=True
                    )

                if kind == 'breed':
                    view = WizardView(x, self.cid, code)
                    await x.response.edit_message(
                        content=view.page_text(x.guild_id),
                        view=view
                    )
                else:
                    await x.response.edit_message(
                        content=cart_text(cart),
                        view=CartView(x, self.cid)
                    )

            select.callback = selected
            self.add_item(select)

    @discord.ui.button(
        label='◀ 上頁',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def prev(self, i, button):
        await i.response.edit_message(
            view=PageView(
                i, self.cid, self.kind,
                self.species, max(0, self.page - 1)
            )
        )

    @discord.ui.button(
        label='下頁 ▶',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def next(self, i, button):
        await i.response.edit_message(
            view=PageView(
                i, self.cid, self.kind,
                self.species, self.page + 1
            )
        )

    @discord.ui.button(
        label='返回選購清單',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content=cart_text(market.preview(i.guild_id, self.cid)),
            view=CartView(i, self.cid)
        )


class QuantityModal(discord.ui.Modal, title='勾選商品｜填寫各商品數量'):
    """最多5件一組，避免超出 Discord Modal 的欄位數限制。"""
    def __init__(self, cid, codes, next_step=None, breed_code=None, product_context=None, labels=None):
        super().__init__()
        self.cid=int(cid)
        self.codes=list(codes)
        self.next_step=next_step
        self.breed_code=breed_code
        self.product_context=product_context
        self.fields=[]
        labels=labels or {}
        for code in self.codes:
            field=discord.ui.TextInput(label=f'{labels.get(code,code)[:34]} 數量'[:45],default='1',max_length=3,required=True)
            self.add_item(field)
            self.fields.append(field)

    async def on_submit(self, i):
        try:
            amounts=[int(field.value) for field in self.fields]
            await asyncio.to_thread(market.select_many,i.guild_id,self.cid,i.user.id, list(zip(self.codes,amounts)))
        except (ValueError, LookupError, PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}', ephemeral=True)
        if self.next_step is not None:
            state=market.preview(i.guild_id,self.cid)
            current=WizardView(i,self.cid,self.breed_code,max(0,self.next_step-1))
            group,group_label,mandatory=current.pages[current.step]
            if mandatory and group in state['missing_basics']:
                return await update_flow(i,
                    current.page_text(i.guild_id)+'\n⚠️ 此頁用品尚未選齊。食盆與水盆必須各有一款。',current)
            view=WizardView(i,self.cid,self.breed_code,self.next_step)
            if self.next_step>=len(view.pages):
                state=market.preview(i.guild_id,self.cid)
                if not state['ready']:
                    view=WizardView(i,self.cid,self.breed_code,0)
                    return await update_flow(i,cart_text(state)+'\n請回到缺少的必選用品頁面補齊。',view)
                await asyncio.to_thread(agreement.start,i.guild_id,self.cid,i.user.id)
                terms_view=TermsPages(i,self.cid)
                return await update_flow(i,terms_view.text(),terms_view)
            return await update_flow(i,view.page_text(i.guild_id),view)
        context=self.product_context or {}
        view=SupplyProductsView(i,self.cid,context.get('species','貓'),context.get('category','飲食用品'),context.get('subcategory','飼料'),context.get('page',0))
        return await update_flow(i,cart_text(market.preview(i.guild_id,self.cid))+'\n繼續選購或前往結帳。',view)


class WizardView(Guard):
    def __init__(self, i, cid, breed_code, step=0, item_page=0):
        super().__init__(i, cid)
        self.breed_code = breed_code

        breed = next(
            (
                r for r in market.catalog(i.guild_id, 'breed')
                if r['code'] == breed_code
            ),
            None
        )
        if not breed:
            raise LookupError('品種已下架')

        self.species = breed['species']
        self.pages = market.wizard_pages(i.guild_id, breed_code)
        self.step = max(0, min(int(step), len(self.pages)))
        self.item_page=max(0,int(item_page))

        if self.step < len(self.pages):
            group, label, required = self.pages[self.step]
            choices = market.wizard_items(
                i.guild_id, self.species, group, breed_code
            )

            if choices:
                self.item_page=min(self.item_page,(len(choices)-1)//PRODUCTS_PER_PAGE)
                shown=choices[self.item_page*PRODUCTS_PER_PAGE:(self.item_page+1)*PRODUCTS_PER_PAGE]
                select=discord.ui.Select(
                    placeholder=f'勾選商品（可複選）｜{label}'[:150],
                    options=[discord.SelectOption(label=f'{x["label"]}｜{x["price"]:,}金幣'[:100],value=x['code']) for x in shown],
                    min_values=1,max_values=min(5,len(shown)),row=0)
                async def chosen(x):
                    await x.response.send_modal(QuantityModal(
                        self.cid,select.values,self.step+1,self.breed_code,
                        labels={item['code']:item['label'] for item in shown}))
                select.callback=chosen
                self.add_item(select)
                if len(choices)>PRODUCTS_PER_PAGE:
                    back=discord.ui.Button(label='◀ 商品前頁',style=discord.ButtonStyle.secondary,row=3,disabled=self.item_page==0)
                    forward=discord.ui.Button(label='商品後頁 ▶',style=discord.ButtonStyle.secondary,row=3,disabled=(self.item_page+1)*PRODUCTS_PER_PAGE>=len(choices))
                    async def prev_products(x):
                        view=WizardView(x,self.cid,self.breed_code,self.step,self.item_page-1)
                        await x.response.edit_message(content=view.page_text(x.guild_id),view=view)
                    async def next_products(x):
                        view=WizardView(x,self.cid,self.breed_code,self.step,self.item_page+1)
                        await x.response.edit_message(content=view.page_text(x.guild_id),view=view)
                    back.callback=prev_products;forward.callback=next_products
                    self.add_item(back);self.add_item(forward)

    def page_text(self, gid, index=None):
        index = self.step if index is None else index
        state = market.preview(gid, self.cid)

        if index >= len(self.pages):
            return cart_text(state) + '\n✅ 已完成用品選購，將進入飼主須知。' 

        group, label, required = self.pages[index]
        requirement = '必選' if required else '可略過'
        choices=market.wizard_items(gid,self.species,group,self.breed_code)
        displayed=choices[self.item_page*PRODUCTS_PER_PAGE:(self.item_page+1)*PRODUCTS_PER_PAGE]
        parts=[f'🐾 **逐頁選購 {index + 1}/{len(self.pages)}**｜{label}（{requirement}）',
               f'商品頁 {self.item_page+1}/{max(1,(len(choices)+PRODUCTS_PER_PAGE-1)//PRODUCTS_PER_PAGE)}｜目前總額：{state["total"]:,} 金幣',
               '☑ 可複選商品，再分別填寫購買數量。']
        parts.extend(f'• {x["label"]}：{x["price"]:,} 金幣' for x in displayed)
        return '\n'.join(parts)[:1950]

    @discord.ui.button(
        label='◀ 上一頁',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def prev(self, i, button):
        step = max(0, self.step - 1)
        view = WizardView(i, self.cid, self.breed_code, step)
        await i.response.edit_message(
            content=view.page_text(i.guild_id),
            view=view
        )

    @discord.ui.button(
        label='下一頁／略過 ▶',
        style=discord.ButtonStyle.primary,
        row=1
    )
    async def next(self, i, button):
        state = market.preview(i.guild_id, self.cid)

        if self.step < len(self.pages):
            group, label, required = self.pages[self.step]
            if required and group in state['missing_basics']:
                return await i.response.send_message(
                    f'⚠️ {label} 為必選用品。',
                    ephemeral=True
                )

        step = min(self.step + 1, len(self.pages))
        if step >= len(self.pages):
            state=market.preview(i.guild_id,self.cid)
            if not state['ready']:
                return await i.response.send_message(cart_text(state),ephemeral=True)
            await asyncio.to_thread(agreement.start,i.guild_id,self.cid,i.user.id)
            view=TermsPages(i,self.cid)
            return await i.response.edit_message(content=view.text(),view=view)
        view = WizardView(i, self.cid, self.breed_code, step)
        await i.response.edit_message(content=view.page_text(i.guild_id),view=view)

class CheckoutModal(
    discord.ui.Modal,
    title='統一結帳・晶片施打與飼主登記'
):
    name = discord.ui.TextInput(
        label='寵物名字', max_length=50
    )

    def __init__(self, cid):
        super().__init__()
        self.cid = cid

    async def on_submit(self, i):
        try:
            result = await asyncio.to_thread(
                market.checkout,
                i.guild_id,
                self.cid,
                i.user.id,
                self.name.value,
                f'discord:pet:checkout:{i.id}'
            )
        except (ValueError, PermissionError, LookupError) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'✅ **寵物購買完成**\n'
            f'寵物：{result["name"]}（{result["species"]}）\n'
            f'寵物編號：#{result["pet_id"]}\n'
            f'實付：{result["total"]:,} 金幣\n'
            f'晶片：`{result["chip"]}`\n'
            '可至 `/查看 → 寵物相關` 照顧寵物。',
            ephemeral=True
        )


class SignTermsModal(
    discord.ui.Modal,
    title='飼主須知｜正式簽署'
):
    name = discord.ui.TextInput(
        label='角色正式姓名', max_length=100
    )
    identity = discord.ui.TextInput(
        label='身分證字號', max_length=60
    )

    def __init__(self, cid):
        super().__init__()
        self.cid = int(cid)

    async def on_submit(self, i):
        try:
            result = await asyncio.to_thread(
                agreement.sign,
                i.guild_id,
                self.cid,
                i.user.id,
                self.name.value,
                self.identity.value,
                len(agreement.PAGES)
            )
        except (ValueError, LookupError, PermissionError) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        state=market.preview(i.guild_id,self.cid)
        await update_flow(
            i,
            f'✅ 飼主須知 V{result["version"]} 簽署完成！\n'
            f'簽名：{result["name"]}\n'
            f'身分證字號：`{result["identity_code"]}`\n\n'
            + cart_text(state) + '\n最後一步：請確認付款與晶片登記。',
            FinalPaymentView(i,self.cid)
        )


class TermsPages(Guard):
    def __init__(self, i, cid, index=0):
        super().__init__(i, cid)
        self.index = int(index)
        self.prev.disabled = self.index == 0
        self.next.disabled = (
            self.index == len(agreement.PAGES) - 1
        )
        self.sign.disabled = (
            self.index != len(agreement.PAGES) - 1
        )

    def text(self):
        title, body = agreement.PAGES[self.index]
        return (
            f'📜 **飼主須知 V{agreement.VERSION}**'
            f'｜第 {self.index + 1}/{len(agreement.PAGES)} 頁\n'
            f'**{title}**\n{body}\n\n'
            '請逐頁閱讀，最後一頁才可正式簽署。'
        )

    @discord.ui.button(
        label='◀ 上一頁',
        style=discord.ButtonStyle.secondary,
        row=0
    )
    async def prev(self, i, button):
        index = max(0, self.index - 1)
        view = TermsPages(i, self.cid, index)
        await i.response.edit_message(
            content=view.text(), view=view
        )

    @discord.ui.button(
        label='下一頁 ▶',
        style=discord.ButtonStyle.primary,
        row=0
    )
    async def next(self, i, button):
        try:
            index = await asyncio.to_thread(
                agreement.advance,
                i.guild_id,
                self.cid,
                i.user.id,
                self.index
            )
        except (ValueError, PermissionError) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        view = TermsPages(i, self.cid, index)
        await i.response.edit_message(
            content=view.text(), view=view
        )

    @discord.ui.button(
        label='簽署飼主須知',
        style=discord.ButtonStyle.success,
        row=1
    )
    async def sign(self, i, button):
        if self.index != len(agreement.PAGES) - 1:
            return await i.response.send_message(
                '請先閱讀全部頁面。',
                ephemeral=True
            )
        await i.response.send_modal(
            SignTermsModal(self.cid)
        )


class FinalPaymentView(Guard):
    @discord.ui.button(label='✅ 正式付款與晶片登記',style=discord.ButtonStyle.success)
    async def pay(self,i,button):
        state=market.preview(i.guild_id,self.cid)
        if not state['ready']:
            return await i.response.send_message(cart_text(state),ephemeral=True)
        await i.response.send_modal(CheckoutModal(self.cid))

    @discord.ui.button(label='返回用品頁調整',style=discord.ButtonStyle.secondary)
    async def back(self,i,button):
        state=market.preview(i.guild_id,self.cid)
        breed=next((x for x in state['items'] if x['kind']=='breed'),None)
        if not breed:return await i.response.send_message('請先選擇寵物。',ephemeral=True)
        view=WizardView(i,self.cid,breed['code'],0)
        await i.response.edit_message(content=view.page_text(i.guild_id),view=view)


class CartView(Guard):
    def __init__(self, i, cid):
        super().__init__(i, cid)
        present = market.preview(i.guild_id, cid)['items']

        if present:
            select = discord.ui.Select(
                placeholder='移除商品',
                options=[
                    discord.SelectOption(
                        label=(
                            ('寵物：' if x['kind'] == 'breed' else '用品：')
                            + x['label']
                        )[:100],
                        value=x['kind'] + ':' + x['code']
                    )
                    for x in present[:25]
                ],
                row=3
            )

            async def remove_item(x):
                kind, code = select.values[0].split(':', 1)
                try:
                    state = await asyncio.to_thread(
                        market.remove,
                        x.guild_id,
                        self.cid,
                        x.user.id,
                        kind,
                        code
                    )
                except (
                    ValueError, LookupError, PermissionError
                ) as exc:
                    return await x.response.send_message(
                        f'⚠️ {exc}', ephemeral=True
                    )

                await x.response.edit_message(
                    content=cart_text(state),
                    view=CartView(x, self.cid)
                )

            select.callback = remove_item
            self.add_item(select)

    @discord.ui.button(
        label='挑選寵物',
        style=discord.ButtonStyle.primary,
        row=0
    )
    async def breed(self, i, button):
        await i.response.edit_message(
            content='🐾 請選擇物種。',
            view=PageView(i, self.cid, 'species')
        )

    @discord.ui.button(
        label='確認付款與晶片登記',
        style=discord.ButtonStyle.success,
        row=1
    )
    async def checkout(self, i, button):
        state = market.preview(i.guild_id, self.cid)

        if not state['pet_selected']:
            if not state['items']:
                return await i.response.send_message(
                    '請先選擇寵物或用品。',
                    ephemeral=True
                )
            try:
                result = await asyncio.to_thread(
                    market.checkout_supplies,
                    i.guild_id,
                    self.cid,
                    i.user.id,
                    f'discord:pet:supplies:{i.id}'
                )
            except (
                ValueError, LookupError, PermissionError
            ) as exc:
                return await i.response.send_message(
                    f'⚠️ {exc}', ephemeral=True
                )

            return await i.response.send_message(
                f'✅ 已支付 {result["total"]:,} 金幣。',
                ephemeral=True
            )

        if not state['ready']:
            return await i.response.send_message(
                cart_text(state),
                ephemeral=True
            )

        await i.response.send_modal(
            CheckoutModal(self.cid)
        )

    @discord.ui.button(
        label='返回寵物中心',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content='🐾 **寵物中心**\n購買寵物會依序自動顯示品種、用品、須知與付款。\n用品商城可按類別選擇、勾選多款並分別填數量。',
            view=PetHomeView(i, self.cid)
        )


class RenameModal(
    discord.ui.Modal,
    title='修改寵物名稱'
):
    name = discord.ui.TextInput(
        label='新的寵物名字', max_length=50
    )

    def __init__(self, cid, pid):
        super().__init__()
        self.cid = cid
        self.pid = pid

    async def on_submit(self, i):
        try:
            name = await asyncio.to_thread(
                market.rename,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id,
                self.name.value
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )
        await i.response.send_message(
            f'✅ 寵物已更名為 **{name}**。',
            ephemeral=True
        )


class PetActionsView(Guard):
    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)

        actions = [
            ('play', '陪玩'),
            ('walk', '公園散步'),
            ('feed', '餵食'),
            ('wrong_food', '餵錯食物')
        ]

        select = discord.ui.Select(
            placeholder='選擇寵物照顧行為',
            options=[
                discord.SelectOption(label=label, value=key)
                for key, label in actions
            ],
            row=0
        )

        async def chosen(x):
            action = select.values[0]
            try:
                handler = market.feed if action == 'feed' else pet.care
                args = (x.guild_id, self.pid, self.cid, x.user.id)
                if action != 'feed':
                    args += (action,)
                result = await asyncio.to_thread(handler, *args)
            except (
                ValueError, LookupError, PermissionError
            ) as exc:
                return await x.response.send_message(
                    f'⚠️ {exc}', ephemeral=True
                )

            details = ''
            try:
                extra = await asyncio.to_thread(
                    behaviour.interaction,
                    x.guild_id,
                    self.pid,
                    self.cid,
                    x.user.id,
                    action
                )
                if extra['injury']:
                    details += (
                        f'\n⚠️ 主人健康 -{extra["injury"]}'
                    )
                if extra['messages']:
                    details += '\n' + '\n'.join(extra['messages'])
            except (
                ValueError, LookupError, PermissionError
            ) as exc:
                details += f'\n⚠️ 行為結算：{exc}'

            await x.response.send_message(
                f'✅ 健康 {result["health"]}'
                f'｜依賴 {result["bond"]}'
                f'｜狀態 {result["state"]}'
                + details,
                ephemeral=True
            )

        select.callback = chosen
        self.add_item(select)

    @discord.ui.button(
        label='居家訓練',
        style=discord.ButtonStyle.primary,
        row=2
    )
    async def train(self, i, button):
        await i.response.edit_message(
            content='🎓 寵物訓練',
            view=TrainingView(i, self.cid, self.pid)
        )

    @discord.ui.button(
        label='用品與維修',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def supplies(self, i, button):
        await i.response.edit_message(
            content='🧰 寵物用品',
            view=EquipmentView(i, self.cid, self.pid)
        )

    @discord.ui.button(
        label='修改名字',
        style=discord.ButtonStyle.primary,
        row=1
    )
    async def rename(self, i, button):
        await i.response.send_modal(
            RenameModal(self.cid, self.pid)
        )

    @discord.ui.button(
        label='返回寵物清單',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content='🐾 選擇寵物',
            view=PetSelectView(i, self.cid)
        )


class TrainingView(Guard):
    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)
        courses = list(behaviour.SETTINGS['training'])
        species = next(
            (
                p['species']
                for p in pet.list_pets(i.guild_id, cid)
                if p['id'] == self.pid
            ),
            ''
        )

        if species != '狗' and '服從訓練' in courses:
            courses.remove('服從訓練')

        select = discord.ui.Select(
            placeholder='選擇訓練課程',
            options=[
                discord.SelectOption(label=c, value=c)
                for c in courses
            ],
            row=0
        )

        async def chosen(x):
            try:
                r = await asyncio.to_thread(
                    behaviour.training,
                    x.guild_id,
                    self.pid,
                    self.cid,
                    x.user.id,
                    select.values[0]
                )
            except (
                ValueError, LookupError, PermissionError
            ) as exc:
                return await x.response.send_message(
                    f'⚠️ {exc}', ephemeral=True
                )

            await x.response.send_message(
                f'🎓 {"成功" if r["success"] else "未成功"}'
                f'｜成長 +{r["growth"]}'
                f'｜技能 +{r["skill_gain"]}'
                f'｜疲勞 {r["fatigue"]}/100',
                ephemeral=True
            )

        select.callback = chosen
        self.add_item(select)

    @discord.ui.button(
        label='查看訓練狀態',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def status(self, i, button):
        try:
            result = await asyncio.to_thread(
                behaviour.state,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'🐾 {result["name"]}'
            f'｜疲勞 {result["fatigue"]}/100'
            f'｜當前暴躁 {result["anger"]}/100',
            ephemeral=True
        )

    @discord.ui.button(
        label='付費專業訓練', style=discord.ButtonStyle.success, row=1
    )
    async def professional_training(self, i, button):
        await i.response.edit_message(
            content='🎓 專業訓練｜課程需先付費、經過指定時數再結算',
            view=ProfessionalTrainingView(i, self.cid, self.pid)
        )

    @discord.ui.button(
        label='返回寵物',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content='🐾 寵物照顧',
            view=PetActionsView(i, self.cid, self.pid)
        )


class ProfessionalCourseManageView(Guard):
    """Actions on one paid course. Never auto charge or auto settle on display."""

    def __init__(self, i, cid, pid, course_id):
        super().__init__(i, cid)
        self.pid = int(pid)
        self.course_id = int(course_id)

    async def _apply(self, i, operation):
        try:
            result = await asyncio.to_thread(
                getattr(professional, operation),
                i.guild_id, self.course_id, self.cid, i.user.id
            )
        except (ValueError, LookupError, PermissionError) as exc:
            return await i.response.send_message(f'⚠️ {exc}', ephemeral=True)
        await i.response.send_message(
            f'🎓 {operation}｜{result}', ephemeral=True
        )

    @discord.ui.button(label='課程結算', style=discord.ButtonStyle.success, row=0)
    async def settle_course(self, i, button):
        await self._apply(i, 'settle')

    @discord.ui.button(label='恢復課程', style=discord.ButtonStyle.primary, row=0)
    async def resume_course(self, i, button):
        await self._apply(i, 'resume')

    @discord.ui.button(label='取消課程', style=discord.ButtonStyle.danger, row=0)
    async def cancel_course(self, i, button):
        await self._apply(i, 'cancel')

    @discord.ui.button(label='返回專業訓練', style=discord.ButtonStyle.secondary, row=1)
    async def back(self, i, button):
        await i.response.edit_message(
            content='🎓 專業訓練', view=ProfessionalTrainingView(i, self.cid, self.pid)
        )


class ProfessionalTrainingView(Guard):
    """Separate paid professional sessions from instant home training."""

    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)
        animal = next((p for p in pet.list_pets(i.guild_id, cid)
                       if int(p['id']) == self.pid), None)
        species = animal['species'] if animal else ''
        courses = professional.available(species)
        options = [discord.SelectOption(
            label=f'{name}｜{price:,}金幣｜{hours}小時'[:100],
            value=name
        ) for name, price, hours in courses[:25]]
        if not options:
            return
        select = discord.ui.Select(
            placeholder='選擇並付費報名專業課程', options=options, row=0
        )

        async def chosen(x):
            try:
                record = await asyncio.to_thread(
                    professional.book,
                    x.guild_id, self.pid, self.cid, x.user.id,
                    select.values[0], f'pet:professional:interaction:{x.id}'
                )
            except (ValueError, LookupError, PermissionError) as exc:
                return await x.response.send_message(f'⚠️ {exc}', ephemeral=True)
            await x.response.send_message(
                f'✅ 報名 {record["course"]}｜金幣 {record["fee"]:,} '
                f'｜預定結束：{record["ends_at"]}。'
                '課程完成後請在管理課程中結算。', ephemeral=True
            )
        select.callback = chosen
        self.add_item(select)

    @discord.ui.button(label='管理已報名課程', style=discord.ButtonStyle.primary, row=1)
    async def manage_courses(self, i, button):
        from core.database import get_connection
        with get_connection() as db:
            professional.setup(db)
            records = [dict(r) for r in db.execute(
                "SELECT id,course,state,ends_at FROM rp_pet_professional_courses_v3 "
                "WHERE guild_id=? AND pet_id=? AND character_id=? "
                "AND state IN ('booked','running','paused') "
                "ORDER BY id DESC LIMIT 25",
                (str(i.guild_id), self.pid, self.cid)
            )]
        if not records:
            return await i.response.send_message('目前沒有待結算或暫停的課程。', ephemeral=True)
        menu = discord.ui.Select(
            placeholder='選擇要管理的課程',
            options=[discord.SelectOption(
                label=f'#{r["id"]} {r["course"]}（{r["state"]}）'[:100],
                value=str(r['id'])
            ) for r in records]
        )
        view = Guard(i, self.cid)
        async def chosen(x):
            await x.response.edit_message(
                content='🎓 課程管理',
                view=ProfessionalCourseManageView(x, self.cid, self.pid, int(menu.values[0]))
            )
        menu.callback = chosen
        view.add_item(menu)
        await i.response.edit_message(content='🎓 選擇已報名課程', view=view)

    @discord.ui.button(label='返回居家訓練', style=discord.ButtonStyle.secondary, row=1)
    async def back(self, i, button):
        await i.response.edit_message(
            content='🎓 居家訓練', view=TrainingView(i, self.cid, self.pid)
        )


class EquipmentView(Guard):
    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)

        try:
            equipped = behaviour.state(
                i.guild_id, pid, cid, i.user.id
            )['equipment']
        except (
            ValueError, LookupError, PermissionError
        ):
            equipped = []

        if equipped:
            select = discord.ui.Select(
                placeholder='選擇維修用品',
                options=[
                    discord.SelectOption(
                        label=(
                            f'#{x["id"]} {x["code"]}'
                            f' 耐久{x["durability"]}/100'
                        )[:100],
                        value=str(x['id'])
                    )
                    for x in equipped[:25]
                ],
                row=0
            )

            async def repair(x):
                try:
                    result = await asyncio.to_thread(
                        behaviour.repair,
                        x.guild_id,
                        self.pid,
                        self.cid,
                        x.user.id,
                        int(select.values[0])
                    )
                except (
                    ValueError, LookupError, PermissionError
                ) as exc:
                    return await x.response.send_message(
                        f'⚠️ {exc}', ephemeral=True
                    )

                await x.response.send_message(
                    f'✅ 耐久 {result["durability"]}/100'
                    f'｜花費 {result["cost"]:,} 金幣',
                    ephemeral=True
                )

            select.callback = repair
            self.add_item(select)

        available = [
            x for x in market.inventory(i.guild_id, cid)
            if x['code'].startswith('v21:')
            and x['quantity'] > 0
            and x['code'].split(':')[-2]
            not in ('feed', 'treat', 'supplement')
        ]

        if available:
            select = discord.ui.Select(
                placeholder='從背包配置用品',
                options=[
                    discord.SelectOption(
                        label=(
                            f'{x["code"]} ×{x["quantity"]}'
                        )[:100],
                        value=x['code']
                    )
                    for x in available[:25]
                ],
                row=1
            )

            async def equip(x):
                try:
                    result = await asyncio.to_thread(
                        behaviour.equip,
                        x.guild_id,
                        self.pid,
                        self.cid,
                        x.user.id,
                        select.values[0]
                    )
                except (
                    ValueError, LookupError, PermissionError
                ) as exc:
                    return await x.response.send_message(
                        f'⚠️ {exc}', ephemeral=True
                    )

                await x.response.send_message(
                    f'✅ 已配置，用品編號 #{result}',
                    ephemeral=True
                )

            select.callback = equip
            self.add_item(select)

    @discord.ui.button(
        label='返回寵物',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content='🐾 寵物照顧',
            view=PetActionsView(i, self.cid, self.pid)
        )


class PetSelectView(Guard):
    def __init__(self, i, cid, page=0):
        super().__init__(i, cid)
        rows = pet.list_pets(i.guild_id, cid)
        self.empty = not bool(rows)
        self.max_page = max(
            0, (len(rows) - 1) // PAGE_SIZE
        )
        self.page = min(
            max(int(page), 0),
            self.max_page
        )
        current = rows[
            self.page * PAGE_SIZE:
            (self.page + 1) * PAGE_SIZE
        ]

        if current:
            select = discord.ui.Select(
                placeholder='選擇自己的寵物',
                options=[
                    discord.SelectOption(
                        label=(
                            f'#{r["id"]} {r["name"]}'
                            f'｜{r["species"]}（{r["state"]}）'
                        )[:100],
                        value=str(r['id'])
                    )
                    for r in current
                ],
                row=0
            )

            async def chosen(x):
                row = next(
                    r for r in current
                    if r['id'] == int(select.values[0])
                )
                await x.response.edit_message(
                    content=(
                        f'🐾 {row["name"]}\n'
                        f'健康 {row["health"]}'
                        f'｜依賴 {row["bond"]}\n'
                        f'晶片：`{row["chip"]}`'
                    ),
                    view=PetActionsView(
                        x, self.cid, row['id']
                    )
                )

            select.callback = chosen
            self.add_item(select)

    @discord.ui.button(
        label='上一頁',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def prev(self, i, button):
        await i.response.edit_message(
            content='🐾 選擇寵物',
            view=PetSelectView(
                i, self.cid, self.page - 1
            )
        )

    @discord.ui.button(
        label='下一頁',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def next(self, i, button):
        await i.response.edit_message(
            content='🐾 選擇寵物',
            view=PetSelectView(
                i, self.cid, self.page + 1
            )
        )


class MissingModal(
    discord.ui.Modal,
    title='寵物協尋啟事'
):
    pet_id = discord.ui.TextInput(
        label='走失寵物編號', max_length=12
    )
    location = discord.ui.TextInput(
        label='最後出現位置', max_length=100
    )
    description = discord.ui.TextInput(
        label='特徵及協尋線索',
        style=discord.TextStyle.paragraph,
        max_length=1000
    )
    reward = discord.ui.TextInput(
        label='懸賞金幣',
        default='0',
        max_length=12
    )

    def __init__(self, cid):
        super().__init__()
        self.cid = cid

    async def on_submit(self, i):
        try:
            pid = int(self.pet_id.value)
            existing = next(
                (
                    x for x in pet.list_pets(
                        i.guild_id, self.cid
                    )
                    if x['id'] == pid
                ),
                None
            )

            if not existing:
                raise PermissionError('你不是這隻寵物的飼主')

            if existing['state'] != 'missing':
                await asyncio.to_thread(
                    pet.mark_missing,
                    i.guild_id, pid,
                    self.cid, i.user.id,
                    self.location.value
                )

            result = await asyncio.to_thread(
                pet.publish_poster,
                i.guild_id,
                pid,
                self.cid,
                i.user.id,
                self.description.value,
                self.location.value,
                int(self.reward.value)
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        try:
            from core.channel_bindings_v1 import get_binding
            channel_id = get_binding(
                i.guild_id, 'pet_missing'
            )
            channel = (
                i.guild.get_channel(int(channel_id))
                if channel_id else None
            )

            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f'🐾 **寵物協尋公告**\n'
                    f'寵物 #{pid}\n'
                    f'最後地點：'
                    f'{discord.utils.escape_mentions(self.location.value)}\n'
                    f'{discord.utils.escape_mentions(self.description.value)}\n'
                    f'懸賞：{result["reward"]:,} 金幣',
                    allowed_mentions=discord.AllowedMentions.none()
                )
        except Exception:
            LOG.exception('協尋公告失敗')

        await i.response.send_message(
            '✅ 已建立協尋公告。',
            ephemeral=True
        )


class EncounterResultView(Guard):
    def __init__(self, i, cid, encounter):
        super().__init__(i, cid)
        self.encounter = encounter

        choices = [
            ('leave', '離開'),
            ('observe', '觀察'),
            ('shelter', '嘗試收留'),
            ('hospital', '帶去醫院掃晶片'),
            ('report', '通知動物救援')
        ]

        select = discord.ui.Select(
            placeholder='遇見動物後的處理方式',
            options=[
                discord.SelectOption(label=label, value=key)
                for key, label in choices
            ]
        )

        async def chosen(x):
            try:
                result = await asyncio.to_thread(
                    pet.resolve_encounter,
                    x.guild_id,
                    self.encounter['id'],
                    self.cid,
                    x.user.id,
                    select.values[0]
                )
                note = ''

                if (
                    result.get('pet_id')
                    and result['choice'] == 'hospital'
                ):
                    recovered = await asyncio.to_thread(
                        pet.complete_rescue,
                        x.guild_id,
                        result['pet_id'],
                        authorized=True,
                        rescuer_character=self.cid
                    )
                    note = (
                        f'\n💉 晶片比對完成，'
                        f'懸賞 {recovered["reward"]:,} 金幣已結算。'
                    )

                    try:
                        from modules.notifications import queue_notification
                        with get_connection() as db:
                            owner = db.execute(
                                '''SELECT main_character_id
                                FROM rp_pets_v1
                                WHERE guild_id=? AND id=?''',
                                (
                                    str(x.guild_id),
                                    int(result['pet_id'])
                                )
                            ).fetchone()

                        if owner:
                            await asyncio.to_thread(
                                queue_notification,
                                x.guild_id,
                                'private',
                                'role',
                                '🐾 寵物晶片比對成功，已尋回。',
                                character_id=int(
                                    owner['main_character_id']
                                ),
                                dedupe_key=(
                                    f'pet:chip:auto:{x.guild_id}:'
                                    f'{self.encounter["id"]}'
                                )
                            )
                    except Exception:
                        LOG.exception('寵物尋回通知失敗')

            except (
                ValueError, LookupError, PermissionError
            ) as exc:
                return await x.response.send_message(
                    f'⚠️ {exc}', ephemeral=True
                )

            await x.response.edit_message(
                content='🐾 ' + result['message'] + note,
                view=None
            )

        select.callback = chosen
        self.add_item(select)


MEDICAL_GROUPS = {
    'check': (
        '健康檢查',
        [
            'first_followup', 'basic_check',
            'advanced_check', 'full_check',
            'precision_check', 'emergency_check',
            'behavior_check', 'postop_check'
        ]
    ),
    'lab': (
        '診斷檢驗',
        [
            'blood_basic', 'blood_complete',
            'urine_stool', 'skin_parasite',
            'xray', 'ultrasound', 'ct', 'mri',
            'endoscopy', 'pathology'
        ]
    ),
    'illness': (
        '疾病治療',
        ['illness_1', 'illness_2', 'illness_3', 'illness_4']
    ),
    'injury': (
        '外傷治療',
        ['injury_1', 'injury_2', 'injury_3', 'injury_4']
    ),
    'surgery': (
        '手術',
        [
            'suture', 'surgery_general',
            'surgery_complex', 'fracture_fix',
            'fracture_rebuild', 'organ_surgery',
            'emergency_surgery'
        ]
    ),
    'anesthesia': (
        '麻醉與術後',
        [
            'sedation', 'anesthesia_general',
            'anesthesia_high', 'postop_meds',
            'postop_special'
        ]
    ),
    'medicine': (
        '藥物及支持照護',
        [
            'medication', 'medication_long',
            'infection_special', 'nutrition_support',
            'fluids', 'specialist', 'chronic_followup'
        ]
    ),
    'hospital': (
        '住院',
        [
            'ward_basic', 'ward_comfort',
            'ward_icu', 'ward_isolation'
        ]
    ),
    'emergency': (
        '緊急急救',
        [
            'emergency_basic', 'emergency_severe',
            'emergency_critical', 'emergency_special'
        ]
    ),
    'prevention': (
        '預防醫療',
        [
            'prevention_consult', 'parasite_basic',
            'prevention_advanced', 'vaccination',
            'prevention_bundle', 'annual_health',
            'annual_premium', 'species_special'
        ]
    ),
    'hospital_extra': (
        '住院與出院照護',
        [
            'nutrition_meal', 'comfort_care',
            'discharge_check'
        ]
    ),
    'clinical': (
        '診斷、病歷與住院管理', []
    ),
}


class MedicalConfirmView(Guard):
    def __init__(self, i, cid, pid, code, days=0):
        super().__init__(i, cid)
        self.pid = int(pid)
        self.code = code
        self.days = days

    @discord.ui.button(
        label='確認付款及醫療服務',
        style=discord.ButtonStyle.success
    )
    async def confirm(self, i, button):
        code = self.code
        try:
            p = next(
                (
                    r for r in pet.list_pets(
                        i.guild_id, self.cid
                    )
                    if r['id'] == self.pid
                ),
                None
            )
            if not p:
                raise PermissionError('沒有寵物操作權限')

            kind = medical.PRICES[code][2]

            if kind in (
                'illness', 'injury', 'surgery', 'emergency'
            ):
                raise ValueError(
                    '治療及急救請使用正式診斷入口。'
                )

            result = await asyncio.to_thread(
                medical.pay,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id,
                [code],
                f'discord:pet:medical:{i.id}',
                ward_days=self.days
            )
        except (
            ValueError, PermissionError, LookupError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        self.confirm.disabled = True
        await i.response.edit_message(
            content=(
                f'✅ 醫療服務付款完成\n'
                f'服務：{medical.PRICES[code][0]}\n'
                f'帳單 #{result["invoice_id"]}\n'
                f'實付 {result["total"]:,} 金幣'
            ),
            view=self
        )


class MedicalItems(Guard):
    def __init__(self, i, cid, pid, group):
        super().__init__(i, cid)
        self.pid = int(pid)
        self.group = group

        title, codes = MEDICAL_GROUPS[group]

        if not codes:
            return

        select = discord.ui.Select(
            placeholder=f'選擇{title}',
            options=[
                discord.SelectOption(
                    label=(
                        f'{medical.PRICES[c][0]}'
                        f'｜{medical.PRICES[c][1]:,} 金幣'
                    )[:100],
                    value=c
                )
                for c in codes
            ]
        )

        async def chosen(x):
            code = select.values[0]

            if group in (
                'illness', 'injury', 'surgery', 'emergency'
            ):
                return await x.response.edit_message(
                    content='請先進行正式診斷。',
                    view=ClinicalDiagnosis(
                        x, self.cid, self.pid
                    )
                )

            if group == 'hospital':
                return await x.response.edit_message(
                    content='🏥 住院管理',
                    view=ClinicalStay(
                        x, self.cid, self.pid
                    )
                )

            free = code == 'first_followup'

            if free:
                info = medical.first_check(
                    x.guild_id, self.pid
                )
                if not info:
                    return await x.response.send_message(
                        '沒有首次複檢紀錄。',
                        ephemeral=True
                    )
                if info['completed_at']:
                    return await x.response.send_message(
                        '首次複檢已完成。',
                        ephemeral=True
                    )

            quote = medical.quote(
                [code],
                free_first=free
            )

            await x.response.send_message(
                f'🩺 **醫療付款確認**\n'
                f'{medical.PRICES[code][0]}\n'
                f'原價：{quote["original_total"]:,} 金幣\n'
                f'預計實付：{quote["total"]:,} 金幣',
                view=MedicalConfirmView(
                    x, self.cid, self.pid, code
                ),
                ephemeral=True
            )

        select.callback = chosen
        self.add_item(select)


class MedicalCategories(Guard):
    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)

        select = discord.ui.Select(
            placeholder='選擇醫療分類',
            options=[
                discord.SelectOption(
                    label=value[0],
                    value=key
                )
                for key, value in MEDICAL_GROUPS.items()
            ]
        )

        async def chosen(x):
            group = select.values[0]

            if group == 'clinical':
                return await x.response.edit_message(
                    content='🏥 臨床診斷及住院管理',
                    view=ClinicalDiagnosis(
                        x, self.cid, self.pid
                    )
                )

            await x.response.edit_message(
                content=(
                    f'🏥 醫療中心｜'
                    f'{MEDICAL_GROUPS[group][0]}'
                ),
                view=MedicalItems(
                    x, self.cid, self.pid, group
                )
            )

        select.callback = chosen
        self.add_item(select)


class ClinicalDiagnosis(Guard):
    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)

    @discord.ui.button(
        label='診斷目前傷病',
        style=discord.ButtonStyle.primary
    )
    async def diagnose(self, i, button):
        try:
            result = await asyncio.to_thread(
                clinical.diagnose,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        quote = medical.quote([
            f'{result["kind"]}_{result["severity"]}'
        ])

        await i.response.edit_message(
            content=(
                f'🩺 {result["diagnosis"]}'
                f'（LV{result["severity"]}）\n'
                f'症狀：{result["symptoms"]}\n'
                f'治療報價：{quote["total"]:,} 金幣'
            ),
            view=ClinicalTreatment(
                i, self.cid, self.pid,
                result['case_id']
            )
        )

    @discord.ui.button(
        label='歷史病歷與帳單',
        style=discord.ButtonStyle.secondary
    )
    async def history(self, i, button):
        try:
            result = await asyncio.to_thread(
                clinical.history,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        lines = [
            f'#{x["id"]}｜實付 {x["paid"]:,}'
            for x in result['invoices'][:12]
        ]

        await i.response.send_message(
            '📋 醫療帳單\n'
            + ('\n'.join(lines) or '尚無紀錄'),
            ephemeral=True
        )

    @discord.ui.button(
        label='住院管理',
        style=discord.ButtonStyle.secondary
    )
    async def hospital(self, i, button):
        await i.response.edit_message(
            content='🏥 住院管理',
            view=ClinicalStay(
                i, self.cid, self.pid
            )
        )

    @discord.ui.button(
        label='首次複檢延期',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def postpone(self, i, button):
        try:
            p = next(
                (
                    x for x in pet.list_pets(
                        i.guild_id, self.cid
                    )
                    if x['id'] == self.pid
                ),
                None
            )
            if not p:
                raise PermissionError('非授權飼主')

            stay = await asyncio.to_thread(
                clinical.stay_info,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id
            )

            reason = (
                'hospitalized'
                if stay else
                'missing'
                if p['state'] == 'missing' else
                'critical'
                if p['state'] == 'critical' else None
            )

            if not reason:
                raise ValueError(
                    '目前不符合自動延期條件。'
                )

            result = await asyncio.to_thread(
                clinical.postpone_followup,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id,
                reason
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'✅ 新截止時間：{result["deadline_at"]}',
            ephemeral=True
        )

    @discord.ui.button(
        label='返回醫療分類',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content='🏥 寵物醫療中心',
            view=MedicalCategories(
                i, self.cid, self.pid
            )
        )


class ClinicalTreatment(Guard):
    def __init__(self, i, cid, pid, case_id):
        super().__init__(i, cid)
        self.pid = int(pid)
        self.case_id = int(case_id)

    @discord.ui.button(
        label='確認治療及付款',
        style=discord.ButtonStyle.success
    )
    async def pay(self, i, button):
        try:
            result = await asyncio.to_thread(
                clinical.treat,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id,
                self.case_id,
                f'discord:clinical:treat:{i.id}'
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.edit_message(
            content=(
                f'✅ 治療完成\n'
                f'帳單 #{result["invoice_id"]}\n'
                f'實付 {result["total"]:,} 金幣'
            ),
            view=None
        )


class ClinicalStayDays(
    discord.ui.Modal,
    title='住院管理｜天數'
):
    days = discord.ui.TextInput(
        label='住院／續住天數（1～30）',
        default='1',
        max_length=2
    )

    def __init__(self, cid, pid, action, ward=None):
        super().__init__()
        self.cid = cid
        self.pid = pid
        self.action = action
        self.ward = ward

    async def on_submit(self, i):
        try:
            days = int(self.days.value)
            if not 1 <= days <= 30:
                raise ValueError('請填寫1～30天')

            if self.action == 'extend':
                stay = await asyncio.to_thread(
                    clinical.stay_info,
                    i.guild_id,
                    self.pid,
                    self.cid,
                    i.user.id
                )
                if not stay:
                    raise ValueError('目前未住院')

                cost = (
                    medical.PRICES[stay['ward_code']][1]
                    * days
                )
            else:
                cost = medical.PRICES[self.ward][1] * days

        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'🏥 預付 {days} 天\n'
            f'費用：{cost:,} 金幣\n'
            '請確認是否付款。',
            view=ClinicalStayConfirm(
                i, self.cid, self.pid,
                self.action, days, self.ward
            ),
            ephemeral=True
        )


class ClinicalStayConfirm(Guard):
    def __init__(self, i, cid, pid, action, days, ward):
        super().__init__(i, cid)
        self.pid = pid
        self.action = action
        self.days = days
        self.ward = ward

    @discord.ui.button(
        label='確認支付',
        style=discord.ButtonStyle.success
    )
    async def confirm(self, i, button):
        try:
            if self.action == 'extend':
                result = await asyncio.to_thread(
                    clinical.extend,
                    i.guild_id,
                    self.pid,
                    self.cid,
                    i.user.id,
                    self.days,
                    f'discord:clinical:extend:{i.id}'
                )
            else:
                result = await asyncio.to_thread(
                    clinical.admit,
                    i.guild_id,
                    self.pid,
                    self.cid,
                    i.user.id,
                    self.ward,
                    self.days,
                    f'discord:clinical:admit:{i.id}'
                )

        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.edit_message(
            content=(
                f'✅ 住院操作完成\n'
                f'費用：{result["total"]:,} 金幣'
            ),
            view=None
        )


class ClinicalStay(Guard):
    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)

        select = discord.ui.Select(
            placeholder='選擇住院病房',
            options=[
                discord.SelectOption(
                    label='普通病房｜10,000／天',
                    value='ward_basic'
                ),
                discord.SelectOption(
                    label='高級舒適病房｜25,000／天',
                    value='ward_comfort'
                ),
                discord.SelectOption(
                    label='重症監護 ICU｜60,000／天',
                    value='ward_icu'
                ),
                discord.SelectOption(
                    label='特殊隔離病房｜40,000／天',
                    value='ward_isolation'
                )
            ],
            row=0
        )

        async def chosen(x):
            await x.response.send_modal(
                ClinicalStayDays(
                    self.cid, self.pid,
                    'admit', select.values[0]
                )
            )

        select.callback = chosen
        self.add_item(select)

    @discord.ui.button(
        label='查看住院狀態',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def status(self, i, button):
        try:
            stay = await asyncio.to_thread(
                clinical.stay_info,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        if stay:
            message = (
                f'🏥 病房：{stay["ward_code"]}\n'
                f'已預付至：{stay["covered_until"]}'
            )
        else:
            message = '目前未住院。'

        await i.response.send_message(
            message, ephemeral=True
        )

    @discord.ui.button(
        label='一般探病',
        style=discord.ButtonStyle.primary,
        row=1
    )
    async def visit(self, i, button):
        try:
            result = await asyncio.to_thread(
                clinical.visit,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'🐾 探病完成！依賴 +{result["bond_plus"]}',
            ephemeral=True
        )

    @discord.ui.button(
        label='續住',
        style=discord.ButtonStyle.primary,
        row=1
    )
    async def extend(self, i, button):
        await i.response.send_modal(
            ClinicalStayDays(
                self.cid, self.pid, 'extend'
            )
        )

    @discord.ui.button(
        label='轉換病房',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def transfer(self, i, button):
        await i.response.send_message(
            '🏥 請選擇新病房。',
            view=ClinicalWardTransfer(
                i, self.cid, self.pid
            ),
            ephemeral=True
        )

    @discord.ui.button(
        label='確認出院',
        style=discord.ButtonStyle.success,
        row=2
    )
    async def discharge(self, i, button):
        try:
            result = await asyncio.to_thread(
                clinical.discharge,
                i.guild_id,
                self.pid,
                self.cid,
                i.user.id,
                f'discord:clinical:discharge:{i.id}'
            )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'✅ 出院成功\n'
            f'狀態：{result["status"]}\n'
            f'出院檢查：{result["total"]:,} 金幣',
            ephemeral=True
        )

    @discord.ui.button(
        label='返回醫療分類',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content='🏥 醫療中心',
            view=MedicalCategories(
                i, self.cid, self.pid
            )
        )


class ClinicalWardTransfer(Guard):
    def __init__(self, i, cid, pid):
        super().__init__(i, cid)
        self.pid = int(pid)

        select = discord.ui.Select(
            placeholder='選擇新病房',
            options=[
                discord.SelectOption(
                    label=(
                        f'{medical.PRICES[code][0]}'
                        f'｜{medical.PRICES[code][1]:,}／天'
                    )[:100],
                    value=code
                )
                for code in clinical.WARD
            ]
        )

        async def chosen(x):
            try:
                result = await asyncio.to_thread(
                    clinical.transfer_ward,
                    x.guild_id,
                    self.pid,
                    self.cid,
                    x.user.id,
                    select.values[0],
                    f'discord:clinical:transfer:{x.id}'
                )
            except (
                ValueError, LookupError, PermissionError
            ) as exc:
                return await x.response.send_message(
                    f'⚠️ {exc}', ephemeral=True
                )

            await x.response.edit_message(
                content=(
                    '✅ 轉換病房完成\n'
                    f'差額：{result["total"]:,} 金幣'
                ),
                view=None
            )

        select.callback = chosen
        self.add_item(select)


class ServiceView(Guard):
    def __init__(self, i, cid, kind):
        super().__init__(i, cid)
        self.kind = kind
        rows = pet.list_pets(i.guild_id, cid)

        if rows:
            select = discord.ui.Select(
                placeholder='選擇需要醫療的寵物',
                options=[
                    discord.SelectOption(
                        label=f'{r["name"]} #{r["id"]}'[:100],
                        value=str(r['id'])
                    )
                    for r in rows[:25]
                ]
            )

            async def chosen(x):
                await x.response.edit_message(
                    content='🏥 請選擇醫療分類。',
                    view=MedicalCategories(
                        x, self.cid,
                        int(select.values[0])
                    )
                )

            select.callback = chosen
            self.add_item(select)

    @discord.ui.button(
        label='返回寵物中心',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def back(self, i, button):
        await i.response.edit_message(
            content='🐾 **寵物中心**\n購買寵物會依序自動顯示品種、用品、須知與付款。\n用品商城可按類別選擇、勾選多款並分別填數量。',
            view=PetHomeView(i, self.cid)
        )


class SupplySpeciesView(Guard):
    def __init__(self,i,cid):
        super().__init__(i,cid)
        options=[discord.SelectOption(label=sp,value=sp) for sp in ('貓','狗','鸚鵡')]
        sel=discord.ui.Select(placeholder='選擇用品適用的寵物物種',options=options)
        async def chosen(x):
            view=SupplyCategoryView(x,self.cid,sel.values[0]);await x.response.edit_message(content='🛒 請選擇商城分類。',view=view)
        sel.callback=chosen;self.add_item(sel)

class SupplyCategoryView(Guard):
    def __init__(self,i,cid,species):
        super().__init__(i,cid);self.species=species
        for index,category in enumerate(market.shop_categories(i.guild_id,species)):
            button=discord.ui.Button(label=category[:75],style=discord.ButtonStyle.primary,row=index//5)
            async def chosen(x,c=category):
                view=SupplySubcategoryView(x,self.cid,species,c)
                await x.response.edit_message(content=f'🛒 {species}｜{c}：選擇商品細項。',view=view)
            button.callback=chosen;self.add_item(button)
        cart=discord.ui.Button(label='🧺 查看購物車',style=discord.ButtonStyle.success,row=2)
        async def show_cart(x):
            await x.response.edit_message(content=cart_text(market.preview(x.guild_id,self.cid)),view=CartView(x,self.cid))
        cart.callback=show_cart;self.add_item(cart)

class SupplySubcategoryView(Guard):
    def __init__(self,i,cid,species,category,page=0):
        super().__init__(i,cid);self.species=species;self.category=category;self.page=max(0,int(page))
        subs=market.shop_subcategories(i.guild_id,species,category)
        self.page=min(self.page,max(0,(len(subs)-1)//PAGE_SIZE))
        curr=subs[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
        if curr:
            sel=discord.ui.Select(placeholder='選擇用品細項',options=[discord.SelectOption(label=c[:100],value=c) for c in curr])
            async def chosen(x):
                view=SupplyProductsView(x,self.cid,species,category,sel.values[0]);await x.response.edit_message(content=view.page_text(x.guild_id),view=view)
            sel.callback=chosen;self.add_item(sel)
        back=discord.ui.Button(label='◀ 前頁',style=discord.ButtonStyle.secondary,disabled=self.page==0,row=1)
        forward=discord.ui.Button(label='後頁 ▶',style=discord.ButtonStyle.secondary,disabled=(self.page+1)*PAGE_SIZE>=len(subs),row=1)
        async def prev(x):
            await x.response.edit_message(view=SupplySubcategoryView(x,self.cid,species,category,self.page-1))
        async def next_(x):
            await x.response.edit_message(view=SupplySubcategoryView(x,self.cid,species,category,self.page+1))
        back.callback=prev;forward.callback=next_;self.add_item(back);self.add_item(forward)

class SupplyProductsView(Guard):
    def __init__(self,i,cid,species,category,subcategory,page=0):
        super().__init__(i,cid);self.species=species;self.category=category;self.subcategory=subcategory
        self.rows=market.shop_products(i.guild_id,species,category,subcategory)
        self.page=min(max(0,int(page)),max(0,(len(self.rows)-1)//PRODUCTS_PER_PAGE))
        rows=self.rows[self.page*PRODUCTS_PER_PAGE:(self.page+1)*PRODUCTS_PER_PAGE]
        if rows:
            sel=discord.ui.Select(placeholder='☑ 勾選要買的商品（最多5項）',
                options=[discord.SelectOption(label=f'{r["label"]}｜{r["price"]:,}金幣'[:100],value=r['code']) for r in rows],
                min_values=1,max_values=min(5,len(rows)),row=0)
            async def chosen(x):
                await x.response.send_modal(QuantityModal(self.cid,sel.values,product_context={
                    'species':species,'category':category,'subcategory':subcategory,'page':self.page},
                    labels={r['code']:r['label'] for r in rows}))
            sel.callback=chosen;self.add_item(sel)
        for title,delta in [('◀ 前頁',-1),('後頁 ▶',1)]:
            b=discord.ui.Button(label=title,style=discord.ButtonStyle.secondary,row=1,
                disabled=not 0 <= self.page+delta <= (len(self.rows)-1)//PRODUCTS_PER_PAGE)
            async def change(x,d=delta):
                view=SupplyProductsView(x,self.cid,species,category,subcategory,self.page+d)
                await x.response.edit_message(content=view.page_text(x.guild_id),view=view)
            b.callback=change;self.add_item(b)
        back=discord.ui.Button(label='換商品分類',style=discord.ButtonStyle.secondary,row=2)
        async def change_category(x):
            await x.response.edit_message(content='🛒 選擇商品分類。',view=SupplyCategoryView(x,self.cid,species))
        back.callback=change_category;self.add_item(back)
        cart=discord.ui.Button(label='🧺 購物車／用品結帳',style=discord.ButtonStyle.success,row=2)
        async def checkout_supplies(x):
            await x.response.edit_message(content=cart_text(market.preview(x.guild_id,self.cid)),view=CartView(x,self.cid))
        cart.callback=checkout_supplies;self.add_item(cart)
    def page_text(self,gid):
        rows=self.rows[self.page*PRODUCTS_PER_PAGE:(self.page+1)*PRODUCTS_PER_PAGE]
        lines=[f'🛒 **{self.species}｜{self.category}｜{self.subcategory}**  第{self.page+1}/{max(1,(len(self.rows)+PRODUCTS_PER_PAGE-1)//PRODUCTS_PER_PAGE)}頁',
               '勾選不同款式後，逐件填寫數量；加入購物車不立即付款。']
        lines += [f'• {r["label"]}：{r["price"]:,}金幣' for r in rows]
        return '\n'.join(lines)[:1900]


class PetHomeView(Guard):
    def __init__(self, i, cid):
        super().__init__(i, cid)

        options = [
            ('pet_purchase', '🐾 購買寵物（自動逐頁選用品）'),
            ('supply_shop', '🛒 寵物用品商城'),
            ('cart', '🧺 查看購物車／單獨用品結帳'),
            ('check', '🩺 寵物醫療中心'),
            ('treat', '🏥 傷病治療'),
            ('training', '🎓 寵物訓練'),
            ('terms', '📜 飼主須知')
        ]

        if any(
            p['state'] == 'missing'
            for p in pet.list_pets(i.guild_id, cid)
        ):
            options.append(
                ('missing', '🔎 寵物協尋')
            )

        select = discord.ui.Select(
            placeholder='選擇寵物中心服務',
            options=[
                discord.SelectOption(
                    label=label, value=key
                )
                for key, label in options
            ]
        )

        async def chosen(x):
            mode = select.values[0]

            if mode == 'pet_purchase':
                return await x.response.edit_message(content='🐾 請選擇寵物物種。',view=PageView(x,self.cid,'species'))
            if mode == 'supply_shop':
                return await x.response.edit_message(content='🛒 請選擇用品物種。',view=SupplySpeciesView(x,self.cid))
            if mode == 'cart':
                state = market.preview(
                    x.guild_id, self.cid
                )
                return await x.response.edit_message(
                    content=cart_text(state),
                    view=CartView(x, self.cid)
                )

            if mode == 'training':
                return await x.response.edit_message(
                    content='🎓 選擇寵物。',
                    view=PetSelectView(x, self.cid)
                )

            if mode == 'terms':
                await asyncio.to_thread(
                    agreement.start,
                    x.guild_id,
                    self.cid,
                    x.user.id
                )
                view = TermsPages(x, self.cid)
                return await x.response.send_message(
                    view.text(), view=view,
                    ephemeral=True
                )

            if mode in ('check', 'treat'):
                return await x.response.edit_message(
                    content='🏥 選擇寵物。',
                    view=ServiceView(
                        x, self.cid, mode
                    )
                )

            if mode == 'missing':
                return await x.response.send_modal(
                    MissingModal(self.cid)
                )

        select.callback = chosen
        self.add_item(select)

        if staff(i):
            button = discord.ui.Button(
                label='🔧 寵物及商品管理',
                style=discord.ButtonStyle.secondary,
                row=1
            )

            async def admin(x):
                if not staff(x):
                    return await x.response.send_message(
                        '僅限管理員。',
                        ephemeral=True
                    )
                await x.response.send_message(
                    '🔧 寵物商品管理',
                    view=AdminView(),
                    ephemeral=True
                )

            button.callback = admin
            self.add_item(button)


class AdminEdit(
    discord.ui.Modal,
    title='管理員｜寵物／商品上架下架'
):
    kind = discord.ui.TextInput(
        label='類型：breed 或 item',
        default='breed',
        max_length=8
    )
    code = discord.ui.TextInput(
        label='商品代碼', max_length=80
    )
    label = discord.ui.TextInput(
        label='品種／商品名稱', max_length=100
    )
    species = discord.ui.TextInput(
        label='物種',
        required=False,
        max_length=50
    )
    price = discord.ui.TextInput(
        label='售價（-1代表下架）',
        max_length=15
    )

    async def on_submit(self, i):
        if not staff(i):
            return await i.response.send_message(
                '沒有管理權限。',
                ephemeral=True
            )

        try:
            price = int(self.price.value)
            old = next(
                (
                    r for r in market.catalog(
                        i.guild_id, active_only=False
                    )
                    if r['kind'] == self.kind.value
                    and r['code'] == self.code.value
                ),
                None
            )

            if price == -1:
                if not old:
                    raise LookupError('找不到商品')

                result = market.manage(
                    i.guild_id,
                    old['kind'],
                    old['code'],
                    old['label'],
                    old['price'],
                    species=old['species'],
                    stock=old['stock'],
                    active=False,
                    basic=bool(old['basic'])
                )
            else:
                result = market.manage(
                    i.guild_id,
                    self.kind.value,
                    self.code.value,
                    self.label.value,
                    price,
                    species=self.species.value,
                    stock=old['stock'] if old else -1,
                    active=True,
                    basic=bool(old['basic']) if old else False
                )
        except (
            ValueError, LookupError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'✅ 已更新：`{result["code"]}`',
            ephemeral=True
        )


class AdminStockModal(
    discord.ui.Modal,
    title='管理員｜庫存及必備用品設定'
):
    kind = discord.ui.TextInput(
        label='類型', default='item', max_length=8
    )
    code = discord.ui.TextInput(
        label='商品代碼', max_length=80
    )
    stock = discord.ui.TextInput(
        label='庫存（-1不限數量）',
        default='-1',
        max_length=12
    )
    basic = discord.ui.TextInput(
        label='是否必備（是／否）',
        default='否',
        max_length=3
    )

    async def on_submit(self, i):
        if not staff(i):
            return await i.response.send_message(
                '沒有管理權限。',
                ephemeral=True
            )

        try:
            row = next(
                (
                    r for r in market.catalog(
                        i.guild_id,
                        active_only=False
                    )
                    if r['kind'] == self.kind.value.strip()
                    and r['code'] == self.code.value.strip()
                ),
                None
            )

            if not row:
                raise LookupError('商品不存在')

            basic = self.basic.value.strip() in (
                '是', 'yes', 'true', '1'
            )

            result = market.manage(
                i.guild_id,
                row['kind'],
                row['code'],
                row['label'],
                row['price'],
                species=row['species'],
                stock=int(self.stock.value),
                active=bool(row['active']),
                basic=basic,
                description=row['description']
            )
        except (
            LookupError, ValueError, PermissionError
        ) as exc:
            return await i.response.send_message(
                f'⚠️ {exc}', ephemeral=True
            )

        await i.response.send_message(
            f'✅ 已更新 `{result["code"]}`。',
            ephemeral=True
        )


class AdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(
        label='新增／修改／上下架商品',
        style=discord.ButtonStyle.primary
    )
    async def edit(self, i, button):
        if not staff(i):
            return await i.response.send_message(
                '沒有管理權限。',
                ephemeral=True
            )
        await i.response.send_modal(AdminEdit())

    @discord.ui.button(
        label='設定庫存／必備用品',
        style=discord.ButtonStyle.secondary
    )
    async def stock(self, i, button):
        if not staff(i):
            return await i.response.send_message(
                '沒有管理權限。',
                ephemeral=True
            )
        await i.response.send_modal(AdminStockModal())

    @discord.ui.button(
        label='檢視上架清單',
        style=discord.ButtonStyle.secondary
    )
    async def listings(self, i, button):
        if not staff(i):
            return await i.response.send_message(
                '沒有管理權限。',
                ephemeral=True
            )

        rows = market.catalog(
            i.guild_id,
            active_only=False
        )

        lines = [
            f'{r["kind"]}｜{r["label"]}'
            f'｜{r["price"]:,} 金幣'
            for r in rows
        ]

        await i.response.send_message(
            '\n'.join(lines)[:1900] or '目前沒有商品。',
            ephemeral=True
        )


class CharacterSelect(discord.ui.View):
    def __init__(self, i, rows):
        super().__init__(timeout=600)
        self.uid = i.user.id
        self.gid = i.guild_id

        select = discord.ui.Select(
            placeholder='選擇角色',
            options=[
                discord.SelectOption(
                    label=f'#{r["id"]} {r["name"]}'[:100],
                    value=str(r['id'])
                )
                for r in rows
            ]
        )

        async def chosen(x):
            if (
                x.user.id != self.uid
                or x.guild_id != self.gid
            ):
                return await x.response.send_message(
                    '這不是你的角色選單。',
                    ephemeral=True
                )

            await x.response.edit_message(
                content='🐾 **寵物中心**\n購買寵物會依序自動顯示品種、用品、須知與付款。\n用品商城可按類別選擇、勾選多款並分別填數量。',
                view=PetHomeView(
                    x, int(select.values[0])
                )
            )

        select.callback = chosen
        self.add_item(select)
        if staff(i):
            button=discord.ui.Button(label='🔧 管理員｜寵物及商品管理',style=discord.ButtonStyle.secondary,row=1)
            async def admin(x):
                if not staff(x):return await x.response.send_message('沒有管理權限。',ephemeral=True)
                return await x.response.send_message('🔧 寵物商品管理',view=AdminView(),ephemeral=True)
            button.callback=admin;self.add_item(button)


class PetCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.worker.start()

    def cog_unload(self):
        self.worker.cancel()

    @app_commands.command(
        name='寵物中心',
        description='寵物購買、用品、醫療、住院及協尋'
    )
    async def pet_center(self, i: discord.Interaction):
        if not i.guild:
            return await i.response.send_message(
                '僅限伺服器使用。',
                ephemeral=True
            )

        rows = await asyncio.to_thread(
            mine, i.guild_id, i.user.id
        )

        if not rows:
            if staff(i):
                return await i.response.send_message(
                    '🔧 寵物商品管理',
                    view=AdminView(),
                    ephemeral=True
                )
            return await i.response.send_message(
                '請先啟用自己的角色。',
                ephemeral=True
            )

        await i.response.send_message(
            '🐾 請選擇角色。',
            view=CharacterSelect(i, rows),
            ephemeral=True
        )

    @tasks.loop(hours=1)
    async def worker(self):
        try:
            with get_connection() as db:
                exists = db.execute(
                    """SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='rp_pets_v1'"""
                ).fetchone()

                gids = (
                    [
                        r[0] for r in db.execute(
                            'SELECT DISTINCT guild_id FROM rp_pets_v1'
                        )
                    ]
                    if exists else []
                )

            for gid in gids:
                if not is_open(gid):
                    continue

                await asyncio.to_thread(pet.daily, gid)
                await asyncio.to_thread(
                    behaviour.daily_equipment, gid
                )

                with get_connection() as db:
                    ids = [
                        int(r[0])
                        for r in db.execute(
                            """SELECT id FROM rp_pets_v1
                            WHERE guild_id=? AND state!='deceased'""",
                            (str(gid),)
                        )
                    ]

                for pid in ids:
                    await asyncio.to_thread(
                        medical.settle_recovery,
                        gid,
                        pid
                    )

                notices = await asyncio.to_thread(
                    medical.due_notices, gid
                )

                if notices:
                    from modules.notifications import queue_notification

                    for notice in notices:
                        try:
                            queued = await asyncio.to_thread(
                                queue_notification,
                                gid,
                                'private',
                                'role',
                                notice['message'],
                                character_id=notice['character_id'],
                                dedupe_key=(
                                    f'pet:v23:followup:{gid}:'
                                    f'{notice["pet_id"]}:'
                                    f'{notice["stage"]}'
                                )
                            )
                            if queued:
                                await asyncio.to_thread(
                                    medical.mark_followup_notice,
                                    gid,
                                    notice['pet_id'],
                                    notice['stage']
                                )
                        except Exception:
                            LOG.exception('首次複檢通知失敗')

            await asyncio.to_thread(
                behaviour.dispatch_notices
            )

        except Exception:
            LOG.exception('寵物定時結算失敗')

    @worker.before_loop
    async def ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(PetCenter(bot))
