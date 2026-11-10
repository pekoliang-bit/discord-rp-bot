"""玩家商鋪入口：沿用 personal_shops / storefront，獨立補上職缺與錄取帳本。

不增加斜線指令、不複製商品與訂單、不會自動扣薪資。店鋪核准後的
personal_shops 紀錄由既有 player_shops.approve_store 建立。
"""
from __future__ import annotations

import discord
from core.database import get_connection, transaction
from core.permissions import Actor
from modules import storefront
from modules.zzz_shop_home import ShopHome


def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_personal_store_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, shop_id INTEGER NOT NULL,
            title TEXT NOT NULL, description TEXT NOT NULL,
            salary INTEGER NOT NULL CHECK(salary>=0),
            pay_cycle_days INTEGER NOT NULL CHECK(pay_cycle_days IN (7,14,30)),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_personal_store_job_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, job_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            decided_at TEXT,
            UNIQUE(guild_id,job_id,character_id),
            CHECK(status IN ('pending','accepted','rejected')) )''')


def _owned_shop(actor, shop_id):
    with get_connection() as db:
        row = db.execute('''SELECT s.id, s.name, s.employee_limit, s.status
          FROM personal_shops s JOIN characters c
          ON c.guild_id=s.guild_id AND c.id=s.character_id
          WHERE s.guild_id=? AND s.id=? AND c.owner_id=?''',
          (actor.guild_id, int(shop_id), actor.user_id)).fetchone()
    return dict(row) if row else None


def jobs(gid, shop_id):
    initialize()
    with get_connection() as db:
        return [dict(row) for row in db.execute('''SELECT * FROM rp_personal_store_jobs
          WHERE guild_id=? AND shop_id=? AND active=1 ORDER BY id''',
          (str(gid), int(shop_id))).fetchall()]


def post_job(actor, shop_id, title, description, salary, cycle):
    title, description = title.strip(), description.strip()
    if not 1 <= len(title) <= 80 or not 1 <= len(description) <= 500:
        raise ValueError('職稱需 1～80 字，工作介紹需 1～500 字。')
    salary, cycle = int(salary), int(cycle)
    if salary < 0 or cycle not in (7,14,30):
        raise ValueError('薪資需非負；週期只能是 7、14 或 30 天。')
    with transaction() as db:
        shop = storefront.require_shop_manager(db,actor,int(shop_id))
        if shop['status'] not in ('open','supervised'):
            raise ValueError('商鋪尚未營業。')
        cur=db.execute('''INSERT INTO rp_personal_store_jobs
          (guild_id,shop_id,title,description,salary,pay_cycle_days) VALUES (?,?,?,?,?,?)''',
          (actor.guild_id,int(shop_id),title,description,salary,cycle))
        return cur.lastrowid


def apply_job(actor, shop_id, job_id, character_id):
    with transaction() as db:
        row=db.execute('''SELECT j.id,s.character_id AS owner_character
          FROM rp_personal_store_jobs j JOIN personal_shops s
          ON s.guild_id=j.guild_id AND s.id=j.shop_id
          WHERE j.guild_id=? AND j.shop_id=? AND j.id=? AND j.active=1
            AND s.status IN ('open','supervised')''',
          (actor.guild_id,int(shop_id),int(job_id))).fetchone()
        if row is None: raise ValueError('職缺已下架或不存在。')
        character=db.execute('''SELECT owner_id,status FROM characters
          WHERE guild_id=? AND id=?''',(actor.guild_id,int(character_id))).fetchone()
        if not character or str(character['owner_id'])!=str(actor.user_id) or character['status']!='active':
            raise ValueError('只能用本人已啟用角色應徵。')
        if int(row['owner_character'])==int(character_id):
            raise ValueError('店主不能應徵自己的商鋪。')
        existing=db.execute('''SELECT status FROM rp_personal_store_job_applications
          WHERE guild_id=? AND job_id=? AND character_id=?''',
          (actor.guild_id,int(job_id),int(character_id))).fetchone()
        if existing: raise ValueError('你已申請這個職缺。')
        db.execute('''INSERT INTO rp_personal_store_job_applications
          (guild_id,job_id,character_id) VALUES (?,?,?)''',
          (actor.guild_id,int(job_id),int(character_id)))


def pending_apps(actor,shop_id):
    if not _owned_shop(actor,shop_id): raise PermissionError('只有店主能管理應徵。')
    initialize()
    with get_connection() as db:
        return [dict(r) for r in db.execute('''SELECT a.id,j.title,c.name,
          j.salary,j.pay_cycle_days FROM rp_personal_store_job_applications a
          JOIN rp_personal_store_jobs j ON j.guild_id=a.guild_id AND j.id=a.job_id
          JOIN characters c ON c.guild_id=a.guild_id AND c.id=a.character_id
          WHERE a.guild_id=? AND j.shop_id=? AND a.status='pending'
          ORDER BY a.id LIMIT 100''',(actor.guild_id,int(shop_id))).fetchall()]


def decide_application(actor,shop_id,app_id,approve):
    with transaction() as db:
        shop=storefront.require_shop_manager(db,actor,int(shop_id))
        applicant=db.execute('''SELECT a.id,a.character_id
          FROM rp_personal_store_job_applications a
          JOIN rp_personal_store_jobs j ON j.guild_id=a.guild_id AND j.id=a.job_id
          WHERE a.guild_id=? AND j.shop_id=? AND a.id=? AND a.status='pending' ''',
          (actor.guild_id,int(shop_id),int(app_id))).fetchone()
        if not applicant: raise ValueError('找不到待處理的應徵。')
        if approve:
            count=db.execute('''SELECT COUNT(DISTINCT a.character_id)
              FROM rp_personal_store_job_applications a
              JOIN rp_personal_store_jobs j ON j.guild_id=a.guild_id AND j.id=a.job_id
              WHERE a.guild_id=? AND j.shop_id=? AND a.status='accepted' ''',
              (actor.guild_id,int(shop_id))).fetchone()[0]
            if count>=int(shop['employee_limit']): raise ValueError('聘用人數已達商鋪上限。')
        db.execute('''UPDATE rp_personal_store_job_applications SET status=?,
          decided_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?''',
          ('accepted' if approve else 'rejected',actor.guild_id,int(app_id)))


async def _error(i, error):
    msg=f'⚠️ {str(error)[:1700]}'
    if i.response.is_done(): await i.followup.send(msg,ephemeral=True)
    else: await i.response.send_message(msg,ephemeral=True)


class ShopPicker(discord.ui.Select):
    def __init__(self,rows):
        self.rows={str(x['id']):x for x in rows}
        super().__init__(placeholder='選擇現有玩家商鋪',row=0,options=[
          discord.SelectOption(label=x['name'][:100],value=str(x['id']),description=x['category'][:100])
          for x in rows])

    async def callback(self,i):
        item=self.rows.get(self.values[0]);actor=Actor.from_interaction(i)
        if not item:return await _error(i,'商鋪清單失效，請重新開啟。')
        await i.response.edit_message(content=detail_text(i.guild_id,item['id']),
            view=StoreDetail(actor,item['id'],self.view.character_id))


class StoreList(discord.ui.View):
    def __init__(self,actor,character_id,page=0):
        super().__init__(timeout=600)
        self.actor=actor;self.character_id=int(character_id)
        self.rows=storefront.list_stores(actor.guild_id)
        self.page=max(0,min(int(page),max(0,(len(self.rows)-1)//25)))
        items=self.rows[self.page*25:self.page*25+25]
        if items:self.add_item(ShopPicker(items))
        self.previous.disabled=self.page==0
        self.next_page.disabled=(self.page+1)*25>=len(self.rows)

    async def interaction_check(self,i):
        if str(i.user.id)!=str(self.actor.user_id) or str(i.guild_id)!=str(self.actor.guild_id):
            await _error(i,'這不是你的商鋪介面。');return False
        return True

    @discord.ui.button(label='◀ 上一頁',row=1)
    async def previous(self,i,b):
        v=StoreList(self.actor,self.character_id,self.page-1)
        await i.response.edit_message(content=list_text(v),view=v)

    @discord.ui.button(label='下一頁 ▶',row=1)
    async def next_page(self,i,b):
        v=StoreList(self.actor,self.character_id,self.page+1)
        await i.response.edit_message(content=list_text(v),view=v)

    @discord.ui.button(label='⬅ 返回商城',row=2)
    async def back(self,i,b):
        from modules.shop import _hub
        await _hub(i,self.character_id,edit=True)


def list_text(view):
    return f'🏪 **玩家商鋪**｜第 {view.page+1}/{max(1,(len(view.rows)+24)//25)} 頁\n從下拉選單選擇目前營業中的商鋪。' if view.rows else '🏪 目前沒有已開業的玩家商鋪。'


def detail_text(gid,sid):
    with get_connection() as db:
        shop=storefront.require_shop(db,str(gid),int(sid))
    products=storefront.list_products(str(gid),int(sid))
    output=[f"🏪 **{shop['name']}**｜{shop['category']}",f'上架商品：{len(products)} 件（下拉選擇商品後購買）','']
    for p in products[:12]:
        output.append(f"• {p['name']}｜{int(p['price']):,} 金幣｜庫存：{p['stock'] if p['stock'] is not None else '不限'}")
    available=jobs(gid,sid)
    if available:
        output.append('\n👔 **招聘中**')
        for job in available[:6]:output.append(f"• {job['title']}｜{job['salary']:,} 金幣／{job['pay_cycle_days']} 天\n  {job['description'][:90]}")
    return '\n'.join(output)[:1850]


class ProductSelect(discord.ui.Select):
    def __init__(self,products):
        super().__init__(placeholder='選擇要購買的商品',row=0,options=[discord.SelectOption(
            label=p['name'][:100],value=str(p['id']),description=f"{p['price']:,} 金幣") for p in products[:25]])
    async def callback(self,i):
        self.view.product_id=int(self.values[0]);await i.response.edit_message(
            content=detail_text(i.guild_id,self.view.shop_id)+f'\n\n✅ 已選擇商品，按「購買」繼續。',view=self.view)


class BuyModal(discord.ui.Modal,title='購買玩家商鋪商品'):
    quantity=discord.ui.TextInput(label='購買數量',default='1',max_length=5)
    def __init__(self,actor,cid,product_id):
        super().__init__();self.actor=actor;self.cid=cid;self.pid=product_id
    async def on_submit(self,i):
        try:
            result=storefront.purchase(self.actor,self.cid,self.pid,int(str(self.quantity)),f'player-store:{i.id}')
            await i.response.send_message(str(result),ephemeral=True)
        except Exception as exc:await _error(i,exc)


class JobSelect(discord.ui.Select):
    def __init__(self,items):
        super().__init__(placeholder='選擇要應徵的職位',row=1,options=[discord.SelectOption(
            label=x['title'][:100],value=str(x['id']),description=f"薪水 {x['salary']:,}／{x['pay_cycle_days']} 天") for x in items[:25]])
    async def callback(self,i):
        self.view.job_id=int(self.values[0]);await i.response.edit_message(
            content=detail_text(i.guild_id,self.view.shop_id)+'\n\n✅ 已選擇職缺，按「送出應徵」繼續。',view=self.view)


class StoreDetail(discord.ui.View):
    def __init__(self,actor,shop_id,character_id):
        super().__init__(timeout=600)
        self.actor=actor;self.shop_id=int(shop_id);self.cid=int(character_id)
        self.product_id=None;self.job_id=None
        products=storefront.list_products(actor.guild_id,shop_id)
        if products:self.add_item(ProductSelect(products))
        positions=jobs(actor.guild_id,shop_id)
        if positions:self.add_item(JobSelect(positions))
        self.buy.disabled=not bool(products)
        self.apply.disabled=not bool(positions)
        self.owner_tools.disabled=not bool(_owned_shop(actor,shop_id))

    async def interaction_check(self,i):
        if str(i.user.id)!=str(self.actor.user_id) or str(i.guild_id)!=str(self.actor.guild_id):
            await _error(i,'這不是你的商鋪面板。');return False
        return True

    @discord.ui.button(label='🛒 購買',style=discord.ButtonStyle.success,row=2)
    async def buy(self,i,b):
        if not self.product_id:return await _error(i,'請先用下拉選單選擇商品。')
        await i.response.send_modal(BuyModal(self.actor,self.cid,self.product_id))

    @discord.ui.button(label='👔 送出應徵',row=2)
    async def apply(self,i,b):
        if not self.job_id:return await _error(i,'請先選擇職位。')
        try:
            apply_job(self.actor,self.shop_id,self.job_id,self.cid)
            await i.response.send_message('✅ 應徵已送出，等待店家回覆。',ephemeral=True)
        except (ValueError,PermissionError) as exc:await _error(i,exc)

    @discord.ui.button(label='🏬 店家管理',row=3)
    async def owner_tools(self,i,b):
        view=OwnerPanel(self.actor,self.shop_id,self.cid)
        await i.response.edit_message(content='🏬 **店家管理**｜上架、改價與招聘',view=view)

    @discord.ui.button(label='⬅ 返回玩家商鋪',row=3)
    async def back(self,i,b):
        v=StoreList(self.actor,self.cid)
        await i.response.edit_message(content=list_text(v),view=v)


class AddProduct(discord.ui.Modal,title='店家上架新商品'):
    name=discord.ui.TextInput(label='商品名稱',max_length=100)
    category=discord.ui.TextInput(label='商品分類',max_length=60)
    description=discord.ui.TextInput(label='商品介紹',required=False,max_length=500)
    price=discord.ui.TextInput(label='售價（金幣）',max_length=15)
    stock=discord.ui.TextInput(label='庫存（留空代表不限）',required=False,max_length=8)
    def __init__(self,actor,sid):super().__init__();self.actor=actor;self.sid=sid
    async def on_submit(self,i):
        try:
            n=storefront.create_product(self.actor,self.sid,str(self.name),str(self.category),int(str(self.price)),
                stock=int(str(self.stock)) if str(self.stock).strip() else None,description=str(self.description))
            await i.response.send_message(f'✅ 商品已上架，編號 #{n}。',ephemeral=True)
        except Exception as exc:await _error(i,exc)


class EditProduct(discord.ui.Modal,title='修改已上架商品'):
    price=discord.ui.TextInput(label='新價格（金幣）',max_length=15)
    stock=discord.ui.TextInput(label='新庫存（留空不修改）',required=False,max_length=8)
    def __init__(self,actor,sid,pid):super().__init__();self.actor=actor;self.sid=sid;self.pid=pid
    async def on_submit(self,i):
        try:
            storefront.update_product(self.actor,self.sid,self.pid,price=int(str(self.price)),
                stock=int(str(self.stock)) if str(self.stock).strip() else None)
            await i.response.send_message('✅ 商品價格及庫存已更新。',ephemeral=True)
        except Exception as exc:await _error(i,exc)


class OwnerProductSelect(discord.ui.Select):
    def __init__(self,products):
        super().__init__(placeholder='選擇要改價的商品',row=0,options=[discord.SelectOption(
            label=x['name'][:90],value=str(x['id']),description=f"{x['price']:,} 金幣") for x in products[:25]])
    async def callback(self,i):
        await i.response.send_modal(EditProduct(self.view.actor,self.view.sid,int(self.values[0])))


class AddJob(discord.ui.Modal,title='新增店鋪職缺'):
    title_field=discord.ui.TextInput(label='職稱',max_length=80)
    description=discord.ui.TextInput(label='工作介紹',style=discord.TextStyle.paragraph,max_length=500)
    salary=discord.ui.TextInput(label='薪資（金幣／週期）',max_length=12)
    cycle=discord.ui.TextInput(label='發薪週期（7／14／30 天）',default='7',max_length=2)
    def __init__(self,actor,sid):super().__init__();self.actor=actor;self.sid=sid
    async def on_submit(self,i):
        try:
            n=post_job(self.actor,self.sid,str(self.title_field),str(self.description),int(str(self.salary)),int(str(self.cycle)))
            await i.response.send_message(f'✅ 職缺 #{n} 已發布，應徵錄取後將列入員工名單。薪資自動撥款尚未啟用。',ephemeral=True)
        except Exception as exc:await _error(i,exc)


class PendingAppSelect(discord.ui.Select):
    def __init__(self,applications):
        super().__init__(placeholder='選擇應徵者',row=0,options=[discord.SelectOption(
            label=f"{a['name']}｜{a['title']}"[:100],value=str(a['id']),description=f"薪資 {a['salary']:,}") for a in applications[:25]])
    async def callback(self,i):
        self.view.application_id=int(self.values[0]);await i.response.edit_message(content='選擇「錄取」或「拒絕」：',view=self.view)


class PendingPanel(discord.ui.View):
    def __init__(self,actor,sid,cid):
        super().__init__(timeout=600);self.actor=actor;self.sid=sid;self.cid=cid;self.application_id=None
        applications=pending_apps(actor,sid)
        if applications:self.add_item(PendingAppSelect(applications))
        self.approve.disabled=self.reject.disabled=not bool(applications)
    async def interaction_check(self,i):
        if str(i.user.id)!=str(self.actor.user_id):await _error(i,'不是你的店家面板');return False
        return True
    async def _decide(self,i,approve):
        if not self.application_id:return await _error(i,'請先選擇應徵者。')
        try:
            decide_application(self.actor,self.sid,self.application_id,approve)
            await i.response.edit_message(content='✅ 應徵已處理。',view=OwnerPanel(self.actor,self.sid,self.cid))
        except (ValueError,PermissionError) as exc:await _error(i,exc)
    @discord.ui.button(label='✅ 錄取',style=discord.ButtonStyle.success,row=1)
    async def approve(self,i,b):await self._decide(i,True)
    @discord.ui.button(label='❌ 拒絕',style=discord.ButtonStyle.danger,row=1)
    async def reject(self,i,b):await self._decide(i,False)
    @discord.ui.button(label='⬅ 返回店家管理',row=2)
    async def back(self,i,b):await i.response.edit_message(content='🏬 店家管理',view=OwnerPanel(self.actor,self.sid,self.cid))


class OwnerPanel(discord.ui.View):
    def __init__(self,actor,sid,cid):
        super().__init__(timeout=600);self.actor=actor;self.sid=sid;self.cid=cid
        with get_connection() as db:storefront.require_shop_manager(db,actor,sid)
    async def interaction_check(self,i):
        if str(i.user.id)!=str(self.actor.user_id):await _error(i,'不是你的店家面板');return False
        return True
    @discord.ui.button(label='➕ 上架商品',style=discord.ButtonStyle.primary,row=0)
    async def new(self,i,b):await i.response.send_modal(AddProduct(self.actor,self.sid))
    @discord.ui.button(label='✏️ 改價／庫存',row=0)
    async def edit(self,i,b):
        products=storefront.list_products(self.actor.guild_id,self.sid)
        if not products:return await _error(i,'目前沒有可修改的上架商品。')
        v=OwnerEditPanel(self.actor,self.sid,self.cid,products)
        await i.response.edit_message(content='選擇要修改的商品：',view=v)
    @discord.ui.button(label='👔 發布職缺',row=1)
    async def job(self,i,b):await i.response.send_modal(AddJob(self.actor,self.sid))
    @discord.ui.button(label='📋 審核應徵',row=1)
    async def applications(self,i,b):
        v=PendingPanel(self.actor,self.sid,self.cid)
        await i.response.edit_message(content='📋 待審應徵',view=v)
    @discord.ui.button(label='⬅ 返回商鋪',row=2)
    async def back(self,i,b):await i.response.edit_message(content=detail_text(i.guild_id,self.sid),view=StoreDetail(self.actor,self.sid,self.cid))


class OwnerEditPanel(discord.ui.View):
    def __init__(self,actor,sid,cid,products):
        super().__init__(timeout=600);self.actor=actor;self.sid=sid;self.cid=cid
        self.add_item(OwnerProductSelect(products))
    async def interaction_check(self,i):
        if str(i.user.id)!=str(self.actor.user_id):await _error(i,'不是你的店家面板');return False
        return True
    @discord.ui.button(label='⬅ 返回店家管理',row=1)
    async def back(self,i,b):await i.response.edit_message(content='🏬 店家管理',view=OwnerPanel(self.actor,self.sid,self.cid))


async def show_store_list(i,character_id):
    from modules import player_shops
    player_shops.initialize();storefront.initialize_storefront();initialize()
    actor=Actor.from_interaction(i)
    v=StoreList(actor,character_id)
    if i.response.is_done():await i.followup.send(list_text(v),view=v,ephemeral=True)
    else:await i.response.edit_message(content=list_text(v),view=v)


class ShopHubView(ShopHome):
    """商城首頁僅保留官方下拉、玩家商鋪與切換角色。"""
    def __init__(self,owner_id,character_id,guild_id):
        super().__init__(owner_id,character_id,False,'',guild_id)
        for child in list(self.children):
            if isinstance(child,discord.ui.Button):self.remove_item(child)
        switch=discord.ui.Button(label='🔄 切換角色',style=discord.ButtonStyle.primary,row=1)
        personal=discord.ui.Button(label='🏪 玩家商鋪',style=discord.ButtonStyle.secondary,row=1)
        async def switch_callback(i):
            from modules.shop import ShopCharacterPicker,_shop_owned_characters
            actor=Actor.from_interaction(i)
            choices=_shop_owned_characters(actor)
            if not choices:return await _error(i,'沒有已啟用角色。')
            await i.response.edit_message(content='請選擇本次購物角色：',view=ShopCharacterPicker(i,choices))
        async def personal_callback(i):
            await show_store_list(i,self.character_id)
        switch.callback=switch_callback;personal.callback=personal_callback
        self.add_item(switch);self.add_item(personal)
        action = discord.ui.Button(label='⚡ 購買行動值',style=discord.ButtonStyle.primary,row=2)
        async def action_callback(i):
            if i.user.id != self.owner_id or i.guild_id != self.guild_id:
                return await _error(i,'這不是你的商城面板。')
            from modules.zzz_shop_home import ActionBuyModal
            await i.response.send_modal(ActionBuyModal(self))
        action.callback=action_callback
        self.add_item(action)
