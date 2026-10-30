"""Discord RP 舉報審核互動面板（第 7 檔）。

放置：modules/report_review_view.py
既有 /審核 選單呼叫：
    from modules.report_review_view import start_report_review_home
    await start_report_review_home(interaction)

依賴：discord.py 2.x、report_review_service、report_permission_service。
不註冊指令、不處分使用者、不公開發送案件內容。
所有操作由服務層再次檢查權限，View 僅作使用體驗防護。
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

from modules import report_permission_service as perms
from modules import report_review_service as reviews

LOG = logging.getLogger(__name__)
PAGE_SIZE = 5
VIEW_TIMEOUT = 600
STATUS_LABELS = {
    "pending": "待受理", "investigating": "調查中", "needs_info": "待補件",
    "pending_owner": "待群主處理", "substantiated": "舉報成立",
    "unsubstantiated": "舉報不成立", "withdrawn": "已撤回", "closed": "已結案",
}
SANCTION_LABELS = {
    "warning": "警告", "short_timeout": "短期禁言",
    "long_timeout": "長期禁言（群主批准）", "kick": "踢出（群主批准）",
    "ban": "封禁（群主批准）", "temporary_ban": "暫時封禁（群主批准）",
    "suspend": "停權（群主批准）",
}


def _clean(value: object, limit: int = 700) -> str:
    """去除會觸發 @everyone 或 Markdown 誤導的展示字元。"""
    s = str(value or "").replace("@", "＠").replace("`", "ˋ")
    return (s[:limit - 1] + "…") if len(s) > limit else s


def _case_embed(case: dict, *, full: bool = False) -> discord.Embed:
    case_id = int(case["id"])
    e = discord.Embed(
        title=f"舉報案件 REP-{case_id:06d}",
        description=f"狀態：**{STATUS_LABELS.get(str(case.get('status')), '處理中')}**",
        color=discord.Color.orange(),
    )
    e.add_field(name="被舉報玩家", value=_clean(case.get("target_user_id"), 50), inline=True)
    e.add_field(name="舉報種類", value=_clean(case.get("report_type"), 80), inline=True)
    e.add_field(name="違規分類", value=_clean(case.get("reason_code"), 100), inline=True)
    if case.get("target_character_id"):
        e.add_field(name="涉及角色 ID", value=_clean(case["target_character_id"], 80), inline=True)
    if case.get("owner_only"):
        e.add_field(name="權限", value="僅群主", inline=True)
    if full:
        e.add_field(name="事件描述", value=_clean(case.get("description"), 1000), inline=False)
        if case.get("assigned_reviewer_id"):
            e.add_field(name="承辦人 ID", value=_clean(case["assigned_reviewer_id"], 50), inline=False)
    e.set_footer(text="私密案件・不得轉發・舉報人身分僅群主可查")
    return e


async def _private(interaction: discord.Interaction, *, content: str | None = None,
                   embed: discord.Embed | None = None,
                   view: discord.ui.View | None = None) -> None:
    kwargs = {"content": content, "embed": embed, "view": view,
              "ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def _show_error(interaction: discord.Interaction, error: Exception) -> None:
    if isinstance(error, (ValueError, LookupError, perms.ReportPermissionError)):
        await _private(interaction, content="無法完成操作：" + _clean(error, 500))
    else:
        LOG.exception("Report review view failed")
        await _private(interaction, content="操作暫時失敗。請重新整理案件，確認是否已成功。")


class GuardedView(discord.ui.View):
    """互動訊息只接受原開啟者，且每次重新檢查審核資格。"""
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.guild_id = interaction.guild_id
        self.actor_id = interaction.user.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild_id != self.guild_id or
                interaction.user.id != self.actor_id):
            await _private(interaction, content="這不是你的審核面板。請使用自己的 /審核。")
            return False
        try:
            perms.require_discipline_or_owner(interaction)
        except Exception as exc:
            await _show_error(interaction, exc)
            return False
        return True


class NoteModal(discord.ui.Modal):
    """受理備註、補件問題、調查結論、移交原因共用 Modal。"""
    def __init__(self, *, report_id: int, action: str):
        labels = {
            "accept": "受理案件", "request": "要求補充資料",
            "resume": "恢復調查", "refer": "移交群主",
            "yes": "判定舉報成立", "no": "判定舉報不成立",
            "close": "結束未成立案件",
        }
        super().__init__(title=labels[action], timeout=300)
        self.report_id, self.action = report_id, action
        self.note = discord.ui.TextInput(
            label="原因／說明" if action != "request" else "希望舉報人補充的內容",
            style=discord.TextStyle.paragraph,
            placeholder="請輸入具體內容（不包含舉報人的身分）",
            required=action in {"request", "refer", "yes", "no"},
            max_length=1500,
        )
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            note = str(self.note.value)
            # 所有操作都會於服務層查詢真實權限與案件最新狀態。
            if self.action == "accept":
                result = await reviews.accept_report(interaction, self.report_id, note=note)
            elif self.action == "request":
                result = await reviews.request_more_information(interaction, self.report_id, question=note)
            elif self.action == "resume":
                result = await reviews.resume_investigation(interaction, self.report_id, note=note)
            elif self.action == "refer":
                result = await reviews.refer_to_owner(interaction, self.report_id, reason=note)
            elif self.action in {"yes", "no"}:
                result = await reviews.decide_report(
                    interaction, self.report_id, substantiated=(self.action == "yes"), reason=note,
                )
            else:
                result = await reviews.close_unsubstantiated(interaction, self.report_id, note=note)
            await _private(interaction,
                           content=f"案件 REP-{self.report_id:06d} 已更新：{STATUS_LABELS.get(result.status, result.status)}。",
                           view=CaseView(interaction, self.report_id))
        except Exception as exc:
            await _show_error(interaction, exc)


class SanctionModal(discord.ui.Modal, title="建立處分提案"):
    def __init__(self, report_id: int, sanction_type: str):
        super().__init__(timeout=300)
        self.report_id, self.sanction_type = report_id, sanction_type
        self.reason = discord.ui.TextInput(label="處分理由", style=discord.TextStyle.paragraph,
                                          max_length=1500, required=True)
        self.duration = discord.ui.TextInput(
            label="持續秒數（非限時處分留空）", required=False, max_length=12,
            placeholder="例如 3600；禁言最長 2419200 秒",
        )
        self.add_item(self.reason)
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            raw = str(self.duration.value).strip()
            if raw and not raw.isdecimal():
                raise ValueError("持續秒數必須是正整數。")
            duration = int(raw) if raw else None
            sanction_id = await reviews.propose_sanction(
                interaction, self.report_id, sanction_type=self.sanction_type,
                reason=str(self.reason.value), duration_seconds=duration,
            )
            await _private(
                interaction,
                content=(f"已建立處分提案 #{sanction_id}（{SANCTION_LABELS[self.sanction_type]}）。"
                         "此操作**不會立即執行 Discord 處分**；後續交由處分模組依權限核准及執行。"),
                view=CaseView(interaction, self.report_id),
            )
        except Exception as exc:
            await _show_error(interaction, exc)


class SanctionSelect(discord.ui.Select):
    def __init__(self, report_id: int):
        self.report_id = report_id
        super().__init__(placeholder="選擇要提出的處分", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=name, value=key)
                                  for key, name in SANCTION_LABELS.items()])

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            details = await reviews.get_review_details(interaction, self.report_id)
            if details["report"]["status"] != "substantiated":
                raise ValueError("僅成立案件可提出處分。")
            await interaction.response.send_modal(SanctionModal(self.report_id, self.values[0]))
        except Exception as exc:
            await _show_error(interaction, exc)


class SanctionView(GuardedView):
    def __init__(self, interaction: discord.Interaction, report_id: int):
        super().__init__(interaction)
        self.add_item(SanctionSelect(report_id))


class EvidenceView(GuardedView):
    def __init__(self, interaction: discord.Interaction, report_id: int, evidence: list[dict], page: int = 0):
        super().__init__(interaction)
        self.report_id, self.evidence, self.page = report_id, evidence, page
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * PAGE_SIZE >= len(evidence)

    async def display(self, interaction: discord.Interaction):
        # 重新取得證據，確保權限在工作人員身分異動後立即失效。
        details = await reviews.get_review_details(interaction, self.report_id)
        evidence = details["evidence"]
        self.evidence = evidence
        self.page = min(self.page, max(0, (len(evidence) - 1) // PAGE_SIZE))
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * PAGE_SIZE >= len(evidence)
        emb = discord.Embed(title=f"REP-{self.report_id:06d}・證據列表",
                            description=f"共 {len(evidence)} 筆；第 {self.page + 1} 頁",
                            color=discord.Color.blurple())
        for i, item in enumerate(evidence[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE],
                                 start=self.page * PAGE_SIZE + 1):
            # 禁止任意 URL 使用 Discord Markdown 自動解析造成提及或惡意排版。
            url = _clean(item.get("evidence_url"), 350)
            note = _clean(item.get("note"), 200)
            emb.add_field(name=f"證據 {i}", value=f"網址：{url}\n備註：{note or '無'}", inline=False)
        await _private(interaction, embed=emb, view=self)

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        try:
            await self.display(interaction)
        except Exception as exc:
            await _show_error(interaction, exc)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        try:
            await self.display(interaction)
        except Exception as exc:
            await _show_error(interaction, exc)


class CaseView(GuardedView):
    def __init__(self, interaction: discord.Interaction, report_id: int):
        super().__init__(interaction)
        self.report_id = report_id
        if not perms.is_guild_owner(interaction):
            self.remove_item(self.owner_identity)

    async def _modal(self, interaction: discord.Interaction, action: str) -> None:
        try:
            await reviews.get_review_details(interaction, self.report_id)
            await interaction.response.send_modal(NoteModal(report_id=self.report_id, action=action))
        except Exception as exc:
            await _show_error(interaction, exc)

    @discord.ui.button(label="查看詳情", style=discord.ButtonStyle.primary, row=0)
    async def detail(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            details = await reviews.get_review_details(interaction, self.report_id)
            await _private(interaction, embed=_case_embed(details["report"], full=True), view=CaseView(interaction, self.report_id))
        except Exception as exc:
            await _show_error(interaction, exc)

    @discord.ui.button(label="查看證據", style=discord.ButtonStyle.secondary, row=0)
    async def evidence(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            details = await reviews.get_review_details(interaction, self.report_id)
            await EvidenceView(interaction, self.report_id, details["evidence"]).display(interaction)
        except Exception as exc:
            await _show_error(interaction, exc)

    @discord.ui.button(label="受理", style=discord.ButtonStyle.success, row=0)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._modal(interaction, "accept")

    @discord.ui.button(label="要求補件", style=discord.ButtonStyle.secondary, row=0)
    async def request(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._modal(interaction, "request")

    @discord.ui.button(label="恢復調查", style=discord.ButtonStyle.secondary, row=0)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._modal(interaction, "resume")

    @discord.ui.button(label="判定成立", style=discord.ButtonStyle.success, row=1)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._modal(interaction, "yes")

    @discord.ui.button(label="判定不成立", style=discord.ButtonStyle.danger, row=1)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._modal(interaction, "no")

    @discord.ui.button(label="移交群主", style=discord.ButtonStyle.secondary, row=1)
    async def refer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._modal(interaction, "refer")

    @discord.ui.button(label="提出處分", style=discord.ButtonStyle.primary, row=1)
    async def sanction(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            details = await reviews.get_review_details(interaction, self.report_id)
            if details["report"]["status"] != "substantiated":
                raise ValueError("只有已確認成立的案件能提出處分。")
            await _private(interaction, content="請選擇處分類型；本階段只會建立提案。",
                           view=SanctionView(interaction, self.report_id))
        except Exception as exc:
            await _show_error(interaction, exc)

    @discord.ui.button(label="結束未成立案件", style=discord.ButtonStyle.secondary, row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._modal(interaction, "close")

    @discord.ui.button(label="查看舉報人（群主）", style=discord.ButtonStyle.secondary, row=2)
    async def owner_identity(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # 唯一可取得秘密 ID 的介面；存取控制由服務層再次驗證。
            reporter_id = perms.get_reporter_identity_for_owner(interaction, self.report_id)
            await _private(interaction, content=f"此案件舉報人 Discord ID：{_clean(reporter_id, 50)}。請保密。")
        except Exception as exc:
            await _show_error(interaction, exc)


class CaseSelect(discord.ui.Select):
    def __init__(self, cases: list[dict]):
        options = [discord.SelectOption(
            label=f"REP-{int(r['id']):06d}・{STATUS_LABELS.get(str(r.get('status')), '處理中')}",
            value=str(r["id"]), description=f"被舉報玩家：{_clean(r.get('target_user_id'), 55)}",
        ) for r in cases]
        super().__init__(placeholder="選擇案件查看操作", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        rid = int(self.values[0])
        try:
            detail = await reviews.get_review_details(interaction, rid)
            await _private(interaction, embed=_case_embed(detail["report"], full=True),
                           view=CaseView(interaction, rid))
        except Exception as exc:
            await _show_error(interaction, exc)


class QueueView(GuardedView):
    def __init__(self, interaction: discord.Interaction, cases: list[dict], page: int = 0):
        super().__init__(interaction)
        self.cases = cases
        self.page = page
        self._build()

    def _build(self) -> None:
        # 先移除動態 Select，避免刷新後堆疊重複元件。
        for item in list(self.children):
            if isinstance(item, CaseSelect):
                self.remove_item(item)
        subset = self.cases[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        if subset:
            self.add_item(CaseSelect(subset))
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * PAGE_SIZE >= len(self.cases)

    async def display(self, interaction: discord.Interaction) -> None:
        self.cases = await reviews.list_review_queue(interaction, limit=100)
        self.page = min(self.page, max(0, (len(self.cases) - 1) // PAGE_SIZE))
        self._build()
        e = discord.Embed(title="舉報審核中心", color=discord.Color.blurple())
        if not self.cases:
            e.description = "目前沒有你有權處理的案件。"
        else:
            e.description = f"共 {len(self.cases)} 筆案件，第 {self.page + 1} 頁。"
            for row in self.cases[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]:
                e.add_field(name=f"REP-{int(row['id']):06d}",
                            value=f"{STATUS_LABELS.get(str(row.get('status')), '處理中')}｜被舉報玩家 {_clean(row.get('target_user_id'), 40)}",
                            inline=False)
        await _private(interaction, embed=e, view=self)

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        try:
            await self.display(interaction)
        except Exception as exc:
            await _show_error(interaction, exc)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        try:
            await self.display(interaction)
        except Exception as exc:
            await _show_error(interaction, exc)

    @discord.ui.button(label="重新整理", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.display(interaction)
        except Exception as exc:
            await _show_error(interaction, exc)


async def start_report_review_home(interaction: discord.Interaction) -> None:
    """入口：由既有 /審核 的舉報分類呼叫。"""
    try:
        perms.require_discipline_or_owner(interaction)
        queue = await reviews.list_review_queue(interaction, limit=100)
        await QueueView(interaction, queue).display(interaction)
    except Exception as exc:
        await _show_error(interaction, exc)


__all__ = ["start_report_review_home", "QueueView", "CaseView", "EvidenceView"]
