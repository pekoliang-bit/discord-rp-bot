
"""公司／集團選擇器：只顯示目前伺服器正式啟用的商業主體。"""
from __future__ import annotations
import discord
from core.company_public_ids import list_companies


class CompanyChooserView(discord.ui.View):
    def __init__(self, guild_id, user_id, on_selected, *, kinds=('company',), title='選擇公司'):
        super().__init__(timeout=300)
        self.gid, self.uid = str(guild_id), str(user_id)
        self.on_selected = on_selected
        self.title = title
        self.kinds = tuple(kinds)
        self.companies = list_companies(self.gid, self.kinds)
        self.page = 0
        self.render()

    def render(self):
        self.clear_items()
        if not self.companies:
            return
        start = self.page * 25
        page = self.companies[start:start + 25]
        opts = []
        for internal_id, name, kind, code in page:
            opts.append(discord.SelectOption(
                label=f'{name}'[:100],
                value=internal_id,
                description=f'{"集團" if kind == "group" else "公司"}｜{code}'[:100],
            ))
        select = discord.ui.Select(placeholder='選擇已成立的公司／集團', options=opts, row=0)

        async def choose(interaction):
            selected = select.values[0]
            if selected not in {item[0] for item in list_companies(self.gid, self.kinds)}:
                await interaction.response.send_message('公司選項已失效，請重新開啟。', ephemeral=True)
                return
            outcome = self.on_selected(selected)
            if isinstance(outcome, discord.ui.Modal):
                await interaction.response.send_modal(outcome)
            else:
                await outcome(interaction)

        select.callback = choose
        self.add_item(select)
        if len(self.companies) > 25:
            prev = discord.ui.Button(label='◀ 上一頁', style=discord.ButtonStyle.secondary,
                                     disabled=self.page == 0, row=1)
            nxt = discord.ui.Button(label='下一頁 ▶', style=discord.ButtonStyle.secondary,
                                    disabled=(self.page + 1)*25 >= len(self.companies), row=1)

            async def move(interaction, delta):
                self.page += delta
                self.render()
                await interaction.response.edit_message(view=self)

            prev.callback = lambda interaction: move(interaction, -1)
            nxt.callback = lambda interaction: move(interaction, 1)
            self.add_item(prev)
            self.add_item(nxt)

    async def interaction_check(self, interaction):
        if str(interaction.guild_id) != self.gid or str(interaction.user.id) != self.uid:
            await interaction.response.send_message('這不是你的公司選擇介面。', ephemeral=True)
            return False
        return True


async def open_company_chooser(interaction, user_id, modal_type, *, kinds=('company',)):
    try:
        chooser = CompanyChooserView(interaction.guild_id, user_id,
                                     lambda company: modal_type(user_id, interaction.guild_id, company), kinds=kinds)
    except Exception:
        await interaction.response.send_message('公司清單暫時無法取得，請稍後再試。', ephemeral=True)
        return
    if not chooser.companies:
        await interaction.response.send_message('這個伺服器目前沒有可選擇的正式公司／集團。', ephemeral=True)
        return
    await interaction.response.send_message('請先從下拉選單選擇公司，再填寫表單。', view=chooser, ephemeral=True)
