"""測試專用內容：只在名稱完全是「測試」的頻道／討論串使用。"""
from __future__ import annotations

import logging
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from core.database import get_connection, transaction
from modules.lottery_threads import init_lottery_db
from modules.story_builder import init_db as init_story_db
from modules.story_play import init_db as init_play_db
from modules.character_forums import initialize as init_forums

LOG = logging.getLogger(__name__)
TEST_NAME = "測試"


def init_test_db():
    # 先初始化依賴，再創建追蹤表；不修改正式內容。
    init_lottery_db()
    init_story_db()
    init_play_db()
    init_forums()
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_test_content (
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            thread_created INTEGER NOT NULL DEFAULT 0,
            lottery_id INTEGER NOT NULL,
            story_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, channel_id)
        )""")


def is_owner(i):
    return bool(i.guild and i.guild.owner_id == i.user.id)


def test_location(i):
    if not i.guild or not i.channel:
        return False
    channel = i.channel
    if channel.name == TEST_NAME:
        return True
    return isinstance(channel, discord.Thread) and bool(
        channel.parent and channel.parent.name == TEST_NAME
    )


async def respond(i, text):
    text = discord.utils.escape_mentions(str(text))[:1900]
    if i.response.is_done():
        await i.followup.send(text, ephemeral=True)
    else:
        await i.response.send_message(text, ephemeral=True)


def test_data(guild_id, channel_id):
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM rp_test_content WHERE guild_id=? AND channel_id=?",
            (str(guild_id), str(channel_id)),
        ).fetchone()
    return dict(row) if row else None


def seed(guild_id, channel_id, thread_id, owner_id, owns_thread):
    """單筆交易建立專用 NPC、兩回劇情、獎單；只記錄新增 ID。"""
    gid, cid = str(guild_id), str(channel_id)
    with transaction() as db:
        if db.execute(
            "SELECT 1 FROM rp_test_content WHERE guild_id=? AND channel_id=?",
            (gid, cid),
        ).fetchone():
            raise ValueError("這個測試頻道已經建立測試資料，請先使用 /測試清除。")
        if db.execute(
            "SELECT 1 FROM rp_lottery_lists WHERE guild_id=? AND name=?",
            (gid, TEST_NAME),
        ).fetchone():
            raise ValueError("已有名稱為「測試」的正式／其他獎單。為避免覆蓋，請先另行處理。")
        person = db.execute(
            """INSERT INTO characters(guild_id,owner_id,name,status,character_type)
               VALUES(?,?,?,?,?)""",
            (gid, str(owner_id), "【測試專用】劇情 NPC", "active", "official_npc"),
        ).lastrowid
        sid = db.execute(
            """INSERT INTO rp_stories
               (guild_id,character_id,kind,title,status,creator_id)
               VALUES(?,?,?,?,?,?)""",
            (gid, person, "side", "【測試專用】兩回攻略劇情", "ready", str(owner_id)),
        ).lastrowid
        # 段落1有選擇，段落2完成第一回，段落3完成第二回。
        segments = [
            (1, "測試 NPC", "歡迎進行劇情測試！請從選單選擇一個回答。",
             '[{"text":"向她打招呼","target":2},{"text":"點頭示意","target":2}]', None),
            (2, "測試 NPC", "第一回即將結束。下一段會進入第二回。", '[]', 3),
            (3, "測試 NPC", "第二回結束，這是測試劇情的終點。", '[]', None),
        ]
        db.executemany(
            """INSERT INTO rp_story_segments
               (story_id,segment_no,speaker,dialogue,choices_json,next_segment)
               VALUES(?,?,?,?,?,?)""",
            [(sid, *part) for part in segments],
        )
        db.executemany(
            """INSERT INTO rp_story_episodes(story_id,episode_no,title,start_segment,rewards_json)
               VALUES(?,?,?,?,?)""",
            [(sid, 1, "第一次見面", 1, '[{"kind":"coins","value":10,"quantity":1}]'),
             (sid, 2, "測試結局", 3, '[{"kind":"coins","value":10,"quantity":1}]')],
        )
        # story_play.available_stories() 可在測試討論串找到此專用劇情。
        db.execute(
            """INSERT INTO character_forum_links(guild_id,character_id,story_id)
               VALUES(?,?,?)""",
            (gid, person, str(thread_id)),
        )
        lid = db.execute(
            """INSERT INTO rp_lottery_lists(guild_id,name,created_by,enabled)
               VALUES(?,?,?,1)""", (gid, TEST_NAME, str(owner_id)),
        ).lastrowid
        prize_cols = {x['name'] for x in db.execute('PRAGMA table_info(rp_lottery_prizes)')}
        if 'unlimited' not in prize_cols:
            raise ValueError('抽獎模組尚未更新不限量獎品資料表。')
        db.executemany(
            """INSERT INTO rp_lottery_prizes(lottery_id,name,prize_type,amount,remaining,unlimited)
               VALUES(?,?,?,?,?,?)""",
            [(lid, "金幣10", "money", 10, 3, 0),
             (lid, "【測試】商品領獎樣本", "gift", 0, 0, 1)],
        )
        db.execute(
            """INSERT INTO rp_test_content
               (guild_id,channel_id,thread_id,thread_created,lottery_id,story_id,character_id)
               VALUES(?,?,?,?,?,?,?)""",
            (gid, cid, str(thread_id), int(owns_thread), lid, sid, person),
        )
    return lid, sid, person


def clear(guild_id, channel_id):
    """按追蹤 ID 清理，不以名稱模糊搜尋；不回收已發放的金幣。"""
    gid, cid = str(guild_id), str(channel_id)
    with transaction() as db:
        r = db.execute(
            'SELECT * FROM rp_test_content WHERE guild_id=? AND channel_id=?',
            (gid, cid),
        ).fetchone()
        if not r:
            raise ValueError("此處沒有由測試工具建立的資料。")
        lid, sid, char = int(r['lottery_id']), int(r['story_id']), int(r['character_id'])
        # 測試獎單如果被改名也不影響清除；但不能誤刪不相關 ID。
        db.execute('DELETE FROM rp_lottery_deliveries WHERE guild_id=? AND draw_id IN (SELECT id FROM rp_lottery_draws WHERE lottery_id=?)', (gid, lid)) if _table(db,'rp_lottery_deliveries') else None
        db.execute('DELETE FROM rp_lottery_product_bindings WHERE guild_id=? AND prize_id IN (SELECT id FROM rp_lottery_prizes WHERE lottery_id=?)', (gid, lid)) if _table(db,'rp_lottery_product_bindings') else None
        db.execute('DELETE FROM rp_lottery_draws WHERE guild_id=? AND lottery_id=?', (gid, lid))
        db.execute('DELETE FROM rp_lottery_prizes WHERE lottery_id=?', (lid,))
        db.execute('DELETE FROM rp_lottery_lists WHERE guild_id=? AND id=?', (gid, lid))
        if _table(db,'rp_exclusive_gift_uses'):
            db.execute('DELETE FROM rp_exclusive_gift_uses WHERE guild_id=? AND gift_rowid IN (SELECT rowid FROM rp_episode_gifts WHERE guild_id=? AND story_id=?)', (gid, gid, sid))
        for table in ('rp_episode_gifts','rp_episode_completions','rp_story_progress'):
            if _table(db, table):
                db.execute(f'DELETE FROM {table} WHERE guild_id=? AND story_id=?', (gid, sid))
        db.execute('DELETE FROM rp_story_episodes WHERE story_id=?', (sid,))
        db.execute('DELETE FROM rp_story_segments WHERE story_id=?', (sid,))
        db.execute('DELETE FROM rp_stories WHERE guild_id=? AND id=?', (gid, sid))
        db.execute('DELETE FROM character_forum_links WHERE guild_id=? AND character_id=?', (gid, char))
        db.execute('DELETE FROM characters WHERE guild_id=? AND id=?', (gid, char))
        db.execute('DELETE FROM rp_test_content WHERE guild_id=? AND channel_id=?', (gid, cid))
        return dict(r)


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class TestContent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        init_test_db()

    @app_commands.command(name='測試建立', description='在名稱為「測試」的頻道建立測試獎單及兩回劇情')
    @app_commands.guild_only()
    async def build(self, i: discord.Interaction):
        if not is_owner(i):
            return await respond(i, '⛔ 只有群主可以建立測試資料。')
        if not test_location(i):
            return await respond(i, '⚠️ 請在名稱完全為「測試」的頻道或其討論串使用。')
        channel = i.channel
        # 若從測試討論串啟動，將其作為測試位置；否則自動建立同名討論串。
        root_id = channel.parent_id if isinstance(channel, discord.Thread) else channel.id
        if test_data(i.guild_id, root_id):
            return await respond(i, '⚠️ 這個頻道已有測試資料。請先 /測試清除。')
        await i.response.defer(ephemeral=True, thinking=True)
        made = False
        thread = channel if isinstance(channel, discord.Thread) else None
        try:
            if thread is None:
                if isinstance(channel, discord.TextChannel):
                    thread = await channel.create_thread(name=TEST_NAME, type=discord.ChannelType.public_thread, reason='RP 系統臨時測試')
                elif isinstance(channel, discord.ForumChannel):
                    result = await channel.create_thread(name=TEST_NAME, content='RP 抽獎與劇情測試討論串')
                    thread = result.thread
                else:
                    raise ValueError('請使用文字頻道、論壇頻道或討論串。')
                made = True
            if thread.name != TEST_NAME:
                raise ValueError('測試討論串／帖子名稱必須完全為「測試」。')
            lid, sid, cid = seed(i.guild_id, root_id, thread.id, i.user.id, made)
        except Exception as exc:
            if made and thread is not None:
                try: await thread.delete(reason='測試資料建立失敗，移除臨時討論串')
                except discord.HTTPException: LOG.exception('清理測試討論串失敗')
            LOG.exception('測試資料建立失敗')
            return await respond(i, f'⚠️ 測試建立失敗：{exc}')
        await respond(i,
            f'✅ 測試資料建立完成！\n🎟️ 測試獎單 #{lid}（金幣10 限量3／商品不限量）\n'
            f'📖 測試劇情 #{sid}（官方 NPC #{cid}，兩回，各10金幣）\n'
            f'📌 請到 <#{thread.id}> 使用 `/抽獎`、`/劇情`。\n'
            '🗑️ 測試完回到此頻道使用 `/測試清除`。'
        )

    @app_commands.command(name='測試查看', description='查看這個「測試」頻道建立的測試資料')
    @app_commands.guild_only()
    async def inspect(self, i: discord.Interaction):
        if not is_owner(i): return await respond(i, '⛔ 僅群主可查看測試管理資料。')
        if not test_location(i): return await respond(i, '⚠️ 請在「測試」頻道操作。')
        root = i.channel.parent_id if isinstance(i.channel,discord.Thread) else i.channel.id
        r = test_data(i.guild_id,root)
        if not r: return await respond(i, '目前沒有測試工具建立的資料。')
        await respond(i,f"🎟️ 獎單 #{r['lottery_id']}\n📖 劇情 #{r['story_id']}\n👤 測試 NPC #{r['character_id']}\n📌 <#{r['thread_id']}>")

    @app_commands.command(name='測試清除', description='刪除本測試頻道生成的測試獎單、劇情、NPC與紀錄')
    @app_commands.guild_only()
    async def remove(self, i: discord.Interaction):
        if not is_owner(i): return await respond(i, '⛔ 只有群主可以清除測試資料。')
        if not test_location(i): return await respond(i, '⚠️ 請在「測試」頻道操作。')
        root = i.channel.parent_id if isinstance(i.channel,discord.Thread) else i.channel.id
        row = test_data(i.guild_id,root)
        if not row: return await respond(i, '沒有可以清除的測試資料。')
        if str(i.channel.id) == row['thread_id'] and row['thread_created']:
            return await respond(i,'⚠️ 請回到上層「測試」頻道再執行 `/測試清除`，避免刪除目前正在操作的討論串。')
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            deleted = clear(i.guild_id,root)
        except Exception as exc:
            LOG.exception('清理測試資料失敗')
            return await respond(i, f'⚠️ 清除失敗：{exc}')
        message = '✅ 測試獎單、劇情、NPC與測試通關／中獎紀錄已刪除。'
        if deleted['thread_created']:
            thread = i.guild.get_thread(int(deleted['thread_id']))
            if thread is None:
                try: thread = await i.guild.fetch_channel(int(deleted['thread_id']))
                except discord.HTTPException: thread = None
            if thread:
                try: await thread.delete(reason='測試完成，自動刪除臨時測試討論串')
                except discord.HTTPException:
                    message += '\n⚠️ 討論串刪除失敗，請手動刪除。'
        message += '\n注意：測試中已發到真實錢包或背包的獎勵不會自動扣回。'
        await respond(i,message)


async def setup(bot: commands.Bot):
    await bot.add_cog(TestContent(bot))
