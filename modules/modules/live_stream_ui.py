"""Discord RP 網路系統：唯一頂層指令 /網路。

直播功能使用 Discord 私人面板、按鈕、下拉選單和 Modal。
不建立 /直播，也不建立任何 /網路 子指令。
目前直播模擬收入僅為估算，不修改角色金幣。
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core import live_stream as live
from core.database import get_connection

LOG = logging.getLogger(__name__)
STREAM_KINDS = [
    discord.SelectOption(label="個人直播", value="personal", emoji="🎙️"),
    discord.SelectOption(label="團播", value="group", emoji="👥", description="團播邀請與分潤將於後續整合"),
    discord.SelectOption(label="直播帶貨", value="sales", emoji="🛍️", description="目前不會建立真實訂單"),
    discord.SelectOption(label="直播 PK", value="pk", emoji="⚔️", description="目前不會進行正式 PK 結算"),
    discord.SelectOption(label="官方直播", value="official", emoji="📺"),
    discord.SelectOption(label="會員直播", value="members", emoji="💎"),
]
DURATIONS = [
    discord.SelectOption(label="即時直播", value="realtime", description="手動結束"),
    discord.SelectOption(label="固定 15 分鐘", value="15"),
    discord.SelectOption(label="固定 30 分鐘", value="30"),
    discord.SelectOption(label="固定 60 分鐘", value="60"),
    discord.SelectOption(label="固定 120 分鐘", value="120"),
    discord.SelectOption(label="固定 240 分鐘", value="240"),
]


def _my_characters(guild_id: int, user_id: int) -> list[tuple[int, str]]:
    with get_connection() as db:
        rows = db.execute(
            "SELECT id, name FROM characters WHERE guild_id=? AND owner_id=? "
            "AND status='active' ORDER BY id LIMIT 26",
            (str(guild_id), str(user_id)),
        ).fetchall()
    return [(int(row["id"]), str(row["name"])) for row in rows]


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: int = 300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("⛔ 這是其他玩家的操作面板。請自行使用 /網路。", ephemeral=True)
            return False
        return True


class StartModal(discord.ui.Modal, title="建立直播間"):
    live_title = discord.ui.TextInput(label="直播標題", placeholder="今天想直播什麼？", min_length=1, max_length=80)

    def __init__(self, character_id: int, kind: str, duration: str):
        super().__init__()
        self.character_id = character_id
        self.kind = kind
        self.duration = duration

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("只能在伺服器開播。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            mode = "realtime" if self.duration == "realtime" else "fixed"
            mins = None if mode == "realtime" else int(self.duration)
            sid = await asyncio.to_thread(
                live.start, interaction.guild.id, interaction.user.id,
                self.character_id, str(self.live_title.value), self.kind, mode, mins,
            )
        except (LookupError, PermissionError, ValueError) as exc:
            await interaction.followup.send(f"⚠️ 無法開播：{exc}", ephemeral=True)
            return
        except Exception:
            LOG.exception("直播開播失敗")
            await interaction.followup.send("⚠️ 開播遇到系統錯誤，請管理員查看日誌。", ephemeral=True)
            return
        await interaction.followup.send(
            f"🔴 已開播！直播場次 **#{sid}**｜{self.live_title.value}\n"
            "目前模擬打賞僅供顯示，不會自動加入角色錢包。",
            ephemeral=True,
        )


class StartView(OwnedView):
    def __init__(self, owner_id: int, character_id: int):
        super().__init__()
        self.character_id = character_id
        self.kind = "personal"
        self.duration = "realtime"
        kinds = discord.ui.Select(placeholder="選擇直播類型", options=STREAM_KINDS)
        durations = discord.ui.Select(placeholder="選擇計時方式", options=DURATIONS)

        async def pick_kind(interaction: discord.Interaction):
            self.kind = kinds.values[0]
            await interaction.response.defer(ephemeral=True)

        async def pick_duration(interaction: discord.Interaction):
            self.duration = durations.values[0]
            await interaction.response.defer(ephemeral=True)

        kinds.callback = pick_kind
        durations.callback = pick_duration
        self.add_item(kinds)
        self.add_item(durations)

    @discord.ui.button(label="確認並輸入直播標題", style=discord.ButtonStyle.success, emoji="🔴")
    async def launch(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(StartModal(self.character_id, self.kind, self.duration))


class NetworkView(OwnedView):
    def __init__(self, owner_id: int, characters: list[tuple[int, str]]):
        super().__init__()
        self.character_id = characters[0][0]
        options = [discord.SelectOption(label=f"{name[:80]} (#{cid})", value=str(cid)) for cid, name in characters[:25]]
        selector = discord.ui.Select(placeholder="選擇你要操作的角色", options=options)

        async def pick(interaction: discord.Interaction):
            self.character_id = int(selector.values[0])
            await interaction.response.send_message(
                f"✅ 已選擇角色 #{self.character_id}。請點下面的直播按鈕。", ephemeral=True,
            )
        selector.callback = pick
        self.add_item(selector)

    @discord.ui.button(label="直播開播", style=discord.ButtonStyle.success, emoji="🔴", row=1)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "請選擇直播類型與計時方式，再輸入標題。",
            view=StartView(self.owner_id, self.character_id), ephemeral=True,
        )

    @discord.ui.button(label="直播狀態", style=discord.ButtonStyle.primary, emoji="📊", row=1)
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("只能在伺服器使用。", ephemeral=True)
            return
        try:
            data = await asyncio.to_thread(live.current, interaction.guild.id, self.character_id)
        except Exception:
            LOG.exception("查看直播狀態失敗")
            await interaction.response.send_message("⚠️ 目前無法讀取直播狀態。", ephemeral=True)
            return
        if not data:
            await interaction.response.send_message("這名角色目前沒有進行中的直播。", ephemeral=True)
            return
        embed = discord.Embed(title=f"🔴 {data['title']}", color=discord.Color.red())
        embed.add_field(name="場次編號", value=f"#{data['id']}")
        embed.add_field(name="目前在線", value=f"{data['viewers']:,}")
        embed.add_field(name="最高在線", value=f"{data['peak_viewers']:,}")
        embed.add_field(name="模擬打賞（不可提領）", value=f"{data['simulated_tips']:,} 金幣", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="結束直播", style=discord.ButtonStyle.danger, emoji="⏹️", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("只能在伺服器使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await asyncio.to_thread(live.current, interaction.guild.id, self.character_id)
            if not data:
                await interaction.followup.send("這名角色沒有進行中的直播。", ephemeral=True)
                return
            result = await asyncio.to_thread(live.stop, interaction.guild.id, interaction.user.id, data["id"])
        except (LookupError, PermissionError, ValueError) as exc:
            await interaction.followup.send(f"⚠️ 無法結束：{exc}", ephemeral=True)
            return
        except Exception:
            LOG.exception("結束直播失敗")
            await interaction.followup.send("⚠️ 無法結束直播，請查看系統日誌。", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ 場次 #{result['id']} 已結束。最高在線：{result['peak_viewers']:,} 人。",
            ephemeral=True,
        )


class LiveStreamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="網路", description="開啟網路系統（社群、直播及其他操作入口）")
    @app_commands.guild_only()
    async def network(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("請在伺服器使用 /網路。", ephemeral=True)
            return
        try:
            chars = await asyncio.to_thread(_my_characters, interaction.guild.id, interaction.user.id)
        except Exception:
            LOG.exception("網路面板角色查詢失敗")
            await interaction.response.send_message("⚠️ 暫時無法讀取角色資料。", ephemeral=True)
            return
        if not chars:
            await interaction.response.send_message("你目前沒有已啟用的角色，無法操作網路直播。", ephemeral=True)
            return
        note = "\n你擁有超過 25 個角色，目前先顯示前 25 名。" if len(chars) > 25 else ""
        await interaction.response.send_message(
            "🌐 **網路中心**\n選擇角色後，使用下方按鈕操作直播。"
            "\n其他社群功能將整合在這個面板，不會新增斜線子指令。" + note,
            view=NetworkView(interaction.user.id, chars), ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LiveStreamCog(bot))
