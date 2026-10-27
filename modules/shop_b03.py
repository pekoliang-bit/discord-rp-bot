"""B03 珠寶鐘錶店：內建50件佩戴型商品；群主可一鍵上架。

/ b03 上架（實際斜線指令無空格：/b03 上架）
/ b03 查詢

商品沿用 shop_products，裝備／屬性生效交由 gear.py 處理。
不修改任何玩家背包、金幣、永久屬性。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction
from modules import shop

LOG = logging.getLogger(__name__)

# 代碼|系列|商品名稱|屬性|增加數值|售價。裝備欄位由 gear.py 依名稱判別。
CATALOG_TEXT = """
JW-001|晨星序曲|晨星銀戒指|容貌|4|1800
JW-002|晨星序曲|月痕寶石戒指|路人緣|5|2100
JW-003|晨星序曲|雪晶細環戒指|容貌|6|2600
JW-004|晨星序曲|晨霧珍珠耳環|路人緣|5|2200
JW-005|晨星序曲|星芒水滴耳墜|容貌|7|3100
JW-006|晨星序曲|流銀星河項鍊|容貌|8|3600
JW-007|晨星序曲|月影珍珠頸鍊|路人緣|8|3700
JW-008|晨星序曲|霜華銀色手環|容貌|6|2700
JW-009|晨星序曲|星羽細鑽手錶|商業價值|9|4300
JW-010|晨星序曲|銀河流光髮冠|熱度|10|5000
JW-011|緋紅盛典|紅曜石戒指|容貌|8|3800
JW-012|緋紅盛典|玫瑰烈焰戒指|熱度|10|4600
JW-013|緋紅盛典|赤焰水晶耳釘|路人緣|9|3900
JW-014|緋紅盛典|赤霞流蘇耳環|容貌|11|5000
JW-015|緋紅盛典|薔薇之誓項鍊|路人緣|12|5400
JW-016|緋紅盛典|紅寶石頸鍊|容貌|13|5900
JW-017|緋紅盛典|落日鎏金手鐲|商業價值|12|5600
JW-018|緋紅盛典|緋夜限定腕錶|商業價值|14|6500
JW-019|緋紅盛典|赤紅寶冠髮冠|熱度|15|6900
JW-020|緋紅盛典|王室焰心戒指|大眾認知度|16|7600
JW-021|海月珍藏|海藍寶石戒指|容貌|10|4500
JW-022|海月珍藏|深海月光戒指|路人緣|12|5300
JW-023|海月珍藏|海沫珍珠耳環|容貌|13|6000
JW-024|海月珍藏|藍晶流星耳墜|熱度|14|6400
JW-025|海月珍藏|海神之淚項鍊|容貌|15|6900
JW-026|海月珍藏|藍月石頸鍊|路人緣|16|7200
JW-027|海月珍藏|深海浪花手環|商業價值|15|7000
JW-028|海月珍藏|蒼海陀飛輪手錶|商業價值|17|8000
JW-029|海月珍藏|潮汐藍寶髮冠|熱度|18|8300
JW-030|海月珍藏|海皇寶璽戒指|大眾認知度|20|9200
JW-031|夜幕典藏|黑鑽誓言戒指|容貌|15|6900
JW-032|夜幕典藏|暗夜星耀戒指|路人緣|16|7300
JW-033|夜幕典藏|黑曜流蘇耳墜|熱度|18|8400
JW-034|夜幕典藏|秘銀夜羽耳環|容貌|19|8700
JW-035|夜幕典藏|永夜之心項鍊|商業價值|20|9400
JW-036|夜幕典藏|幻月黑鑽頸鍊|容貌|22|10100
JW-037|夜幕典藏|黑金蛇紋手鐲|路人緣|21|9700
JW-038|夜幕典藏|夜梟典藏腕錶|商業價值|23|10700
JW-039|夜幕典藏|暗星鎏金髮冠|熱度|25|11500
JW-040|夜幕典藏|午夜君主戒指|大眾認知度|26|11900
JW-041|星冕高定|星辰盟約戒指|容貌|25|12000
JW-042|星冕高定|白金帝冕戒指|商業價值|28|13300
JW-043|星冕高定|極光幻彩耳環|路人緣|30|14500
JW-044|星冕高定|女王之翼耳墜|熱度|32|15300
JW-045|星冕高定|星海永恆項鍊|容貌|35|17000
JW-046|星冕高定|璀璨銀河頸鍊|大眾認知度|38|18300
JW-047|星冕高定|王冠璨金手鐲|商業價值|40|19500
JW-048|星冕高定|命運星盤腕錶|商業價值|45|22000
JW-049|星冕高定|永恆黎明髮冠|熱度|50|25000
JW-050|星冕高定|萬象星皇戒指|大眾認知度|55|28000
""".strip()


def catalog():
    result = []
    for line in CATALOG_TEXT.splitlines():
        sku, collection, name, stat, bonus, price = line.split('|')
        bonus, price = int(bonus), int(price)
        if price < bonus * 300:
            raise ValueError(f'{sku} 售價低於最低加成價格')
        result.append((sku, collection, name, stat, bonus, price))
    if len(result) != 50 or len({item[0] for item in result}) != 50:
        raise RuntimeError('B03 應有50件不重複商品')
    return result


def init_db():
    shop.init_shop_db()
    with transaction() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS b03_catalog (
            guild_id TEXT NOT NULL,
            sku TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            collection TEXT NOT NULL,
            PRIMARY KEY (guild_id,sku),
            UNIQUE(guild_id,product_id)
        )''')


def import_catalog(guild_id):
    gid = str(guild_id)
    items = catalog()
    created, skipped = 0, 0
    with transaction() as conn:
        registered = {r['sku'] for r in conn.execute(
            'SELECT sku FROM b03_catalog WHERE guild_id=?', (gid,)
        )}
        for sku, collection, name, stat, bonus, price in items:
            if sku in registered:
                skipped += 1
                continue
            now = shop.utc_now()
            cur = conn.execute('''INSERT INTO shop_products (
                guild_id,name,category,description,price,stock,scope,
                effect_kind,effect_stat,effect_value,effect_context,
                active,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)''', (
                gid,name,f'B03-{collection}',
                f'B03 珠寶鐘錶｜{collection}｜{sku}｜穿戴生效',
                price,None,'public','context',stat,bonus,'穿戴期間',now,now,
            ))
            conn.execute('''INSERT INTO b03_catalog
                (guild_id,sku,product_id,collection) VALUES (?,?,?,?)''',
                (gid,sku,int(cur.lastrowid),collection))
            created += 1
    return f'✅ B03 上架完成！\n本次新增：{created} 件\n已有登記、略過：{skipped} 件\n穿戴加成由 gear.py 自動結算。'


def status(guild_id):
    with transaction() as conn:
        count = conn.execute('SELECT COUNT(*) FROM b03_catalog WHERE guild_id=?',
                             (str(guild_id),)).fetchone()[0]
    return f'💎 B03 珠寶鐘錶店：已登記 {count}/50 件。'


def owner_only(interaction):
    if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
        raise ValueError('只有伺服器群主可以執行 B03 管理。')


class B03Shop(commands.Cog):
    group = app_commands.Group(name='b03', description='B03 珠寶鐘錶商品管理', guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(init_db)

    @group.command(name='上架', description='群主一次上架 B03 的50件珠寶鐘錶')
    async def publish(self, interaction: discord.Interaction):
        try:
            owner_only(interaction)
        except ValueError as exc:
            await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(import_catalog, interaction.guild_id)
        except (ValueError, sqlite3.Error) as exc:
            result = f'⚠️ 上架取消：{exc}'
        except Exception:
            LOG.exception('B03 上架失敗')
            result = '⚠️ 上架失敗，請查看 Railway 日誌。'
        await interaction.followup.send(result, ephemeral=True)

    @group.command(name='查詢', description='查看B03目前上架進度')
    async def query(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(status, interaction.guild_id)
        except Exception:
            LOG.exception('B03 查詢失敗')
            result = '⚠️ 查詢失敗。'
        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(B03Shop(bot))
