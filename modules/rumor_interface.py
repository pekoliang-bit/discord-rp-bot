
"""Discord /傳聞 玩家操作入口（公關評級整合版）。

管理設定仍歸 /官方。
每次公關付款及狀態查詢均由後端核對角色權限。
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from modules import (
    pr_rumor,
    rumor_investigation,
    rumor_police,
    rumor_network,
    rumor_public_relations,
)

from core.rumor_pr_quote import quote_pr_action
from core.rumor_pr_paid_flow import perform_paid_pr_action
from core.rumor_pr_history import get_pr_history
from core.rumor_pr_grade_status import get_pr_grade_status
from core.rumor_pr_grade_display import format_pr_grade_status


LOG = logging.getLogger(__name__)


def _integer(value, label):
    try:
        number = int(str(value).strip())
        if number <= 0:
            raise ValueError
        return number
    except ValueError as exc:
        raise ValueError(
            f"{label}必須是正整數。"
        ) from exc


class GenericRumorModal(discord.ui.Modal):
    def __init__(self, purpose, fields):
        super().__init__(
            title=purpose[:45],
            timeout=180,
        )
        self.purpose = purpose
        self.fields = []

        for label, placeholder in fields:
            item = discord.ui.TextInput(
                label=label[:45],
                placeholder=placeholder[:100],
                max_length=500,
            )
            self.fields.append(item)
            self.add_item(item)

    async def on_submit(self, interaction):
        try:
            values = [
                item.value.strip()
                for item in self.fields
            ]

            gid = interaction.guild_id
            uid = interaction.user.id
            purpose = self.purpose

            if purpose == "設定傳聞分類授權":
                cid = _integer(
                    values[0], "角色編號"
                )

                if values[2] not in ("開啟", "關閉"):
                    raise ValueError(
                        "狀態請填寫開啟或關閉。"
                    )

                pr_rumor.set_category_consent(
                    gid,
                    uid,
                    cid,
                    values[1],
                    values[2] == "開啟",
                )
                result = "分類授權已更新。"

            elif purpose == "登記證據":
                if values[4] not in ("真", "偽"):
                    raise ValueError(
                        "真偽必須填真或偽。"
                    )

                eid = (
                    rumor_investigation.register_evidence(
                        gid,
                        uid,
                        _integer(
                            values[0], "傳聞編號"
                        ),
                        _integer(
                            values[1], "角色編號"
                        ),
                        values[2],
                        values[3],
                        authentic=values[4] == "真",
                    )
                )

                result = f"證據 #{eid} 已登記。"

            elif purpose == "進行基礎調查":
                data = (
                    rumor_investigation
                    .attempt_basic_investigation(
                        gid,
                        uid,
                        _integer(
                            values[0], "傳聞編號"
                        ),
                        _integer(
                            values[1], "角色編號"
                        ),
                    )
                )

                result = (
                    f"{'成功' if data['success'] else '未取得有效線索'}；"
                    f"調查進度 {data['progress']}/100。"
                )

            elif purpose == "虛構警方報案":
                evidence = (
                    _integer(
                        values[2], "證據編號"
                    )
                    if values[2]
                    else None
                )

                data = rumor_police.report_rumor(
                    gid,
                    uid,
                    _integer(
                        values[0], "傳聞編號"
                    ),
                    _integer(
                        values[1], "角色編號"
                    ),
                    evidence,
                )

                result = (
                    f"已受理警方案件 "
                    f"#{data['police_case_id']}；"
                    f"預計結案 "
                    f"<t:{data['due_at']}:F>。"
                )

            elif purpose == "向警方補交證據":
                added = rumor_police.submit_evidence(
                    gid,
                    uid,
                    _integer(
                        values[0], "警方案件編號"
                    ),
                    _integer(
                        values[1], "角色編號"
                    ),
                    _integer(
                        values[2], "證據編號"
                    ),
                )

                result = (
                    "證據已提交。"
                    if added
                    else "這份證據已經提交過。"
                )

            elif purpose == "網路公開曝光":
                eid = rumor_network.publish_exposure(
                    gid,
                    uid,
                    _integer(
                        values[0], "傳聞編號"
                    ),
                    _integer(
                        values[1], "角色編號"
                    ),
                    values[2],
                    values[3],
                )

                result = (
                    f"網路曝光紀錄 #{eid} 已建立。"
                )

            elif purpose == "提供網友線索":
                eid = rumor_network.submit_crowd_clue(
                    gid,
                    uid,
                    _integer(
                        values[0], "傳聞編號"
                    ),
                    _integer(
                        values[1], "角色編號"
                    ),
                    values[2],
                    values[3],
                )

                result = (
                    f"線索 #{eid} 已提交，等待查證。"
                )

            elif purpose == "查詢警方案件":
                data = rumor_police.police_case_status(
                    gid,
                    uid,
                    _integer(
                        values[0], "警方案件編號"
                    ),
                    _integer(
                        values[1], "角色編號"
                    ),
                )

                result = (
                    f"案件 #{data['id']}："
                    f"{data['status']}，"
                    f"已提交證據 "
                    f"{data['evidence_count']} 份。"
                )

            elif purpose == "查看公關歷史":
                history = get_pr_history(
                    gid,
                    uid,
                    _integer(
                        values[0], "傳聞編號"
                    ),
                    _integer(
                        values[1], "角色編號"
                    ),
                )

                lines = (
                    "\n".join(history.lines)
                    or "尚無付費公關紀錄"
                )

                result = (
                    f"📣 傳聞 #{history.rumor_id}"
                    f"｜{history.status}\n"
                    f"已記錄公關分數："
                    f"{history.score}\n"
                    f"公關行動：{history.count} 次；"
                    f"金幣支出：{history.coins}；"
                    f"付費行動值支出："
                    f"{history.paid_actions}\n"
                    f"最近操作：\n{lines}\n"
                    "※ 此分數僅為公關行動累積，"
                    "不是正式 S～D 評級。"
                )

            elif purpose == "查看傳聞結果":
                data = get_pr_grade_status(
                    gid,
                    uid,
                    _integer(
                        values[0], "傳聞編號"
                    ),
                    _integer(
                        values[1], "角色編號"
                    ),
                )

                result = format_pr_grade_status(data)

            else:
                raise ValueError("未知操作。")

        except (
            ValueError,
            PermissionError,
            TypeError,
            LookupError,
            RuntimeError,
        ) as exc:
            result = f"❌ {exc}"

        await interaction.response.send_message(
            result[:1900],
            ephemeral=True,
        )


FORM_DEFINITIONS = {
    "category": (
        "設定傳聞分類授權",
        [
            ("角色編號", "例如 15"),
            ("分類", "戀情／商業／工作／人際／私生活／其他"),
            ("狀態", "開啟／關閉"),
        ],
    ),

    "evidence": (
        "登記證據",
        [
            ("傳聞編號", "例如 8"),
            ("持有角色編號", "自己已啟用的角色"),
            (
                "證據類型",
                "目擊／對話／照片／影音／文件／交易／其他",
            ),
            ("可信度", "低／中／高／決定性"),
            ("真偽", "真／偽"),
        ],
    ),

    "investigate": (
        "進行基礎調查",
        [
            ("傳聞編號", "例如 8"),
            ("調查角色編號", "自己的角色"),
        ],
    ),

    "report": (
        "虛構警方報案",
        [
            ("傳聞編號", "例如 8"),
            ("報案角色編號", "自己的角色"),
            ("證據編號（可留空）", "選填"),
        ],
    ),

    "submit": (
        "向警方補交證據",
        [
            ("警方案件編號", "例如 2"),
            ("持有角色編號", "自己的角色"),
            ("證據編號", "例如 6"),
        ],
    ),

    "expose": (
        "網路公開曝光",
        [
            ("傳聞編號", "例如 8"),
            ("角色編號", "自己的角色"),
            ("曝光形式", "公開爆料／媒體報導"),
            ("標題", "80 字內"),
        ],
    ),

    "crowd": (
        "提供網友線索",
        [
            ("傳聞編號", "例如 8"),
            ("角色編號", "自己的角色"),
            (
                "線索類型",
                "目擊線索／內容比對／知情者爆料／意外牽連",
            ),
            ("內容", "5 至 500 字"),
        ],
    ),

    "police_status": (
        "查詢警方案件",
        [
            ("警方案件編號", "例如 2"),
            ("報案角色編號", "自己的角色"),
        ],
    ),

    "history": (
        "查看公關歷史",
        [
            ("傳聞編號", "例如 8"),
            ("角色編號", "本人角色編號"),
        ],
    ),

    "result": (
        "查看傳聞結果",
        [
            ("傳聞編號", "例如 8"),
            ("角色編號", "本人角色編號"),
        ],
    ),
}


class PRConfirmView(discord.ui.View):
    """報價為參考，付款時會重新檢查條件。"""

    def __init__(
        self,
        owner_id,
        guild_id,
        rumor_id,
        character_id,
        strategy,
        resource,
    ):
        super().__init__(timeout=120)

        self.owner_id = owner_id
        self.guild_id = guild_id
        self.rumor_id = rumor_id
        self.character_id = character_id
        self.strategy = strategy
        self.resource = resource

    @discord.ui.button(
        label="確認支付並執行",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的公關付款確認。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            data = await asyncio.to_thread(
                perform_paid_pr_action,
                self.guild_id,
                interaction.user.id,
                self.character_id,
                self.rumor_id,
                self.strategy,
                self.resource,
                f"pr-confirm:{interaction.id}",
                trusted=True,
            )

            unit = (
                "金幣"
                if data.resource == "coins"
                else "付費行動值"
            )

            text = (
                "✅ 公關操作完成！\n"
                f"策略：{self.strategy}\n"
                f"消耗：{data.cost} {unit}\n"
                f"本次公關分數："
                f"+{data.effective_score}\n"
                f"效益：{data.multiplier:.0%}；"
                f"第 {data.repeat_count} 次使用"
                + (
                    "\n（此請求已處理，不重複扣費）"
                    if data.duplicate
                    else ""
                )
            )

        except (
            ValueError,
            PermissionError,
            LookupError,
            RuntimeError,
        ) as exc:
            text = f"❌ 未完成付款：{exc}"

        except Exception:
            LOG.exception(
                "公關付款發生未預期錯誤"
            )
            text = (
                "❌ 付款狀態待確認，"
                "請通知管理員核對紀錄，"
                "勿重複嘗試。"
            )

        else:
            for item in self.children:
                item.disabled = True
            self.stop()

        await interaction.followup.send(
            text[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="取消",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的確認視窗。",
                ephemeral=True,
            )
            return

        for item in self.children:
            item.disabled = True

        self.stop()

        await interaction.response.send_message(
            "已取消，沒有扣費。",
            ephemeral=True,
        )


class PRPaymentModal(
    discord.ui.Modal,
    title="公關策略｜付款前確認",
):
    rumor_id = discord.ui.TextInput(
        label="傳聞編號",
        placeholder="例如 8",
        max_length=20,
    )

    character_id = discord.ui.TextInput(
        label="本人角色編號",
        placeholder="例如 15",
        max_length=20,
    )

    strategy = discord.ui.TextInput(
        label="策略名稱（必須與官方設定完全一致）",
        max_length=80,
    )

    resource = discord.ui.TextInput(
        label="支付方式：金幣／行動值",
        placeholder="金幣",
        max_length=10,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        try:
            rid = _integer(
                self.rumor_id.value,
                "傳聞編號",
            )

            cid = _integer(
                self.character_id.value,
                "角色編號",
            )

            resource = {
                "金幣": "coins",
                "行動值": "paid_actions",
            }.get(
                self.resource.value.strip()
            )

            if resource is None:
                raise ValueError(
                    "支付方式僅能填金幣或行動值"
                )

            strategy = self.strategy.value.strip()

            quote = await asyncio.to_thread(
                quote_pr_action,
                interaction.guild_id,
                interaction.user.id,
                cid,
                rid,
                strategy,
                resource,
            )

            unit = (
                "金幣"
                if resource == "coins"
                else "付費行動值"
            )

            msg = (
                "📣 **公關付款確認**\n"
                f"案件 #{rid}｜"
                f"角色 #{cid}｜"
                f"{strategy}\n"
                f"案件等級：T{quote.tier}\n"
                f"支付：**{quote.cost} {unit}**\n"
                f"第 {quote.repeat_count} 次使用"
                f"（效果 {quote.multiplier:.0%}）\n"
                f"預估公關分數："
                f"+{quote.estimated_score}\n\n"
                "請確認後再支付；"
                "若有人同時操作或官方修改設定，"
                "實際結果可能改變。"
            )

            view = PRConfirmView(
                interaction.user.id,
                interaction.guild_id,
                rid,
                cid,
                strategy,
                resource,
            )

            await interaction.response.send_message(
                msg,
                view=view,
                ephemeral=True,
            )

        except (
            ValueError,
            PermissionError,
            LookupError,
            RuntimeError,
        ) as exc:
            await interaction.response.send_message(
                f"❌ {exc}",
                ephemeral=True,
            )

        except Exception:
            LOG.exception("公關報價失敗")

            await interaction.response.send_message(
                "❌ 公關報價暫時無法取得，"
                "請通知管理員。",
                ephemeral=True,
            )


class RumorActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="建立傳聞",
                value="create",
            ),
            discord.SelectOption(
                label="查看案件清單",
                value="records",
            ),
            discord.SelectOption(
                label="整體傳聞授權",
                value="consent",
            ),
            discord.SelectOption(
                label="設定分類授權",
                value="category",
            ),
            discord.SelectOption(
                label="登記證據",
                value="evidence",
            ),
            discord.SelectOption(
                label="基礎調查",
                value="investigate",
            ),
            discord.SelectOption(
                label="報警",
                value="report",
            ),
            discord.SelectOption(
                label="警方補交證據",
                value="submit",
            ),
            discord.SelectOption(
                label="網路曝光",
                value="expose",
            ),
            discord.SelectOption(
                label="網友線索",
                value="crowd",
            ),
            discord.SelectOption(
                label="公關策略",
                value="pr",
            ),
            discord.SelectOption(
                label="公關歷史與累積分數",
                value="history",
            ),
            discord.SelectOption(
                label="警方案件查詢",
                value="police_status",
            ),
            discord.SelectOption(
                label="公關評級狀態",
                value="result",
            ),
        ]

        super().__init__(
            placeholder="選擇傳聞操作",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        key = self.values[0]

        if key == "pr":
            await interaction.response.send_modal(
                PRPaymentModal()
            )
            return

        if key == "create":
            await interaction.response.send_modal(
                pr_rumor.RumorModal()
            )
            return

        if key == "consent":
            await interaction.response.send_modal(
                pr_rumor.ConsentModal()
            )
            return

        if key == "records":
            try:
                rows = pr_rumor.list_rumors(
                    interaction.guild_id,
                    interaction.user.id,
                )

                msg = "\n".join(
                    f"#{r['id']}｜"
                    f"角色#{r['character_id']}｜"
                    f"{r['title']}｜{r['status']}"
                    for r in rows
                ) or "目前沒有可查看的案件。"

            except (
                ValueError,
                LookupError,
                PermissionError,
            ) as exc:
                msg = f"❌ {exc}"

            await interaction.response.send_message(
                msg[:1900],
                ephemeral=True,
            )
            return

        title, fields = FORM_DEFINITIONS[key]

        await interaction.response.send_modal(
            GenericRumorModal(title, fields)
        )


class RumorPlayerPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(RumorActionSelect())


class RumorInterface(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="傳聞",
        description=(
            "傳聞、調查、網路曝光、"
            "報警與公關操作"
        ),
    )
    @app_commands.guild_only()
    async def rumor(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.send_message(
            "📰 傳聞中心｜"
            "選擇你要執行的操作。\n"
            "目前是整合測試版本；"
            "管理功能統一放在 /官方。",
            view=RumorPlayerPanel(),
            ephemeral=True,
        )


async def setup(bot):
    pr_rumor.initialize()
    rumor_police.initialize()
    rumor_network.initialize()
    rumor_public_relations.initialize()

    await bot.add_cog(
        RumorInterface(bot)
    )
