"""Discord RP 舉報整合入口（第 11 檔）。

重要：不註冊任何新的 slash command，尤其不重複註冊 /申請、/審核。
現有 modules/player_applications.py 與 modules/review_center.py 在適當按鈕
callback 中呼叫 open_report_application / open_report_review 即可。

本檔另提供私密申訴、群主保存期限面板，以及可選的背景維護排程。
排程預設「只預覽清理」，必須由部署者明確啟用才會真的清理。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ext import commands

from core.database import get_connection
from modules import report_permission_service as perms
from modules import report_repository as repository
from modules import report_view, report_review_view
from modules import report_appeal_service as appeals
from modules import report_sanction_service as sanctions
from modules import report_retention_service as retention

LOG = logging.getLogger(__name__)
VIEW_TIMEOUT = 300


async def _send(interaction: discord.Interaction, *, content: str | None = None,
                embed: discord.Embed | None = None, view: discord.ui.View | None = None):
    if interaction.response.is_done():
        return await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=True)
    return await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, (ValueError, PermissionError, LookupError)):
        return str(exc)[:1500]
    LOG.exception('舉報整合操作異常', exc_info=exc)
    return '操作失敗；請聯絡群主檢查機器人日誌。'


async def _fail(interaction: discord.Interaction, exc: Exception):
    await _send(interaction, content='⚠️ ' + _error_text(exc))


def _bound(interaction: discord.Interaction, guild_id: int, user_id: int) -> bool:
    return interaction.guild is not None and interaction.guild.id == guild_id and interaction.user.id == user_id


async def open_report_application(interaction: discord.Interaction) -> None:
    """接入 /申請 → 舉報 的正式入口，不要求角色已啟用。"""
    await report_view.start_report_home(interaction)


async def open_report_review(interaction: discord.Interaction) -> None:
    """接入 /審核 → 舉報；權限由審核模組再次驗證。"""
    await report_review_view.start_report_review_home(interaction)


class _BoundView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=VIEW_TIMEOUT)
        if interaction.guild is None:
            raise ValueError('只能在伺服器內操作。')
        self.guild_id = interaction.guild.id
        self.user_id = interaction.user.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not _bound(interaction, self.guild_id, self.user_id):
            await _send(interaction, content='這不是你的私人操作面板。')
            return False
        return True


class AppealSubmitModal(discord.ui.Modal, title='針對處分提出申訴'):
    sanction_id = discord.ui.TextInput(label='處分編號（數字）', min_length=1, max_length=20,
                                       placeholder='例如：128')
    reason = discord.ui.TextInput(label='申訴理由（10～3000 字）', style=discord.TextStyle.paragraph,
                                  min_length=10, max_length=3000)

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.guild_id, self.user_id = guild_id, user_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _bound(interaction, self.guild_id, self.user_id):
            return await _send(interaction, content='無權提出此申訴。')
        if not str(self.sanction_id.value).isdigit():
            return await _send(interaction, content='處分編號必須為數字。')
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await appeals.submit_appeal(interaction,
                sanction_id=int(self.sanction_id.value), reason=str(self.reason.value))
            await _send(interaction, content=f'✅ 已提出申訴 #{result.appeal_id}。每件處分僅能申訴一次。')
        except Exception as exc:
            await _fail(interaction, exc)


class AppealDecisionModal(discord.ui.Modal, title='申訴審查決定'):
    appeal_id = discord.ui.TextInput(label='申訴編號', max_length=20)
    decision = discord.ui.TextInput(label='結果 uphold / revoke / reduce', max_length=8)
    reason = discord.ui.TextInput(label='裁決原因', style=discord.TextStyle.paragraph,
                                  min_length=3, max_length=1000)

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.guild_id, self.user_id = guild_id, user_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _bound(interaction, self.guild_id, self.user_id):
            return await _send(interaction, content='無權操作。')
        if not str(self.appeal_id.value).isdigit():
            return await _send(interaction, content='申訴編號必須為數字。')
        d = str(self.decision.value).strip().lower()
        if d not in {'uphold', 'revoke', 'reduce'}:
            return await _send(interaction, content='結果只能輸入 uphold、revoke 或 reduce。')
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await appeals.decide_appeal(interaction, appeal_id=int(self.appeal_id.value),
                            decision=d, reason=str(self.reason.value))
            await _send(interaction, content=f'申訴 #{result.appeal_id}：{result.status}。{result.message}')
        except Exception as exc:
            await _fail(interaction, exc)


class AppealClaimModal(discord.ui.Modal, title='受理申訴'):
    appeal_id = discord.ui.TextInput(label='申訴編號', max_length=20)

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.guild_id, self.user_id = guild_id, user_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _bound(interaction, self.guild_id, self.user_id):
            return await _send(interaction, content='無權操作。')
        if not str(self.appeal_id.value).isdigit():
            return await _send(interaction, content='申訴編號必須為數字。')
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await appeals.claim_appeal(interaction, appeal_id=int(self.appeal_id.value))
            await _send(interaction, content=f'已受理申訴 #{result.appeal_id}：{result.status}')
        except Exception as exc:
            await _fail(interaction, exc)


class AppealHomeView(_BoundView):
    @discord.ui.button(label='提出申訴', style=discord.ButtonStyle.primary)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AppealSubmitModal(self.guild_id, self.user_id))

    @discord.ui.button(label='我的申訴', style=discord.ButtonStyle.secondary)
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            rows = await appeals.get_my_appeals(interaction, limit=20)
            if not rows:
                return await _send(interaction, content='沒有申訴紀錄。')
            lines = [f"#{x['id']}｜處分 #{x['sanction_id']}｜{x['status']}" for x in rows]
            await _send(interaction, content='**我的申訴**\n'+'\n'.join(lines)[:1800])
        except Exception as exc:
            await _fail(interaction, exc)


async def open_report_appeals(interaction: discord.Interaction) -> None:
    """可由既有申請入口或處分通知按鈕呼叫。"""
    await _send(interaction, content='**舉報申訴中心**｜每件處分最多申訴一次。',
                view=AppealHomeView(interaction))


class AppealReviewView(_BoundView):
    @discord.ui.button(label='查看待審申訴', style=discord.ButtonStyle.primary)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            rows = await appeals.list_appeal_queue(interaction, limit=20)
            if not rows:
                return await _send(interaction, content='目前沒有你有權處理的申訴。')
            await _send(interaction, content='**待審申訴**\n'+ '\n'.join(
                f"#{r['id']}｜處分 #{r['sanction_id']}｜{r['status']}" for r in rows)[:1800])
        except Exception as exc:
            await _fail(interaction, exc)

    @discord.ui.button(label='受理申訴', style=discord.ButtonStyle.secondary)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AppealClaimModal(self.guild_id, self.user_id))

    @discord.ui.button(label='申訴裁決', style=discord.ButtonStyle.danger)
    async def decide(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AppealDecisionModal(self.guild_id, self.user_id))


async def open_report_appeal_review(interaction: discord.Interaction) -> None:
    try:
        perms.require_discipline_or_owner(interaction)
        await _send(interaction, content='**申訴審核**｜原案件處理人員必須迴避。',
                    view=AppealReviewView(interaction))
    except Exception as exc:
        await _fail(interaction, exc)


class RetentionPolicyModal(discord.ui.Modal, title='舉報資料保存期限'):
    normal = discord.ui.TextInput(label='一般案件保存天數（1～3650）', default='180', max_length=4)
    major = discord.ui.TextInput(label='重大案件保存天數（1～3650）', default='365', max_length=4)

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.guild_id, self.user_id = guild_id, user_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _bound(interaction, self.guild_id, self.user_id):
            return await _send(interaction, content='無權操作。')
        try:
            perms.require_guild_owner(interaction)
            if not str(self.normal.value).isdigit() or not str(self.major.value).isdigit():
                raise ValueError('期限必須為整數天數。')
            result = await retention.set_retention_policy(interaction,
                normal_days=int(self.normal.value), major_days=int(self.major.value))
            await _send(interaction, content=(f'✅ 保存政策已更新：一般 {result.normal_days} 天，'
                                              f'重大 {result.major_days} 天。'))
        except Exception as exc:
            await _fail(interaction, exc)


class RetentionExtendModal(discord.ui.Modal, title='延長個別案件保存'):
    report_id = discord.ui.TextInput(label='案件編號（數字）', max_length=20)
    days = discord.ui.TextInput(label='延長天數', max_length=4)

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.guild_id, self.user_id = guild_id, user_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _bound(interaction, self.guild_id, self.user_id):
            return await _send(interaction, content='無權操作。')
        try:
            perms.require_guild_owner(interaction)
            if not self.report_id.value.isdigit() or not self.days.value.isdigit():
                raise ValueError('案件編號及天數必須是整數。')
            # 延長保留的具體日期由服務層處理，避免自行操作資料庫。
            await retention.extend_case_retention(interaction, report_id=int(self.report_id.value),
                                                  extra_days=int(self.days.value), reason='群主手動延長')
            await _send(interaction, content='✅ 已更新案件保存期限。')
        except Exception as exc:
            await _fail(interaction, exc)


class RetentionConfirmView(_BoundView):
    @discord.ui.button(label='確認清理符合條件的案件', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            perms.require_guild_owner(interaction)
            await interaction.response.defer(ephemeral=True, thinking=True)
            result = await retention.cleanup_as_owner(interaction, limit=50, dry_run=False)
            await _send(interaction, content=f'清理完成：刪除 {result.deleted} 筆符合條件的案件。')
            for c in self.children:
                c.disabled = True
        except Exception as exc:
            await _fail(interaction, exc)


class RetentionHomeView(_BoundView):
    @discord.ui.button(label='查看目前政策', style=discord.ButtonStyle.secondary)
    async def policy(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            perms.require_guild_owner(interaction)
            p = retention.get_policy(self.guild_id)
            await _send(interaction, content=f'一般案件 {p.normal_days} 天；重大案件 {p.major_days} 天。')
        except Exception as exc:
            await _fail(interaction, exc)

    @discord.ui.button(label='修改期限', style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RetentionPolicyModal(self.guild_id, self.user_id))

    @discord.ui.button(label='延長單一案件', style=discord.ButtonStyle.secondary)
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RetentionExtendModal(self.guild_id, self.user_id))

    @discord.ui.button(label='預覽到期清理', style=discord.ButtonStyle.secondary)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            perms.require_guild_owner(interaction)
            result = await retention.cleanup_as_owner(interaction, limit=50, dry_run=True)
            await _send(interaction, content=(f'檢查 {result.examined} 筆；可清理 {result.deleted} 筆。'
                                             '預覽不會刪除資料。'), view=RetentionConfirmView(interaction))
        except Exception as exc:
            await _fail(interaction, exc)


async def open_report_retention_settings(interaction: discord.Interaction) -> None:
    try:
        perms.require_guild_owner(interaction)
        await _send(interaction, content='**群主專屬｜舉報保存設定**',
                    view=RetentionHomeView(interaction))
    except Exception as exc:
        await _fail(interaction, exc)


@dataclass
class MaintenanceOptions:
    interval_hours: float = 24
    cleanup_enabled: bool = False  # 預設只預覽；部署者明確開啟才會刪除
    unban_enabled: bool = True


class ReportMaintenance:
    """可信任的內部背景工作：不註冊指令、不直接發送敏感通知。"""
    def __init__(self, bot: commands.Bot, options: Optional[MaintenanceOptions] = None):
        self.bot = bot
        self.options = options or MaintenanceOptions()
        if self.options.interval_hours < 1:
            raise ValueError('維護間隔至少一小時。')
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name='rp_report_maintenance')

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for guild in list(self.bot.guilds):
                try:
                    if self.options.unban_enabled:
                        await sanctions.process_expired_temporary_bans(guild, limit=25)
                    result = await asyncio.to_thread(
                        retention.run_retention_cleanup, guild_id=guild.id,
                        limit=50, dry_run=not self.options.cleanup_enabled)
                    LOG.info('舉報保存檢查 guild=%s examined=%s due=%s removed=%s',
                             guild.id, result.examined, len(result.report_ids),
                             result.deleted if self.options.cleanup_enabled else 0)
                except Exception:
                    LOG.exception('舉報背景維護失敗 guild=%s', guild.id)
            await asyncio.sleep(self.options.interval_hours * 3600)


# 手動由 setup_hook / 可信任的整合 Cog 啟動，禁止隱式自動刪除。
_maintenance: Optional[ReportMaintenance] = None


def start_report_maintenance(bot: commands.Bot, *,
                             options: Optional[MaintenanceOptions] = None) -> ReportMaintenance:
    global _maintenance
    if _maintenance is not None and _maintenance.bot is bot:
        return _maintenance
    if _maintenance is not None:
        raise RuntimeError('另一個 bot 已啟用舉報維護排程。')
    _maintenance = ReportMaintenance(bot, options)
    _maintenance.start()
    return _maintenance


async def stop_report_maintenance():
    global _maintenance
    if _maintenance is not None:
        await _maintenance.stop()
        _maintenance = None


# 注意：main.py 的 discover_extensions() 會掃描每個 modules/*.py。
# 提供空 setup 讓此模組可被自動載入，但不額外註冊任何 Slash 指令。
async def setup(bot: commands.Bot):
    repository.install_schema()
    appeals.install_appeal_schema()
    retention.install_retention_schema()
    LOG.info('舉報整合入口已就緒：使用既有 /申請、/審核 的選單接線。')


__all__ = [
    'open_report_application', 'open_report_review', 'open_report_appeals',
    'open_report_appeal_review', 'open_report_retention_settings',
    'MaintenanceOptions', 'ReportMaintenance', 'start_report_maintenance',
    'stop_report_maintenance', 'setup',
]
