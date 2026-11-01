"""Discord RP 世界背景管理：四組子選單、逐項說明、資料保存。

不另註冊 Discord 指令。由 content_builder.WorldSettingsView 呼叫。
使用既有 world_configs.config_json，不建立玩家資料表，不覆寫其他設定。
"""
from __future__ import annotations

import copy
import discord

# key, 名稱, 說明, 範例, 類型, 上限
SECTIONS = (
    ("basic", "🌍 基本設定", (
        ("world_name", "世界觀名稱", "玩家看到的世界名稱；不會變更伺服器名稱。", "現代娛樂圈", "text", 100),
        ("world_type", "世界觀類型", "例如現代、架空、奇幻；用於背景展示，不會自動啟用其他遊戲規則。", "現代／娛樂圈", "text", 100),
        ("era", "時代背景", "故事所處的年代或時代，可填具體年份。", "現代，2026 年", "text", 150),
        ("background", "世界簡介", "提供玩家閱讀的基本背景介紹，不用把所有規則都塞進此欄。", "這是一座以演藝與商業競爭為中心的城市……", "long", 1900),
    )),
    ("geography", "🗺️ 地理與地區", (
        ("main_regions", "主要國家／地區", "列出故事可使用的國家、城市或地區；具體地區仍由官方地區系統建立。", "臺灣、日本、韓國……", "long", 1200),
        ("region_creation", "地區建立規則", "正式規則：只有官方可以建立地區。本欄僅展示，不開放修改。", "僅官方可建立地區", "readonly", 60),
        ("region_description", "城市與區域介紹", "用文字介紹重要區域；此欄不會新增實體地區或建築。", "市中心為品牌與娛樂產業聚集地。", "long", 1500),
        ("building_note", "區域與建築說明", "設定建築所屬區域的背景說明；實際建築歸屬由地區／建築模組管理。", "商店必須歸屬某個官方建立的區域。", "long", 1000),
    )),
    ("society", "🏛️ 社會與制度", (
        ("economy_note", "貨幣與經濟背景", "用於描述經濟制度；貨幣名稱及銀行數值應至對應分類編輯。", "金幣是常見交易單位。", "long", 1200),
        ("organization_note", "組織制度背景", "描述公司、品牌、集團及陣營之間的關係；具體規則由對應系統管理。", "品牌可隸屬於公司或集團……", "long", 1200),
        ("status_note", "社會地位背景", "解釋地位對劇情與社會的意義；LV1～LV20 門檻仍在地位設定調整。", "地位決定公開活動的社交門檻。", "long", 1200),
        ("special_rules", "特殊世界規則說明", "只描述世界觀特殊背景，不會自行取代細項規則或指令限制。", "此世界具有獨立的媒體評價體系。", "long", 1200),
    )),
    ("history", "📜 故事與歷史", (
        ("history_intro", "世界歷史", "整理世界歷史背景；可分段介紹重要時期。", "數十年前，娛樂產業開始快速發展……", "long", 1900),
        ("milestones", "重要歷史事件", "記錄已發生的事件；一行一項，適合列年份及事件。", "2020：首屆年度影視獎舉辦", "long", 1900),
        ("story_start", "故事起始時間", "玩家角色劇情開始的世界內時間，不影響 Discord 真實時間。", "2026 年 1 月 1 日", "text", 150),
        ("additional_lore", "世界觀補充說明", "填寫不適合放入其他欄位的文化、習俗或歷史背景。", "每年冬季舉行大型音樂祭……", "long", 1900),
    )),
)

DEFAULTS = {"world_name": "現代娛樂圈", "world_type": "現代／娛樂圈", "era": "現代", "region_creation": "僅官方可建立地區"}


def get_field(data: dict, key: str) -> str:
    if key in ("world_name", "background"):
        return str(data.get(key) or DEFAULTS.get(key, ""))
    if key == "region_creation":
        return DEFAULTS[key]
    return str((data.get("world_background") or {}).get(key) or DEFAULTS.get(key, ""))


def save_field(data: dict, key: str, value: str) -> dict:
    if key == "region_creation":
        raise ValueError("地區建立規則不可在此變更")
    result = copy.deepcopy(data)
    if key in ("world_name", "background"):
        result[key] = value
        override = key
    else:
        result.setdefault("world_background", {})[key] = value
        override = "world_background." + key
    result.setdefault("overrides", {})[override] = True
    return result


def _parent():
    # 避免 content_builder 匯入此模組時循環依賴。
    from modules import content_builder
    return content_builder


def _clip(text: str, n=72):
    return text[:n - 1] + "…" if len(text) > n else text


def _allowed(i, user_id, guild_id):
    p = _parent()
    return bool(i.guild_id == guild_id and i.user.id == user_id and p.owner(i))


class BackgroundFieldModal(discord.ui.Modal):
    def __init__(self, guild_id: int, key: str, name: str, hint: str, example: str, kind: str, limit: int):
        super().__init__(title=("修改｜" + name)[:45], timeout=600)
        self.key, self.name, self.guild_id = key, name, guild_id
        old = get_field(_parent().world_get(guild_id), key)
        self.entry = discord.ui.TextInput(
            label=name[:45], required=(key == "world_name"),
            placeholder=example[:100],
            default=old[:limit] or None,
            style=discord.TextStyle.paragraph if kind == "long" else discord.TextStyle.short,
            max_length=limit,
        )
        self.add_item(self.entry)

    async def on_submit(self, i: discord.Interaction):
        p = _parent()
        if i.guild_id != self.guild_id or not p.owner(i):
            return await i.response.send_message("⛔ 只有伺服器群主可修改世界背景。", ephemeral=True)
        value = str(self.entry).strip()
        if self.key == "world_name" and not value:
            return await i.response.send_message("世界觀名稱不能留空。", ephemeral=True)
        data = save_field(p.world_get(i.guild_id), self.key, value)
        p.world_save(i.guild_id, data)
        await i.response.send_message(
            f"✅ **{self.name}** 已儲存。\n目前內容：{_clip(value or '（空白）', 500)}\n"
            "請返回世界背景面板查看其他設定。", ephemeral=True
        )


class BackgroundFieldSelect(discord.ui.Select):
    def __init__(self, guild_id, section_key):
        _, _, fields = next(s for s in SECTIONS if s[0] == section_key)
        data = _parent().world_get(guild_id)
        options = [discord.SelectOption(
            label=name[:100], value=key,
            description=_clip("🔒 唯讀｜" + hint if kind == "readonly" else (get_field(data, key) or hint), 98)
        ) for key, name, hint, example, kind, limit in fields]
        super().__init__(placeholder="選擇要查看或修改的設定項目", options=options, row=0)

    async def callback(self, i):
        p = _parent()
        if not p.owner(i):
            return await i.response.send_message("⛔ 只有群主可修改世界背景。", ephemeral=True)
        await i.response.edit_message(
            content=field_summary(i.guild_id, self.view.section, self.values[0]),
            view=BackgroundFieldView(i, self.view.section, self.values[0])
        )


def section_summary(guild_id, section_key):
    _, title, fields = next(s for s in SECTIONS if s[0] == section_key)
    data = _parent().world_get(guild_id)
    lines = [f"**{title}**", "選擇下方欄位，查看用途、填寫範例，再決定是否修改。", ""]
    for key, name, _, _, kind, _ in fields:
        content = get_field(data, key)
        lines.append(f"• **{name}**：{_clip(content or '尚未設定', 110)}" + ("（固定規則）" if kind == "readonly" else ""))
    return "\n".join(lines)


def field_summary(guild_id, section_key, field_key):
    _, title, fields = next(s for s in SECTIONS if s[0] == section_key)
    key, name, hint, example, kind, _ = next(f for f in fields if f[0] == field_key)
    current = get_field(_parent().world_get(guild_id), key)
    return (f"{title} › **{name}**\n\n📌 **用途**\n{hint}\n\n"
            f"📝 **填寫範例**\n{example}\n\n"
            f"📋 **目前內容**\n{_clip(current or '尚未設定', 1200)}\n\n"
            + ("🔒 此項是固定規則，不能在此變更。" if kind == "readonly" else "點擊下方按鈕開啟專用表單。"))


class BackgroundFieldView(discord.ui.View):
    def __init__(self, i, section_key, field_key):
        super().__init__(timeout=600)
        self.uid, self.gid, self.section, self.field_key = i.user.id, i.guild_id, section_key, field_key
        field = next(f for s in SECTIONS if s[0] == section_key for f in s[2] if f[0] == field_key)
        if field[4] == "readonly":
            self.remove_item(self.edit)

    async def interaction_check(self, i):
        if not _allowed(i, self.uid, self.gid):
            await i.response.send_message("⛔ 這不是你的管理面板，或你沒有群主權限。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✏️ 修改這個欄位", style=discord.ButtonStyle.primary)
    async def edit(self, i, button):
        field = next(f for s in SECTIONS if s[0] == self.section for f in s[2] if f[0] == self.field_key)
        await i.response.send_modal(BackgroundFieldModal(self.gid, *field))

    @discord.ui.button(label="◀️ 返回欄位列表", style=discord.ButtonStyle.secondary)
    async def back(self, i, button):
        await i.response.edit_message(content=section_summary(self.gid, self.section), view=BackgroundSectionView(i, self.section))


class BackgroundSectionView(discord.ui.View):
    def __init__(self, i, section_key):
        super().__init__(timeout=600)
        self.uid, self.gid, self.section = i.user.id, i.guild_id, section_key
        self.add_item(BackgroundFieldSelect(self.gid, section_key))

    async def interaction_check(self, i):
        if not _allowed(i, self.uid, self.gid):
            await i.response.send_message("⛔ 這不是你的管理面板，或你沒有群主權限。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, i, button):
        await i.response.edit_message(content=section_summary(self.gid, self.section), view=BackgroundSectionView(i, self.section))

    @discord.ui.button(label="◀️ 返回世界背景", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, i, button):
        await i.response.edit_message(content=background_summary(self.gid), view=WorldBackgroundView(i))


def background_summary(guild_id):
    data = _parent().world_get(guild_id)
    return ("🌐 **世界背景管理**\n"
            f"名稱：{get_field(data, 'world_name')}\n"
            "請先選擇下方四個子分類。每個欄位均有用途說明、範例和目前設定。\n\n"
            "⚠️ 社會制度等文字欄位是背景描述，具體數值和系統規則仍由各自分類管理。")


class BackgroundSectionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="選擇世界背景子分類", row=0, options=[
            discord.SelectOption(label=title, value=key, description=f"{len(fields)} 項獨立設定")
            for key, title, fields in SECTIONS
        ])

    async def callback(self, i):
        section_key = self.values[0]
        await i.response.edit_message(content=section_summary(i.guild_id, section_key), view=BackgroundSectionView(i, section_key))


class WorldBackgroundView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.uid, self.gid = i.user.id, i.guild_id
        self.add_item(BackgroundSectionSelect())

    async def interaction_check(self, i):
        if not _allowed(i, self.uid, self.gid):
            await i.response.send_message("⛔ 這不是你的管理面板，或你沒有群主權限。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀️ 返回世界觀管理中心", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, i, button):
        p = _parent()
        await i.response.edit_message(content=p.world_summary(i.guild_id), view=p.WorldSettingsView(i))
