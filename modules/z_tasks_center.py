"""唯一正式 /任務 指令：角色、新手進度、當期日常、通告接取及回報。"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from modules.task_defaults import seed_newbie
from modules.task_newbie import character_owner, progress, bot_thread_id, record_success
from modules.task_repository import initialize
from modules.task_service import accept, board, report

KINDS = {
    'daily': '☀️ 日常任務',
    'screen': '🎬 影視通告',
    'variety': '🎤 綜藝通告',
    'endorsement': '📣 代言通告',
    'limited': '⏱️ 限時通告',
    'special': '✨ 特殊通告',
}


def owned(guild_id, user_id):
    with get_connection() as db:
        return [dict(r) for r in db.execute('''SELECT id, name FROM characters
            WHERE guild_id=? AND owner_id=? AND status IN ('active','approved')
            AND character_type='player' ORDER BY id''', (str(guild_id), str(user_id)))]


async def reply(interaction, content, **kwargs):
    kwargs.setdefault('ephemeral', True)
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


class CharacterSelect(discord.ui.Select):
    def __init__(self, characters):
        super().__init__(placeholder='選擇要使用的角色', options=[
            discord.SelectOption(label=str(c['name'] or c['id'])[:80],
                                 value=str(c['id']), description=f"角色 #{c['id']}")
            for c in characters[:25]
        ])

    async def callback(self, interaction):
        await show_character(interaction, int(self.values[0]))


class OwnerView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = int(user_id)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await reply(interaction, '請自行使用 /任務 開啟你的面板。')
            return False
        return True


class CharacterView(OwnerView):
    def __init__(self, user_id, characters):
        super().__init__(user_id)
        self.add_item(CharacterSelect(characters))


class TaskSelect(discord.ui.Select):
    def __init__(self, kind, tasks):
        super().__init__(placeholder='選擇一項任務查看並接取', options=[
            discord.SelectOption(label=str(t['title'])[:95], value=str(t['id']),
                                 description=f"通告 #{t['id']} · {KINDS[kind]}"[:100])
            for t in tasks[:25]
        ])

    async def callback(self, interaction):
        parent = self.view
        gid, cid, task_id = parent.guild_id, parent.character_id, int(self.values[0])
        try:
            tasks = board(gid, cid, interaction.user.id, parent.kind)
            task = next((t for t in tasks if int(t['id']) == task_id), None)
            if task is None:
                raise ValueError('任務已下架、到期或不符合接取資格')
            text = (f"**{discord.utils.escape_markdown(str(task['title']))}**\n"
                    f"任務編號：`{task_id}`\n"
                    f"{discord.utils.escape_markdown(str(task.get('description') or '無補充內容'))[:950]}\n\n"
                    '按下「接取任務」後會儲存這份任務的條件與獎勵版本。')
            await reply(interaction, text, view=AcceptView(interaction.user.id, gid, cid, task_id))
        except (ValueError, KeyError) as exc:
            await reply(interaction, str(exc))


class TaskListView(OwnerView):
    def __init__(self, user_id, guild_id, character_id, kind, tasks):
        super().__init__(user_id)
        self.guild_id = guild_id
        self.character_id = character_id
        self.kind = kind
        self.add_item(TaskSelect(kind, tasks))


class AcceptView(OwnerView):
    def __init__(self, user_id, guild_id, character_id, task_id):
        super().__init__(user_id)
        self.guild_id = guild_id
        self.character_id = character_id
        self.task_id = task_id

    @discord.ui.button(label='接取任務', style=discord.ButtonStyle.success)
    async def accept_button(self, interaction, button):
        try:
            created = accept(self.guild_id, self.character_id,
                             interaction.user.id, self.task_id)
        except ValueError as exc:
            return await reply(interaction, str(exc))
        button.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except discord.HTTPException:
            pass
        await interaction.followup.send(
            '✅ 任務接取成功。完成指定內容後，在任務要求的討論串使用 '
            f'`/任務 角色編號:{self.character_id} 任務編號:{self.task_id}` 回報。'
            if created else '此任務已接取過，請查看接取紀錄。', ephemeral=True,
        )


class KindSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder='選擇任務分類', options=[
            discord.SelectOption(label=KINDS[k], value=k) for k in options
        ])

    async def callback(self, interaction):
        parent = self.view
        kind = self.values[0]
        try:
            tasks = board(parent.guild_id, parent.character_id,
                          interaction.user.id, kind)
        except ValueError as exc:
            return await reply(interaction, str(exc))
        if not tasks:
            return await reply(interaction, '目前沒有已發布且符合接取資格的任務。')
        names = '\n'.join(
            f"• `#{t['id']}` {discord.utils.escape_markdown(str(t['title']))}"
            for t in tasks[:25]
        )
        if kind == 'daily':
            from modules.task_daily import current_cycle
            _, ends_at = current_cycle()
            names = f'本期最多五項，下一次刷新：{ends_at}\n' + names
        await reply(interaction, names[:1800], view=TaskListView(
            interaction.user.id, parent.guild_id, parent.character_id, kind, tasks))


class KindView(OwnerView):
    def __init__(self, user_id, guild_id, character_id, kinds):
        super().__init__(user_id)
        self.guild_id = guild_id
        self.character_id = character_id
        self.add_item(KindSelect(kinds))


async def show_character(interaction, character_id):
    gid, uid = interaction.guild_id, interaction.user.id
    if not character_owner(gid, character_id, uid):
        return await reply(interaction, '無權使用此角色。')
    p = progress(gid, character_id)
    if not p['graduated']:
        expected = bot_thread_id(gid, character_id)
        if expected and str(interaction.channel_id) == str(expected):
            record_success(gid, character_id, uid, interaction.channel_id, '任務')
            p = progress(gid, character_id)
        if not p['graduated']:
            message = (f"🎓 新手指令探索：{p['done']}/{p['total']}\n"
                       f"尚未完成：{'、'.join('/'+x for x in p['remaining'])}")
            message += (f'\n請在 <#{expected}> 完成新手教學。' if expected
                        else '\n角色尚未綁定「機器人使用」帖子，請聯絡管理員。')
            return await reply(interaction, message[:1900])
    # 限時及特殊只有存在可見通告時才顯示。
    categories = []
    for kind in KINDS:
        try:
            if kind in ('limited', 'special') and not board(gid, character_id, uid, kind):
                continue
            categories.append(kind)
        except ValueError:
            continue
    await reply(interaction, f'📋 角色 #{character_id} 的任務中心',
                view=KindView(uid, gid, character_id, categories))


class TasksCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='任務', description='查看任務、接取通告，或在指定討論串回報')
    @app_commands.guild_only()
    @app_commands.describe(角色編號='角色編號；不填則開啟選角面板', 任務編號='已接取且需要回報的任務編號')
    async def tasks(self, interaction: discord.Interaction,
                    角色編號: int | None = None, 任務編號: int | None = None):
        try:
            initialize()
            seed_newbie(interaction.guild_id)
            if 角色編號 is None:
                if 任務編號 is not None:
                    return await reply(interaction, '回報任務時，必須同時填寫角色編號。')
                characters = owned(interaction.guild_id, interaction.user.id)
                if not characters:
                    return await reply(interaction, '請先建立並啟用角色。')
                return await reply(interaction, '請選擇要使用的角色：',
                                   view=CharacterView(interaction.user.id, characters))
            if 任務編號 is not None:
                # 未完成新手期間不接受正式通告回報。
                from modules.task_newbie import is_graduated
                if not is_graduated(interaction.guild_id, 角色編號):
                    return await reply(interaction, '請先完成新手任務。')
                report(interaction.guild_id, 角色編號, interaction.user.id,
                       任務編號, interaction.channel_id)
                return await reply(interaction, '✅ 回報已提交審核；審核完成也不代表獎勵已發放。')
            await show_character(interaction, 角色編號)
        except ValueError as exc:
            await reply(interaction, str(exc)[:1800])


async def setup(bot):
    await bot.add_cog(TasksCenter(bot))
