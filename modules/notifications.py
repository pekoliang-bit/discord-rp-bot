
"""統一通知中心（第一階段）。

公開 API：queue_notification(guild_id, kind, event_type, message, *, character_id=None,
                            priority='normal', dedupe_key=None)
kind: main / character / event / private / rumor
private 會送往角色論壇 notice_id（必須指定 character_id）。
舉報等機密事件不應使用公開頻道；本模組不接管舊有舉報佇列。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import get_connection, transaction
from modules.notification_publish_permissions import is_guild_owner, check_bulletin_ready
from modules.notification_delivery_guard import confirm_existing_delivery
from modules.notification_management_status import bulletin_management_status
from modules.notification_management_view import NotificationManagementView
from modules.notification_release_processor import drain_published_releases

LOG = logging.getLogger(__name__)
KINDS = {
    'main': '📢 主通知', 'character': '👤 角色相關',
    'event': '🎉 活動頻道', 'private': '🔒 私人通知帖',
    'rumor': '🗣️ 傳聞',
}
UPDATE_CATEGORIES = (
    ('story', '📖 攻略劇情'),
    ('system', '⚙️ 系統增加／修改'),
    ('activity', '🎉 活動更新'),
    ('character', '👤 角色／NPC 更新'),
    ('shop', '🏬 商城／商品更新'),
    ('company', '🏢 公司／品牌／集團更新'),
    ('faction', '⚔️ 陣營更新'),
    ('world', '🌍 世界觀更新'),
    ('other', '📌 其他更新'),
)
CATEGORY_LABELS = dict(UPDATE_CATEGORIES)
LEGACY_CATEGORY = {
    'system': 'system', 'role': 'character',
    'company': 'company', 'bank': 'other',
    'faction': 'faction', 'activity': 'activity',
    'shop': 'shop', 'rumor': 'other',
    'content': 'other', 'other': 'other'
}
DEFAULT_NOTIFICATION_GUILD = '1544051203493601380'
DEFAULT_NOTIFICATION_CHANNELS = {
    'main': '1567786757020323960',
    'event': '1567786824326189238',
    'rumor': '1545703079691689994',
}

VALID_LINK = re.compile(
    r'^https://(?:discord\.com/channels/|(?:www\.)?discordapp\.com/channels/)'
    r'[0-9]+/[0-9]+(?:/[0-9]+)?/?(?:\?.*)?$',
    re.I
)


def _safe(value: str, limit: int = 100) -> str:
    """消除提及、換行與格式注入。"""
    return re.sub(
        r'[\r\n`*_\[\]<>@]', ' ', str(value)
    ).strip()[:limit]


def record_update(
    guild_id: int,
    category: str,
    title: str,
    action: str = '新增',
    *,
    source_url: str | None = None,
    character_name: str | None = None,
    update_key: str | None = None
) -> bool:
    """正式公開／已審核完成後由業務模組呼叫。"""
    if category not in CATEGORY_LABELS or action not in (
        '新增', '更新', '移除'
    ):
        raise ValueError('未知公告分類或更新動作')

    if not title.strip():
        raise ValueError('更新項目名稱不能空白')

    if source_url is not None and not VALID_LINK.fullmatch(source_url):
        raise ValueError('來源必須是 Discord 訊息或頻道連結')

    initialize()

    with transaction() as db:
        cur = db.execute(
            '''INSERT OR IGNORE INTO notification_update_items
            (guild_id,category,title,action,source_url,character_name,update_key)
            VALUES (?,?,?,?,?,?,?)''',
            (
                str(guild_id), category, title[:180],
                action, source_url, character_name, update_key
            )
        )
        return cur.rowcount > 0


def _render_digest(rows, legacy):
    """依正式順序組合公告摘要。"""
    aggregates = {
        key: {'新增': 0, '更新': 0, '移除': 0}
        for key, _ in UPDATE_CATEGORIES
    }
    characters = {}

    for r in rows:
        k, a = r['category'], r['action']
        aggregates[k][a] += 1

        if k == 'story':
            name = _safe(
                r['character_name'] or '未分類角色', 50
            )
            characters.setdefault(
                name, {'新增': 0, '更新': 0, '移除': 0}
            )[a] += 1

    for r in legacy:
        key = LEGACY_CATEGORY.get(
            r['event_type'], 'other'
        )
        aggregates[key][r['action']] += r['amount']

    local_now = datetime.now(
        timezone.utc
    ).astimezone(timezone(timedelta(hours=8)))

    lines = [f'📢 **{local_now:%Y年%m月%d日} 更新**']
    active = []

    for key, label in UPDATE_CATEGORIES:
        totals = aggregates[key]

        if not any(totals.values()):
            continue

        active.append(key)
        lines += ['', f'**【{label}】**']

        if key == 'story' and characters:
            for name, counts in sorted(characters.items()):
                details = '、'.join(
                    f'{a} {n} 條'
                    for a, n in counts.items() if n
                )
                lines.append(f'{name}：{details}')

        character_total = sum(
            sum(v.values()) for v in characters.values()
        )

        if key != 'story' or sum(totals.values()) > character_total:
            details = '、'.join(
                f'{a} {n} 項'
                for a, n in totals.items() if n
            )
            lines.append('合計：' + details)

    lines += [
        '',
        '👇 點擊下方分類按鈕，查看詳細更新與原始連結。'
    ]
    return '\n'.join(lines)[:1950], active


EVENT_TYPES = (
    'system', 'role', 'company', 'bank', 'faction',
    'activity', 'shop', 'rumor', 'content', 'other'
)


def initialize():
    """建立通知所需資料表。"""
    with transaction() as db:
        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_channels (
            guild_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            PRIMARY KEY(guild_id, kind))'''
        )

        db.executemany(
            '''INSERT OR IGNORE INTO notification_channels
            (guild_id,kind,channel_id) VALUES (?,?,?)''',
            (
                (DEFAULT_NOTIFICATION_GUILD, kind, channel)
                for kind, channel
                in DEFAULT_NOTIFICATION_CHANNELS.items()
            )
        )

        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_preferences (
            guild_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(guild_id, event_type))'''
        )

        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            event_type TEXT NOT NULL,
            character_id INTEGER,
            body TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            dedupe_key TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT,
            next_attempt_at TEXT,
            UNIQUE(guild_id,dedupe_key))'''
        )

        db.execute(
            '''CREATE INDEX IF NOT EXISTS idx_notify_pending
            ON notification_outbox(status, next_attempt_at, id)'''
        )

        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_digest (
            guild_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            action TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id,event_type,action))'''
        )

        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_update_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            action TEXT NOT NULL,
            source_url TEXT,
            character_name TEXT,
            update_key TEXT,
            announcement_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,update_key))'''
        )

        db.execute(
            '''CREATE INDEX IF NOT EXISTS idx_notification_updates
            ON notification_update_items(
                guild_id,announcement_id,category,id
            )'''
        )

        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            message_id TEXT UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'''
        )

        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_announcement_legacy (
            announcement_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            action TEXT NOT NULL,
            amount INTEGER NOT NULL)'''
        )

        db.execute(
            '''CREATE TABLE IF NOT EXISTS notification_schedule (
            guild_id TEXT PRIMARY KEY,
            digest_days INTEGER NOT NULL DEFAULT 7,
            last_digest_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'''
        )


def queue_notification(
    guild_id: int,
    kind: str,
    event_type: str,
    message: str,
    *,
    character_id: int | None = None,
    priority: str = 'normal',
    dedupe_key: str | None = None
) -> bool:
    """通知加入佇列，不立即送至 Discord。"""
    if kind not in KINDS or event_type not in EVENT_TYPES:
        raise ValueError('通知類別或事件類型無效')

    if kind == 'private' and character_id is None:
        raise ValueError('私人通知需要角色 ID')

    if priority not in ('normal', 'high') or not message.strip():
        raise ValueError('通知優先級或內容無效')

    if len(message) > 1800:
        raise ValueError('單則通知長度不可超過 1800 字')

    initialize()

    with transaction() as db:
        pref = db.execute(
            '''SELECT enabled FROM notification_preferences
            WHERE guild_id=? AND event_type=?''',
            (str(guild_id), event_type)
        ).fetchone()

        if pref and not pref['enabled']:
            return False

        cur = db.execute(
            '''INSERT OR IGNORE INTO notification_outbox
            (guild_id,kind,event_type,character_id,body,priority,dedupe_key)
            VALUES (?,?,?,?,?,?,?)''',
            (
                str(guild_id), kind, event_type,
                character_id, message.strip(),
                priority, dedupe_key
            )
        )
        return cur.rowcount > 0


def count_digest(
    guild_id: int,
    event_type: str,
    action: str,
    amount: int = 1
):
    """低優先級更動只累加數量。"""
    if (
        event_type not in EVENT_TYPES
        or action not in ('新增', '移除', '更新')
        or amount <= 0
    ):
        raise ValueError('無效的彙總事件')

    initialize()

    with transaction() as db:
        db.execute(
            '''INSERT INTO notification_digest
            (guild_id,event_type,action,amount)
            VALUES (?,?,?,?)
            ON CONFLICT(guild_id,event_type,action)
            DO UPDATE SET amount=amount+excluded.amount''',
            (str(guild_id), event_type, action, amount)
        )


def owner_only(interaction: discord.Interaction) -> bool:
    return bool(
        interaction.guild
        and interaction.guild.owner_id == interaction.user.id
    )


class UpdateAnnouncementView(discord.ui.View):
    """固定 custom_id，支援重啟後查看公告。"""

    def __init__(self, active_categories=None):
        super().__init__(timeout=None)

        for key, label in UPDATE_CATEGORIES:
            if (
                active_categories is not None
                and key not in active_categories
            ):
                continue

            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f'rp_update_category:{key}'
            )
            button.callback = self._callback_factory(key)
            self.add_item(button)

    @staticmethod
    def _callback_factory(category):
        async def callback(interaction: discord.Interaction):
            if not interaction.message or not interaction.guild_id:
                await interaction.response.send_message(
                    '無法定位公告。', ephemeral=True
                )
                return

            with get_connection() as db:
                announcement = db.execute(
                    '''SELECT id FROM notification_announcements
                    WHERE guild_id=? AND message_id=?''',
                    (
                        str(interaction.guild_id),
                        str(interaction.message.id)
                    )
                ).fetchone()

                rows = (
                    list(
                        db.execute(
                            '''SELECT title,action,source_url,character_name
                            FROM notification_update_items
                            WHERE announcement_id=? AND category=?
                            ORDER BY id''',
                            (announcement['id'], category)
                        )
                    )
                    if announcement else []
                )

                old = (
                    list(
                        db.execute(
                            '''SELECT action,amount
                            FROM notification_announcement_legacy
                            WHERE announcement_id=? AND category=?''',
                            (announcement['id'], category)
                        )
                    )
                    if announcement else []
                )

            if not announcement or not (rows or old):
                await interaction.response.send_message(
                    '本期此分類沒有可查看的更新。',
                    ephemeral=True
                )
                return

            lines = [
                f'**【{CATEGORY_LABELS[category]}】詳細更新**'
            ]

            for r in rows:
                title = _safe(r['title'], 100)
                name = (
                    f'{_safe(r["character_name"], 45)}｜'
                    if r['character_name'] else ''
                )
                link = (
                    f' [前往內容]({r["source_url"]})'
                    if r['source_url']
                    else '（尚無來源連結）'
                )
                lines.append(
                    f'• {name}{r["action"]}：{title}{link}'
                )

            for r in old:
                lines.append(
                    f'• 舊版統計：{r["action"]} '
                    f'{r["amount"]} 項（沒有逐項來源）'
                )

            chunks = []
            current = lines[0]

            for line in lines[1:]:
                if len(current) + len(line) + 1 > 1850:
                    chunks.append(current)
                    current = lines[0] + '\n' + line
                else:
                    current += '\n' + line

            chunks.append(current)

            await interaction.response.send_message(
                chunks[0],
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none()
            )

            for chunk in chunks[1:]:
                await interaction.followup.send(
                    chunk,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none()
                )

        return callback


class NotificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        placeholder='選擇通知目的地',
        options=[
            discord.SelectOption(label=label, value=key)
            for key, label in KINDS.items()
            if key != 'private'
        ]
    )
    async def destinations(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                '只有群主能修改通知設定。',
                ephemeral=True
            )
            return

        kind = select.values[0]

        await interaction.response.send_message(
            f'{KINDS[kind]}：請選擇文字頻道'
            '（私人通知自動使用角色論壇的「通知」帖子）。',
            view=DestinationView(kind),
            ephemeral=True
        )

    @discord.ui.button(
        label='通知項目開關',
        emoji='⚙️',
        style=discord.ButtonStyle.secondary
    )
    async def switches(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                '只有群主能修改通知設定。',
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            '選擇要啟用／停用的事件類型：',
            view=PreferencesView(),
            ephemeral=True
        )

    @discord.ui.button(
        label='發送測試通知',
        emoji='🧪',
        style=discord.ButtonStyle.secondary
    )
    async def test(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                '只有群主能測試通知。',
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            '請選擇測試目的地：',
            view=TestView(),
            ephemeral=True
        )

    @discord.ui.button(
        label='立即發布更新',
        emoji='📢',
        style=discord.ButtonStyle.success
    )
    async def publish_bulletin(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not is_guild_owner(interaction):
            await interaction.response.send_message(
                '只有伺服器群主可以發布更新公告。',
                ephemeral=True
            )
            return

        from modules.notification_bulletin_service import prepare_bulletin

        ok, reason = check_bulletin_ready(
            interaction.guild_id
        )

        if not ok:
            await interaction.response.send_message(
                reason, ephemeral=True
            )
            return

        bulletin_id = prepare_bulletin(
            interaction.guild_id,
            include_legacy=False
        )

        if bulletin_id is None:
            await interaction.response.send_message(
                '目前沒有可排入公告的更新。',
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f'✅ 公告 #{bulletin_id} 已排入佇列。'
            '成功送達主通知頻道後才會標記完成。',
            ephemeral=True
        )

    @discord.ui.button(
        label='公告管理',
        emoji='📋',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def manage_bulletins(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                '只有本伺服器群主能管理公告。',
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            bulletin_management_status(interaction.guild_id),
            view=NotificationManagementView(
                interaction.guild_id
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )

    @discord.ui.button(
        label='狀態刷新',
        emoji='🔄',
        style=discord.ButtonStyle.primary
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.edit_message(
            content=status_text(interaction.guild_id),
            view=NotificationView()
        )


class DestinationView(discord.ui.View):
    def __init__(self, kind):
        super().__init__(timeout=180)
        self.kind = kind

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder='選擇通知頻道',
        channel_types=[discord.ChannelType.text]
    )
    async def choose(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                '只有群主能修改設定。',
                ephemeral=True
            )
            return

        channel = select.values[0]

        with transaction() as db:
            db.execute(
                '''INSERT INTO notification_channels
                (guild_id,kind,channel_id)
                VALUES (?,?,?)
                ON CONFLICT(guild_id,kind)
                DO UPDATE SET channel_id=excluded.channel_id''',
                (
                    str(interaction.guild_id),
                    self.kind,
                    str(channel.id)
                )
            )

        await interaction.response.edit_message(
            content=f'✅ {KINDS[self.kind]} 設定為 <#{channel.id}>。',
            view=None
        )


class PreferencesView(discord.ui.View):
    @discord.ui.select(
        placeholder='點選事件類型以切換啟用狀態',
        options=[
            discord.SelectOption(label=x, value=x)
            for x in EVENT_TYPES
        ]
    )
    async def choose(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                '只有群主能修改設定。',
                ephemeral=True
            )
            return

        key = select.values[0]

        with transaction() as db:
            row = db.execute(
                '''SELECT enabled
                FROM notification_preferences
                WHERE guild_id=? AND event_type=?''',
                (str(interaction.guild_id), key)
            ).fetchone()

            new = 0 if row is None or row['enabled'] else 1

            db.execute(
                '''INSERT INTO notification_preferences
                (guild_id,event_type,enabled)
                VALUES (?,?,?)
                ON CONFLICT(guild_id,event_type)
                DO UPDATE SET enabled=excluded.enabled''',
                (str(interaction.guild_id), key, new)
            )

        await interaction.response.edit_message(
            content=(
                f'{key}：{"✅ 啟用" if new else "🚫 停用"}'
                '（再選一次可切換）'
            ),
            view=self
        )


class TestView(discord.ui.View):
    @discord.ui.select(
        placeholder='選擇要測試的公開頻道',
        options=[
            discord.SelectOption(label=label, value=key)
            for key, label in KINDS.items()
            if key != 'private'
        ]
    )
    async def choose(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                '只有群主能測試通知。',
                ephemeral=True
            )
            return

        kind = select.values[0]

        with get_connection() as db:
            row = db.execute(
                '''SELECT channel_id
                FROM notification_channels
                WHERE guild_id=? AND kind=?''',
                (str(interaction.guild_id), kind)
            ).fetchone()

        if not row:
            await interaction.response.send_message(
                '請先設定該類別的通知頻道。',
                ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(
            int(row['channel_id'])
        )

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                '找不到已設定的文字頻道。',
                ephemeral=True
            )
            return

        try:
            await channel.send(
                '🧪 通知中心測試成功。',
                allowed_mentions=discord.AllowedMentions.none()
            )
        except (
            discord.Forbidden,
            discord.HTTPException
        ) as exc:
            await interaction.response.send_message(
                f'發送失敗：{type(exc).__name__}。'
                '請確認發訊權限。',
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f'✅ 測試通知已送至 {channel.mention}。',
            ephemeral=True
        )


def status_text(guild_id):
    if guild_id is None:
        return '請在伺服器使用 /通知。'

    initialize()

    with get_connection() as db:
        channels = {
            r['kind']: r['channel_id']
            for r in db.execute(
                '''SELECT kind,channel_id
                FROM notification_channels
                WHERE guild_id=?''',
                (str(guild_id),)
            )
        }

        prefs = {
            r['event_type']: bool(r['enabled'])
            for r in db.execute(
                '''SELECT event_type,enabled
                FROM notification_preferences
                WHERE guild_id=?''',
                (str(guild_id),)
            )
        }

        queue = db.execute(
            """SELECT COUNT(*) AS n
            FROM notification_outbox
            WHERE guild_id=? AND status='pending'""",
            (str(guild_id),)
        ).fetchone()['n']

    lines = [
        '🔔 **通知管理中心**',
        '通知頻道：'
    ]

    lines.extend(
        f'{label}：'
        f'{f"<#{channels[key]}>" if key in channels else "未設定"}'
        for key, label in KINDS.items()
        if key != 'private'
    )

    lines += [
        '🔒 私人通知：角色論壇的固定「通知」帖子'
        '（不使用 Discord 私訊）',
        '事件類型：' + '、'.join(
            f'{key}{"✅" if prefs.get(key, True) else "🚫"}'
            for key in EVENT_TYPES
        ),
        f'待發通知：{queue} 則',
        '低優先級內容彙總預設每 7 天發送一次。'
    ]

    return '\n'.join(lines)


class Notifications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        initialize()
        self.bot.add_view(UpdateAnnouncementView())
        self.dispatcher.start()

    def cog_unload(self):
        self.dispatcher.cancel()

    @app_commands.command(
        name='通知',
        description='查看通知中心與管理通知頻道、訂閱及測試'
    )
    @app_commands.guild_only()
    async def notification(
        self,
        interaction: discord.Interaction
    ):
        if not interaction.guild:
            return

        await interaction.response.send_message(
            status_text(interaction.guild_id),
            view=(
                NotificationView()
                if owner_only(interaction)
                else None
            ),
            ephemeral=True
        )

    @tasks.loop(seconds=45)
    async def dispatcher(self):
        try:
            drain_published_releases(limit=100)
        except Exception:
            LOG.exception(
                "正式更新事件轉入通知清單失敗"
            )

        now = datetime.now(timezone.utc)

        with get_connection() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    '''SELECT * FROM notification_outbox
                    WHERE status='pending'
                    AND (
                        next_attempt_at IS NULL
                        OR next_attempt_at<=?
                    )
                    ORDER BY
                        CASE priority
                        WHEN 'high' THEN 0
                        ELSE 1 END,
                        id
                    LIMIT 30''',
                    (
                        now.strftime('%Y-%m-%d %H:%M:%S'),
                    )
                )
            ]

        for row in rows:
            if confirm_existing_delivery(
                row['id'],
                int(row['guild_id']),
                row['dedupe_key']
            ):
                continue

            guild = self.bot.get_guild(
                int(row['guild_id'])
            )

            if guild is None:
                continue

            with get_connection() as db:
                if row['kind'] == 'private':
                    try:
                        target = db.execute(
                            '''SELECT notice_id
                            FROM character_forum_links
                            WHERE guild_id=? AND character_id=?''',
                            (
                                row['guild_id'],
                                row['character_id']
                            )
                        ).fetchone()

                        channel_id = (
                            target['notice_id']
                            if target else None
                        )

                    except sqlite3.OperationalError:
                        channel_id = None

                else:
                    target = db.execute(
                        '''SELECT channel_id
                        FROM notification_channels
                        WHERE guild_id=? AND kind=?''',
                        (
                            row['guild_id'],
                            row['kind']
                        )
                    ).fetchone()

                    channel_id = (
                        target['channel_id']
                        if target else None
                    )

            if not channel_id:
                continue

            channel = guild.get_channel_or_thread(
                int(channel_id)
            )

            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(
                        int(channel_id)
                    )
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException
                ):
                    channel = None

            if (
                channel is None
                or getattr(channel, 'guild', None) != guild
            ):
                continue

            if row['kind'] == 'private':
                from modules.character_forums import (
                    _private_forum_is_secure,
                    marker
                )

                with get_connection() as db:
                    owner_row = db.execute(
                        '''SELECT owner_id
                        FROM characters
                        WHERE guild_id=? AND id=?''',
                        (
                            row['guild_id'],
                            row['character_id']
                        )
                    ).fetchone()

                forum = getattr(
                    channel, 'parent', None
                )

                if (
                    not owner_row
                    or not isinstance(
                        channel, discord.Thread
                    )
                    or forum is None
                    or forum.topic != marker(
                        row['guild_id'],
                        row['character_id']
                    )
                    or not _private_forum_is_secure(forum, owner_row['owner_id'])
                ):
                    LOG.warning(
                        '私人通知 #%s 暫緩：'
                        '論壇權限或角色綁定尚未核實',
                        row['id']
                    )
                    continue

            try:
                is_announcement = bool(
                    re.fullmatch(r'bulletin-[0-9]+', row['dedupe_key'] or '')
                )

                active_categories = None

                if is_announcement:
                    announcement_id = int(
                        row['dedupe_key'].split('-')[-1]
                    )

                    with get_connection() as db:
                        cats = {
                            r['category']
                            for r in db.execute(
                                '''SELECT DISTINCT category
                                FROM notification_update_items
                                WHERE announcement_id=?''',
                                (announcement_id,)
                            )
                        }

                        cats |= {
                            r['category']
                            for r in db.execute(
                                '''SELECT DISTINCT category
                                FROM notification_announcement_legacy
                                WHERE announcement_id=?''',
                                (announcement_id,)
                            )
                        }

                    active_categories = cats

                message = await channel.send(
                    row['body'],
                    view=(
                        UpdateAnnouncementView(
                            active_categories
                        )
                        if is_announcement
                        else None
                    ),
                    allowed_mentions=(
                        discord.AllowedMentions.none()
                    )
                )

            except (
                discord.Forbidden,
                discord.HTTPException
            ) as exc:
                attempts = row['attempts'] + 1
                delay = min(
                    60 * (2 ** min(attempts, 6)),
                    3600
                )

                next_try = (
                    now + timedelta(seconds=delay)
                ).strftime('%Y-%m-%d %H:%M:%S')

                with transaction() as db:
                    db.execute(
                        '''UPDATE notification_outbox
                        SET attempts=?,
                            last_error=?,
                            next_attempt_at=?,
                            status=?
                        WHERE id=?
                        AND status='pending' ''',
                        (
                            attempts,
                            type(exc).__name__,
                            next_try,
                            (
                                'failed'
                                if attempts >= 8
                                else 'pending'
                            ),
                            row['id']
                        )
                    )

            else:
                with transaction() as db:
                    if is_announcement:
                        announcement_id = int(
                            row['dedupe_key'].split('-')[-1]
                        )

                        db.execute(
                            '''UPDATE notification_announcements
                            SET message_id=?
                            WHERE id=?''',
                            (
                                str(message.id),
                                announcement_id
                            )
                        )

                    db.execute(
                        '''UPDATE notification_outbox
                        SET status='sent',
                            sent_at=CURRENT_TIMESTAMP
                        WHERE id=? AND status='pending' ''',
                        (row['id'],)
                    )

        await self.dispatch_digests(now)

    async def dispatch_digests(self, now):
        with get_connection() as db:
            guild_ids = {
                r['guild_id']
                for r in db.execute(
                    '''SELECT DISTINCT guild_id
                    FROM notification_digest'''
                )
            }

            guild_ids |= {
                r['guild_id']
                for r in db.execute(
                    '''SELECT DISTINCT guild_id
                    FROM notification_update_items
                    WHERE announcement_id IS NULL'''
                )
            }

        for guild_id in guild_ids:
            with transaction() as db:
                db.execute(
                    '''INSERT OR IGNORE
                    INTO notification_schedule(guild_id)
                    VALUES (?)''',
                    (guild_id,)
                )

                schedule = db.execute(
                    '''SELECT *
                    FROM notification_schedule
                    WHERE guild_id=?''',
                    (guild_id,)
                ).fetchone()

                try:
                    last = datetime.strptime(
                        schedule['last_digest_at'],
                        '%Y-%m-%d %H:%M:%S'
                    ).replace(tzinfo=timezone.utc)

                except ValueError:
                    continue

                if now - last < timedelta(
                    days=schedule['digest_days']
                ):
                    continue

                rows = list(
                    db.execute(
                        '''SELECT *
                        FROM notification_update_items
                        WHERE guild_id=?
                        AND announcement_id IS NULL
                        ORDER BY id''',
                        (guild_id,)
                    )
                )

                legacy = list(
                    db.execute(
                        '''SELECT *
                        FROM notification_digest
                        WHERE guild_id=?''',
                        (guild_id,)
                    )
                )

                if not rows and not legacy:
                    continue

                dest = db.execute(
                    '''SELECT 1
                    FROM notification_channels
                    WHERE guild_id=?
                    AND kind='main' ''',
                    (guild_id,)
                ).fetchone()

                if not dest:
                    continue

                body, active = _render_digest(
                    rows, legacy
                )

                result = db.execute(
                    '''INSERT INTO notification_announcements
                    (guild_id) VALUES (?)''',
                    (guild_id,)
                )

                announcement_id = result.lastrowid

                if rows:
                    db.executemany(
                        '''UPDATE notification_update_items
                        SET announcement_id=?
                        WHERE id=?''',
                        (
                            (announcement_id, r['id'])
                            for r in rows
                        )
                    )

                for r in legacy:
                    db.execute(
                        '''INSERT INTO notification_announcement_legacy
                        (announcement_id,category,action,amount)
                        VALUES (?,?,?,?)''',
                        (
                            announcement_id,
                            LEGACY_CATEGORY.get(
                                r['event_type'], 'other'
                            ),
                            r['action'],
                            r['amount']
                        )
                    )

                db.execute(
                    '''INSERT INTO notification_outbox
                    (guild_id,kind,event_type,body,dedupe_key)
                    VALUES (?,?,?,?,?)''',
                    (
                        guild_id,
                        'main',
                        'content',
                        body,
                        f'bulletin-{announcement_id}'
                    )
                )

                db.execute(
                    '''DELETE FROM notification_digest
                    WHERE guild_id=?''',
                    (guild_id,)
                )

                db.execute(
                    '''UPDATE notification_schedule
                    SET last_digest_at=CURRENT_TIMESTAMP
                    WHERE guild_id=?''',
                    (guild_id,)
                )

    @dispatcher.before_loop
    async def before_dispatcher(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Notifications(bot))
