"""世界觀／功能開關管理：個別說明、確認後切換及儲存。

由 modules.content_builder.WorldSettingsView 呼叫；不另建立斜線指令。
注意：本模組保存設定，業務指令仍需採用 is_feature_enabled() 才會被停用。
"""
from __future__ import annotations

import discord

# (鍵, 名稱, 功能說明, 停用影響)
FEATURES = (
    ("bank", "🏦 銀行", "銀行存款、提款、轉帳、定存等金融功能的總開關。", "停用銀行後，已串接此開關的銀行操作將拒絕新請求；既有帳戶及金額不刪除。"),
    ("shop", "🏬 商城", "官方商城與店主商城的購買入口總開關。", "停用商城後，已串接此開關的購買操作將拒絕新請求；既有背包、商品及訂單不刪除。"),
    ("company", "🏢 公司", "公司、品牌、集團等組織管理功能的總開關。", "停用後，已串接此開關的公司操作將拒絕新請求；已建立的公司及合約不刪除。"),
    ("faction", "⚔️ 陣營", "角色陣營、經費、積分及陣營商店功能的總開關。", "停用後，已串接此開關的陣營操作將拒絕新請求；既有經費及積分保留。"),
    ("rumor", "🗣️ 傳聞", "傳聞建立、查看及相關互動的總開關。", "停用後，已串接此開關的傳聞操作將拒絕新請求；歷史傳聞資料仍保存。"),
)
FEATURE_BY_KEY = {item[0]: item for item in FEATURES}


def _parent():
    from modules import content_builder
    return content_builder


def is_feature_enabled(guild_id: int | str, feature_key: str) -> bool:
    """其他模組可使用的查詢入口；未知鍵名拒絕而非預設放行。"""
    if feature_key not in FEATURE_BY_KEY:
        raise KeyError(f"Unknown world feature: {feature_key}")
    config = _parent().world_get(guild_id)
    features = config.get("features")
    if not isinstance(features, dict):
        return True
    return features.get(feature_key, True) is not False


def feature_summary(guild_id: int | str) -> str:
    text = ["🔘 **世界觀｜功能開關**", "選擇一個功能，查看用途、停用影響與設定狀態。"]
    for key, name, _, _ in FEATURES:
        text.append(f"{'✅ 啟用' if is_feature_enabled(guild_id, key) else '⛔ 停用'}｜{name}")
    text.extend(("", "⚠️ 這裡控制設定值；業務模組須接入查詢入口才會真正阻擋操作。", "關閉功能不會刪除既有資料。"))
    return "\n".join(text)


def _valid(interaction: discord.Interaction, uid: int, gid: int) -> bool:
    return interaction.guild_id == gid and interaction.user.id == uid and _parent().owner(interaction)


class FeatureSelect(discord.ui.Select):
    def __init__(self, guild_id):
        options = [discord.SelectOption(label=name, value=key, description="目前啟用" if is_feature_enabled(guild_id, key) else "目前停用") for key, name, _, _ in FEATURES]
        super().__init__(placeholder="選擇要查看／修改的功能", options=options, row=0)

    async def callback(self, interaction):
        key = self.values[0]
        await interaction.response.edit_message(content=feature_detail(interaction.guild_id, key), view=FeatureDetailView(interaction, key))


def feature_detail(gid, key):
    _, name, explanation, effect = FEATURE_BY_KEY[key]
    return (f"🔘 **功能設定｜{name}**\n\n"
            f"**目前狀態：**{'✅ 啟用' if is_feature_enabled(gid, key) else '⛔ 停用'}\n\n"
            f"**功能用途**\n{explanation}\n\n"
            f"**停用後的影響**\n{effect}\n\n"
            "**注意：**實際停用效果取決於對應業務模組是否已完成開關串接。")


class FeatureHomeView(discord.ui.View):
    def __init__(self, interaction):
        super().__init__(timeout=600)
        self.uid, self.gid = interaction.user.id, interaction.guild_id
        self.add_item(FeatureSelect(self.gid))

    async def interaction_check(self, interaction):
        if not _valid(interaction, self.uid, self.gid):
            await interaction.response.send_message("⛔ 只有開啟面板的群主能修改設定。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀️ 返回世界觀", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        p = _parent()
        await interaction.response.edit_message(content=p.world_summary(interaction.guild_id), view=p.WorldSettingsView(interaction))


class FeatureDetailView(discord.ui.View):
    def __init__(self, interaction, key):
        super().__init__(timeout=600)
        self.uid, self.gid, self.key = interaction.user.id, interaction.guild_id, key

    async def interaction_check(self, interaction):
        if not _valid(interaction, self.uid, self.gid):
            await interaction.response.send_message("⛔ 只有開啟面板的群主能修改設定。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="變更啟用狀態", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction, button):
        target = not is_feature_enabled(self.gid, self.key)
        name = FEATURE_BY_KEY[self.key][1]
        action = "啟用" if target else "停用"
        await interaction.response.edit_message(
            content=f"⚠️ **確認{action}：{name}？**\n\n{FEATURE_BY_KEY[self.key][3]}\n\n此操作只更改世界觀配置，不會刪除玩家資料。",
            view=FeatureConfirmView(interaction, self.key, target))

    @discord.ui.button(label="◀️ 返回功能列表", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content=feature_summary(self.gid), view=FeatureHomeView(interaction))


class FeatureConfirmView(discord.ui.View):
    def __init__(self, interaction, key, target):
        super().__init__(timeout=300)
        self.uid, self.gid, self.key, self.target = interaction.user.id, interaction.guild_id, key, target

    async def interaction_check(self, interaction):
        if not _valid(interaction, self.uid, self.gid):
            await interaction.response.send_message("⛔ 只有開啟面板的群主能修改設定。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="確認儲存", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction, button):
        p = _parent()
        data = p.world_get(self.gid)
        if not isinstance(data.get("features"), dict):
            data["features"] = {}
        data["features"][self.key] = self.target
        data.setdefault("overrides", {})["features." + self.key] = True
        p.world_save(self.gid, data)
        await interaction.response.edit_message(content="✅ **設定已儲存**\n\n" + feature_detail(self.gid, self.key), view=FeatureDetailView(interaction, self.key))

    @discord.ui.button(label="取消，返回設定", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content=feature_detail(self.gid, self.key), view=FeatureDetailView(interaction, self.key))
