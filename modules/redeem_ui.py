"""B09 redemption buttons for /查看 and /官方. Uses modules.redeem as the sole transaction engine."""
from __future__ import annotations
import asyncio
import logging
import sqlite3
import discord
from core.database import get_connection
from core.permissions import Actor
from modules import redeem

LOG = logging.getLogger(__name__)


def _owner(interaction):
    return bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)


def _owned_character(interaction, cid):
    with get_connection() as db:
        row = db.execute(
            "SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?",
            (str(interaction.guild_id), int(cid)),
        ).fetchone()
    return bool(row and str(row['owner_id']) == str(interaction.user.id) and row['status'] == 'active')


def _bag_parent(parent):
    from modules.character_view import PanelSubView
    return PanelSubView(parent, 'bag', 0)


def _embed(title, text):
    return discord.Embed(title=title, description=str(text)[:3900], color=discord.Color.blurple())


class ApplyModal(discord.ui.Modal, title='🎁 申請特殊憑證兌換'):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.product = discord.ui.TextInput(label='B09 商品數字編號', placeholder='例如：125', max_length=12)
        self.desired = discord.ui.TextInput(label='申請內容／希望變更的名稱', style=discord.TextStyle.paragraph, max_length=180)
        self.add_item(self.product)
        self.add_item(self.desired)

    async def on_submit(self, interaction):
        if not _owned_character(interaction, self.parent.character_id):
            await interaction.response.send_message('⛔ 只能替自己已啟用的角色申請。', ephemeral=True)
            return
        try:
            pid = int(str(self.product.value).strip())
            if pid <= 0:
                raise ValueError('商品編號必須大於零。')
            result = await asyncio.to_thread(
                redeem.submit, str(interaction.guild_id), str(interaction.user.id),
                self.parent.character_id, pid, str(self.desired.value),
            )
            text = result + '\n\n' + await asyncio.to_thread(
                redeem.list_requests, str(interaction.guild_id),
                str(interaction.user.id), self.parent.character_id,
            )
            await interaction.response.edit_message(
                content=None, embed=_embed('🎁 我的兌換紀錄', text),
                view=PlayerRedeemView(self.parent),
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
        except Exception:
            LOG.exception('B09 面板兌換申請失敗')
            await interaction.response.send_message('⚠️ 申請失敗，請查看 Railway 日誌。', ephemeral=True)


class PlayerRedeemView(discord.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=900)
        self.parent = parent

    async def interaction_check(self, interaction):
        return await self.parent.interaction_check(interaction)

    @discord.ui.button(label='🎁 申請兌換', style=discord.ButtonStyle.success, row=0)
    async def apply(self, interaction, button):
        if not _owned_character(interaction, self.parent.character_id):
            await interaction.response.send_message('⛔ 只能操作自己的背包。', ephemeral=True)
            return
        await interaction.response.send_modal(ApplyModal(self.parent))

    @discord.ui.button(label='📋 我的紀錄', style=discord.ButtonStyle.primary, row=0)
    async def history(self, interaction, button):
        result = await asyncio.to_thread(
            redeem.list_requests, str(interaction.guild_id), str(interaction.user.id),
            self.parent.character_id,
        )
        await interaction.response.edit_message(
            content=None, embed=_embed('🎁 我的兌換紀錄', result), view=PlayerRedeemView(self.parent),
        )

    @discord.ui.button(label='🎒 返回背包', style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await _bag_parent(self.parent).show(interaction, 'bag', 0)


async def show_player(interaction, parent):
    if not _owned_character(interaction, parent.character_id):
        await interaction.response.send_message('⛔ 只能操作自己的背包。', ephemeral=True)
        return
    with get_connection() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='b09_catalog'").fetchone()
        rows = db.execute(
            """SELECT p.id,p.name,i.quantity FROM shop_inventory i
            JOIN b09_catalog b ON b.guild_id=i.guild_id AND b.product_id=i.product_id
            JOIN shop_products p ON p.guild_id=i.guild_id AND p.id=i.product_id
            WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0 ORDER BY p.id LIMIT 15""",
            (str(interaction.guild_id), parent.character_id),
        ).fetchall() if exists else []
    contents = '\n'.join(f"#{r['id']} {r['name']} ×{r['quantity']}" for r in rows) or '目前沒有 B09 特殊憑證。'
    await interaction.response.edit_message(
        content=None,
        embed=_embed('🎁 特殊道具兌換', f'**持有憑證**\n{contents}\n\n申請時不扣券，核准完成才扣除。'),
        view=PlayerRedeemView(parent),
    )


class ReviewModal(discord.ui.Modal, title='🎁 審核 B09 兌換'):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.request = discord.ui.TextInput(label='申請編號', max_length=12)
        self.note = discord.ui.TextInput(label='審核備註（其他類型完成時必填）', required=False,
                                         style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.request)
        self.add_item(self.note)

    async def on_submit(self, interaction):
        if not _owner(interaction):
            await interaction.response.send_message('⛔ 只有群主能審核。', ephemeral=True)
            return
        try:
            req_id = int(str(self.request.value).strip())
            if req_id <= 0:
                raise ValueError('申請編號必須大於零。')
            result = await asyncio.to_thread(
                redeem.review, str(interaction.guild_id), str(interaction.user.id),
                req_id, self.operation, str(self.note.value or ''),
            )
            pending = await asyncio.to_thread(
                redeem.list_requests, str(interaction.guild_id), str(interaction.user.id), None, True,
            )
            await interaction.response.edit_message(
                content=None, embed=_embed('🎁 兌換審核', result + '\n\n**待審**\n' + pending),
                view=AdminRedeemView(interaction.user.id, interaction.guild_id),
            )
        except (ValueError, sqlite3.Error) as exc:
            await interaction.response.send_message(f'⚠️ {exc}', ephemeral=True)
        except Exception:
            LOG.exception('B09 面板兌換審核失敗')
            await interaction.response.send_message('⚠️ 審核失敗，請查看 Railway 日誌。', ephemeral=True)


class AdminRedeemView(discord.ui.View):
    def __init__(self, user_id, guild_id):
        super().__init__(timeout=900)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)

    async def interaction_check(self, interaction):
        if (interaction.user.id != self.user_id or interaction.guild_id != self.guild_id or not _owner(interaction)):
            await interaction.response.send_message('⛔ 這不是你的群主審核面板。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='🔎 刷新待審', style=discord.ButtonStyle.primary, row=0)
    async def refresh(self, interaction, button):
        await show_admin(interaction)

    @discord.ui.button(label='✅ 完成', style=discord.ButtonStyle.success, row=0)
    async def approve(self, interaction, button):
        await interaction.response.send_modal(ReviewModal('完成'))

    @discord.ui.button(label='↩️ 退回', style=discord.ButtonStyle.danger, row=0)
    async def reject(self, interaction, button):
        await interaction.response.send_modal(ReviewModal('退回'))

    @discord.ui.button(label='🏛️ 返回官方', style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        from modules.official_center import OfficialHome
        await interaction.response.edit_message(content='🏛️ **官方人員管理中心**', embed=None,
                                                view=OfficialHome(interaction))


async def show_admin(interaction):
    if not _owner(interaction):
        await interaction.response.send_message('⛔ 只有群主能審核 B09 兌換。', ephemeral=True)
        return
    result = await asyncio.to_thread(
        redeem.list_requests, str(interaction.guild_id), str(interaction.user.id), None, True,
    )
    await interaction.response.edit_message(
        content=None, embed=_embed('🎁 B09 待審申請', result),
        view=AdminRedeemView(interaction.user.id, interaction.guild_id),
    )


class RedeemManageButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='🎁 兌換審核', style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction):
        await show_admin(interaction)

async def setup(bot):
    LOG.info('B09 兌換面板模組已載入（不註冊指令）。')
