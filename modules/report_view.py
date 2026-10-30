"""Discord RP 舉報玩家操作面板（第 5 檔）。

安裝：modules/report_view.py
依賴：discord.py 2.x、modules.report_modals、modules.report_service、
      modules.report_permission_service。

在既有 /申請 選單中呼叫：
    from modules.report_view import start_report_home
    await start_report_home(interaction)

不註冊斜線指令、不建立頻道、不公開任何舉報內容。
正式案件與身分查詢全部通過第 3 檔 service；UI 不讀取 reporter secrets。
"""
from __future__ import annotations

import logging
from datetime import datetime

import discord

from modules import report_modals
from modules import report_permission_service as permissions
from modules import report_service as service

LOG = logging.getLogger(__name__)
PAGE_SIZE = 5
VIEW_TIMEOUT = 600
STATUS_NAMES = {
    "pending": "待受理", "investigating": "調查中", "needs_info": "待補件",
    "pending_owner": "待群主審核", "substantiated": "舉報成立",
    "unsubstantiated": "舉報不成立", "withdrawn": "已撤回", "closed": "已結案",
}
EDITABLE = service.REPORTER_EDITABLE_STATES


def _status(status: str) -> str:
    return STATUS_NAMES.get(status, "處理中")


def _date(value: object) -> str:
    if not value:
        return "未記錄"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return "未記錄"


async def _private(interaction: discord.Interaction, *, content: str | None = None,
                   embed: discord.Embed | None = None,
                   view: discord.ui.View | None = None) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content=content, embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)


async def _error(interaction: discord.Interaction, exc: Exception) -> None:
    if isinstance(exc, (ValueError, LookupError, permissions.ReportPermissionError)):
        await _private(interaction, content=f"操作未完成：{str(exc)[:600]}")
    else:
        LOG.exception("Report player view operation failed")
        await _private(interaction, content="操作暫時失敗，請稍後透過「我的舉報」確認狀態，避免重複送出。")


class _PrivateView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int, *, timeout: float = VIEW_TIMEOUT):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id or interaction.user.id != self.user_id:
            await _private(interaction, content="這不是你的舉報操作面板。")
            return False
        return True


class ReportHomeView(_PrivateView):
    """私密首頁；支援開始新案件及查看本人案件。"""

    @discord.ui.button(label="建立舉報", style=discord.ButtonStyle.primary, emoji="📝")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await report_modals.start_report_form(interaction)

    @discord.ui.button(label="我的舉報", style=discord.ButtonStyle.secondary, emoji="📋")
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_my_reports(interaction)


class EvidenceAdditionModal(discord.ui.Modal, title="追加舉報證據"):
    evidence = discord.ui.TextInput(
        label="HTTPS 證據連結（每行一筆，最多 10 筆）",
        placeholder="https://discord.com/channels/...",
        style=discord.TextStyle.paragraph,
        min_length=12,
        max_length=4000,
        required=True,
    )

    def __init__(self, *, guild_id: int, user_id: int, report_id: int):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.guild_id = guild_id
        self.user_id = user_id
        self.report_id = report_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.guild_id or interaction.user.id != self.user_id:
            await _private(interaction, content="你不能修改其他人的舉報案件。")
            return
        try:
            urls = report_modals.validate_evidence(str(self.evidence.value))
        except (ValueError, TypeError) as exc:
            await _private(interaction, content=f"證據格式錯誤：{exc}")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            inserted = await service.add_reporter_evidence(
                interaction, report_id=self.report_id, evidence_urls=urls,
            )
            detail = await service.get_my_report(interaction, report_id=self.report_id)
        except Exception as exc:
            await _error(interaction, exc)
            return
        await interaction.followup.send(
            f"案件 REP-{self.report_id:06d} 已新增 {inserted} 筆證據。"
            + ("（重複網址已略過）" if inserted < len(urls) else "")
            + f"\n目前證據總數：{detail['evidence_count']} 筆。",
            ephemeral=True,
        )


class WithdrawalConfirmView(_PrivateView):
    """撤回需要第二次明確確認；調查中的案件不一定停止調查。"""
    def __init__(self, guild_id: int, user_id: int, report_id: int):
        super().__init__(guild_id, user_id, timeout=180)
        self.report_id = report_id
        self.busy = False

    @discord.ui.button(label="確認提出撤回", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.busy:
            await _private(interaction, content="此操作正在處理，請勿重複提交。")
            return
        self.busy = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            recorded = await service.withdraw_report(interaction, report_id=self.report_id)
        except Exception as exc:
            self.busy = False
            await _error(interaction, exc)
            return
        self.stop()
        message = (
            "撤回意願已記錄。一般待受理案件可能直接撤回；重大案件或調查中的案件可繼續調查。"
            if recorded else "這個案件已撤回、結案，或先前已提出撤回，未再次處理。"
        )
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="保留舉報", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="已取消撤回操作，案件維持原狀。", view=None)


class ReportDetailView(_PrivateView):
    def __init__(self, guild_id: int, user_id: int, report_id: int, *, can_edit: bool):
        super().__init__(guild_id, user_id)
        self.report_id = report_id
        self.add_evidence.disabled = not can_edit
        self.withdraw.disabled = not can_edit

    @discord.ui.button(label="重新整理", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            detail = await service.get_my_report(interaction, report_id=self.report_id)
            embed = _detail_embed(detail)
            updated = ReportDetailView(self.guild_id, self.user_id, self.report_id,
                                       can_edit=detail["status"] in EDITABLE)
        except Exception as exc:
            await _error(interaction, exc)
            return
        await interaction.response.edit_message(embed=embed, view=updated)

    @discord.ui.button(label="追加證據", style=discord.ButtonStyle.primary, emoji="📎")
    async def add_evidence(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            detail = await service.get_my_report(interaction, report_id=self.report_id)
            if detail["status"] not in EDITABLE:
                await _private(interaction, content="目前案件狀態不允許補充證據。")
                return
        except Exception as exc:
            await _error(interaction, exc)
            return
        await interaction.response.send_modal(EvidenceAdditionModal(
            guild_id=self.guild_id, user_id=self.user_id, report_id=self.report_id,
        ))

    @discord.ui.button(label="申請撤回", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            detail = await service.get_my_report(interaction, report_id=self.report_id)
            if detail["status"] not in EDITABLE:
                await _private(interaction, content="案件目前不可撤回。")
                return
        except Exception as exc:
            await _error(interaction, exc)
            return
        await _private(
            interaction,
            content=(f"確定要對 REP-{self.report_id:06d} 提出撤回嗎？"
                     "\n撤回意願會留存，調查中的嚴重案件不保證停止調查。"),
            view=WithdrawalConfirmView(self.guild_id, self.user_id, self.report_id),
        )

    @discord.ui.button(label="返回列表", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_my_reports(interaction)


def _detail_embed(detail: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"舉報案件 REP-{int(detail['id']):06d}",
        description="僅顯示案件必要進度；調查人員內部意見及舉報人身分不會出現在此面板。",
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name="案件狀態", value=_status(str(detail["status"])), inline=True)
    embed.add_field(name="證據總數", value=str(detail["evidence_count"]), inline=True)
    embed.add_field(name="建立時間", value=_date(detail["created_at"]), inline=False)
    embed.set_footer(text="需要補件時請選擇「追加證據」，每次最多 10 筆 HTTPS 網址。")
    return embed


class ReportListSelect(discord.ui.Select):
    def __init__(self, records: list[dict]):
        options = [
            discord.SelectOption(
                label=f"REP-{int(r['id']):06d}",
                description=f"{_status(str(r['status']))}｜{_date(r.get('created_at'))}"[:100],
                value=str(r["id"]),
            ) for r in records
        ]
        super().__init__(placeholder="選擇要查看的舉報案件", min_values=1, max_values=1,
                         options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ReportListView = self.view  # type: ignore[assignment]
        try:
            report_id = int(self.values[0])
            detail = await service.get_my_report(interaction, report_id=report_id)
            detail_view = ReportDetailView(view.guild_id, view.user_id, report_id,
                                           can_edit=detail["status"] in EDITABLE)
        except Exception as exc:
            await _error(interaction, exc)
            return
        await interaction.response.edit_message(embed=_detail_embed(detail), view=detail_view)


class ReportListView(_PrivateView):
    def __init__(self, guild_id: int, user_id: int, reports: list[dict], page: int = 0):
        super().__init__(guild_id, user_id)
        self.reports = reports
        self.page = max(0, min(page, (len(reports) - 1) // PAGE_SIZE if reports else 0))
        self._build()

    def _build(self) -> None:
        self.clear_items()
        first = self.page * PAGE_SIZE
        segment = self.reports[first:first + PAGE_SIZE]
        if segment:
            self.add_item(ReportListSelect(segment))
        controls = [
            ("上一頁", discord.ButtonStyle.secondary, self.page > 0, self._previous),
            ("下一頁", discord.ButtonStyle.secondary, (self.page+1)*PAGE_SIZE < len(self.reports), self._next),
            ("重新整理", discord.ButtonStyle.primary, True, self._reload),
            ("建立舉報", discord.ButtonStyle.success, True, self._create),
        ]
        for label, style, enabled, callback in controls:
            item = discord.ui.Button(label=label, style=style, disabled=not enabled, row=1)
            item.callback = callback
            self.add_item(item)

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="我的舉報紀錄",
            description="以下僅列出你本人提交的案件；使用選單查看進度與操作。",
            colour=discord.Colour.blurple(),
        )
        if not self.reports:
            embed.add_field(name="目前沒有案件", value="你尚未提交過舉報。", inline=False)
        else:
            for r in self.reports[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]:
                embed.add_field(name=f"REP-{int(r['id']):06d}｜{_status(str(r['status']))}",
                                value=f"建立：{_date(r.get('created_at'))}", inline=False)
        total_pages = max(1, (len(self.reports)+PAGE_SIZE-1)//PAGE_SIZE)
        embed.set_footer(text=f"第 {self.page+1}/{total_pages} 頁｜目前最多讀取 100 件近期案件")
        return embed

    async def _change_page(self, interaction: discord.Interaction, delta: int) -> None:
        self.page = max(0, min(self.page+delta, max(0, (len(self.reports)-1)//PAGE_SIZE)))
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _previous(self, interaction: discord.Interaction) -> None:
        await self._change_page(interaction, -1)

    async def _next(self, interaction: discord.Interaction) -> None:
        await self._change_page(interaction, 1)

    async def _reload(self, interaction: discord.Interaction) -> None:
        try:
            self.reports = await service.list_my_reports(interaction, limit=100)
        except Exception as exc:
            await _error(interaction, exc)
            return
        self.page = min(self.page, max(0, (len(self.reports)-1)//PAGE_SIZE))
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _create(self, interaction: discord.Interaction) -> None:
        await report_modals.start_report_form(interaction)


async def show_my_reports(interaction: discord.Interaction) -> None:
    """既有 /申請 或 ReportHomeView 呼叫；僅查詢互動者自身案件。"""
    if interaction.guild is None or interaction.guild_id is None:
        await _private(interaction, content="請在伺服器內查看舉報紀錄。")
        return
    try:
        records = await service.list_my_reports(interaction, limit=100)
    except Exception as exc:
        await _error(interaction, exc)
        return
    view = ReportListView(interaction.guild_id, interaction.user.id, records)
    await _private(interaction, embed=view.embed(), view=view)


async def start_report_home(interaction: discord.Interaction) -> ReportHomeView | None:
    """從既有 /申請 選擇舉報時呼叫，不新增 slash command。"""
    if interaction.guild is None or interaction.guild_id is None:
        await _private(interaction, content="舉報功能只能在伺服器內使用。")
        return None
    view = ReportHomeView(interaction.guild_id, interaction.user.id)
    embed = discord.Embed(
        title="舉報中心",
        description=("請選擇「建立舉報」或「我的舉報」。\n"
                     "舉報人身分只有群主可以查詢；但證據內容可能間接暴露身分。\n"
                     "被舉報對象是審核員、紀律人員或其他官方人員時，案件由群主專屬處理。"),
        colour=discord.Colour.blurple(),
    )
    await _private(interaction, embed=embed, view=view)
    return view


__all__ = [
    "ReportHomeView", "ReportListView", "ReportDetailView", "EvidenceAdditionModal",
    "WithdrawalConfirmView", "show_my_reports", "start_report_home",
]
