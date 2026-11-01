"""世界觀／頻道連結：逐項挑選、確認、更換及解除綁定。

只保存世界觀用途的參考頻道，不建立頻道、不變更 Discord 權限，
也不會覆寫 /通知 系統自己的通知目的地設定。
"""
from __future__ import annotations
import discord

CHANNEL_SLOTS = (
    ("world_content", "📚 世界觀資料頻道", "集中提供世界觀介紹與資料索引。", "供玩家找到背景設定；不會自動發布任何消息。"),
    ("world_backup", "🗄️ 世界觀備援頻道", "主要資料頻道失效時的備援連結。", "僅記錄備用位置，不會自動複製訊息。"),
    ("world_rules", "📜 世界規則頻道", "集中展示玩家需要遵守的世界規則。", "不會代替 Discord 實際權限設定。"),
    ("world_regions", "🗺️ 地區資料頻道", "展示官方建立的國家、城市與地區資訊。", "不能讓玩家藉由指定頻道建立地區。"),
    ("world_story", "📖 世界故事資料頻道", "收錄世界歷史、故事設定與主線參考資料。", "與通知系統的『攻略劇情更新』目的地分開。"),
)
SLOTS = {item[0]: item for item in CHANNEL_SLOTS}


def parent():
    from modules import content_builder
    return content_builder


def get_slot(guild_id, slot):
    raw = parent().world_get(guild_id).get("channels", {}).get(slot)
    return str(raw) if raw is not None and str(raw).isdigit() else None


def channel_summary(guild_id):
    out = ["🔗 **世界觀｜頻道連結**", "逐項選擇用途，再指定伺服器內的文字頻道。"]
    for key, name, _, _ in CHANNEL_SLOTS:
        cid = get_slot(guild_id, key)
        out.append(f"{name}：{f'<#{cid}>' if cid else '未設定'}")
    out.extend(("", "ℹ️ 這些是世界觀資料索引，不是 `/通知` 的公告頻道。", "設定僅保存連結；機器人是否能閱讀／發訊仍由 Discord 頻道權限決定。"))
    return "\n".join(out)


def detail(guild_id, key):
    _, name, usage, impact = SLOTS[key]
    cid = get_slot(guild_id, key)
    return (f"🔗 **{name}**\n\n**用途：**{usage}\n**實際效果：**{impact}\n"
            f"**目前頻道：**{f'<#{cid}>' if cid else '尚未綁定'}\n\n"
            "**操作：**下方選擇器可指定或更換文字頻道；若需解除請點『解除綁定』並再次確認。\n"
            "**注意：**不會自動調整頻道權限、移動資料或刪除頻道。")


def allowed(i, uid, gid):
    return i.guild_id == gid and i.user.id == uid and parent().owner(i)


class SlotSelect(discord.ui.Select):
    def __init__(self, gid):
        opts = [discord.SelectOption(label=name, value=key, description="已設定" if get_slot(gid, key) else "未設定") for key, name, _, _ in CHANNEL_SLOTS]
        super().__init__(placeholder="選擇要管理的頻道用途", options=opts, row=0)

    async def callback(self, interaction):
        key = self.values[0]
        await interaction.response.edit_message(content=detail(interaction.guild_id, key), view=ChannelDetailView(interaction, key))


class ChannelHomeView(discord.ui.View):
    def __init__(self, interaction):
        super().__init__(timeout=600)
        self.uid, self.gid = interaction.user.id, interaction.guild_id
        self.add_item(SlotSelect(self.gid))

    async def interaction_check(self, interaction):
        if not allowed(interaction, self.uid, self.gid):
            await interaction.response.send_message("⛔ 只有開啟面板的群主可以修改設定。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀️ 返回世界觀", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        p = parent()
        await interaction.response.edit_message(content=p.world_summary(self.gid), view=p.WorldSettingsView(interaction))


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, key):
        super().__init__(placeholder="選擇要綁定的文字頻道", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=0)
        self.key = key

    async def callback(self, interaction):
        ch = self.values[0]
        if ch.guild.id != interaction.guild_id:
            await interaction.response.send_message("請選擇本伺服器的頻道。", ephemeral=True)
            return
        await interaction.response.edit_message(content=(detail(interaction.guild_id, self.key) + f"\n\n⚠️ **確定綁定 {ch.mention}？**"), view=ChannelBindConfirmView(interaction, self.key, ch.id))


class ChannelDetailView(discord.ui.View):
    def __init__(self, interaction, key):
        super().__init__(timeout=600)
        self.uid, self.gid, self.key = interaction.user.id, interaction.guild_id, key
        self.add_item(ChannelPicker(key))

    async def interaction_check(self, interaction):
        if not allowed(interaction, self.uid, self.gid):
            await interaction.response.send_message("⛔ 無權操作此面板。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="解除綁定", style=discord.ButtonStyle.danger, row=1)
    async def unbind(self, interaction, button):
        if not get_slot(self.gid, self.key):
            await interaction.response.send_message("這個用途尚未綁定頻道。", ephemeral=True)
            return
        await interaction.response.edit_message(content=detail(self.gid, self.key) + "\n\n⚠️ 確定解除此頻道連結？不會刪除 Discord 頻道或內容。", view=ChannelUnbindConfirmView(interaction, self.key))

    @discord.ui.button(label="◀️ 返回用途列表", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content=channel_summary(self.gid), view=ChannelHomeView(interaction))


class ChannelBindConfirmView(discord.ui.View):
    def __init__(self, interaction, key, channel_id):
        super().__init__(timeout=600)
        self.uid, self.gid, self.key, self.channel_id = interaction.user.id, interaction.guild_id, key, channel_id

    async def interaction_check(self, interaction):
        if not allowed(interaction, self.uid, self.gid):
            await interaction.response.send_message("⛔ 無權操作此面板。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="確認儲存", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        p = parent()
        channel = interaction.guild.get_channel(self.channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("找不到該文字頻道，請重新選擇。", ephemeral=True)
            return
        data = p.world_get(self.gid)
        data.setdefault("channels", {})[self.key] = str(self.channel_id)
        data.setdefault("overrides", {})[f"channels.{self.key}"] = True
        p.world_save(self.gid, data)
        await interaction.response.edit_message(content="✅ 頻道連結已保存。\n\n" + detail(self.gid, self.key), view=ChannelDetailView(interaction, self.key))

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content=detail(self.gid, self.key), view=ChannelDetailView(interaction, self.key))


class ChannelUnbindConfirmView(discord.ui.View):
    def __init__(self, interaction, key):
        super().__init__(timeout=600)
        self.uid, self.gid, self.key = interaction.user.id, interaction.guild_id, key

    async def interaction_check(self, interaction):
        if not allowed(interaction, self.uid, self.gid):
            await interaction.response.send_message("⛔ 無權操作此面板。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="確認解除", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        p = parent()
        data = p.world_get(self.gid)
        data.setdefault("channels", {}).pop(self.key, None)
        data.setdefault("overrides", {})[f"channels.{self.key}"] = True
        p.world_save(self.gid, data)
        await interaction.response.edit_message(content="✅ 已解除頻道連結（Discord 頻道沒有刪除）。\n\n" + detail(self.gid, self.key), view=ChannelDetailView(interaction, self.key))

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content=detail(self.gid, self.key), view=ChannelDetailView(interaction, self.key))
