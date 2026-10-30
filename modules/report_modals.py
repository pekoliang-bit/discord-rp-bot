"""Discord RP 舉報表單 UI（第 4 檔）。

安裝位置：modules/report_modals.py
相依：discord.py 2.x、modules.report_service、modules.report_permission_service。

使用方式（在既有 /申請 的互動回呼裡）：
    from modules.report_modals import start_report_form
    await start_report_form(interaction)

此模組只提供 View / Modal，不新增斜線指令，也不自行建立頻道。
所有表單回覆使用 ephemeral；資料僅在最後確認時經 report_service 提交。
表單是暫存 UI，重啟後未送出的草稿會消失；正式案件不受影響。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import discord

from modules import report_repository as repo
from modules import report_permission_service as permissions
from modules import report_service as service

FORM_TIMEOUT = 15 * 60
MAX_DESCRIPTION = 1800
MAX_NOTE = 450
MAX_EVIDENCE = 10
# 文字輸入最多 4000 字；每一筆網址需符合既有 repo.URL_PATTERN。
ALLOWED_TYPES = {"player", "character", "staff", "other"}
REASONS = {
    "harassment": "騷擾／辱罵／霸凌",
    "cheating": "作弊或濫用漏洞",
    "fraud": "惡意欺騙／交易爭議",
    "privacy": "洩漏個資／隱私",
    "abuse": "濫用職權",
    "rules": "其他伺服器規則違反",
}


@dataclass
class ReportDraft:
    guild_id: int
    reporter_id: int
    report_type: str = "player"
    target_user_id: int | None = None
    target_character_id: int | None = None
    reason_code: str = "rules"
    description: str = ""
    occurrence_note: str = ""
    evidence_urls: list[str] = field(default_factory=list)
    submitted: bool = False
    busy: bool = False


def validate_evidence(raw: str) -> list[str]:
    """逐行 HTTPS URL；拒絕憑證、混淆網址、重複與無效數量。"""
    urls = list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip()))
    if not 1 <= len(urls) <= MAX_EVIDENCE:
        raise ValueError("首次必須提供 1～10 筆證據網址，每行一筆。")
    for url in urls:
        if not repo.URL_PATTERN.fullmatch(url):
            raise ValueError("證據必須是有效的 HTTPS 網址，且不可包含空白。")
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("證據網址必須使用 HTTPS，且不可包含帳號或密碼。")
        if parts.port is not None and parts.port not in (443,):
            raise ValueError("證據網址不可指定非標準埠號。")
    return urls


def _is_draft_owner(interaction: discord.Interaction, draft: ReportDraft) -> bool:
    return bool(interaction.guild_id == draft.guild_id and
                interaction.user and interaction.user.id == draft.reporter_id)


async def _private_message(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def _summary_embed(d: ReportDraft, *, final: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title="舉報資料確認" if final else "舉報申請｜分步表單",
        description=("請確認內容無誤後按下「正式送出」。" if final else
                     "內容不會發布到一般頻道；只有群主可查詢你的身分。"),
        colour=discord.Colour.orange(),
    )
    embed.add_field(name="類型", value={"player":"舉報玩家", "character":"指定角色",
                                     "staff":"官方／工作人員", "other":"其他"}[d.report_type], inline=True)
    embed.add_field(name="對象", value=f"<@{d.target_user_id}>" if d.target_user_id else "尚未選擇", inline=True)
    if d.report_type == "character":
        embed.add_field(name="角色編號", value=str(d.target_character_id or "尚未輸入"), inline=True)
    embed.add_field(name="違規分類", value=REASONS[d.reason_code], inline=True)
    embed.add_field(name="事件內容", value=(discord.utils.escape_mentions(d.description[:700]) or "尚未填寫"), inline=False)
    embed.add_field(name="證據連結", value=f"{len(d.evidence_urls)} 筆（連結不在預覽中展開）", inline=True)
    embed.add_field(name="時間／頻道", value=discord.utils.escape_mentions(d.occurrence_note[:150] or "未提供"), inline=True)
    embed.set_footer(text="匿名保護無法排除證據內容本身透露舉報人的可能。")
    return embed


class _DraftModal(discord.ui.Modal):
    def __init__(self, view: "ReportFormView", *, title: str):
        super().__init__(title=title, timeout=FORM_TIMEOUT)
        self.form_view = view

    async def _save(self, interaction: discord.Interaction) -> bool:
        if not _is_draft_owner(interaction, self.form_view.draft):
            await _private_message(interaction, "你不能編輯其他人的舉報草稿。")
            return False
        if self.form_view.draft.submitted:
            await _private_message(interaction, "這份舉報已提交，不能再次送出。")
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction, text: str) -> None:
        self.form_view._refresh_controls()
        # Modal 不保證能直接更新原始 ephemeral 訊息，回覆新的私密面板。
        await interaction.response.send_message(text, embed=_summary_embed(self.form_view.draft),
                                                view=self.form_view, ephemeral=True)


class CharacterModal(_DraftModal):
    character_id = discord.ui.TextInput(label="被舉報角色編號（數字 ID）", min_length=1,
                                        max_length=20, placeholder="由角色資料頁取得角色編號")

    def __init__(self, view: "ReportFormView"):
        super().__init__(view, title="指定被舉報角色")
        if view.draft.target_character_id:
            self.character_id.default = str(view.draft.target_character_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self._save(interaction):
            return
        try:
            value = int(str(self.character_id.value).strip())
            if value <= 0:
                raise ValueError
        except ValueError:
            await _private_message(interaction, "角色編號必須是正整數。")
            return
        self.form_view.draft.target_character_id = value
        await self._refresh(interaction, "角色編號已保存。正式送出時會驗證是否屬於被舉報玩家。")


class DescriptionModal(_DraftModal):
    details = discord.ui.TextInput(label="事件經過", style=discord.TextStyle.paragraph,
                                   min_length=10, max_length=MAX_DESCRIPTION,
                                   placeholder="請說明具體行為、事件背景及相關事實")
    occurrence = discord.ui.TextInput(label="發生時間／頻道（選填）", style=discord.TextStyle.paragraph,
                                      required=False, max_length=MAX_NOTE)

    def __init__(self, view: "ReportFormView"):
        super().__init__(view, title="填寫舉報事實")
        self.details.default = view.draft.description or None
        self.occurrence.default = view.draft.occurrence_note or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self._save(interaction):
            return
        value = str(self.details.value).strip()
        if len(value) < 10:
            await _private_message(interaction, "請至少提供 10 個字的事件描述。")
            return
        self.form_view.draft.description = value
        self.form_view.draft.occurrence_note = str(self.occurrence.value or "").strip()
        await self._refresh(interaction, "事件描述已保存。")


class EvidenceModal(_DraftModal):
    urls = discord.ui.TextInput(label="證據 HTTPS 網址（每行一筆，1～10 筆）",
                                style=discord.TextStyle.paragraph, min_length=8,
                                max_length=4000, placeholder="https://discord.com/channels/...\nhttps://example.com/evidence")

    def __init__(self, view: "ReportFormView"):
        super().__init__(view, title="提交舉報證據")
        self.urls.default = "\n".join(view.draft.evidence_urls) or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self._save(interaction):
            return
        try:
            parsed = validate_evidence(str(self.urls.value))
        except (ValueError, UnicodeError) as exc:
            await _private_message(interaction, str(exc))
            return
        self.form_view.draft.evidence_urls = parsed
        await self._refresh(interaction, f"已保存 {len(parsed)} 筆證據連結。")


class TargetSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="第一步：選擇被舉報的 Discord 使用者", min_values=1,
                         max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ReportFormView = self.view  # type: ignore[assignment]
        if not view or not await view._check(interaction):
            return
        selected = self.values[0]
        if selected.id == interaction.client.user.id:
            await _private_message(interaction, "如需反映機器人問題，請使用官方問題回報管道。")
            return
        if view.draft.target_user_id != selected.id:
            view.draft.target_character_id = None
        view.draft.target_user_id = selected.id
        await view._redraw(interaction)


class ReportTypeSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="選擇舉報對象類型", row=1, options=[
            discord.SelectOption(label="玩家", value="player", default=current == "player"),
            discord.SelectOption(label="指定角色", value="character", default=current == "character"),
            discord.SelectOption(label="審核員／紀律人員／官方", value="staff", default=current == "staff"),
            discord.SelectOption(label="其他違規事項", value="other", default=current == "other"),
        ])

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ReportFormView = self.view  # type: ignore[assignment]
        if not view or not await view._check(interaction):
            return
        view.draft.report_type = self.values[0]
        if view.draft.report_type != "character":
            view.draft.target_character_id = None
        await view._redraw(interaction)


class ReasonSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="選擇違規分類", row=2, options=[
            discord.SelectOption(label=label, value=code, default=current == code)
            for code, label in REASONS.items()
        ])

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ReportFormView = self.view  # type: ignore[assignment]
        if not view or not await view._check(interaction):
            return
        view.draft.reason_code = self.values[0]
        await view._redraw(interaction)


class ReportFormView(discord.ui.View):
    """單一舉報人的短效私密填表操作面板；送出後不可重複使用。"""
    def __init__(self, guild_id: int, reporter_id: int):
        super().__init__(timeout=FORM_TIMEOUT)
        self.draft = ReportDraft(guild_id=guild_id, reporter_id=reporter_id)
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        self.clear_items()
        self.add_item(TargetSelect())
        self.add_item(ReportTypeSelect(self.draft.report_type))
        self.add_item(ReasonSelect(self.draft.reason_code))
        # 第一階段的操作按鈕全部放在第 4 行；第 5 行確認及取消。
        for label, custom, callback in [
            ("指定角色", "character", self._character),
            ("填寫經過", "description", self._description),
            ("貼上證據", "evidence", self._evidence),
        ]:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary,
                                       row=3, disabled=self.draft.submitted or
                                       (custom == "character" and self.draft.report_type != "character"))
            button.callback = callback
            self.add_item(button)
        confirm = discord.ui.Button(label="正式送出", style=discord.ButtonStyle.success,
                                    row=4, disabled=self.draft.submitted or self.draft.busy)
        confirm.callback = self._submit
        self.add_item(confirm)
        cancel = discord.ui.Button(label="取消草稿", style=discord.ButtonStyle.danger,
                                   row=4, disabled=self.draft.submitted)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self._check(interaction)

    async def _check(self, interaction: discord.Interaction) -> bool:
        if not _is_draft_owner(interaction, self.draft):
            await _private_message(interaction, "你不能操作其他人的舉報表單。")
            return False
        if self.draft.submitted:
            await _private_message(interaction, "本次表單已完成，請從「我的舉報」查看案件。")
            return False
        return True

    async def _redraw(self, interaction: discord.Interaction) -> None:
        self._refresh_controls()
        await interaction.response.edit_message(embed=_summary_embed(self.draft), view=self)

    async def _character(self, interaction: discord.Interaction) -> None:
        if await self._check(interaction):
            await interaction.response.send_modal(CharacterModal(self))

    async def _description(self, interaction: discord.Interaction) -> None:
        if await self._check(interaction):
            await interaction.response.send_modal(DescriptionModal(self))

    async def _evidence(self, interaction: discord.Interaction) -> None:
        if await self._check(interaction):
            await interaction.response.send_modal(EvidenceModal(self))

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if not await self._check(interaction):
            return
        self.draft.submitted = True
        self.stop()
        await interaction.response.edit_message(content="舉報草稿已取消（未建立案件）。", embed=None, view=None)

    async def _submit(self, interaction: discord.Interaction) -> None:
        if not await self._check(interaction):
            return
        d = self.draft
        if d.busy:
            await _private_message(interaction, "正在提交，請勿重複點擊。")
            return
        if d.target_user_id is None:
            await _private_message(interaction, "請先選擇被舉報的 Discord 玩家。")
            return
        if d.report_type == "character" and d.target_character_id is None:
            await _private_message(interaction, "指定角色舉報必須填寫角色編號。")
            return
        if len(d.description.strip()) < 10:
            await _private_message(interaction, "請先填寫事件經過（至少 10 字）。")
            return
        if not d.evidence_urls:
            await _private_message(interaction, "請先提交至少一筆證據網址。")
            return
        d.busy = True
        self._refresh_controls()
        # 延遲回應，避免資料庫／Discord 驗證延遲導致互動逾時。
        await interaction.response.defer(ephemeral=True)
        try:
            submission = service.ReportSubmission(
                target_user_id=d.target_user_id,
                report_type=d.report_type,
                reason_code=d.reason_code,
                description=d.description,
                evidence_urls=tuple(d.evidence_urls),
                target_character_id=d.target_character_id,
                occurrence_note=d.occurrence_note,
            )
            summary = await service.submit_report(interaction, submission)
        except (ValueError, LookupError, permissions.ReportPermissionError) as exc:
            d.busy = False
            self._refresh_controls()
            await interaction.followup.send(f"未能送出：{exc}", ephemeral=True)
            return
        except Exception:
            # 不回報內部細節，避免資料庫／權限資料洩漏。
            # 跨服務交易可能已建案；不主動解鎖送出，以避免重複建立。
            d.submitted = True
            self.stop()
            await interaction.followup.send(
                "送出狀態暫時無法確認。請先從「我的舉報」確認是否已建立案件，勿直接重送。",
                ephemeral=True,
            )
            return
        d.submitted = True
        self.stop()
        await interaction.followup.send(
            f"舉報已提交，案件編號：REP-{summary.report_id:06d}。"
            "你可以在「我的舉報」查看進度或追加證據。",
            ephemeral=True,
        )


async def start_report_form(interaction: discord.Interaction) -> ReportFormView | None:
    """供既有 /申請 Cog 呼叫。不可在公開頻道發送完整舉報資料。"""
    if interaction.guild is None or interaction.guild_id is None:
        await _private_message(interaction, "請在 Discord 伺服器內使用舉報功能。")
        return None
    view = ReportFormView(interaction.guild_id, interaction.user.id)
    await _send_start(interaction, view)
    return view


async def _send_start(interaction: discord.Interaction, view: ReportFormView) -> None:
    embed = _summary_embed(view.draft)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


__all__ = ["ReportDraft", "ReportFormView", "start_report_form", "validate_evidence",
           "CharacterModal", "DescriptionModal", "EvidenceModal"]
