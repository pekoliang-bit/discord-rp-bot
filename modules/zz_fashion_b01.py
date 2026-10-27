"""服飾精品店 B01｜50 款預設服飾、永久商品、穿戴容貌加成。

部署：放進 modules/zz_fashion_b01.py。
依賴既有 modules.shop、core.database，不建立新的頂層斜線指令。
登入後自動為每個伺服器建立 50 件預設商品，重啟不重複建立。
"""
from __future__ import annotations

import logging
import sqlite3

import discord
from discord.ext import commands

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)
CATEGORY = "服飾精品店 B01"
BRANDS = {
    "自由前線": "平價大眾｜簡約百搭、風格多變",
    "浮島漫行": "平價日系｜慵懶日常、靜奢大地色",
    "墨骨": "平價至高價｜新中式新浪潮、東方極簡雅痞",
    "CHRONOS LAB 時序研究所": "中價機能時裝｜賽博龐克、近未來機能都市",
    "虛像折射 ANOMALIE": "中高價｜先鋒解構、中性暗黑前衛",
    "劇院午夜 THÉÂTRE DE MINUIT": "高價｜新古典浪漫、巴洛克哥特復古",
}

# (商品代碼, 品牌, 名稱, 適用, 售價, 容貌點數)
ITEMS = [
    ("FS-001", "自由前線", "純棉百搭短袖T恤", "中性", 600, 2),
    ("FS-002", "自由前線", "都市直筒牛仔褲", "男", 900, 3),
    ("FS-003", "自由前線", "經典純色連帽外套", "中性", 1200, 4),
    ("FS-004", "自由前線", "基礎款休閒襯衫", "男", 900, 3),
    ("FS-005", "自由前線", "日常百褶短裙", "女", 900, 3),
    ("FS-006", "自由前線", "輕量帆布運動鞋", "中性", 1200, 4),
    ("FS-007", "自由前線", "簡約針織開襟衫", "女", 1500, 5),
    ("FS-008", "自由前線", "都市通勤西裝褲", "男", 1500, 5),
    ("FS-009", "自由前線", "基礎版型休閒西裝", "男", 2100, 7),
    ("FS-010", "自由前線", "自由宣言經典套裝", "女", 3000, 10),
    ("FS-011", "浮島漫行", "晨霧亞麻寬鬆襯衫", "中性", 1200, 4),
    ("FS-012", "浮島漫行", "麥田棉質闊腿褲", "男", 1500, 5),
    ("FS-013", "浮島漫行", "奶油色柔軟針織衫", "女", 1500, 5),
    ("FS-014", "浮島漫行", "林間午後碎花長裙", "女", 2100, 7),
    ("FS-015", "浮島漫行", "暖茶色羊毛開襟衫", "中性", 2400, 8),
    ("FS-016", "浮島漫行", "島嶼漫步帆布鞋", "中性", 1800, 6),
    ("FS-017", "浮島漫行", "秋日森林格紋圍巾", "中性", 1200, 4),
    ("FS-018", "浮島漫行", "白晝微光棉麻洋裝", "女", 2700, 9),
    ("FS-019", "浮島漫行", "月白色寬版風衣", "男", 3300, 11),
    ("FS-020", "浮島漫行", "浮島漫行・四季旅行套裝", "男", 4200, 14),
    ("FS-021", "墨骨", "墨線立領短袖", "中性", 900, 3),
    ("FS-022", "墨骨", "青竹改良盤扣襯衫", "男", 1800, 6),
    ("FS-023", "墨骨", "山嵐水墨直筒長褲", "男", 2100, 7),
    ("FS-024", "墨骨", "玄色雲紋長衫", "中性", 3300, 11),
    ("FS-025", "墨骨", "白鶴交領改良外套", "女", 4500, 15),
    ("FS-026", "墨骨", "墨竹暗紋長裙", "女", 5100, 17),
    ("FS-027", "墨骨", "青瓷色新中式套裝", "女", 6900, 23),
    ("FS-028", "墨骨", "山河入墨刺繡長袍", "男", 9500, 30),
    ("FS-029", "墨骨", "墨骨・無相高級套裝", "男", 15800, 40),
    ("FS-030", "墨骨", "千里江山訂製華服", "女", 28000, 55),
    ("FS-031", "CHRONOS LAB 時序研究所", "01型機能短袖", "中性", 2100, 7),
    ("FS-032", "CHRONOS LAB 時序研究所", "城市漫遊多口袋工裝褲", "男", 3000, 10),
    ("FS-033", "CHRONOS LAB 時序研究所", "NEON反光機能外套", "中性", 3900, 13),
    ("FS-034", "CHRONOS LAB 時序研究所", "時序偏移戰術長靴", "中性", 4500, 15),
    ("FS-035", "CHRONOS LAB 時序研究所", "數位幽靈防風披肩", "女", 5400, 18),
    ("FS-036", "CHRONOS LAB 時序研究所", "ZERO-G模組化套裝", "男", 6900, 23),
    ("FS-037", "CHRONOS LAB 時序研究所", "霓虹斷層都市機能服", "女", 8800, 28),
    ("FS-038", "CHRONOS LAB 時序研究所", "未來都市限定套裝", "中性", 12500, 35),
    ("FS-039", "虛像折射 ANOMALIE", "錯位剪裁黑色襯衫", "男", 5400, 18),
    ("FS-040", "虛像折射 ANOMALIE", "失序結構不對稱長褲", "中性", 6300, 21),
    ("FS-041", "虛像折射 ANOMALIE", "破碎輪廓解構外套", "女", 8100, 27),
    ("FS-042", "虛像折射 ANOMALIE", "虛像折射・雙面長風衣", "中性", 9800, 32),
    ("FS-043", "虛像折射 ANOMALIE", "異形幾何高級時裝", "女", 14800, 40),
    ("FS-044", "虛像折射 ANOMALIE", "ANOMALIE・虛無系列套裝", "男", 22000, 50),
    ("FS-045", "虛像折射 ANOMALIE", "鏡界錯位高級訂製服", "中性", 36000, 65),
    ("FS-046", "劇院午夜 THÉÂTRE DE MINUIT", "午夜玫瑰蕾絲禮服", "女", 12800, 35),
    ("FS-047", "劇院午夜 THÉÂTRE DE MINUIT", "黑天鵝絲絨宮廷套裝", "男", 18800, 45),
    ("FS-048", "劇院午夜 THÉÂTRE DE MINUIT", "月蝕詠嘆調巴洛克禮服", "女", 28800, 58),
    ("FS-049", "劇院午夜 THÉÂTRE DE MINUIT", "血色歌劇高級訂製禮服", "女", 48000, 75),
    ("FS-050", "劇院午夜 THÉÂTRE DE MINUIT", "永夜謝幕・皇家典藏禮服", "男", 88000, 100),
]


def validate():
    assert len(ITEMS) == 50 and len({x[0] for x in ITEMS}) == 50
    for code, brand, name, gender, price, points in ITEMS:
        if brand not in BRANDS or gender not in ("男", "女", "中性"):
            raise ValueError(f"商品分類錯誤：{code}")
        if price < 300 * points or points <= 0:
            raise ValueError(f"商品低於最低售價：{code}")


def ensure_schema():
    with transaction() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS rp_fashion_b01_codes (
            guild_id TEXT NOT NULL,
            code TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            brand TEXT NOT NULL,
            gender TEXT NOT NULL CHECK(gender IN ('男','女','中性')),
            PRIMARY KEY(guild_id,code),
            UNIQUE(guild_id,product_id)
        )""")
        # 這兩個觸發器只管 B01 商品，不會改動其他商城商品。
        conn.execute("""CREATE TRIGGER IF NOT EXISTS rp_b01_price_floor
            BEFORE UPDATE OF price ON shop_products
            WHEN EXISTS (SELECT 1 FROM rp_fashion_b01_codes m
                WHERE m.guild_id=OLD.guild_id AND m.product_id=OLD.id)
                AND (NEW.price<OLD.price OR NEW.price<NEW.effect_value*300)
            BEGIN SELECT RAISE(ABORT, 'B01 服飾價格不可下調或低於每點300金幣'); END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS rp_b01_fixed_effect
            BEFORE UPDATE OF effect_kind,effect_stat,effect_value ON shop_products
            WHEN EXISTS (SELECT 1 FROM rp_fashion_b01_codes m
                WHERE m.guild_id=OLD.guild_id AND m.product_id=OLD.id)
                AND (NEW.effect_kind!=OLD.effect_kind OR
                     COALESCE(NEW.effect_stat,'')!=COALESCE(OLD.effect_stat,'') OR
                     NEW.effect_value!=OLD.effect_value)
            BEGIN SELECT RAISE(ABORT, 'B01 服飾加成為固定數值'); END""")


def seed_guild(guild_id):
    guild_id = str(guild_id)
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)", (guild_id,))
        for code, brand, name, gender, price, points in ITEMS:
            row = conn.execute("""SELECT p.id,p.price,p.effect_value FROM rp_fashion_b01_codes m
                JOIN shop_products p ON p.guild_id=m.guild_id AND p.id=m.product_id
                WHERE m.guild_id=? AND m.code=?""", (guild_id,code)).fetchone()
            if row:
                # 歷史價格只會向上修正，絕不降價。
                floor = max(price, 300 * int(row["effect_value"]))
                if row["price"] < floor:
                    conn.execute("UPDATE shop_products SET price=? WHERE id=? AND guild_id=?", (floor,row["id"],guild_id))
                continue
            description = (f"{code}｜{brand}｜適用：{gender}。"
                f"{BRANDS[brand]}。非消耗型服飾；穿戴期間容貌 +{points}。")
            cur = conn.execute("""INSERT INTO shop_products
                (guild_id,name,category,description,price,stock,scope,
                effect_kind,effect_stat,effect_value,effect_context,created_at,updated_at)
                VALUES (?,?,?,?,?,NULL,'public','context','容貌',?,'穿戴中',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (guild_id,name,CATEGORY,description,price,points))
            conn.execute("""INSERT INTO rp_fashion_b01_codes
                (guild_id,code,product_id,brand,gender) VALUES (?,?,?,?,?)""",
                (guild_id,code,cur.lastrowid,brand,gender))


def get_fashion_page(guild_id, brand="全部", gender="全部", page=0):
    params = [str(guild_id)]
    filters = "m.guild_id=? AND p.active=1"
    if brand != "全部":
        filters += " AND m.brand=?"
        params.append(brand)
    if gender != "全部":
        filters += " AND m.gender=?"
        params.append(gender)
    with get_connection() as conn:
        total = conn.execute(f"""SELECT COUNT(*) AS total FROM rp_fashion_b01_codes m
            JOIN shop_products p ON p.guild_id=m.guild_id AND p.id=m.product_id
            WHERE {filters}""",params).fetchone()["total"]
        max_page = max(0,(total-1)//8)
        page = min(max(0,page),max_page)
        rows = conn.execute(f"""SELECT m.code,m.brand,m.gender,p.id,p.name,p.price,p.effect_value
            FROM rp_fashion_b01_codes m JOIN shop_products p
            ON p.guild_id=m.guild_id AND p.id=m.product_id
            WHERE {filters} ORDER BY m.code LIMIT 8 OFFSET ?""",
            [*params,page*8]).fetchall()
    lines = [f"👗 **{CATEGORY}**",f"品牌：{brand}｜適用：{gender}",f"商品：{total} 件｜第 {page+1}/{max_page+1} 頁",""]
    for r in rows:
        lines.append(f"**{r['code']}｜{r['name']}**")
        lines.append(f"{r['brand']}｜{r['gender']}｜💰 {r['price']:,}｜容貌 +{r['effect_value']}")
        lines.append(f"購買時輸入商城商品編號 **#{r['id']}**")
    return "\n".join(lines),page,max_page


def equipped_appearance_bonus(guild_id, character_id):
    with get_connection() as conn:
        row=conn.execute("""SELECT COALESCE(SUM(p.effect_value),0) AS points
            FROM shop_context_equipment e
            JOIN rp_fashion_b01_codes f ON f.guild_id=e.guild_id AND f.product_id=e.product_id
            JOIN shop_products p ON p.guild_id=e.guild_id AND p.id=e.product_id
            JOIN shop_inventory i ON i.guild_id=e.guild_id AND i.product_id=e.product_id
                AND i.character_id=e.character_id
            WHERE e.guild_id=? AND e.character_id=? AND i.quantity>0
            AND p.effect_kind='context' AND p.effect_stat='容貌'""",
            (str(guild_id),int(character_id))).fetchone()
    return int(row["points"])


class BrandSelect(discord.ui.Select):
    def __init__(self):
        options=[discord.SelectOption(label="全部品牌",value="全部")]
        options += [discord.SelectOption(label=b[:100],value=b) for b in BRANDS]
        super().__init__(placeholder="依品牌篩選",options=options,row=0)

    async def callback(self,interaction):
        v=self.view
        v.brand=self.values[0]
        v.page=0
        await v.refresh(interaction)


class GenderSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="依服飾適用性篩選",options=[
            discord.SelectOption(label="全部適用",value="全部"),
            discord.SelectOption(label="男裝",value="男"),
            discord.SelectOption(label="女裝",value="女"),
            discord.SelectOption(label="中性",value="中性"),
        ],row=1)

    async def callback(self,interaction):
        v=self.view
        v.gender=self.values[0]
        v.page=0
        await v.refresh(interaction)


class FashionView(discord.ui.View):
    def __init__(self,interaction,character_id,brand="全部",gender="全部",page=0):
        super().__init__(timeout=900)
        self.owner_id=interaction.user.id
        self.guild_id=interaction.guild_id
        self.character_id=character_id
        self.brand=brand
        self.gender=gender
        _,self.page,last=get_fashion_page(self.guild_id,brand,gender,page)
        self.add_item(BrandSelect())
        self.add_item(GenderSelect())
        self.previous.disabled=self.page<=0
        self.next.disabled=self.page>=last

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id or interaction.guild_id!=self.guild_id:
            await interaction.response.send_message("請使用自己的商店面板。",ephemeral=True)
            return False
        return True

    async def refresh(self,interaction):
        content,page,_=get_fashion_page(self.guild_id,self.brand,self.gender,self.page)
        await interaction.response.edit_message(content=content,view=FashionView(interaction,self.character_id,self.brand,self.gender,page))

    @discord.ui.button(label="⬅️ 上一頁",style=discord.ButtonStyle.secondary,row=2)
    async def previous(self,interaction,button):
        self.page-=1
        await self.refresh(interaction)

    @discord.ui.button(label="➡️ 下一頁",style=discord.ButtonStyle.secondary,row=2)
    async def next(self,interaction,button):
        self.page+=1
        await self.refresh(interaction)

    @discord.ui.button(label="🛒 購買這件衣服",style=discord.ButtonStyle.success,row=2)
    async def buy(self,interaction,button):
        from modules.shop import ShopActionModal
        menu=type("ShopMenuContext",(),{
            "owner_id":self.owner_id,
            "character_id":self.character_id,
            "black":False,
            "category":CATEGORY,
        })()
        await interaction.response.send_modal(ShopActionModal(menu,"buy"))


def patch_shop():
    from modules import shop
    if getattr(shop.ShopMenu,"_b01_installed",False):
        return
    old_init=shop.ShopMenu.__init__

    def new_init(self,owner_id,character_id,black,category):
        old_init(self,owner_id,character_id,black,category)
        if not black:
            button=discord.ui.Button(label="👗 服飾精品 B01",style=discord.ButtonStyle.primary,row=2)

            async def open_fashion(interaction):
                try:
                    from core.permissions import Actor
                    from modules.shop import require_character
                    with get_connection() as conn:
                        require_character(conn,Actor.from_interaction(interaction),character_id)
                    content,_,_=get_fashion_page(interaction.guild_id)
                    await interaction.response.send_message(content,view=FashionView(interaction,character_id),ephemeral=True)
                except Exception as exc:
                    LOG.exception("服飾介面錯誤")
                    await interaction.response.send_message(f"⚠️ {exc}",ephemeral=True)

            button.callback=open_fashion
            self.add_item(button)

    shop.ShopMenu.__init__=new_init
    shop.ShopMenu._b01_installed=True


def patch_view_attributes():
    # 穿戴加成不永久寫進角色基礎屬性：脫下後自然消失。
    from modules import character_view
    if getattr(character_view,"_b01_attributes_installed",False):
        return
    original=character_view.read_attributes

    def effective_attributes(actor,character_id):
        data=dict(original(actor,character_id))
        bonus=equipped_appearance_bonus(actor.guild_id,character_id)
        if bonus:
            data["容貌"]=int(data.get("容貌",0))+bonus
        return data

    character_view.read_attributes=effective_attributes
    character_view._b01_attributes_installed=True


class FashionB01(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self.initialized=False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.initialized:
            return
        try:
            validate()
            ensure_schema()
            for guild in self.bot.guilds:
                seed_guild(guild.id)
            patch_shop()
            patch_view_attributes()
            self.initialized=True
            LOG.info("B01 服飾精品店已載入，共 50 件預設商品。")
        except Exception:
            LOG.exception("B01 服飾精品店初始化失敗")

    @commands.Cog.listener()
    async def on_guild_join(self,guild):
        if self.initialized:
            try:
                seed_guild(guild.id)
            except Exception:
                LOG.exception("無法建立 B01 服飾商品：%s",guild.id)


async def setup(bot:commands.Bot):
    await bot.add_cog(FashionB01(bot))
