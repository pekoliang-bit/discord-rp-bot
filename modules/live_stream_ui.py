
"""Discord RP 網路直播系統。

唯一斜線指令：/網路
所有直播功能均使用按鈕、選單和 Modal 操作。
模擬直播收入不會自動轉成角色金幣。
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core import live_stream as live
from core import live_social as social
from core import live_teams as teams
from core import live_team_treasury as treasury
from core import live_pk as pk
from core import live_commerce as commerce
from core import live_shop_adapter as shop_adapter
from core import live_checkout as checkout
from core.database import get_connection

LOG = logging.getLogger(__name__)

STREAM_KINDS = [
    discord.SelectOption(
        label="個人直播", value="personal", emoji="🎙️"
    ),
    discord.SelectOption(
        label="團播", value="group", emoji="👥",
        description="主持人邀請，受邀角色本人同意"
    ),
    discord.SelectOption(
        label="直播帶貨", value="sales", emoji="🛍️",
        description="官方商品驗證後可正式結算"
    ),
    discord.SelectOption(
        label="直播 PK", value="pk", emoji="⚔️",
        description="雙方同意後以真實禮物計分"
    ),
    discord.SelectOption(
        label="官方直播", value="official", emoji="📺"
    ),
    discord.SelectOption(
        label="會員直播", value="members", emoji="💎"
    ),
]

DURATIONS = [
    discord.SelectOption(
        label="即時直播", value="realtime",
        description="手動結束"
    ),
    discord.SelectOption(label="固定 15 分鐘", value="15"),
    discord.SelectOption(label="固定 30 分鐘", value="30"),
    discord.SelectOption(label="固定 60 分鐘", value="60"),
    discord.SelectOption(label="固定 120 分鐘", value="120"),
    discord.SelectOption(label="固定 240 分鐘", value="240"),
]


def _my_characters(guild_id: int, user_id: int):
    with get_connection() as db:
        rows = db.execute(
            "SELECT id, name FROM characters "
            "WHERE guild_id=? AND owner_id=? "
            "AND status='active' ORDER BY id LIMIT 26",
            (str(guild_id), str(user_id)),
        ).fetchall()

    return [
        (int(row["id"]), str(row["name"]))
        for row in rows
    ]


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout=300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(
        self, interaction: discord.Interaction
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "⛔ 這是其他玩家的操作面板，請自行使用 /網路。",
                ephemeral=True,
            )
            return False
        return True


class StartModal(discord.ui.Modal, title="建立直播間"):
    live_title = discord.ui.TextInput(
        label="直播標題",
        placeholder="今天想直播什麼？",
        min_length=1,
        max_length=80,
    )

    def __init__(self, character_id, kind, duration):
        super().__init__()
        self.character_id = character_id
        self.kind = kind
        self.duration = duration

    async def on_submit(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器開播。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True, thinking=True
        )

        try:
            mode = (
                "realtime"
                if self.duration == "realtime"
                else "fixed"
            )
            minutes = (
                None
                if mode == "realtime"
                else int(self.duration)
            )

            session_id = await asyncio.to_thread(
                live.start,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
                str(self.live_title.value),
                self.kind,
                mode,
                minutes,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ 無法開播：{exc}", ephemeral=True
            )
            return

        except Exception:
            LOG.exception("直播開播失敗")
            await interaction.followup.send(
                "⚠️ 開播遇到系統錯誤，請管理員查看日誌。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"🔴 已開播！場次 #{session_id}\n"
            f"標題：{self.live_title.value}\n"
            "模擬打賞僅作為展示，不會自動加入錢包。",
            ephemeral=True,
        )


class StartView(OwnedView):
    def __init__(self, owner_id, character_id):
        super().__init__(owner_id)
        self.character_id = character_id
        self.kind = "personal"
        self.duration = "realtime"

        kinds = discord.ui.Select(
            placeholder="選擇直播類型",
            options=STREAM_KINDS,
        )
        durations = discord.ui.Select(
            placeholder="選擇計時方式",
            options=DURATIONS,
        )

        async def pick_kind(interaction):
            self.kind = kinds.values[0]
            await interaction.response.defer()

        async def pick_duration(interaction):
            self.duration = durations.values[0]
            await interaction.response.defer()

        kinds.callback = pick_kind
        durations.callback = pick_duration

        self.add_item(kinds)
        self.add_item(durations)

    @discord.ui.button(
        label="確認並輸入直播標題",
        style=discord.ButtonStyle.success,
        emoji="🔴",
    )
    async def launch(self, interaction, button):
        await interaction.response.send_modal(
            StartModal(
                self.character_id,
                self.kind,
                self.duration,
            )
        )


class InviteModal(discord.ui.Modal, title="邀請角色加入團播"):
    target_id = discord.ui.TextInput(
        label="受邀角色編號",
        placeholder="填入對方角色 ID",
        min_length=1,
        max_length=12,
    )

    def __init__(self, host_character_id):
        super().__init__()
        self.host_character_id = host_character_id

    async def on_submit(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "必須在伺服器使用。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            session = await asyncio.to_thread(
                live.current,
                interaction.guild.id,
                self.host_character_id,
            )

            if not session:
                raise ValueError("目前沒有主持直播")

            if (
                session["host_character_id"]
                != self.host_character_id
            ):
                raise ValueError("這不是你的主持場次")

            name = await asyncio.to_thread(
                social.invite,
                interaction.guild.id,
                session["id"],
                interaction.user.id,
                int(str(self.target_id.value)),
            )

            await interaction.followup.send(
                f"✅ 已邀請 {name} 加入場次 "
                f"#{session['id']}。\n"
                "對方需於一小時內在自己的 /網路 "
                "面板接受邀請。",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ 邀請失敗：{exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("團播邀請失敗")
            await interaction.followup.send(
                "⚠️ 邀請失敗，請查看日誌。",
                ephemeral=True,
            )


class GiftModal(discord.ui.Modal, title="送出直播禮物"):
    session_id = discord.ui.TextInput(
        label="直播場次編號",
        max_length=12,
    )
    gift_name = discord.ui.TextInput(
        label="禮物名稱",
        placeholder="花束／應援燈／星光／皇冠／銀河／傳奇",
        max_length=8,
    )
    anonymous = discord.ui.TextInput(
        label="匿名？（是／否）",
        default="否",
        max_length=2,
    )

    def __init__(self, sender_character_id):
        super().__init__()
        self.sender_character_id = sender_character_id

    async def on_submit(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "必須在伺服器使用。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            name = str(self.gift_name.value).strip()
            anonymous = (
                str(self.anonymous.value).strip().lower()
                in {"是", "y", "yes", "true"}
            )

            result = await asyncio.to_thread(
                social.gift,
                interaction.guild.id,
                int(str(self.session_id.value)),
                interaction.user.id,
                self.sender_character_id,
                name,
                interaction.id,
                anonymous,
            )

            await interaction.followup.send(
                f"🎁 贈禮成功：{name}\n"
                f"扣除：{result['gross_amount']:,} 金幣\n"
                f"直播成員分潤總額："
                f"{result['received_amount']:,} 金幣\n"
                f"平台費：{result['platform_fee']:,} 金幣",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ 無法贈禮：{exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("直播禮物結算失敗")
            await interaction.followup.send(
                "⚠️ 結算異常，請管理員核對帳本。",
                ephemeral=True,
            )


class SplitProposalModal(
    discord.ui.Modal,
    title="提出團播分潤"
):
    session_id = discord.ui.TextInput(
        label="團播場次編號", max_length=15
    )
    shares = discord.ui.TextInput(
        label="角色編號:百分比（逗號分隔）",
        placeholder="12:50,15:30,18:20",
        max_length=250,
    )

    def __init__(self, character_id):
        super().__init__()
        self.character_id = character_id

    async def on_submit(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "只能在伺服器操作", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            mapping = {}

            for entry in str(self.shares.value).split(","):
                cid, pct = entry.strip().split(":", 1)
                cid = int(cid)

                if cid in mapping:
                    raise ValueError("角色編號重複")

                mapping[cid] = int(pct) * 100

            if sum(mapping.values()) != 10000:
                raise ValueError("分潤總額必須為 100%")

            version = await asyncio.to_thread(
                teams.propose_split,
                interaction.guild.id,
                interaction.user.id,
                int(str(self.session_id.value)),
                mapping,
            )

            await interaction.followup.send(
                f"✅ 已提出分潤方案 v{version}。\n"
                "所有在場成員都必須同意，"
                "方案才會生效；此前維持平均分潤。",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("分潤提案失敗")
            await interaction.followup.send(
                "⚠️ 提案失敗，請查看日誌。",
                ephemeral=True,
            )


class SplitApprovalModal(
    discord.ui.Modal,
    title="同意團播分潤"
):
    session_id = discord.ui.TextInput(
        label="團播場次編號", max_length=15
    )
    version = discord.ui.TextInput(
        label="分潤方案版本", max_length=10
    )
    decision = discord.ui.TextInput(
        label="同意或拒絕",
        default="同意",
        max_length=4,
    )

    def __init__(self, character_id):
        super().__init__()
        self.character_id = character_id

    async def on_submit(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "只能在伺服器操作", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            approve = (
                str(self.decision.value).strip() == "同意"
            )

            state = await asyncio.to_thread(
                teams.approve_split,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
                int(str(self.session_id.value)),
                int(str(self.version.value)),
                approve,
            )

            await interaction.followup.send(
                f"✅ 分潤方案狀態：{state}\n"
                "active 代表全員同意並生效。",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("分潤審核失敗")
            await interaction.followup.send(
                "⚠️ 處理失敗，請查看日誌。",
                ephemeral=True,
            )


class NetworkView(OwnedView):
    def __init__(self, owner_id, characters):
        super().__init__(owner_id)
        self.character_id = characters[0][0]

        options = [
            discord.SelectOption(
                label=f"{name[:80]} (#{cid})",
                value=str(cid),
            )
            for cid, name in characters[:25]
        ]

        selector = discord.ui.Select(
            placeholder="選擇你要操作的角色",
            options=options,
        )

        async def pick(interaction):
            self.character_id = int(selector.values[0])

            await interaction.response.send_message(
                f"✅ 已選擇角色 #{self.character_id}",
                ephemeral=True,
            )

        selector.callback = pick
        self.add_item(selector)

    @discord.ui.button(
        label="直播開播",
        style=discord.ButtonStyle.success,
        emoji="🔴",
        row=1,
    )
    async def start_button(self, interaction, button):
        await interaction.response.send_message(
            "請選擇直播類型、時長，再輸入標題。",
            view=StartView(
                self.owner_id, self.character_id
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="直播狀態",
        style=discord.ButtonStyle.primary,
        emoji="📊",
        row=1,
    )
    async def status_button(self, interaction, button):
        if not interaction.guild:
            await interaction.response.send_message(
                "只能在伺服器使用。",
                ephemeral=True,
            )
            return

        try:
            data = await asyncio.to_thread(
                live.current,
                interaction.guild.id,
                self.character_id,
            )

        except Exception:
            LOG.exception("查詢直播狀態失敗")
            await interaction.response.send_message(
                "⚠️ 無法讀取直播狀態。",
                ephemeral=True,
            )
            return

        if not data:
            await interaction.response.send_message(
                "這名角色目前沒有直播。",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🔴 {data['title']}",
            color=discord.Color.red(),
        )
        embed.add_field(
            name="場次",
            value=f"#{data['id']}",
        )
        embed.add_field(
            name="目前在線",
            value=f"{data['viewers']:,}",
        )
        embed.add_field(
            name="最高在線",
            value=f"{data['peak_viewers']:,}",
        )
        embed.add_field(
            name="模擬打賞（不可提領）",
            value=f"{data['simulated_tips']:,} 金幣",
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed, ephemeral=True
        )

    @discord.ui.button(
        label="結束直播",
        style=discord.ButtonStyle.danger,
        emoji="⏹️",
        row=1,
    )
    async def stop_button(self, interaction, button):
        if not interaction.guild:
            await interaction.response.send_message(
                "只能在伺服器使用。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            data = await asyncio.to_thread(
                live.current,
                interaction.guild.id,
                self.character_id,
            )

            if not data:
                await interaction.followup.send(
                    "這名角色沒有進行中的直播。",
                    ephemeral=True,
                )
                return

            result = await asyncio.to_thread(
                live.stop,
                interaction.guild.id,
                interaction.user.id,
                data["id"],
            )

            await interaction.followup.send(
                f"✅ 場次 #{result['id']} 已結束。\n"
                f"最高在線："
                f"{result['peak_viewers']:,} 人",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("結束直播失敗")
            await interaction.followup.send(
                "⚠️ 無法結束直播。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="邀請團播",
        style=discord.ButtonStyle.secondary,
        emoji="👥",
        row=2,
    )
    async def invite_button(self, interaction, button):
        await interaction.response.send_modal(
            InviteModal(self.character_id)
        )

    async def _respond_invite(self, interaction, accept):
        if not interaction.guild:
            await interaction.response.send_message(
                "只能在伺服器使用。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            pending = await asyncio.to_thread(
                social.pending_for_character,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
            )

            if not pending:
                await interaction.followup.send(
                    "目前沒有待處理的團播邀請。",
                    ephemeral=True,
                )
                return

            target = pending[0]

            state = await asyncio.to_thread(
                social.respond,
                interaction.guild.id,
                target["id"],
                interaction.user.id,
                self.character_id,
                accept,
            )

            messages = {
                "accepted": "✅ 已加入團播！",
                "declined": "已拒絕邀請。",
                "expired": "邀請已過期。",
            }

            await interaction.followup.send(
                f"{messages[state]}\n"
                f"場次 #{target['session_id']}",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("團播邀請處理失敗")
            await interaction.followup.send(
                "⚠️ 系統錯誤，請查看日誌。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="接受邀請",
        style=discord.ButtonStyle.success,
        emoji="✅",
        row=2,
    )
    async def accept_button(self, interaction, button):
        await self._respond_invite(interaction, True)

    @discord.ui.button(
        label="拒絕邀請",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
        row=2,
    )
    async def decline_button(self, interaction, button):
        await self._respond_invite(interaction, False)

    @discord.ui.button(
        label="送直播禮物",
        style=discord.ButtonStyle.primary,
        emoji="🎁",
        row=3,
    )
    async def gift_button(self, interaction, button):
        await interaction.response.send_modal(
            GiftModal(self.character_id)
        )

    @discord.ui.button(
        label="提出分潤方案",
        style=discord.ButtonStyle.secondary,
        emoji="📊",
        row=3,
    )
    async def propose_split(self, interaction, button):
        await interaction.response.send_modal(
            SplitProposalModal(self.character_id)
        )

    @discord.ui.button(
        label="同意分潤",
        style=discord.ButtonStyle.secondary,
        emoji="🤝",
        row=3,
    )
    async def approve_split(self, interaction, button):
        await interaction.response.send_modal(
            SplitApprovalModal(self.character_id)
        )

    @discord.ui.button(
        label="直播 PK",
        style=discord.ButtonStyle.secondary,
        emoji="⚔️",
        row=3,
    )
    async def pk_center(self, interaction, button):
        await interaction.response.send_message(
            "⚔️ PK 中心：邀請、接受、結束 PK。\n"
            "只計算真實直播禮物。",
            view=PKView(
                self.owner_id, self.character_id
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="直播帶貨",
        style=discord.ButtonStyle.secondary,
        emoji="🛍️",
        row=4,
    )
    async def commerce_center(self, interaction, button):
        await interaction.response.send_message(
            "🛍️ 帶貨中心：商品提案、官方核准、"
            "正式購買與業績查詢。",
            view=CommerceView(
                self.owner_id, self.character_id
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="直播團隊",
        style=discord.ButtonStyle.primary,
        emoji="🏠",
        row=4,
    )
    async def team_center(self, interaction, button):
        await interaction.response.send_message(
            "🏠 直播團隊中心：建立、加入、金庫"
            "及提款審核。",
            view=TeamCenterView(
                self.owner_id, self.character_id
            ),
            ephemeral=True,
        )


class TeamModal(discord.ui.Modal):
    first = discord.ui.TextInput(
        label="名稱／角色編號／金額／申請編號",
        max_length=60,
    )
    second = discord.ui.TextInput(
        label="第二欄",
        required=False,
        max_length=60,
    )

    def __init__(self, owner_id, character_id, mode):
        titles = {
            "create": "建立直播團隊",
            "invite": "邀請加入直播團隊",
            "deposit": "團隊金庫存入",
            "withdraw": "申請團隊金庫提款",
            "approve": "審核團隊提款",
        }

        super().__init__(title=titles[mode])

        self.owner_id = owner_id
        self.character_id = character_id
        self.mode = mode

        descriptions = {
            "create": ("團隊名稱", "不需要填寫"),
            "invite": ("受邀角色編號", "不需要填寫"),
            "deposit": ("存入金額", "不需要填寫"),
            "withdraw": ("收款角色編號", "提款金額"),
            "approve": ("提款申請編號", "同意／拒絕"),
        }

        self.first.label, self.second.label = descriptions[mode]
        self.second.required = mode == "withdraw"

    async def on_submit(self, interaction):
        if (
            interaction.guild is None
            or interaction.user.id != self.owner_id
        ):
            await interaction.response.send_message(
                "⛔ 無權操作此面板。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True, thinking=True
        )

        gid = interaction.guild.id
        uid = interaction.user.id
        cid = self.character_id

        try:
            team = await asyncio.to_thread(
                treasury.find_team, gid, uid, cid
            )

            if self.mode == "create":
                tid = await asyncio.to_thread(
                    teams.create_team,
                    gid, uid, cid, str(self.first.value),
                )
                message = f"✅ 已建立直播團隊 #{tid}"

            elif not team:
                raise ValueError("尚未加入永久直播團隊")

            elif self.mode == "invite":
                await asyncio.to_thread(
                    teams.invite_team_member,
                    gid, uid, team["id"],
                    int(str(self.first.value)),
                )
                message = (
                    "✅ 已發送團隊邀請，"
                    "對方需在 /網路 → 直播團隊接受。"
                )

            elif self.mode == "deposit":
                amount = int(str(self.first.value))

                created = await asyncio.to_thread(
                    treasury.deposit,
                    gid, uid, cid, team["id"],
                    amount, str(interaction.id),
                )

                message = (
                    f"✅ 已存入 {amount:,} 金幣。"
                    if created else
                    "這筆存款已處理，沒有重複扣款。"
                )

            elif self.mode == "withdraw":
                recipient = int(str(self.first.value))
                amount = int(str(self.second.value))

                rid = await asyncio.to_thread(
                    treasury.request_withdrawal,
                    gid, uid, cid, team["id"],
                    recipient, amount,
                )

                message = (
                    f"✅ 提款申請 #{rid} 已建立，"
                    "需另一名正式成員批准。"
                )

            elif self.mode == "approve":
                answer = str(
                    self.second.value or "同意"
                ).strip()

                approve = answer not in {
                    "拒絕", "否", "no", "N"
                }

                outcome = await asyncio.to_thread(
                    treasury.decide_withdrawal,
                    gid, uid, cid,
                    int(str(self.first.value)),
                    approve,
                )

                message = f"提款申請結果：{outcome}"

            else:
                raise ValueError("未知操作")

            await interaction.followup.send(
                message, ephemeral=True
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ 操作失敗：{exc}",
                ephemeral=True,
            )

        except Exception:
            LOG.exception("直播團隊管理失敗")
            await interaction.followup.send(
                "⚠️ 發生系統錯誤，請查看日誌。",
                ephemeral=True,
            )


class TeamCenterView(OwnedView):
    def __init__(self, owner_id, character_id):
        super().__init__(owner_id)
        self.character_id = character_id

    async def _modal(self, interaction, mode):
        await interaction.response.send_modal(
            TeamModal(
                self.owner_id,
                self.character_id,
                mode,
            )
        )

    @discord.ui.button(
        label="建立團隊", emoji="🏠", row=0
    )
    async def create(self, interaction, button):
        await self._modal(interaction, "create")

    @discord.ui.button(
        label="邀請成員", emoji="✉️", row=0
    )
    async def invite(self, interaction, button):
        await self._modal(interaction, "invite")

    @discord.ui.button(
        label="接受團隊邀請", emoji="✅", row=0
    )
    async def accept(self, interaction, button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器使用。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            pending = await asyncio.to_thread(
                treasury.pending_invites,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
            )

            if not pending:
                await interaction.followup.send(
                    "目前沒有團隊邀請。",
                    ephemeral=True,
                )
                return

            target = pending[0]

            await asyncio.to_thread(
                teams.respond_team_invite,
                interaction.guild.id,
                interaction.user.id,
                target["id"],
                self.character_id,
                True,
            )

            await interaction.followup.send(
                f"✅ 已加入 {target['name']}",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("接受團隊邀請失敗")
            await interaction.followup.send(
                "⚠️ 系統錯誤。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="金庫餘額", emoji="💰", row=1
    )
    async def money(self, interaction, button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器使用。",
                ephemeral=True,
            )
            return

        try:
            team = await asyncio.to_thread(
                treasury.find_team,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
            )

            if not team:
                raise ValueError("尚未加入團隊")

            total = await asyncio.to_thread(
                treasury.balance,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
                team["id"],
            )

            await interaction.response.send_message(
                f"🏦 {team['name']}\n"
                f"團隊金庫：{total:,} 金幣",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("金庫餘額查詢失敗")
            await interaction.response.send_message(
                "⚠️ 系統錯誤。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="存入金庫", emoji="📥", row=1
    )
    async def deposit(self, interaction, button):
        await self._modal(interaction, "deposit")

    @discord.ui.button(
        label="提出提款", emoji="📤", row=1
    )
    async def withdraw(self, interaction, button):
        await self._modal(interaction, "withdraw")

    @discord.ui.button(
        label="審核提款", emoji="🗳️", row=1
    )
    async def approve(self, interaction, button):
        await self._modal(interaction, "approve")

    @discord.ui.button(
        label="查看待審提款", emoji="📋", row=2
    )
    async def pending(self, interaction, button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器使用。",
                ephemeral=True,
            )
            return

        try:
            rows = await asyncio.to_thread(
                treasury.pending_withdrawals,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
            )

            message = "\n".join(
                f"#{r['id']}："
                f"{r['amount']:,} 金幣 → "
                f"角色 #{r['recipient_character_id']}"
                for r in rows
            ) or "沒有待審提款"

            await interaction.response.send_message(
                message[:1900],
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("查詢待審提款失敗")
            await interaction.response.send_message(
                "⚠️ 系統錯誤。",
                ephemeral=True,
            )


class PKModal(discord.ui.Modal):
    number = discord.ui.TextInput(
        label="對方直播場次／PK 編號",
        max_length=15,
    )

    def __init__(self, character_id, mode):
        titles = {
            "challenge": "發起 PK 邀請",
            "finish": "結束直播 PK",
        }

        super().__init__(title=titles[mode])
        self.character_id = character_id
        self.mode = mode

    async def on_submit(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器操作。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            gid = interaction.guild.id
            uid = interaction.user.id
            number = int(str(self.number.value))

            if self.mode == "challenge":
                session = await asyncio.to_thread(
                    live.current,
                    gid,
                    self.character_id,
                )

                if (
                    not session
                    or session["stream_type"] != "pk"
                    or session["owner_id"] != str(uid)
                ):
                    raise ValueError(
                        "請先用這名角色主持 PK 直播"
                    )

                pk_id = await asyncio.to_thread(
                    pk.challenge,
                    gid, uid, session["id"], number,
                )

                message = (
                    f"✅ PK 邀請 #{pk_id} 已發送。"
                    "對方需自行在 /網路 同意。"
                )

            else:
                result = await asyncio.to_thread(
                    pk.finish, gid, uid, number
                )

                winner = (
                    result["winner_character_id"]
                    or "平手"
                )

                message = (
                    f"🏁 PK #{number} 已結算\n"
                    f"比分："
                    f"{result['challenger_score']:,}："
                    f"{result['opponent_score']:,}\n"
                    f"勝者角色：{winner}\n"
                    "不額外發放金幣。"
                )

            await interaction.followup.send(
                message, ephemeral=True
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("PK 操作失敗")
            await interaction.followup.send(
                "⚠️ PK 操作失敗。",
                ephemeral=True,
            )


class PKView(OwnedView):
    def __init__(self, owner_id, character_id):
        super().__init__(owner_id)
        self.character_id = character_id

    @discord.ui.button(
        label="邀請 PK", emoji="⚔️"
    )
    async def challenge(self, interaction, button):
        await interaction.response.send_modal(
            PKModal(self.character_id, "challenge")
        )

    @discord.ui.button(
        label="接受邀請", emoji="✅"
    )
    async def accept(self, interaction, button):
        if not interaction.guild:
            await interaction.response.send_message(
                "請在伺服器操作。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            rows = await asyncio.to_thread(
                pk.pending,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
            )

            if not rows:
                await interaction.followup.send(
                    "沒有待處理的 PK 邀請。",
                    ephemeral=True,
                )
                return

            state = await asyncio.to_thread(
                pk.respond,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
                rows[0]["id"],
                True,
            )

            await interaction.followup.send(
                f"✅ PK #{rows[0]['id']} 狀態：{state}",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("接受 PK 邀請失敗")
            await interaction.followup.send(
                "⚠️ 系統錯誤。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="結束 PK", emoji="🏁"
    )
    async def finish(self, interaction, button):
        await interaction.response.send_modal(
            PKModal(self.character_id, "finish")
        )


class OfferModal(
    discord.ui.Modal,
    title="提交直播帶貨商品"
):
    session_id = discord.ui.TextInput(
        label="直播場次編號", max_length=15
    )
    product_ref = discord.ui.TextInput(
        label="既有商城商品識別碼",
        max_length=100,
    )
    title_field = discord.ui.TextInput(
        label="商品名稱", max_length=80
    )
    price = discord.ui.TextInput(
        label="商城標價（金幣）",
        max_length=15,
    )
    cap = discord.ui.TextInput(
        label="可售數量上限",
        max_length=12,
    )

    async def on_submit(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "只能在伺服器操作。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            offer_id = await asyncio.to_thread(
                commerce.propose_offer,
                interaction.guild.id,
                interaction.user.id,
                int(str(self.session_id.value)),
                str(self.product_ref.value),
                str(self.title_field.value),
                int(str(self.price.value)),
                int(str(self.cap.value)),
            )

            await interaction.followup.send(
                f"✅ 帶貨商品提案 #{offer_id} 已登記。\n"
                "需完成官方商品驗證後，"
                "玩家才能正式付款購買。",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("帶貨商品提案失敗")
            await interaction.followup.send(
                "⚠️ 登記失敗。",
                ephemeral=True,
            )


class OffersLookupModal(
    discord.ui.Modal,
    title="查看帶貨商品"
):
    session_id = discord.ui.TextInput(
        label="直播場次編號", max_length=15
    )

    async def on_submit(self, interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "只能在伺服器操作。",
                ephemeral=True,
            )
            return

        try:
            rows = await asyncio.to_thread(
                commerce.offers,
                interaction.guild.id,
                int(str(self.session_id.value)),
            )

            message = "\n".join(
                f"#{r['id']} {r['title']}｜"
                f"{r['unit_price']:,} 金幣｜"
                f"{r['status']}"
                for r in rows[:20]
            ) or "本場沒有商品提案"

            await interaction.response.send_message(
                message[:1900],
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}", ephemeral=True
            )

        except Exception:
            LOG.exception("帶貨商品查詢失敗")
            await interaction.response.send_message(
                "⚠️ 查詢失敗。",
                ephemeral=True,
            )


class VerifyOfferModal(
    discord.ui.Modal,
    title="官方驗證直播商品"
):
    offer_id = discord.ui.TextInput(
        label="帶貨提案編號",
        max_length=15,
    )

    async def on_submit(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器操作。",
                ephemeral=True,
            )
            return

        member = interaction.user
        guild = interaction.guild

        allowed = (
            member.id == guild.owner_id
            or member.guild_permissions.manage_guild
        )

        if not allowed:
            await interaction.response.send_message(
                "⛔ 只有群主或授權管理員能審核商品。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            result = await asyncio.to_thread(
                shop_adapter.verify_official_offer,
                guild.id,
                int(str(self.offer_id.value)),
                authorized=True,
            )

            await interaction.followup.send(
                f"✅ 已核准商品 #{result['offer_id']}\n"
                f"名稱：{result['name']}",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ 核准失敗：{exc}",
                ephemeral=True,
            )

        except Exception:
            LOG.exception("帶貨商品核准失敗")
            await interaction.followup.send(
                "⚠️ 核准失敗。",
                ephemeral=True,
            )


class CheckoutOfferModal(
    discord.ui.Modal,
    title="直播商品正式購買"
):
    offer_id = discord.ui.TextInput(
        label="已核准帶貨商品編號",
        max_length=15,
    )
    quantity = discord.ui.TextInput(
        label="購買數量（1～99）",
        default="1",
        max_length=3,
    )

    def __init__(self, character_id):
        super().__init__()
        self.character_id = character_id

    async def on_submit(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器操作。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            result = await asyncio.to_thread(
                checkout.checkout,
                interaction.guild.id,
                interaction.user.id,
                self.character_id,
                int(str(self.offer_id.value)),
                int(str(self.quantity.value)),
                str(interaction.id),
            )

            await interaction.followup.send(
                f"✅ 已完成直播訂單 #{result['id']}\n"
                f"支付：{result['paid_amount']:,} 金幣\n"
                f"主播佣金："
                f"{result['commission_amount']:,} 金幣\n"
                "商品已加入角色背包。",
                ephemeral=True,
            )

        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(
                f"⚠️ 購買失敗：{exc}",
                ephemeral=True,
            )

        except Exception:
            LOG.exception("直播商品結算失敗")
            await interaction.followup.send(
                "⚠️ 結算異常，請管理員核對帳本。",
                ephemeral=True,
            )


class LiveSalesTotalsModal(
    discord.ui.Modal,
    title="直播帶貨業績"
):
    session_id = discord.ui.TextInput(
        label="直播場次編號",
        max_length=15,
    )

    async def on_submit(self, interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "只能在伺服器操作。",
                ephemeral=True,
            )
            return

        try:
            result = await asyncio.to_thread(
                checkout.order_totals,
                interaction.guild.id,
                int(str(self.session_id.value)),
            )

            await interaction.response.send_message(
                f"🛍️ 真實訂單：{result['orders']} 筆\n"
                f"銷售：{result['units']} 件\n"
                f"銷售額：{result['sales']:,} 金幣\n"
                f"佣金：{result['commissions']:,} 金幣",
                ephemeral=True,
            )

        except Exception:
            LOG.exception("直播業績查詢失敗")
            await interaction.response.send_message(
                "⚠️ 查詢失敗。",
                ephemeral=True,
            )


class CommerceView(OwnedView):
    def __init__(self, owner_id, character_id):
        super().__init__(owner_id)
        self.character_id = character_id

    @discord.ui.button(
        label="提出帶貨商品",
        emoji="🛍️",
    )
    async def new(self, interaction, button):
        await interaction.response.send_modal(
            OfferModal()
        )

    @discord.ui.button(
        label="商品提案列表",
        emoji="📋",
    )
    async def items(self, interaction, button):
        await interaction.response.send_modal(
            OffersLookupModal()
        )

    @discord.ui.button(
        label="官方驗證商品",
        emoji="✅",
        row=1,
    )
    async def verify(self, interaction, button):
        await interaction.response.send_modal(
            VerifyOfferModal()
        )

    @discord.ui.button(
        label="購買直播商品",
        emoji="🛒",
        row=1,
    )
    async def buy(self, interaction, button):
        await interaction.response.send_modal(
            CheckoutOfferModal(self.character_id)
        )

    @discord.ui.button(
        label="真實銷售業績",
        emoji="📈",
        row=1,
    )
    async def totals(self, interaction, button):
        await interaction.response.send_modal(
            LiveSalesTotalsModal()
        )


class LiveStreamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="網路",
        description="開啟網路系統與直播操作面板",
    )
    @app_commands.guild_only()
    async def network(
        self, interaction: discord.Interaction
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "請在伺服器使用 /網路。",
                ephemeral=True,
            )
            return

        try:
            characters = await asyncio.to_thread(
                _my_characters,
                interaction.guild.id,
                interaction.user.id,
            )

        except Exception:
            LOG.exception("網路面板角色查詢失敗")
            await interaction.response.send_message(
                "⚠️ 暫時無法讀取角色資料。",
                ephemeral=True,
            )
            return

        if not characters:
            await interaction.response.send_message(
                "你目前沒有已啟用的角色，"
                "無法操作網路直播。",
                ephemeral=True,
            )
            return

        note = (
            "\n角色超過 25 名，先顯示前 25 名。"
            if len(characters) > 25
            else ""
        )

        await interaction.response.send_message(
            "🌐 **網路中心**\n"
            "選擇角色後，可以操作直播、"
            "團播、禮物、團隊、PK 與帶貨。\n"
            "所有功能均使用按鈕，"
            "不新增其他斜線指令。"
            + note,
            view=NetworkView(
                interaction.user.id,
                characters,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LiveStreamCog(bot))
