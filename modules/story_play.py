"""Discord RP｜玩家互動劇情（/劇情）。

依賴：modules.story_builder（rp_stories、rp_story_segments）
      modules.character_forums（character_forum_links）
本版：開場、分支選項、連續段落、進度保存、完成紀錄。
獎勵尚未整合：有 rewards_json 的劇情禁止開局，以免漏發獎。
"""
from __future__ import annotations

import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)


def init_db():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_story_progress (
            guild_id TEXT NOT NULL,
            story_id INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            segment_no INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'playing'
                CHECK(status IN ('playing','completed')),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            PRIMARY KEY (guild_id,story_id,player_id),
            FOREIGN KEY(story_id) REFERENCES rp_stories(id) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_story_progress_player ON rp_story_progress(guild_id,player_id,status)")


def _decode(raw, default):
    try:
        result = json.loads(raw or "null")
    except (ValueError, TypeError):
        raise ValueError("劇情設定資料無法解析，請聯絡群主。") from None
    return default if result is None else result


def _check_config(story):
    requirements = _decode(story['prerequisite_json'], {})
    rewards = _decode(story['rewards_json'], [])
    if requirements not in ({}, []):
        raise ValueError("此劇情的觸發條件尚未接入判定系統，目前條件尚未齊全。")
    if rewards not in ([], {}):
        raise ValueError("此劇情有未接入的獎勵，暫不開放攻略，以免完成後漏發。")


def available_stories(guild_id, channel_id):
    """僅允許角色專屬論壇或其劇情討論貼文中開啟；頻道和角色綁定。"""
    with get_connection() as db:
        rows = db.execute("""
            SELECT s.id, s.title, s.kind, s.character_id, c.name AS character_name,
                   s.prerequisite_json, s.rewards_json
            FROM character_forum_links f
            JOIN rp_stories s ON s.guild_id=f.guild_id AND s.character_id=f.character_id
            JOIN characters c ON c.guild_id=s.guild_id AND c.id=s.character_id
            WHERE f.guild_id=? AND (f.forum_id=? OR f.story_id=?)
              AND s.status='ready' AND c.status IN ('approved','active')
            ORDER BY CASE s.kind WHEN 'main' THEN 0 ELSE 1 END, s.id
        """, (str(guild_id), str(channel_id), str(channel_id))).fetchall()
    return [dict(x) for x in rows]


def get_story(guild_id, story_id):
    with get_connection() as db:
        row = db.execute("""SELECT id,title,status,prerequisite_json,rewards_json FROM rp_stories
                            WHERE guild_id=? AND id=?""", (str(guild_id), int(story_id))).fetchone()
    if row is None or row['status'] != 'ready':
        raise ValueError('劇情已取消公開或不存在。')
    result = dict(row)
    _check_config(result)
    return result


def get_segment(story_id, number):
    with get_connection() as db:
        row = db.execute("""SELECT segment_no,speaker,dialogue,choices_json,next_segment
                            FROM rp_story_segments WHERE story_id=? AND segment_no=?""",
                         (int(story_id), int(number))).fetchone()
    if not row:
        raise ValueError('劇情段落不存在，請通知群主檢查。')
    part = dict(row)
    choices = _decode(part['choices_json'], [])
    if not isinstance(choices, list) or len(choices) > 3:
        raise ValueError('選項資料格式不正確。')
    for choice in choices:
        if not isinstance(choice, dict) or not isinstance(choice.get('text'), str):
            raise ValueError('選項資料格式不正確。')
    part['choices'] = choices
    return part


def next_segment_no(db, story_id, part, selected=None):
    if selected is not None:
        target = part['choices'][selected].get('target')
        if target is not None:
            return int(target)
    if part['next_segment'] is not None:
        return int(part['next_segment'])
    row = db.execute('''SELECT MIN(segment_no) FROM rp_story_segments
                        WHERE story_id=? AND segment_no>?''',
                     (int(story_id), int(part['segment_no']))).fetchone()
    return int(row[0]) if row[0] is not None else None


def open_progress(guild_id, story_id, player_id):
    get_story(guild_id, story_id)
    with transaction() as db:
        db.execute('''INSERT OR IGNORE INTO rp_story_progress
                      (guild_id,story_id,player_id,segment_no) VALUES (?,?,?,1)''',
                   (str(guild_id), int(story_id), str(player_id)))
        row = db.execute('''SELECT segment_no,status FROM rp_story_progress
                            WHERE guild_id=? AND story_id=? AND player_id=?''',
                         (str(guild_id), int(story_id), str(player_id))).fetchone()
        return dict(row)


def advance(guild_id, story_id, player_id, expected_number, choice_index=None):
    """原子化驗證當前段落、選項與進度，防止重複點擊跳過劇情。"""
    get_story(guild_id, story_id)
    part = get_segment(story_id, expected_number)
    if part['choices']:
        if choice_index is None or not 0 <= choice_index < len(part['choices']):
            raise ValueError('請先選擇有效的玩家反應。')
    elif choice_index is not None:
        raise ValueError('此段落沒有選項。')
    with transaction() as db:
        row = db.execute('''SELECT segment_no,status FROM rp_story_progress
                            WHERE guild_id=? AND story_id=? AND player_id=?''',
                         (str(guild_id), int(story_id), str(player_id))).fetchone()
        if not row or row['status'] != 'playing':
            raise ValueError('此劇情已結束，或尚未開始。')
        if row['segment_no'] != int(expected_number):
            raise ValueError('劇情進度已更新，請重新使用 /劇情。')
        nxt = next_segment_no(db, story_id, part, choice_index)
        if nxt is not None:
            target = db.execute('SELECT 1 FROM rp_story_segments WHERE story_id=? AND segment_no=?',
                                (int(story_id), int(nxt))).fetchone()
            if target is None:
                raise ValueError('分支指向不存在的段落。')
            db.execute('''UPDATE rp_story_progress SET segment_no=?,updated_at=CURRENT_TIMESTAMP
                          WHERE guild_id=? AND story_id=? AND player_id=?''',
                       (nxt, str(guild_id), int(story_id), str(player_id)))
            return nxt
        db.execute('''UPDATE rp_story_progress
                      SET status='completed',completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                      WHERE guild_id=? AND story_id=? AND player_id=?''',
                   (str(guild_id), int(story_id), str(player_id)))
        return None


async def reply_error(interaction, error):
    message = '⚠️ ' + discord.utils.escape_mentions(str(error))[:1750]
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class PlayView(discord.ui.View):
    def __init__(self, guild_id, story_id, player_id, segment_no):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.story_id = story_id
        self.player_id = player_id
        self.segment_no = segment_no
        self.part = get_segment(story_id, segment_no)
        if self.part['choices']:
            self.add_item(ChoiceSelect(self.part['choices']))
        else:
            self.add_item(ContinueButton())

    async def interaction_check(self, interaction):
        if interaction.guild_id != self.guild_id or interaction.user.id != self.player_id:
            await reply_error(interaction, '這是其他玩家的劇情進度。')
            return False
        return True

    async def run_next(self, interaction, selected=None):
        try:
            nxt = advance(self.guild_id, self.story_id, self.player_id, self.segment_no, selected)
            if nxt is None:
                await interaction.response.edit_message(
                    content='🏁 **劇情攻略完成！**\n已保存通關紀錄。此版本僅開放無獎勵劇情；獎勵系統會於後續整合。',
                    view=None)
                return
            part = get_segment(self.story_id, nxt)
            await interaction.response.edit_message(
                content=render_segment(part),
                view=PlayView(self.guild_id, self.story_id, self.player_id, nxt))
        except (ValueError, sqlite3.Error) as exc:
            await reply_error(interaction, exc)


def render_segment(part):
    speaker = discord.utils.escape_mentions(discord.utils.escape_markdown(part['speaker']))
    text = discord.utils.escape_mentions(part['dialogue'])
    heading = f'📖 **第 {part["segment_no"]} 段**\n**{speaker}：**{text}'
    if part['choices']:
        heading += '\n\n請從下拉選單選擇你的反應。'
    else:
        heading += '\n\n點選「繼續」閱讀下一段。'
    return heading[:1900]


class ChoiceSelect(discord.ui.Select):
    def __init__(self, choices):
        super().__init__(placeholder='選擇玩家反應', min_values=1, max_values=1,
                         options=[discord.SelectOption(label=x['text'][:100], value=str(i))
                                  for i, x in enumerate(choices)])

    async def callback(self, interaction):
        await self.view.run_next(interaction, int(self.values[0]))


class ContinueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='▶️ 繼續', style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        await self.view.run_next(interaction)


class StorySelect(discord.ui.Select):
    def __init__(self, stories):
        super().__init__(placeholder='選擇要攻略的劇情', min_values=1, max_values=1,
                         options=[discord.SelectOption(label=x['title'][:100],
                                                       description=f"{x['character_name']}｜{'主線' if x['kind']=='main' else '支線'}"[:100],
                                                       value=str(x['id'])) for x in stories[:25]])

    async def callback(self, interaction):
        await self.view.begin(interaction, int(self.values[0]))


class StoryPickerView(discord.ui.View):
    def __init__(self, interaction, stories):
        super().__init__(timeout=300)
        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.channel_id = interaction.channel_id
        self.add_item(StorySelect(stories))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id or interaction.channel_id != self.channel_id:
            await reply_error(interaction, '此操作不屬於你。')
            return False
        return True

    async def begin(self, interaction, story_id):
        try:
            choices = available_stories(self.guild_id, self.channel_id)
            if story_id not in {x['id'] for x in choices}:
                raise ValueError('此劇情不能在目前頻道開始。')
            progress = open_progress(self.guild_id, story_id, self.owner_id)
            if progress['status'] == 'completed':
                await interaction.response.edit_message(content='✅ 你已完成這篇劇情，通關紀錄已保存。', view=None)
                return
            part = get_segment(story_id, progress['segment_no'])
            await interaction.response.edit_message(
                content=render_segment(part),
                view=PlayView(self.guild_id, story_id, self.owner_id, progress['segment_no']))
        except (ValueError, sqlite3.Error) as exc:
            await reply_error(interaction, exc)


class StoryPlay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        from modules.story_builder import init_db as init_story_db
        init_story_db()
        init_db()

    @app_commands.command(name='劇情', description='在角色專屬論壇開始或繼續攻略劇情')
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction):
        try:
            choices = available_stories(interaction.guild_id, interaction.channel_id)
            if not choices:
                await interaction.response.send_message(
                    '⚠️ 這裡沒有已就緒的角色劇情。請在對應角色論壇或劇情討論貼文使用 /劇情。',
                    ephemeral=True)
                return
            if len(choices) == 1:
                story_id = choices[0]['id']
                progress = open_progress(interaction.guild_id, story_id, interaction.user.id)
                if progress['status'] == 'completed':
                    await interaction.response.send_message('✅ 你已完成這篇劇情，通關紀錄已保存。', ephemeral=True)
                    return
                part = get_segment(story_id, progress['segment_no'])
                await interaction.response.send_message(
                    render_segment(part),
                    view=PlayView(interaction.guild_id, story_id, interaction.user.id, progress['segment_no']),
                    ephemeral=True)
                return
            await interaction.response.send_message(
                '📖 選擇要攻略的劇情：', view=StoryPickerView(interaction, choices), ephemeral=True)
        except (ValueError, sqlite3.Error) as exc:
            await reply_error(interaction, exc)
        except Exception:
            LOG.exception('玩家劇情指令執行失敗')
            await reply_error(interaction, '劇情系統異常，請查看 Railway 日誌。')


async def setup(bot: commands.Bot):
    await bot.add_cog(StoryPlay(bot))
