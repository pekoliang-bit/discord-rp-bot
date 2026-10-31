"""Discord RP 陣營互動介面。

檔案位置：modules/faction_ui.py

唯一斜線指令：
    /陣營

功能：
- 陣營首頁
- 查看五個預設陣營
- 查看陣營經費與內部排行榜
- 選擇自己的角色
- 替角色加入初始陣營
- 向所屬陣營供奉金幣
- 查看商店及經費規則

不建立其他陣營斜線指令。
"""

from __future__ import annotations

import discord

from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.economy import EconomyError

from modules import faction_core
from modules.faction_donations import donate, item_options, donate_item
from modules import faction_membership


def owned_characters(guild_id, user_id):
    """查詢玩家擁有的已啟用原創角色。"""

    with get_connection() as db:

        return db.execute(
            """
            SELECT
                c.id,
                c.name,
                m.faction_id
            FROM characters AS c
            LEFT JOIN faction_members AS m
              ON m.guild_id = c.guild_id
             AND m.character_id = c.id
            WHERE c.guild_id = ?
              AND c.owner_id = ?
              AND c.status = 'active'
              AND c.character_type = 'player'
            ORDER BY c.id
            """,
            (
                str(guild_id),
                str(user_id),
            ),
        ).fetchall()


def home_text(guild_id):
    """產生陣營首頁文字。"""

    factions = faction_core.list_factions(
        guild_id
    )

    lines = []

    for _, name, pool in factions:

        safe_name = discord.utils.escape_markdown(
            str(name)
        )

        lines.append(
            f"• **{safe_name}**"
            f"｜經費池 {pool:,} 金幣"
        )

    faction_text = "\n".join(lines)

    return (
        "**⚔️ 陣營中心**\n\n"
        f"{faction_text}\n\n"
        "每個角色獨立選擇陣營。\n"
        "每 100 金幣有效入池資金 = 1 積分。\n"
        "退出或轉換陣營的違約金"
        "會進入原陣營經費池，但不計積分。"
    )


class PrivateView(discord.ui.View):
    """限制面板只能由開啟者操作。"""

    def __init__(
        self,
        guild_id,
        user_id,
    ):
        super().__init__(timeout=180)

        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(
        self,
        interaction,
    ):

        if interaction.user.id != self.user_id:

            await interaction.response.send_message(
                "這不是你的陣營操作面板，"
                "請自行使用 /陣營。",
                ephemeral=True,
            )

            return False

        return True


class FactionDetail(PrivateView):
    """陣營資訊與內部排行榜。"""

    def __init__(
        self,
        guild_id,
        user_id,
    ):
        super().__init__(
            guild_id,
            user_id,
        )

        factions = faction_core.list_factions(
            guild_id
        )

        choices = [
            discord.SelectOption(
                label=name[:100],
                value=str(fid),
            )
            for fid, name, _ in factions
        ][:25]

        selector = discord.ui.Select(
            placeholder="選擇要查看的陣營",
            options=choices,
        )

        selector.callback = self.show

        self.selector = selector
        self.add_item(selector)

    async def show(self, interaction):

        try:

            info = faction_core.snapshot(
                self.guild_id,
                int(self.selector.values[0]),
            )

        except ValueError as exc:

            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )

            return

        rank_lines = []

        for index, entry in enumerate(
            info["rank"],
            start=1,
        ):

            cid, name, points = entry

            safe_name = (
                discord.utils.escape_markdown(
                    str(name)
                )
            )

            rank_lines.append(
                f"{index}. {safe_name}"
                f"（角色 #{cid}）"
                f" — {points:,} 分"
            )

        rank = (
            "\n".join(rank_lines)
            if rank_lines
            else "本期尚無有效貢獻。"
        )

        safe_faction_name = (
            discord.utils.escape_markdown(
                str(info["name"])
            )
        )

        await interaction.response.edit_message(
            content=(
                f"**{safe_faction_name}**\n\n"
                f"經費池："
                f"{info['pool']:,} 金幣\n"
                f"目前第 {info['period']} 期\n\n"
                "**內部排行榜**\n"
                f"{rank}"
            ),
            view=self,
        )

    @discord.ui.button(
        label="返回陣營首頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content=home_text(
                self.guild_id
            ),
            view=FactionHome(
                self.guild_id,
                self.user_id,
            ),
        )


class JoinFaction(PrivateView):
    """角色初次加入陣營的選單。"""

    def __init__(
        self,
        guild_id,
        user_id,
        character_id,
    ):
        super().__init__(
            guild_id,
            user_id,
        )

        self.character_id = character_id

        factions = faction_core.list_factions(
            guild_id
        )

        choices = [
            discord.SelectOption(
                label=name[:100],
                value=str(fid),
            )
            for fid, name, _ in factions
        ][:25]

        selector = discord.ui.Select(
            placeholder="替角色選擇初始陣營",
            options=choices,
        )

        selector.callback = self.join

        self.selector = selector
        self.add_item(selector)

    async def join(self, interaction):

        try:

            faction_core.join(
                self.guild_id,
                self.character_id,
                int(self.selector.values[0]),
                self.user_id,
            )

        except ValueError as exc:

            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )

            return

        await interaction.response.edit_message(
            content=(
                f"角色 #{self.character_id}"
                " 已成功加入陣營！\n\n"
                + home_text(self.guild_id)
            ),
            view=FactionHome(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="返回角色選擇",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content="請選擇要加入陣營的角色：",
            view=CharacterSelection(
                self.guild_id,
                self.user_id,
            ),
        )


class CharacterSelection(PrivateView):
    """角色選擇：支援分頁與已加入角色的退出、轉換。"""

    def __init__(self, guild_id, user_id, page=0):
        super().__init__(guild_id, user_id)
        rows = owned_characters(guild_id, user_id)
        self.page = max(0, min(page, max(0, (len(rows)-1)//25)))
        page_rows = rows[self.page*25:(self.page+1)*25]
        if page_rows:
            selector = discord.ui.Select(
                placeholder="選擇角色",
                options=[discord.SelectOption(
                    label=str(row["name"])[:90],
                    description=f"角色 #{row['id']}｜" + ("已有陣營" if row["faction_id"] is not None else "尚未加入"),
                    value=str(row["id"]),
                ) for row in page_rows],
            )
            selector.callback = self.choose
            self.selector = selector
            self.add_item(selector)
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page+1)*25 >= len(rows)

    async def choose(self, interaction):
        cid = int(self.selector.values[0])
        row = next((r for r in owned_characters(self.guild_id, self.user_id)
                    if int(r["id"]) == cid), None)
        if row is None:
            await interaction.response.send_message("角色已無法操作，請重新開啟 /陣營。", ephemeral=True)
            return
        if row["faction_id"] is None:
            view = JoinFaction(self.guild_id, self.user_id, cid)
            content = f"替角色 #{cid} 選擇初始陣營："
        else:
            view = MembershipActions(self.guild_id, self.user_id, cid, int(row["faction_id"]))
            content = f"角色 #{cid} 已加入陣營，請選擇操作："
        await interaction.response.edit_message(content=content, view=view)

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        await interaction.response.edit_message(content="請選擇角色：", view=CharacterSelection(self.guild_id, self.user_id, self.page-1))

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction, button):
        await interaction.response.edit_message(content="請選擇角色：", view=CharacterSelection(self.guild_id, self.user_id, self.page+1))

    @discord.ui.button(label="返回首頁", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content=home_text(self.guild_id), view=FactionHome(self.guild_id, self.user_id))


class MembershipActions(PrivateView):
    def __init__(self, guild_id, user_id, character_id, old_faction_id):
        super().__init__(guild_id, user_id)
        self.character_id = character_id
        self.old_faction_id = old_faction_id

    @discord.ui.button(label="退出陣營", style=discord.ButtonStyle.danger)
    async def leave(self, interaction, button):
        await show_membership_confirmation(interaction, self.guild_id, self.user_id, self.character_id, self.old_faction_id, None)

    @discord.ui.button(label="轉換陣營", style=discord.ButtonStyle.primary)
    async def transfer(self, interaction, button):
        await interaction.response.edit_message(content="請選擇目標陣營：", view=TransferFaction(self.guild_id, self.user_id, self.character_id, self.old_faction_id))

    @discord.ui.button(label="返回角色列表", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content="請選擇角色：", view=CharacterSelection(self.guild_id, self.user_id))


class TransferFaction(PrivateView):
    def __init__(self, guild_id, user_id, character_id, old_faction_id):
        super().__init__(guild_id, user_id)
        self.character_id = character_id
        self.old_faction_id = old_faction_id
        choices = [discord.SelectOption(label=str(name)[:100], value=str(fid))
                   for fid, name, _ in faction_core.list_factions(guild_id)
                   if int(fid) != old_faction_id][:25]
        if choices:
            selector = discord.ui.Select(placeholder="選擇新陣營", options=choices)
            selector.callback = self.choose
            self.selector = selector
            self.add_item(selector)

    async def choose(self, interaction):
        await show_membership_confirmation(interaction, self.guild_id, self.user_id, self.character_id, self.old_faction_id, int(self.selector.values[0]))

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content="請選擇操作：", view=MembershipActions(self.guild_id, self.user_id, self.character_id, self.old_faction_id))


async def show_membership_confirmation(interaction, guild_id, user_id, character_id, old_faction_id, new_faction_id):
    try:
        penalty = faction_membership.get_penalty(guild_id, old_faction_id)
        if penalty is None:
            raise ValueError("原陣營尚未設定退出規則，暫時不能退出或轉換。")
        factions = {int(fid): name for fid, name, _ in faction_core.list_factions(guild_id)}
        if old_faction_id not in factions or (new_faction_id is not None and new_faction_id not in factions):
            raise ValueError("陣營資料已變更，請重新操作。")
        old_name = discord.utils.escape_markdown(str(factions[old_faction_id]))
        new_name = discord.utils.escape_markdown(str(factions[new_faction_id])) if new_faction_id is not None else "無（退出）"
        await interaction.response.edit_message(
            content=(f"**確認陣營異動**\n角色：#{character_id}\n原陣營：{old_name}\n目標：{new_name}\n"
                     f"**違約金：{penalty:,} 金幣**\n違約金進入原陣營金庫，不增加積分。\n"
                     "請確認後才會扣款；若金額變動，交易將被拒絕。"),
            view=MembershipConfirm(guild_id, user_id, character_id, old_faction_id, new_faction_id, penalty),
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)


class MembershipConfirm(PrivateView):
    def __init__(self, guild_id, user_id, character_id, old_faction_id, new_faction_id, penalty):
        super().__init__(guild_id, user_id)
        self.character_id = character_id
        self.old_faction_id = old_faction_id
        self.new_faction_id = new_faction_id
        self.penalty = penalty

    @discord.ui.button(label="確認退出／轉換", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            result = faction_membership.change_membership(
                self.guild_id, self.character_id, self.user_id,
                str(interaction.id), new_faction_id=self.new_faction_id,
                expected_penalty=self.penalty,
            )
            await interaction.edit_original_response(
                content=(f"陣營異動完成！違約金 {result['penalty']:,} 金幣，積分 +0。\n\n"
                         + home_text(self.guild_id)),
                view=FactionHome(self.guild_id, self.user_id),
            )
        except (ValueError, EconomyError) as exc:
            await interaction.followup.send(f"操作失敗：{exc}", ephemeral=True)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="已取消陣營異動。", view=FactionHome(self.guild_id, self.user_id))




class FactionItemDonateModal(discord.ui.Modal, title="供奉背包物品"):
    quantity = discord.ui.TextInput(label="供奉數量", placeholder="例如：1", max_length=6)
    def __init__(self,guild_id,user_id,character_id,product_id,unit_price):
        super().__init__()
        self.guild_id,self.user_id,self.character_id,self.product_id,self.unit_price=(
            guild_id,user_id,character_id,product_id,unit_price)

    async def on_submit(self,interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message('只能操作自己的角色。',ephemeral=True)
        try:
            qty=int(str(self.quantity.value).strip())
            if qty<=0: raise ValueError()
            result=donate_item(self.guild_id,self.character_id,self.user_id,
                               self.product_id,qty,str(interaction.id))
        except (ValueError,EconomyError) as exc:
            return await interaction.response.send_message(f'供奉失敗：{exc}',ephemeral=True)
        await interaction.response.send_message(
            f"物品供奉成功：{result['quantity']} 件，折算 {result['amount']:,} 金幣，"
            f"本次獲得 {result['points']} 積分。",ephemeral=True)


class FactionItemDonateSelect(discord.ui.View):
    def __init__(self,guild_id,user_id,character_id,page=0):
        super().__init__(timeout=180)
        self.guild_id,self.user_id,self.character_id,self.page=guild_id,user_id,character_id,page
        items=item_options(guild_id,character_id,user_id,limit=25,offset=page*25)
        if items:
            options=[discord.SelectOption(label=str(r['name'])[:85],
              description=f"持有 {r['quantity']}｜折算單價 {r['price']} 金幣",
              value=str(r['product_id'])) for r in items]
            select=discord.ui.Select(placeholder='選擇要供奉的物品',options=options,row=0)
            async def selected(interaction):
                if interaction.user.id!=self.user_id:
                    return await interaction.response.send_message('只能操作自己的角色。',ephemeral=True)
                pid=int(select.values[0]); matched=next(r for r in items if int(r['product_id'])==pid)
                await interaction.response.send_modal(FactionItemDonateModal(
                    self.guild_id,self.user_id,self.character_id,pid,int(matched['price'])))
            select.callback=selected
            self.add_item(select)
        if page:
            b=discord.ui.Button(label='◀️',row=1)
            async def previous(interaction):
                await interaction.response.edit_message(view=FactionItemDonateSelect(
                  self.guild_id,self.user_id,self.character_id,self.page-1))
            b.callback=previous;self.add_item(b)
        if len(items)==25:
            b=discord.ui.Button(label='▶️',row=1)
            async def nxt(interaction):
                await interaction.response.edit_message(view=FactionItemDonateSelect(
                  self.guild_id,self.user_id,self.character_id,self.page+1))
            b.callback=nxt;self.add_item(b)

class DonationModal(
    discord.ui.Modal,
    title="向所屬陣營供奉",
):
    """供奉金額輸入表單。"""

    amount = discord.ui.TextInput(
        label="供奉金額（金幣）",
        placeholder="例如：12500",
        max_length=16,
        required=True,
    )

    def __init__(
        self,
        guild_id,
        user_id,
        character_id,
    ):
        super().__init__()

        self.guild_id = guild_id
        self.user_id = user_id
        self.character_id = character_id

    async def on_submit(
        self,
        interaction,
    ):

        if interaction.user.id != self.user_id:

            await interaction.response.send_message(
                "只能操作自己的角色。",
                ephemeral=True,
            )

            return

        try:

            amount = int(
                str(self.amount.value).strip()
            )

        except ValueError:

            await interaction.response.send_message(
                "請輸入正整數金額。",
                ephemeral=True,
            )

            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:

            result = donate(
                self.guild_id,
                self.character_id,
                self.user_id,
                amount,
                str(interaction.id),
            )

        except (
            ValueError,
            EconomyError,
        ) as exc:

            await interaction.followup.send(
                f"供奉失敗：{exc}",
                ephemeral=True,
            )

            return

        if result["duplicated"]:

            message = (
                "這筆交易已經處理過，"
                "沒有重複扣款。"
            )

        else:

            message = "供奉成功！"

        await interaction.followup.send(
            (
                f"**{message}**\n"
                f"經費入池："
                f"{result['amount']:,} 金幣\n"
                f"本次增加積分："
                f"{result['points']:,}"
            ),
            ephemeral=True,
        )


class DonationCharacterSelection(PrivateView):
    """選擇要供奉經費的角色。"""

    def __init__(
        self,
        guild_id,
        user_id,
        page=0,
    ):
        super().__init__(
            guild_id,
            user_id,
        )

        rows = [
            row
            for row in owned_characters(
                guild_id,
                user_id,
            )
            if row["faction_id"] is not None
        ]

        self.page = max(0, min(page, max(0, (len(rows)-1)//25)))
        page_rows = rows[self.page*25:(self.page+1)*25]

        if page_rows:

            options = [
                discord.SelectOption(
                    label=str(row["name"])[:90],
                    description=(
                        f"角色 #{row['id']}"
                    ),
                    value=str(row["id"]),
                )
                for row in page_rows
            ]

            selector = discord.ui.Select(
                placeholder="選擇要供奉的角色",
                options=options,
            )

            selector.callback = self.choose

            self.selector = selector
            self.add_item(selector)

        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page+1)*25 >= len(rows)

    async def choose(self, interaction):

        cid = int(
            self.selector.values[0]
        )

        rows = owned_characters(
            self.guild_id,
            self.user_id,
        )

        valid = any(
            int(row["id"]) == cid
            and row["faction_id"] is not None
            for row in rows
        )

        if not valid:

            await interaction.response.send_message(
                "角色不符合供奉資格。",
                ephemeral=True,
            )

            return

        await interaction.response.send_modal(
            DonationModal(
                self.guild_id,
                self.user_id,
                cid,
            )
        )

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        await interaction.response.edit_message(content="選擇供奉角色：", view=DonationCharacterSelection(self.guild_id, self.user_id, self.page-1))

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction, button):
        await interaction.response.edit_message(content="選擇供奉角色：", view=DonationCharacterSelection(self.guild_id, self.user_id, self.page+1))

    @discord.ui.button(
        label="返回陣營首頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content=home_text(
                self.guild_id
            ),
            view=FactionHome(
                self.guild_id,
                self.user_id,
            ),
        )


class FactionHome(PrivateView):
    """統一的 /陣營 首頁。"""

    @discord.ui.button(
        label="我的角色／加入／退出／轉換",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def join(
        self,
        interaction,
        button,
    ):

        rows = owned_characters(
            self.guild_id,
            self.user_id,
        )

        if not rows:

            await interaction.response.send_message(
                "目前沒有已啟用的原創角色。",
                ephemeral=True,
            )

            return

        await interaction.response.edit_message(
            content=(
                "請選擇要操作的角色。\n"
                "已加入陣營的角色可以退出或轉換。"
            ),
            view=CharacterSelection(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="陣營資訊／內部排行",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def info(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content="請選擇要查看的陣營：",
            view=FactionDetail(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="供奉陣營經費",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def donate_button(
        self,
        interaction,
        button,
    ):

        rows = [
            row
            for row in owned_characters(
                self.guild_id,
                self.user_id,
            )
            if row["faction_id"] is not None
        ]

        if not rows:

            await interaction.response.send_message(
                "沒有已加入陣營的角色。",
                ephemeral=True,
            )

            return

        await interaction.response.edit_message(
            content=(
                "請選擇要供奉的角色。\n"
                "供奉會從角色錢包扣款，"
                "並增加所屬陣營的經費。"
            ),
            view=DonationCharacterSelection(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="商店與經費說明",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def shop(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            (
                "**🛍️ 陣營商店與經費**\n\n"
                "**一般商店**\n"
                "玩家使用個人資金購買"
                "道具與個人加成。\n\n"
                "**專屬商店**\n"
                "當期第一名與第二名"
                "可使用陣營經費，"
                "購買全員正向加成。\n\n"
                "**暗害與防護**\n"
                "相關行動需要支付資金，"
                "詳細規則尚待實作。\n\n"
                "目前商店交易尚未開放，"
                "不會扣除任何資金。"
            ),
            ephemeral=True,
        )




class FactionWalletCheckout(discord.ui.View):
    """玩家本人按確認後才正式扣款；逾期未按不會產生扣款。"""
    def __init__(self, guild_id, user_id, order_id):
        super().__init__(timeout=180)
        self.guild_id = str(guild_id)
        self.user_id = int(user_id)
        self.order_id = int(order_id)

    @discord.ui.button(label="確認付款並領取道具", style=discord.ButtonStyle.success)
    async def pay(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("只有訂單本人可以付款。", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from modules.faction_shop_payments import settle_order
            result = settle_order(self.guild_id, self.order_id, str(self.user_id))
        except (ValueError, TypeError) as exc:
            return await interaction.followup.send(f"付款未完成：{exc}", ephemeral=True)
        for item in self.children:
            item.disabled = True
        message = (f"訂單 #{self.order_id} 已完成，商品已放入角色背包。"
                   if result.get('delivered') else f"訂單 #{self.order_id} 已經結算過，沒有重複扣款。")
        await interaction.followup.send(message, ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except (discord.HTTPException, AttributeError):
            pass

    @discord.ui.button(label="取消訂單", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("只有訂單本人可以取消。", ephemeral=True)
        try:
            from modules.faction_shop_orders import cancel_request
            cancel_request(self.guild_id, self.order_id, str(self.user_id))
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content=f"訂單 #{self.order_id} 已取消，沒有扣款。", view=self)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)


class FactionShopRequestModal(discord.ui.Modal, title="陣營商店｜提出購買申請"):
    quantity = discord.ui.TextInput(label="購買數量（1～99）", default="1", max_length=2)

    def __init__(self, guild_id, user_id, character_id, item_id):
        super().__init__()
        self.guild_id = guild_id
        self.user_id = user_id
        self.character_id = int(character_id)
        self.item_id = int(item_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(str(self.quantity.value).strip())
            from modules.faction_shop_orders import request_purchase
            order = request_purchase(self.guild_id, self.character_id,
                                     self.user_id, self.item_id, qty,
                                     f"discord:{interaction.id}")
            if order['currency'] == 'wallet' and order['effect_type'] == 'item':
                await interaction.response.send_message(
                    f"購買確認｜訂單 #{order['id']}\n商品 #{self.item_id} × {qty}\n"
                    f"應付：{order['total_price']:,} 金幣（角色錢包）\n"
                    "確認後才會扣款、扣庫存並發貨；如果無法交貨，整筆交易會回滾。",
                    view=FactionWalletCheckout(self.guild_id, self.user_id, order['id']),
                    ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"已建立待審訂單 #{order['id']}\n商品 #{self.item_id} × {qty}\n"
                    f"總價：{order['total_price']:,} 金幣\n"
                    "此類訂單尚未開放自動結算，沒有扣款、扣庫存或發貨。",
                    ephemeral=True)
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"無法建立訂單：{exc}", ephemeral=True)


class FactionShopProductSelect(discord.ui.Select):
    def __init__(self, guild_id, user_id, character_id, items):
        self.guild_id = guild_id
        self.user_id = user_id
        self.character_id = int(character_id)
        options = [discord.SelectOption(label=str(item['name'])[:80],
                    description=f"#{item['id']}｜{int(item['price']):,} 金幣"[:100],
                    value=str(item['id'])) for item in items]
        super().__init__(placeholder="選擇商品（符合資格的普通道具可直接購買）", min_values=1, max_values=1,
                         options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FactionShopRequestModal(
            self.guild_id, self.user_id, self.character_id, int(self.values[0])))


# 新版 /陣營：依角色顯示自己的陣營；原有入退轉 UI 保留但不再從首頁提供。
class MyFactionPanel(PrivateView):
    def __init__(self, guild_id, user_id, character_id, section="home", page=0, shop_type="general"):
        super().__init__(guild_id, user_id)
        self.character_id = int(character_id)
        self.section = section
        self.page = max(0, int(page))
        self.shop_type = shop_type
        if section == "home":
            for label, target, style, r in [
                ("積分排行", "rank", discord.ButtonStyle.primary, 0),
                ("成員名單", "members", discord.ButtonStyle.secondary, 0),
                ("商店", "shop", discord.ButtonStyle.success, 1),
                ("經費情況", "funds", discord.ButtonStyle.secondary, 1),
            ]:
                b = discord.ui.Button(label=label, style=style, row=r)
                async def callback(interaction, dest=target):
                    await self.navigate(interaction, dest)
                b.callback = callback
                self.add_item(b)
        else:
            if section == "shop":
                try:
                    from modules.faction_shop import list_items
                    row, fid = self.current_faction()
                    items = list_items(self.guild_id, fid, self.shop_type, limit=8, offset=self.page*8)
                    # 專屬商店付款規則與雙人核准相衝突，暫不受理訂單。
                    if self.shop_type == "exclusive":
                        items = []
                    if items:
                        self.add_item(FactionShopProductSelect(self.guild_id, self.user_id, self.character_id, items))
                except (ValueError, KeyError):
                    pass
                b = discord.ui.Button(label="一般／專屬商店", row=0)
                async def change_shop(interaction):
                    kind = "exclusive" if self.shop_type == "general" else "general"
                    await self.navigate(interaction, "shop", 0, kind)
                b.callback = change_shop
                self.add_item(b)
            if section == "funds":
                b = discord.ui.Button(label="捐獻金幣", style=discord.ButtonStyle.success, row=0)
                async def contribute(interaction):
                    await interaction.response.send_modal(DonationModal(self.guild_id, self.user_id, self.character_id))
                b.callback = contribute
                self.add_item(b)
                b2 = discord.ui.Button(label="捐獻物品", style=discord.ButtonStyle.success, row=1)
                async def contribute_item(interaction):
                    if interaction.user.id != self.user_id:
                        return await interaction.response.send_message('只能操作自己的角色。',ephemeral=True)
                    try:
                        view=FactionItemDonateSelect(self.guild_id,self.user_id,self.character_id)
                        if not view.children:
                            return await interaction.response.send_message('背包沒有可供奉的普通物品。',ephemeral=True)
                        await interaction.response.send_message('選擇供奉物品；依當前官方商品價格折算。',view=view,ephemeral=True)
                    except ValueError as exc:
                        await interaction.response.send_message(str(exc),ephemeral=True)
                b2.callback=contribute_item
                self.add_item(b2)
            if section in ("rank", "members", "shop", "funds"):
                for label, offset in [("◀️", -1), ("▶️", 1)]:
                    b = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=2)
                    async def flip(interaction, delta=offset):
                        await self.navigate(interaction, self.section, max(0, self.page + delta))
                    b.callback = flip
                    self.add_item(b)
            back = discord.ui.Button(label="返回陣營首頁", row=3)
            async def go_back(interaction):
                await self.navigate(interaction, "home", 0)
            back.callback = go_back
            self.add_item(back)

    def current_faction(self):
        rows = owned_characters(self.guild_id, self.user_id)
        row = next((r for r in rows if int(r["id"]) == self.character_id), None)
        if row is None or row["faction_id"] is None:
            raise ValueError("角色已退出陣營或無操作權限，請重新使用 /陣營。")
        return row, int(row["faction_id"])

    def render(self):
        row, fid = self.current_faction()
        info = faction_core.snapshot(self.guild_id, fid)
        name = discord.utils.escape_markdown(str(info["name"]))
        header = f"**⚔️ {name}｜{row['name']}（#{self.character_id}）**\n"
        if self.section == "home":
            with get_connection() as db:
                count = db.execute("SELECT COUNT(*) FROM faction_members WHERE guild_id=? AND faction_id=?", (str(self.guild_id), fid)).fetchone()[0]
            return (header + f"成員：{count} 人\n經費池：{info['pool']:,} 金幣\n目前第 {info['period']} 期\n\n請選擇功能。")
        if self.section == "rank":
            entries = info["rank"]
            lines = [f"{idx + 1}. {discord.utils.escape_markdown(str(n))}（#{cid}）｜{int(points):,} 分" for idx, (cid,n,points) in enumerate(entries)]
            entries = lines[self.page*8:(self.page+1)*8]
            return header + f"**🏆 積分排行｜第 {self.page+1} 頁**\n" + ("\n".join(entries) if entries else "本頁沒有資料。") + "\n每 100 金幣有效入池資金 = 1 積分。"
        if self.section == "members":
            with get_connection() as db:
                members = db.execute("""SELECT c.id,c.name FROM faction_members m JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id WHERE m.guild_id=? AND m.faction_id=? ORDER BY c.id LIMIT 10 OFFSET ?""", (str(self.guild_id), fid, self.page*10)).fetchall()
            lines = [f"• {discord.utils.escape_markdown(str(m['name']))}（#{m['id']}）" for m in members]
            return header + f"**👥 成員名單｜第 {self.page+1} 頁**\n" + ("\n".join(lines) if lines else "本頁沒有成員。")
        if self.section == "shop":
            from modules.faction_shop import list_items
            items = list_items(self.guild_id, fid, self.shop_type, limit=8, offset=self.page*8)
            lines = [f"• #{i['id']} {discord.utils.escape_markdown(str(i['name']))}｜{int(i['price']):,} 金幣" for i in items]
            t = "一般商店" if self.shop_type == "general" else "專屬商店（老大／副手）"
            return header + f"**🛍️ {t}｜第 {self.page+1} 頁**\n" + ("\n".join(lines) if lines else "本頁沒有上架商品。") + "\n普通道具可在確認畫面付款；其他效果暫不開放結算。"
        if self.section == "funds":
            with get_connection() as db:
                entries = db.execute("""SELECT source,amount,created_at FROM faction_ledger WHERE guild_id=? AND faction_id=? ORDER BY id DESC LIMIT 8 OFFSET ?""", (str(self.guild_id), fid, self.page*8)).fetchall()
            lines = [f"• {e['created_at']}｜{e['source']}｜{int(e['amount']):,} 金幣" for e in entries]
            return header + f"**💰 經費情況｜第 {self.page+1} 頁**\n餘額：{info['pool']:,} 金幣\n" + ("\n".join(lines) if lines else "本頁沒有流水紀錄。")
        return header

    async def navigate(self, interaction, section, page=0, shop_type=None):
        panel = MyFactionPanel(self.guild_id, self.user_id, self.character_id, section, page, shop_type or self.shop_type)
        try:
            content = panel.render()
        except (ValueError, KeyError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(content=content[:2000], view=panel)


class FactionCharacterChooser(PrivateView):
    def __init__(self, guild_id, user_id, rows):
        super().__init__(guild_id,user_id)
        choices=[discord.SelectOption(label=str(r['name'])[:90], value=str(r['id']), description=f"角色 #{r['id']}") for r in rows[:25]]
        selector=discord.ui.Select(placeholder="選擇要查看的角色陣營",options=choices)
        async def chosen(interaction):
            cid=int(selector.values[0])
            panel=MyFactionPanel(guild_id,user_id,cid)
            await interaction.response.edit_message(content=panel.render(),view=panel)
        selector.callback=chosen
        self.add_item(selector)


class FactionCog(commands.Cog):
    """陣營系統唯一斜線指令。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="陣營",
        description=(
            "開啟陣營中心："
            "角色加入、經費、排行榜與商店"
        ),
    )
    @app_commands.guild_only()
    async def faction(
        self,
        interaction: discord.Interaction,
    ):

        rows = [r for r in owned_characters(interaction.guild_id, interaction.user.id) if r["faction_id"] is not None]
        if not rows:
            await interaction.response.send_message("目前選用的角色尚無陣營。加入、退出及轉換請至 /申請 辦理。", ephemeral=True)
            return
        if len(rows) > 1:
            await interaction.response.send_message("請選擇要查看的角色：", view=FactionCharacterChooser(interaction.guild_id, interaction.user.id, rows), ephemeral=True)
            return
        panel = MyFactionPanel(interaction.guild_id, interaction.user.id, int(rows[0]["id"]))
        await interaction.response.send_message(content=panel.render(), view=panel, ephemeral=True)


async def setup(bot):
    """Discord Cog 載入入口。"""

    faction_core.initialize()

    await bot.add_cog(
        FactionCog(bot)
    )
