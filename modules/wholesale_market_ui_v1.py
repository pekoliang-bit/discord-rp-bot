"""/商城 → 🏬 批發市場：商品報價、買斷/寄售、庫存。"""
from __future__ import annotations
import asyncio
import logging
import discord
from core import wholesale_market_v1 as market
LOG=logging.getLogger(__name__)

class MarketView(discord.ui.View):
    def __init__(self,user_id,character_id,guild_id,offers):
        super().__init__(timeout=600)
        self.owner_id=int(user_id)
        self.character_id=int(character_id)
        self.guild_id=int(guild_id)
        self.selected_offer=None
        if offers:
            select=discord.ui.Select(placeholder='從供貨商品中選擇進貨品項',row=0,
                options=[discord.SelectOption(label=f"#{p['id']} {p['product_name']}"[:100],value=str(p['id']),
                    description=f"{p['supplier_kind']}｜{p['mode']}｜批發 {p['wholesale_price']}｜剩餘 {p['stock']}"[:100])
                    for p in offers[:25]])
            async def choose(interaction):
                self.selected_offer=int(select.values[0])
                await interaction.response.send_message(f'✅ 已選擇供貨單 #{self.selected_offer}，請按「進貨」。',ephemeral=True)
            select.callback=choose
            self.add_item(select)
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id or interaction.guild_id!=self.guild_id:
            await interaction.response.send_message('請使用自己的商城面板。',ephemeral=True)
            return False
        return True
    @discord.ui.button(label='📦 進貨／接受寄售',style=discord.ButtonStyle.success,row=1)
    async def acquire(self,interaction,button):
        if not self.selected_offer:
            await interaction.response.send_message('請先從選單選擇供貨商品。',ephemeral=True)
            return
        await interaction.response.send_modal(AcquireModal(self.character_id,self.selected_offer))
    @discord.ui.button(label='🏷️ 品牌發布報價',row=1)
    async def brand(self,interaction,button):
        await interaction.response.send_modal(BrandQuoteModal(self.character_id))
    @discord.ui.button(label='📋 查看我的直播倉庫',row=2)
    async def my_inventory(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        rows=await asyncio.to_thread(market.warehouse,interaction.guild.id,self.character_id)
        lines=[f"#{r['id']}｜{r['product_name']}｜{r['mode']}｜{r['stock']} 件｜售價 {r['retail_price']}" for r in rows]
        await interaction.followup.send('📦 **我的直播帶貨倉庫**\n'+('\n'.join(lines[:24]) if lines else '尚未進貨。'),ephemeral=True)
    @discord.ui.button(label='⚙️ 官方發布貨源（群主）',row=2)
    async def official(self,interaction,button):
        if not interaction.guild or interaction.user.id!=interaction.guild.owner_id:
            await interaction.response.send_message('只有伺服器群主可以發布官方貨源。',ephemeral=True)
            return
        await interaction.response.send_modal(OfficialQuoteModal())

class AcquireModal(discord.ui.Modal,title='批發市場進貨'):
    qty=discord.ui.TextInput(label='進貨數量',placeholder='例如10',max_length=6)
    def __init__(self,cid,offer_id):
        super().__init__();self.cid=int(cid);self.offer_id=int(offer_id)
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result=await asyncio.to_thread(market.acquire,interaction.guild.id,interaction.user.id,self.cid,
                self.offer_id,int(str(self.qty.value)),interaction.id)
            await interaction.followup.send(f"✅ 已入庫 {result['quantity']} 件，{result['mode']}；本次付款 {result['amount_paid']:,} 金幣。倉庫批次 #{result['warehouse_id']}。",ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            LOG.exception('批發進貨失敗')
            await interaction.followup.send('⚠️ 進貨失敗，請查看 Railway 日誌。',ephemeral=True)

class BrandQuoteModal(discord.ui.Modal,title='玩家品牌發布批發報價'):
    business_id=discord.ui.TextInput(label='已核准品牌 ID',max_length=40)
    product_id=discord.ui.TextInput(label='商品 ID（需在角色背包有實物）',max_length=16)
    qty=discord.ui.TextInput(label='供貨數量',max_length=8)
    price=discord.ui.TextInput(label='每件批發價（金幣）',max_length=12)
    mode=discord.ui.TextInput(label='買斷 buyout／寄售 consignment',placeholder='buyout',max_length=16)
    def __init__(self,character_id):
        super().__init__();self.cid=int(character_id)
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            offer_id=await asyncio.to_thread(market.publish,interaction.guild.id,interaction.user.id,self.cid,
                int(str(self.product_id.value)),int(str(self.price.value)),int(str(self.qty.value)),
                str(self.mode.value).strip().lower(),business_id=str(self.business_id.value).strip())
            await interaction.followup.send(f'✅ 品牌供貨單 #{offer_id} 已建立，商品實物已保留；其他玩家可從批發市場進貨。',ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            LOG.exception('品牌批發報價失敗')
            await interaction.followup.send('⚠️ 品牌報價失敗，請查看日誌。',ephemeral=True)

class OfficialQuoteModal(discord.ui.Modal,title='群主發布官方批發貨源'):
    product_id=discord.ui.TextInput(label='商城商品 ID',max_length=16)
    price=discord.ui.TextInput(label='每件批發價',max_length=12)
    qty=discord.ui.TextInput(label='供應數量',max_length=8)
    async def on_submit(self,interaction):
        if not interaction.guild or interaction.user.id!=interaction.guild.owner_id:
            await interaction.response.send_message('只有群主可以發布官方貨源。',ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        try:
            oid=await asyncio.to_thread(market.publish_official_verified,interaction.guild.id,
                int(str(self.product_id.value)),int(str(self.price.value)),int(str(self.qty.value)),verified_owner=True)
            await interaction.followup.send(f'✅ 官方供貨單 #{oid} 已發布。',ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            LOG.exception('官方批發報價失敗')
            await interaction.followup.send('⚠️ 無法發布貨源，請查看日誌。',ephemeral=True)

def describe_offers(rows):
    lines=[f"#{x['id']}｜{x['product_name']}｜{('官方' if x['supplier_kind']=='official' else '品牌')}｜{('買斷' if x['mode']=='buyout' else '寄售')}｜批發 {x['wholesale_price']:,}｜庫存 {x['stock']}" for x in rows[:20]]
    return '🏬 **批發市場**｜官方＋玩家品牌\n'+('\n'.join(lines) if lines else '目前尚無上架批發商品，請由群主或品牌持有人發布報價。')+'\n\n帶貨需先進貨至少五種不同商品。'
