"""角色個人攻略劇情｜回目與逐回獎勵。

一篇 rp_stories = 一章；每章內可有多個回目；每回包含多個段落。
回目按 start_segment 切分，每個回目一份獎勵，完成後不可重複領取。
"""
from __future__ import annotations

import json
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from core.database import transaction, get_connection, get_or_create_account
from core.economy import _payment

REWARD_KINDS = {'coins', 'item', 'gift', 'stat'}


def init_db():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_story_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            episode_no INTEGER NOT NULL CHECK(episode_no>=1),
            title TEXT NOT NULL,
            start_segment INTEGER NOT NULL CHECK(start_segment>=1),
            rewards_json TEXT NOT NULL DEFAULT '[]',
            UNIQUE(story_id,episode_no), UNIQUE(story_id,start_segment),
            FOREIGN KEY(story_id) REFERENCES rp_stories(id) ON DELETE CASCADE
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_episode_completions (
            guild_id TEXT NOT NULL, story_id INTEGER NOT NULL,
            episode_no INTEGER NOT NULL, player_id TEXT NOT NULL,
            character_id INTEGER,
            completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,story_id,episode_no,player_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_episode_gifts (
            guild_id TEXT NOT NULL, story_id INTEGER NOT NULL,
            episode_no INTEGER NOT NULL, player_id TEXT NOT NULL,
            character_id INTEGER NOT NULL, gift_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(guild_id,story_id,episode_no,player_id,gift_name)
        )''')
        cols = {row['name'] for row in db.execute('PRAGMA table_info(rp_story_progress)')}
        if 'reward_character_id' not in cols:
            db.execute('ALTER TABLE rp_story_progress ADD COLUMN reward_character_id INTEGER')


def rewards_parse(raw):
    try:
        rewards = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError('獎勵資料無法解析。') from None
    if not isinstance(rewards,list) or len(rewards)>12:
        raise ValueError('每回最多 12 項獎勵。')
    for entry in rewards:
        if not isinstance(entry,dict) or entry.get('kind') not in REWARD_KINDS:
            raise ValueError('獎勵類型不合法。')
        kind=entry['kind']
        value=entry.get('value')
        qty=entry.get('quantity',1)
        if type(qty) is not int or not 1<=qty<=1000:
            raise ValueError('獎勵數量限 1～1000。')
        if kind in ('coins','item') and (type(value) is not int or value<=0):
            raise ValueError('金幣數量／商品編號必須為正整數。')
        if kind=='coins' and value>1000000:
            raise ValueError('單項金幣獎勵上限為 100 萬。')
        if kind in ('gift','stat') and (not isinstance(value,str) or not 1<=len(value.strip())<=80):
            raise ValueError('禮物名稱／數值名稱不合法。')
        if kind=='stat' and qty>100:
            raise ValueError('單次屬性加成不得超過 100。')
    return rewards


def get_episodes(db,story_id):
    return [dict(r) for r in db.execute('''SELECT * FROM rp_story_episodes
        WHERE story_id=? ORDER BY start_segment''',(story_id,))]


def episode_for(episodes, segment_no):
    matches=[e for e in episodes if e['start_segment']<=segment_no]
    return matches[-1] if matches else None


def verify_character(db,guild_id,player_id,character_id):
    if character_id is None:
        raise ValueError('此回有角色專屬獎勵，請使用 /劇情 並填寫本人角色編號。')
    row=db.execute('''SELECT id FROM characters WHERE guild_id=? AND id=?
        AND owner_id=? AND character_type='player' AND status IN ('approved','active')''',
        (str(guild_id),int(character_id),str(player_id))).fetchone()
    if not row:
        raise ValueError('角色編號不屬於你，或該角色未正式啟用。')
    return int(row['id'])


def rewards_needing_character(rewards):
    return any(x['kind'] in ('item','gift','stat') for x in rewards)


def award_episode_in_tx(db,guild_id,story_id,episode,player_id,character_id):
    """應在同一個 transaction() 中呼叫，失敗會回滾全部發獎與通關紀錄。"""
    rewards=rewards_parse(episode['rewards_json'])
    if rewards_needing_character(rewards):
        character_id=verify_character(db,guild_id,player_id,character_id)
    exists=db.execute('''SELECT 1 FROM rp_episode_completions
        WHERE guild_id=? AND story_id=? AND episode_no=? AND player_id=?''',
        (str(guild_id),story_id,episode['episode_no'],str(player_id))).fetchone()
    if exists:
        return []
    summaries=[]
    for idx,entry in enumerate(rewards):
        kind=entry['kind'];value=entry['value'];qty=entry.get('quantity',1)
        if kind=='coins':
            amount=value*qty
            source=get_or_create_account(db,guild_id,'system','issuance','treasury')
            target=get_or_create_account(db,guild_id,'player',str(player_id),'wallet')
            _payment(db,str(guild_id),f'rp_episode:{story_id}:{episode["episode_no"]}:{player_id}:{idx}',source,target,amount,'劇情回目通關獎勵')
            summaries.append(f'金幣 {amount}')
        elif kind=='item':
            item=db.execute('''SELECT name FROM shop_products WHERE guild_id=? AND id=?''',
                            (str(guild_id),value)).fetchone()
            if not item:
                raise ValueError(f'商品 #{value} 不存在，請群主修正回目獎勵。')
            from modules.shop import adjust_inventory
            adjust_inventory(db,str(guild_id),character_id,value,qty)
            summaries.append(f'{item["name"]} ×{qty}')
        elif kind=='gift':
            db.execute('''INSERT INTO rp_episode_gifts
                (guild_id,story_id,episode_no,player_id,character_id,gift_name,quantity)
                VALUES(?,?,?,?,?,?,?)''',
                (str(guild_id),story_id,episode['episode_no'],str(player_id),character_id,value,qty))
            summaries.append(f'專屬禮物：{value} ×{qty}')
        elif kind=='stat':
            row=db.execute('''SELECT value FROM attributes WHERE guild_id=? AND character_id=? AND name=?''',
                (str(guild_id),character_id,value)).fetchone()
            if row is None:
                raise ValueError(f'角色沒有「{value}」屬性，無法領取。')
            db.execute('''UPDATE attributes SET value=value+?, version=version+1
                WHERE guild_id=? AND character_id=? AND name=?''',
                (qty,str(guild_id),character_id,value))
            summaries.append(f'{value} +{qty}')
    db.execute('''INSERT INTO rp_episode_completions
        (guild_id,story_id,episode_no,player_id,character_id)
        VALUES(?,?,?,?,?)''',(str(guild_id),story_id,episode['episode_no'],str(player_id),character_id))
    return summaries


def set_episode(guild_id,story_id,number,title,start_segment):
    if not 1<=number<=999 or not 1<=start_segment<=999 or not 1<=len(title.strip())<=100:
        raise ValueError('回目編號、起始段落或標題不合法。')
    with transaction() as db:
        story=db.execute('''SELECT s.status,s.kind,c.character_type FROM rp_stories s
            JOIN characters c ON c.guild_id=s.guild_id AND c.id=s.character_id
            WHERE s.guild_id=? AND s.id=?''',(str(guild_id),story_id)).fetchone()
        if not story or story['status']!='draft' or story['kind']!='side' or story['character_type']!='official_npc':
            raise ValueError('只有草稿狀態的官方角色個人支線能編輯回目。')
        segment=db.execute('SELECT 1 FROM rp_story_segments WHERE story_id=? AND segment_no=?',
                          (story_id,start_segment)).fetchone()
        if not segment:
            raise ValueError('請先建立這個起始段落。')
        if number==1 and start_segment!=1:
            raise ValueError('第 1 回必須從段落 1 開始。')
        db.execute('''INSERT INTO rp_story_episodes(story_id,episode_no,title,start_segment)
            VALUES(?,?,?,?) ON CONFLICT(story_id,episode_no) DO UPDATE SET
            title=excluded.title,start_segment=excluded.start_segment''',
            (story_id,number,title.strip(),start_segment))


def configure_reward(guild_id,story_id,number,kind,value,quantity):
    if kind not in REWARD_KINDS:raise ValueError('不支援的獎勵類型。')
    if kind in ('coins','item'):
        try:value=int(value)
        except ValueError:raise ValueError('請輸入正確的數字。') from None
    entry={'kind':kind,'value':value,'quantity':quantity}
    rewards_parse([entry])
    with transaction() as db:
        row=db.execute('''SELECT e.rewards_json,s.status,s.kind FROM rp_story_episodes e
            JOIN rp_stories s ON s.id=e.story_id
            WHERE s.guild_id=? AND e.story_id=? AND e.episode_no=?''',
            (str(guild_id),story_id,number)).fetchone()
        if not row or row['status']!='draft' or row['kind']!='side':
            raise ValueError('回目不存在，或劇情不在可編輯草稿狀態。')
        rewards=rewards_parse(row['rewards_json'])
        rewards.append(entry)
        rewards_parse(rewards)
        db.execute('''UPDATE rp_story_episodes SET rewards_json=?
            WHERE story_id=? AND episode_no=?''',
            (json.dumps(rewards,ensure_ascii=False),story_id,number))
        return rewards


class StoryEpisodes(commands.Cog):
    def __init__(self,bot):self.bot=bot
    async def cog_load(self):
        from modules.story_builder import init_db as story_init
        story_init()
        from modules.story_play import init_db as play_init
        play_init()

    @app_commands.command(name='劇情回目',description='群主建立或修改個人攻略劇情的回目')
    @app_commands.guild_only()
    async def episode(self,i:discord.Interaction,劇情編號:int,回目:int,標題:str,起始段落:int):
        if not i.guild or i.guild.owner_id!=i.user.id:
            return await i.response.send_message('⛔ 僅群主可使用。',ephemeral=True)
        try:
            set_episode(i.guild_id,劇情編號,回目,標題,起始段落)
            await i.response.send_message(f'✅ 第 {回目} 回「{標題}」已保存。起始段落：{起始段落}',ephemeral=True)
        except (ValueError,sqlite3.Error) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

    @app_commands.command(name='劇情回目獎勵',description='群主替個人攻略劇情的某一回新增獎勵')
    @app_commands.guild_only()
    @app_commands.choices(類型=[
        app_commands.Choice(name='金幣',value='coins'),
        app_commands.Choice(name='背包商品',value='item'),
        app_commands.Choice(name='專屬禮物',value='gift'),
        app_commands.Choice(name='角色屬性',value='stat'),
    ])
    async def reward(self,i:discord.Interaction,劇情編號:int,回目:int,類型:app_commands.Choice[str],內容:str,數量:int=1):
        if not i.guild or i.guild.owner_id!=i.user.id:
            return await i.response.send_message('⛔ 僅群主可使用。',ephemeral=True)
        try:
            result=configure_reward(i.guild_id,劇情編號,回目,類型.value,內容,數量)
            await i.response.send_message(f'✅ 第 {回目} 回已新增獎勵，目前共 {len(result)} 項。',ephemeral=True)
        except (ValueError,sqlite3.Error) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)


async def setup(bot:commands.Bot):
    await bot.add_cog(StoryEpisodes(bot))
