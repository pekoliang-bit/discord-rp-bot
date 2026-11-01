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
from core import live_social as social
from core import live_teams as teams
from core import live_team_treasury as treasury
from core.database import get_connection

LOG = logging.getLogger(__name__)
STREAM_KINDS = [
    discord.SelectOption(label="個人直播", value="personal", emoji="🎙️"),
    discord.SelectOption(label="團播", value="group", emoji="👥", description="主持人邀請，受邀角色本人同意"),
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


class InviteModal(discord.ui.Modal, title="邀請角色加入團播"):
    target_id = discord.ui.TextInput(label="受邀角色編號", placeholder="填入對方角色 ID", min_length=1, max_length=12)

    def __init__(self, host_character_id: int):
        super().__init__()
        self.host_character_id=host_character_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("必須在伺服器使用。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            session=await asyncio.to_thread(live.current,interaction.guild.id,self.host_character_id)
            if not session or session['host_character_id'] != self.host_character_id:
                raise ValueError("這個角色目前沒有主持團播")
            name=await asyncio.to_thread(social.invite,interaction.guild.id,session['id'],interaction.user.id,int(str(self.target_id.value)))
            await interaction.followup.send(f"✅ 已邀請 **{name}** 加入場次 #{session['id']}。對方需要在自己的 `/網路` 面板按「接受邀請」，一小時內有效。",ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f"⚠️ 邀請失敗：{exc}",ephemeral=True)
        except Exception:
            LOG.exception("直播邀請失敗")
            await interaction.followup.send("⚠️ 邀請失敗，請查看伺服器日誌。",ephemeral=True)


class GiftModal(discord.ui.Modal,title="送出直播禮物"):
    session_id=discord.ui.TextInput(label="直播場次編號",placeholder="從直播狀態查詢場次 #ID",min_length=1,max_length=12)
    gift_name=discord.ui.TextInput(label="禮物名稱",placeholder="花束／應援燈／星光／皇冠／銀河／傳奇",min_length=1,max_length=8)
    anonymous=discord.ui.TextInput(label="匿名？（是／否）",default="否",min_length=1,max_length=2)

    def __init__(self,sender_character_id:int):
        super().__init__()
        self.sender_character_id=sender_character_id

    async def on_submit(self,interaction:discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("必須在伺服器使用。",ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            name=str(self.gift_name.value).strip()
            result=await asyncio.to_thread(social.gift,interaction.guild.id,int(str(self.session_id.value)),interaction.user.id,self.sender_character_id,name,interaction.id,str(self.anonymous.value).strip() in ('是','Y','y','yes','true'))
            await interaction.followup.send(f"🎁 贈禮成功：**{name}**，扣除 {result['gross_amount']:,} 金幣。主播實收 {result['received_amount']:,}，平台費 {result['platform_fee']:,}。",ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f"⚠️ 無法贈禮：{exc}",ephemeral=True)
        except Exception:
            LOG.exception("直播禮物扣款失敗")
            await interaction.followup.send("⚠️ 贈禮未完成，請管理員確認帳本後再操作。",ephemeral=True)


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

    @discord.ui.button(label="邀請團播",style=discord.ButtonStyle.secondary,emoji="👥",row=2)
    async def invite_button(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.send_modal(InviteModal(self.character_id))

    async def _respond_invite(self,interaction:discord.Interaction,accept:bool):
        if interaction.guild is None:
            await interaction.response.send_message("必須在伺服器使用。",ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            pending=await asyncio.to_thread(social.pending_for_character,interaction.guild.id,interaction.user.id,self.character_id)
            if not pending:
                await interaction.followup.send("目前沒有等待你處理的團播邀請。",ephemeral=True)
                return
            target=pending[0]
            state=await asyncio.to_thread(social.respond,interaction.guild.id,target['id'],interaction.user.id,self.character_id,accept)
            msg={'accepted':'✅ 已加入團播！','declined':'已拒絕這次團播邀請。','expired':'邀請已過期。'}[state]
            await interaction.followup.send(f"{msg} 場次 #{target['session_id']}",ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f"⚠️ 處理邀請失敗：{exc}",ephemeral=True)
        except Exception:
            LOG.exception("直播邀請處理失敗")
            await interaction.followup.send("⚠️ 處理邀請失敗，請查看日誌。",ephemeral=True)

    @discord.ui.button(label="接受邀請",style=discord.ButtonStyle.success,emoji="✅",row=2)
    async def accept_button(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._respond_invite(interaction,True)

    @discord.ui.button(label="拒絕邀請",style=discord.ButtonStyle.secondary,emoji="✖️",row=2)
    async def decline_button(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._respond_invite(interaction,False)

    @discord.ui.button(label="送直播禮物",style=discord.ButtonStyle.primary,emoji="🎁",row=3)
    async def gift_button(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.send_modal(GiftModal(self.character_id))



    @discord.ui.button(label="直播團隊",style=discord.ButtonStyle.primary,emoji="🏠",row=4)
    async def team_center(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🏠 直播團隊中心｜建立、加入、金庫存款與雙人審核提款。",
            view=TeamCenterView(self.owner_id,self.character_id),ephemeral=True)


class TeamModal(discord.ui.Modal):
    first = discord.ui.TextInput(label="名稱／角色編號／金額／申請編號", max_length=60)
    second = discord.ui.TextInput(label="第二欄（僅申請提款使用）", required=False, max_length=60)

    def __init__(self, owner_id: int, character_id: int, mode: str):
        titles = {"create":"建立直播團隊", "invite":"邀請加入直播團隊",
                  "deposit":"團隊金庫存入", "withdraw":"申請團隊金庫提款", "approve":"審核團隊提款"}
        super().__init__(title=titles[mode])
        self.owner_id, self.character_id, self.mode = owner_id, character_id, mode
        descriptions={
            "create":("團隊名稱", "不需要填寫"),
            "invite":("受邀角色編號", "不需要填寫"),
            "deposit":("存入金額（金幣）", "不需要填寫"),
            "withdraw":("收款角色編號", "提款金額（金幣）"),
            "approve":("提款申請編號", "同意／拒絕（預設同意）"),
        }
        self.first.label, self.second.label = descriptions[mode]
        self.second.required = mode == "withdraw"

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.user.id != self.owner_id:
            await interaction.response.send_message("⛔ 無權操作此面板", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        gid, uid, cid = interaction.guild.id, interaction.user.id, self.character_id
        try:
            team = await asyncio.to_thread(treasury.find_team,gid,uid,cid)
            if self.mode == "create":
                tid = await asyncio.to_thread(teams.create_team,gid,uid,cid,str(self.first.value))
                msg=f"✅ 已建立直播團隊 #{tid}。"
            elif not team:
                raise ValueError("這名角色尚未加入永久直播團隊")
            elif self.mode == "invite":
                await asyncio.to_thread(teams.invite_team_member,gid,uid,team['id'],int(str(self.first.value)))
                msg="✅ 已發送團隊邀請，對方需自行進入 /網路 → 團隊中心接受。"
            elif self.mode == "deposit":
                amount=int(str(self.first.value))
                created=await asyncio.to_thread(treasury.deposit,gid,uid,cid,team['id'],amount,str(interaction.id))
                msg=f"✅ 已存入 {amount:,} 金幣。" if created else "這筆存款已處理，不會重複扣款。"
            elif self.mode == "withdraw":
                rid=int(str(self.first.value)); amount=int(str(self.second.value))
                request_id=await asyncio.to_thread(treasury.request_withdrawal,gid,uid,cid,team['id'],rid,amount)
                msg=f"✅ 提款申請 #{request_id} 已建立，需另一名正式團隊成員批准才會轉帳。"
            elif self.mode == "approve":
                approve=str(self.second.value or "同意").strip() not in {"拒絕","否","no","N"}
                outcome=await asyncio.to_thread(treasury.decide_withdrawal,gid,uid,cid,int(str(self.first.value)),approve)
                msg=f"提款申請結果：{outcome}。"
            await interaction.followup.send(msg,ephemeral=True)
        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(f"⚠️ 操作失敗：{exc}",ephemeral=True)
        except Exception:
            LOG.exception("直播團隊管理失敗")
            await interaction.followup.send("⚠️ 發生系統錯誤，請查看日誌。",ephemeral=True)


class TeamCenterView(OwnedView):
    def __init__(self, owner_id: int, character_id: int):
        super().__init__(owner_id)
        self.character_id=character_id

    async def _modal(self,interaction,mode):
        await interaction.response.send_modal(TeamModal(self.owner_id,self.character_id,mode))

    @discord.ui.button(label="建立團隊",emoji="🏠",row=0)
    async def create(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._modal(interaction,"create")

    @discord.ui.button(label="邀請成員",emoji="✉️",row=0)
    async def invite(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._modal(interaction,"invite")

    @discord.ui.button(label="接受團隊邀請",emoji="✅",row=0)
    async def accept(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.guild is None: return await interaction.response.send_message("只能在伺服器使用",ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            pending=await asyncio.to_thread(treasury.pending_invites,interaction.guild.id,interaction.user.id,self.character_id)
            if not pending: return await interaction.followup.send("目前沒有團隊邀請。",ephemeral=True)
            await asyncio.to_thread(teams.respond_team_invite,interaction.guild.id,interaction.user.id,pending[0]['id'],self.character_id,True)
            await interaction.followup.send(f"✅ 已加入 {pending[0]['name']}。",ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f"⚠️ {exc}",ephemeral=True)
        except Exception:
            LOG.exception("接受直播團隊邀請失敗")
            await interaction.followup.send("⚠️ 系統錯誤",ephemeral=True)

    @discord.ui.button(label="金庫餘額",emoji="💰",row=1)
    async def money(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.guild is None: return await interaction.response.send_message("只能在伺服器使用",ephemeral=True)
        try:
            team=await asyncio.to_thread(treasury.find_team,interaction.guild.id,interaction.user.id,self.character_id)
            if not team: raise ValueError("尚未加入團隊")
            total=await asyncio.to_thread(treasury.balance,interaction.guild.id,interaction.user.id,self.character_id,team['id'])
            await interaction.response.send_message(f"🏦 {team['name']}｜團隊金庫：**{total:,} 金幣**",ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.response.send_message(f"⚠️ {exc}",ephemeral=True)
        except Exception:
            LOG.exception("直播團隊金庫查詢失敗")
            await interaction.response.send_message("⚠️ 系統錯誤",ephemeral=True)

    @discord.ui.button(label="存入金庫",emoji="📥",row=1)
    async def deposit(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._modal(interaction,"deposit")

    @discord.ui.button(label="提出提款",emoji="📤",row=1)
    async def withdraw(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._modal(interaction,"withdraw")

    @discord.ui.button(label="審核提款",emoji="🗳️",row=1)
    async def approve(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self._modal(interaction,"approve")

    @discord.ui.button(label="查看待審提款",emoji="📋",row=2)
    async def pending(self,interaction:discord.Interaction,button:discord.ui.Button):
        if interaction.guild is None: return await interaction.response.send_message("只能在伺服器使用",ephemeral=True)
        try:
            rows=await asyncio.to_thread(treasury.pending_withdrawals,interaction.guild.id,interaction.user.id,self.character_id)
            message="\n".join(f"#{r['id']}：{r['amount']:,} 金幣 → 角色 #{r['recipient_character_id']}" for r in rows) or "沒有待審提款"
            await interaction.response.send_message(message[:1900],ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.response.send_message(f"⚠️ {exc}",ephemeral=True)
        except Exception:
            LOG.exception("查詢待審提款失敗")
            await interaction.response.send_message("⚠️ 系統錯誤",ephemeral=True)


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
            "🌐 **網路中心**\n選擇角色後，使用按鈕操作直播、團播邀請及真實禮物。"
            "\n其他社群功能將整合在這個面板，不會新增斜線子指令。" + note,
            view=NetworkView(interaction.user.id, chars), ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(LiveStreamCog(bot))
