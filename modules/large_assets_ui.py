"""/資產 正式 Discord 單指令入口：官方市場、個人持有、二手、拍賣、出租、管理。

所有破壞性動作再確認使用者與目前角色；元老贈房須使用已驗證發獎流程。
"""
from __future__ import annotations
import asyncio
import logging
import time
import discord
from discord import app_commands
from discord.ext import commands, tasks
from core.permissions import Actor
from core import large_assets as assets
from core import asset_completions as extras
from modules.character_switch import require_current_character
LOG=logging.getLogger(__name__)

def context(interaction):
    actor=Actor.from_interaction(interaction)
    current=require_current_character(actor)
    return actor,int(current['id'])

def clean_error(exc):
    if isinstance(exc,(ValueError,PermissionError,assets.AssetError)):
        return '⚠️ '+str(exc)[:1700]
    LOG.exception('資產操作失敗',exc_info=exc)
    return '⚠️ 操作未完成。請將此錯誤提供管理員查核；不應重複扣款。'

class Form(discord.ui.Modal):
    def __init__(self,title,fields,action):
        super().__init__(title=title,timeout=300)
        self.action=action
        for key,label,default in fields:
            item=discord.ui.TextInput(label=label,placeholder=default or None,required=True,max_length=120)
            self.add_item(item)
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            actor,cid=context(interaction)
            values=[field.value.strip() for field in self.children]
            result=await asyncio.to_thread(self.action,actor,cid,values)
            await interaction.followup.send(str(result)[:1900],ephemeral=True)
            # 使用既有角色專屬論壇固定「通知」帖子，不新增私人文字頻道。
            try:
                from modules.notifications import queue_notification
                await asyncio.to_thread(queue_notification,int(actor.guild_id),'private','role',str(result)[:1200],
                    character_id=cid,priority='normal')
            except Exception:
                LOG.exception('資產交易完成，但私人通知排隊失敗；不回滾已完成交易')
        except Exception as exc:
            await interaction.followup.send(clean_error(exc),ephemeral=True)

class AssetView(discord.ui.View):
    def __init__(self,uid):
        super().__init__(timeout=540)
        self.uid=str(uid)
    async def interaction_check(self,interaction):
        if str(interaction.user.id)!=self.uid:
            await interaction.response.send_message('請使用你自己的 /資產 介面。',ephemeral=True)
            return False
        return True
    @discord.ui.button(label='官方商品',style=discord.ButtonStyle.primary,row=0,emoji='🛒')
    async def catalog(self,interaction,button):
        try:
            choices=await asyncio.to_thread(assets.catalog,None,interaction.guild_id)
            text='\n'.join(f"`{x['code']}` {x['name']}｜{x['base_price']:,} 金幣"+("（需個別定價）" if x['min_price_only'] else '') for x in choices)
            # split into <= 1900 char chunks
            chunks=[text[i:i+1700] for i in range(0,len(text),1700)]
            await interaction.response.send_message('**🏛️ 官方大型資產商品庫**\n'+chunks[0],ephemeral=True)
            for chunk in chunks[1:]:await interaction.followup.send(chunk,ephemeral=True)
        except Exception as exc:await interaction.response.send_message(clean_error(exc),ephemeral=True)
    @discord.ui.button(label='購買資產',style=discord.ButtonStyle.success,row=0,emoji='💰')
    async def buy(self,interaction,button):
        def action(actor,cid,v):
            code=v[0].upper(); location=v[1]
            return f'✅ 購買成功，資產編號：{assets.buy(actor.guild_id,code,cid,actor.user_id,location)}。'
        await interaction.response.send_modal(Form('購買官方資產',[('code','商品代碼，例如 CAR-02','CAR-02'),('place','所在地／備註，沒有填「未指定」','未指定')],action))
    @discord.ui.button(label='我的資產',row=0,emoji='🏠')
    async def holdings(self,interaction,button):
        try:
            actor,cid=context(interaction)
            rows=await asyncio.to_thread(assets.holdings,actor.guild_id,cid)
            if not rows:msg='目前沒有大型資產。可由「購買資產」進入官方市場。'
            else:msg='\n'.join(f"`#{x['id']}` {x['label']}｜估值 {x['appraised_value']:,}｜持分 {x['basis_points']/100:.0f}%" for x in rows[:24])
            await interaction.response.send_message(msg[:1900],ephemeral=True)
        except Exception as exc:await interaction.response.send_message(clean_error(exc),ephemeral=True)
    @discord.ui.button(label='二手／出租市場',row=0,emoji='🏷️')
    async def market(self,interaction,button):
        try:
            actor,cid=context(interaction)
            rows=await asyncio.to_thread(assets.market,actor.guild_id)
            msg='\n'.join(f"訂單 `#{x['id']}`｜資產 `#{x['asset_id']}` {x['label']}｜{x['mode']}｜{x['price']:,} 金幣" for x in rows[:30]) or '目前沒有有效掛單。'
            await interaction.response.send_message(msg[:1900],ephemeral=True)
        except Exception as exc:await interaction.response.send_message(clean_error(exc),ephemeral=True)
    @discord.ui.button(label='出售／出租／贈與',row=1,emoji='📋')
    async def offer(self,interaction,button):
        def action(actor,cid,v):
            mode=v[1].lower(); price=int(v[2]) if mode!='gift' else 0
            target=int(v[3]) if mode=='gift' else None
            if mode=='auction':duration=24
            else:duration=7
            order=assets.offer(actor.guild_id,int(v[0]),'character',cid,actor.user_id,mode,price,duration,target)
            return f'✅ 掛單建立成功：#{order}，模式 {mode}。贈與須由受贈者接受。'
        await interaction.response.send_modal(Form('刊登資產',[
            ('asset','持有資產編號','例如 15'),('mode','交易類型 sale/rent/auction/gift','sale'),
            ('price','售價／每週租金（贈與填 0）','10000'),('target','受贈角色編號（非贈與填 0）','0')],action))
    @discord.ui.button(label='購買／接受贈與',row=1,emoji='🤝')
    async def accept(self,interaction,button):
        def action(actor,cid,v):return f'✅ 資產 #{assets.accept(actor.guild_id,int(v[0]),cid,actor.user_id)} 交割成功。'
        await interaction.response.send_modal(Form('二手買賣／接受贈與',[('order','交易訂單編號','例如 8')],action))
    @discord.ui.button(label='拍賣出價',row=1,emoji='🔨')
    async def bid(self,interaction,button):
        def action(actor,cid,v):return f'✅ 已託管 {assets.bid(actor.guild_id,int(v[0]),cid,actor.user_id,int(v[1])):,} 金幣。被超越後自動退回。'
        await interaction.response.send_modal(Form('拍賣出價',[('order','拍賣訂單編號','例如 8'),('bid','出價金額（金幣）','10000')],action))
    @discord.ui.button(label='簽訂租約',row=1,emoji='🔑')
    async def rent(self,interaction,button):
        def action(actor,cid,v):
            with assets.get_connection() as db:
                row=db.execute("SELECT asset_id FROM rp_asset_orders WHERE guild_id=? AND id=? AND mode='rent'",(actor.guild_id,int(v[0]))).fetchone()
                if not row:raise assets.AssetError('出租掛單不存在')
            return f'✅ 租約 #{assets.lease(actor.guild_id,row["asset_id"],cid,actor.user_id,int(v[1]),int(v[0]))} 已簽訂。'
        await interaction.response.send_modal(Form('簽訂租屋契約',[('order','出租掛單編號','例如 8'),('weeks','租期週數 1／2／4','1')],action))
    @discord.ui.button(label='住所／維護費',row=2,emoji='🧾')
    async def manage(self,interaction,button):
        def action(actor,cid,v):
            if v[1]=='home':
                assets.residence(actor.guild_id,cid,int(v[0]),actor.user_id);return '✅ 已設定主要住所。'
            if v[1]=='maintenance':
                return f'✅ 已繳納 {assets.pay_maintenance(actor.guild_id,int(v[0]),cid,actor.user_id):,} 金幣。'
            raise assets.AssetError('操作類別須為 home 或 maintenance')
        await interaction.response.send_modal(Form('資產管理',[('asset','資產編號','例如 15'),('action','home 設住所／maintenance 繳費','home')],action))
    @discord.ui.button(label='官方租屋',row=2,emoji='🏢')
    async def official_rent(self,interaction,button):
        def action(actor,cid,v):
            item,lease_id=assets.rent_official(actor.guild_id,v[0].upper(),cid,actor.user_id,int(v[1]))
            return f'✅ 官方租屋簽約成功：住宅 #{item}，租約 #{lease_id}。可至住所管理設定主要住址。'
        await interaction.response.send_modal(Form('租賃官方住宅',[
          ('code','房型代碼 EST-01 至 EST-09','EST-01'),('weeks','租期週數 1／2／4','1')],action))
    @discord.ui.button(label='公司購買資產',row=3,emoji='🏢')
    async def company_buy(self,interaction,button):
        def action(actor,cid,v):
            asset=assets.buy_for_company(actor.guild_id,v[1].upper(),v[0],actor.user_id,v[2])
            return f'✅ 公司資產 #{asset} 購買成功；付款已從具授權的公司金庫完成。'
        await interaction.response.send_modal(Form('公司購入大型資產',[
            ('business','公司編號（非角色 ID）','公司識別碼'),('code','官方商品代碼','CAR-05'),
            ('location','所在地／備註','未指定')],action))
    @discord.ui.button(label='公司持有／出售',row=3,emoji='📊')
    async def company_manage(self,interaction,button):
        def action(actor,cid,v):
            business=v[0];action=v[1].lower()
            with assets.transaction() as db:
                assets._company(db,actor.guild_id,business,actor.user_id)
            if action=='list':
                owned=assets.holdings(actor.guild_id,business,'organization')
                return '\n'.join(f"#{a['id']} {a['label']} ({a['basis_points']/100:.2f}%)" for a in owned[:25]) or '公司名下沒有大型資產。'
            if action=='sale':
                order=assets.offer(actor.guild_id,int(v[2]),'organization',business,actor.user_id,'sale',int(v[3]))
                return f'✅ 公司資產已掛牌，訂單 #{order}。'
            raise assets.AssetError('操作限 list／sale')
        await interaction.response.send_modal(Form('公司資產管理',[
           ('business','公司編號','識別碼'),('action','list 查詢／sale 出售','list'),
           ('asset','要出售的資產編號，查詢填 0','0'),('price','售價，查詢填 0','0')],action))
    @discord.ui.button(label='共有產權邀請',row=4,emoji='🤝')
    async def share_request(self,interaction,button):
        def action(actor,cid,v):
            request=extras.request_share(actor.guild_id,int(v[0]),cid,int(v[1]),int(v[2]),actor.user_id)
            return f'✅ 已發出產權邀請 #{request}。受贈者須使用「接受共有」確認。'
        await interaction.response.send_modal(Form('發起共有產權邀請',[
            ('asset','資產編號','15'),('target','受贈角色 ID','101'),('share','轉移基點，例如 2500 = 25%','2500')],action))

    @discord.ui.button(label='接受共有',row=4,emoji='✅')
    async def share_accept(self,interaction,button):
        def action(actor,cid,v):
            return f'✅ 已登記共有資產 #{extras.accept_share(actor.guild_id,int(v[0]),cid,actor.user_id)}。'
        await interaction.response.send_modal(Form('接受共有產權邀請',[('request','共有邀請編號','1')],action))

    @discord.ui.button(label='租約續租',row=4,emoji='📆')
    async def lease_renew(self,interaction,button):
        def action(actor,cid,v):
            mode=v[0].strip().lower(); lease=int(v[1]);weeks=int(v[2])
            if mode=='official':
                end=extras.renew_lease(actor.guild_id,lease,cid,actor.user_id,weeks)
                return f'✅ 官方租約已延長，結束時間戳：{end}。'
            if mode=='request':
                owner=extras.request_player_renewal(actor.guild_id,lease,cid,actor.user_id,weeks)
                return f'✅ 續租申請已提交，房東角色 #{owner} 可確認。'
            if mode=='approve':
                end=extras.approve_player_renewal(actor.guild_id,lease,cid,actor.user_id)
                return f'✅ 房東已批准續租，結束時間戳：{end}。'
            raise assets.AssetError('操作請填 official、request 或 approve')
        await interaction.response.send_modal(Form('租約續租',[
            ('action','official 官方／request 申請／approve 批准','official'),
            ('lease','租約編號','5'),('weeks','週數 1／2／4，批准時填1','1')],action))

    @discord.ui.button(label='申請／同意退租',row=4,emoji='🔓')
    async def lease_end(self,interaction,button):
        def action(actor,cid,v):
            operation=v[0].strip().lower();lease=int(v[1])
            if operation=='request':
                kind,owner=extras.request_early_termination(actor.guild_id,lease,cid,actor.user_id)
                return f'✅ 已提交提前退租申請，房東類型 {kind}、編號 {owner}。'
            if operation=='approve':
                money=extras.approve_early_termination(actor.guild_id,lease,cid,actor.user_id)
                return f'✅ 雙方提前解約完成，退款 {money:,} 金幣，押金已返還。'
            raise assets.AssetError('操作請填 request 或 approve')
        await interaction.response.send_modal(Form('租約提前解約',[
            ('action','request 申請／approve 房東同意','request'),('lease','租約編號','5')],action))

    @discord.ui.button(label='共有維護費',row=4,emoji='🧾')
    async def shared_dues(self,interaction,button):
        def action(actor,cid,v):
            amount=extras.pay_shared_maintenance(actor.guild_id,int(v[0]),'character',cid,actor.user_id)
            return f'✅ 已支付共有持分維護費 {amount:,} 金幣。'
        await interaction.response.send_modal(Form('繳納共有資產維護費',[('asset','共有資產編號','15')],action))

    @discord.ui.button(label='官方新增資產',row=3,emoji='🛠️')
    async def admin_create(self,interaction,button):
        if not interaction.guild or interaction.guild.owner_id!=interaction.user.id:
            return await interaction.response.send_message('僅伺服器群主可以新增官方資產。',ephemeral=True)
        def action(actor,cid,v):
            if str(actor.user_id)!=str(interaction.guild.owner_id):raise assets.AssetError('沒有群主管理權限')
            code=extras.create_template(actor.guild_id,v[0],v[1],v[2],int(v[3]),v[4] in ('1','是','true'))
            return f'✅ 官方資產 {code} 已新增。'
        await interaction.response.send_modal(Form('官方新增大型資產',[
            ('code','新商品代碼 CAR-11 等','CAR-11'),('category','資產分類，例如 車輛','車輛'),
            ('name','商品名稱','官方車款'),('price','售價（金幣；0為需個別定價）','100000'),
            ('luxury','豪華標記 1／0','0')],action))

    @discord.ui.button(label='官方修改資產',row=3,emoji='✏️')
    async def admin_edit(self,interaction,button):
        if not interaction.guild or interaction.guild.owner_id!=interaction.user.id:
            return await interaction.response.send_message('僅伺服器群主可以修改官方資產。',ephemeral=True)
        def action(actor,cid,v):
            if str(actor.user_id)!=str(interaction.guild.owner_id):raise assets.AssetError('沒有群主管理權限')
            if v[0].upper().startswith('ASSET#'):
                asset_id=int(v[0].split('#',1)[1])
                old,new=extras.appraise_asset(actor.guild_id,asset_id,int(v[2]),actor.user_id)
                return f'✅ 資產 #{asset_id} 市場估值由 {old:,} 調整為 {new:,} 金幣。'
            code=extras.update_template(actor.guild_id,v[0],actor.user_id,name=v[1],base_price=int(v[2]),luxury=v[3] in ('1','是','true'),active=v[4] in ('1','是','true'))
            return f'✅ 官方商品 {code} 設定已更新。既有成交價不追溯修改。'
        await interaction.response.send_modal(Form('編輯官方資產模板',[
            ('code','模板代碼／估值填 ASSET#資產ID','CAR-02'),('name','新商品名稱','都市小型車'),
            ('price','模板售價／資產的新估值','30000'),('luxury','豪華標記 1／0','0'),('active','是否上架 1／0','1')],action))

    @discord.ui.button(label='補登元老住宅',row=3,emoji='🎁')
    async def admin_elder(self,interaction,button):
        if not interaction.guild or interaction.guild.owner_id!=interaction.user.id:
            return await interaction.response.send_message('僅伺服器群主可補登元老獎勵。',ephemeral=True)
        def action(actor,cid,v):
            if str(actor.user_id)!=str(interaction.guild.owner_id):raise assets.AssetError('沒有群主管理權限')
            asset,created,result=extras.repair_elder_home_for_unlocked(actor.guild_id,int(v[0]))
            return f'✅ 元老單間資產 #{asset}；'+('已完成補建。' if created else '原有登記不重複發放。')
        await interaction.response.send_modal(Form('補登已解鎖開城元老房間',[('character','已解鎖元老稱號的角色 ID','101')],action))

    @discord.ui.button(label='銀行與貸款',row=2,emoji='🏦')
    async def finance(self,interaction,button):
        await interaction.response.send_message('資產貸款、分期及抵押審核仍使用既有 `/申請` → 金融事務，還款使用 `/銀行`。本介面不會繞過金融審核或自行產生貸款。',ephemeral=True)

class LargeAssets(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self.asset_jobs.start()
    def cog_unload(self):
        self.asset_jobs.cancel()
    @tasks.loop(hours=12)
    async def asset_jobs(self):
        for guild in self.bot.guilds:
            try:
                await asyncio.to_thread(assets.generate_maintenance,guild.id)
                await asyncio.to_thread(assets.apply_depreciation,guild.id)
                await asyncio.to_thread(extras.dispatch_maintenance_notices,guild.id)
                await asyncio.to_thread(assets.close_expired_leases,guild.id)
                await asyncio.to_thread(assets.expire_unfilled_offers,guild.id)
                # 到期拍賣逐件結算；異常保持待人工處置，不私自挪用託管金幣。
                # 只結算到期掛單；退款/交割均由核心同筆事務完成。
                with assets.get_connection() as db:
                    pending=[r['id'] for r in db.execute("SELECT id FROM rp_asset_orders WHERE guild_id=? AND mode='auction' AND status='open' AND ends_at<=?",(str(guild.id),int(time.time())))]
                for order in pending:
                    try:await asyncio.to_thread(assets.settle_auction,guild.id,order)
                    except Exception:LOG.exception('資產拍賣 #%s 結算失敗',order)
            except Exception:LOG.exception('伺服器 %s 的資產定期任務執行失敗',guild.id)
    @asset_jobs.before_loop
    async def before_asset_jobs(self):
        await self.bot.wait_until_ready()
    @app_commands.command(name='資產',description='大型資產：購買、出租、二手市場、拍賣與產權管理')
    async def asset_command(self,interaction:discord.Interaction):
        try:
            actor,cid=context(interaction)
            await interaction.response.send_message(
                f'🏛️ **大型資產管理中心**\n目前角色：`#{cid}`\n'
                '官方購買、二手交易、租賃與拍賣均須確認實際產權。\n'
                '貸款仍透過既有 `/申請` 與 `/銀行` 處理。',
                view=AssetView(actor.user_id),ephemeral=True)
        except Exception as exc:await interaction.response.send_message(clean_error(exc),ephemeral=True)

async def setup(bot):
    await asyncio.to_thread(assets.init_assets)
    await bot.add_cog(LargeAssets(bot))
