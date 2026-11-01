"""第六批 16/18：公告紀錄管理介面（供既有 /通知 View 呼叫，不新增指令）。"""
from __future__ import annotations

import discord
from core.database import get_connection
from modules.notification_categories import LABELS, ordered_active_categories


def history_rows(guild_id: int, limit: int = 10) -> list[dict]:
    """僅列出此伺服器最近的公告及其更新數量。"""
    with get_connection() as db:
        rows = db.execute('''SELECT a.id,a.created_at,a.message_id,
            COUNT(i.id) AS item_count
            FROM notification_announcements a
            LEFT JOIN notification_update_items i
              ON i.guild_id=a.guild_id AND i.announcement_id=a.id
            WHERE a.guild_id=? GROUP BY a.id ORDER BY a.id DESC LIMIT ?''',
            (str(guild_id), max(1, min(int(limit), 25)))).fetchall()
    return [dict(row) for row in rows]


def history_content(guild_id: int, limit: int = 10) -> str:
    rows = history_rows(guild_id, limit)
    if not rows:
        return '📋 目前尚無更新公告紀錄。'
    lines = ['📋 **更新公告紀錄**（由新到舊）']
    for item in rows:
        state = '✅ 已送達' if item['message_id'] else '⏳ 未確認送達'
        lines.append(f"• #{item['id']}｜{item['created_at']}｜{state}｜{item['item_count']} 筆詳細更新")
    lines.append('選擇下方公告編號，查看該次詳細分類。')
    return '\n'.join(lines)[:1900]


class BulletinHistoryView(discord.ui.View):
    """管理用途，呼叫方須先確認群主身分；callback 會再次檢查。"""
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = int(guild_id)
        rows = history_rows(guild_id, 25)
        if not rows:
            return
        select = discord.ui.Select(
            placeholder='查看公告詳情',
            options=[discord.SelectOption(label=f"公告 #{r['id']}", value=str(r['id']),
                    description=f"{'已送達' if r['message_id'] else '未確認送達'} · {r['item_count']} 筆") for r in rows]
        )
        select.callback = self._select
        self.add_item(select)

    async def _select(self, interaction: discord.Interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message('只有本伺服器群主可查看管理紀錄。', ephemeral=True)
            return
        aid = int(self.children[0].values[0])
        with get_connection() as db:
            a = db.execute('SELECT message_id FROM notification_announcements WHERE guild_id=? AND id=?',
                           (str(self.guild_id), aid)).fetchone()
            if not a:
                await interaction.response.send_message('找不到這筆公告。', ephemeral=True)
                return
            rs = db.execute('SELECT category,COUNT(*) AS n FROM notification_update_items WHERE guild_id=? AND announcement_id=? GROUP BY category',
                            (str(self.guild_id), aid)).fetchall()
        counts = {r['category']: r['n'] for r in rs}
        lines = [f'**公告 #{aid}**', '狀態：' + ('已送達' if a['message_id'] else '未確認送達')]
        lines += [f'• {LABELS.get(key,key)}：{counts[key]} 筆' for key in ordered_active_categories(counts)]
        if not counts:
            lines.append('沒有新版詳細更新紀錄（可能是舊版彙總）。')
        await interaction.response.send_message('\n'.join(lines), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
