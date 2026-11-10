"""逐步申請新增場所／頻道及擴充場所行動；不另設 Slash 指令。

每項申請至少包含打工、活動與課程各一筆（擴充既有場所可單獨追加）。
申請本身不建立 Discord 頻道，也不啟用玩家扣款；須待審核整合後執行。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

import discord

from core.database import get_connection, transaction
from modules.application_extras import init_db


from core.action_application_rules import ProposalError, parse_rewards, preview_cost


class PlaceInformationModal(discord.ui.Modal, title="新增地點／頻道：基本資料"):
    name = discord.ui.TextInput(label="地點／頻道名稱", max_length=100)
    region = discord.ui.TextInput(label="所屬區域／分類", max_length=100)
    channel_type = discord.ui.TextInput(label="Discord 頻道類型", placeholder="文字頻道／論壇頻道", max_length=60)
    purpose = discord.ui.TextInput(label="申請原因、用途與概念", style=discord.TextStyle.paragraph, max_length=900)
    random_event = discord.ui.TextInput(label="隨機事件概念", style=discord.TextStyle.paragraph, max_length=900)

    def __init__(self, state):
        super().__init__()
        self.state = state

    async def on_submit(self, i):
        if not self.state.owns(i):
            return await i.response.send_message("不是你的申請。", ephemeral=True)
        self.state.subject = str(self.name.value).strip()
        self.state.metadata = {"name": self.subject if False else self.state.subject,
            "region": str(self.region.value).strip(),
            "channel_type": str(self.channel_type.value).strip(),
            "purpose": str(self.purpose.value).strip(),
            "random_event": str(self.random_event.value).strip()}
        await i.response.send_message("✅ 地點基本資料已保存。請依序新增打工、活動、課程各至少一項。", view=self.state, ephemeral=True)


class ChannelTarget(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="選擇要擴充行動的現有頻道", min_values=1, max_values=1,
                         channel_types=[discord.ChannelType.text, discord.ChannelType.forum], row=0)

    async def callback(self, i):
        v = self.view
        channel = self.values[0]
        if not v.owns(i):
            return await i.response.send_message("不是你的申請。", ephemeral=True)
        live_channel = i.guild.get_channel(int(channel.id)) if i.guild else None
        if live_channel is None:
            return await i.response.send_message("只能選擇本伺服器已存在的頻道。", ephemeral=True)
        v.subject = str(channel.id)
        v.metadata = {"channel_id": str(channel.id), "channel_name": channel.name}
        await i.response.edit_message(content=f"已選擇 {channel.mention}。請新增打工、活動或課程。", view=v)


class ActionInfoModal(discord.ui.Modal):
    def __init__(self, state, category: str):
        super().__init__(title=f"新增{category}行動", timeout=600)
        self.state = state
        self.category = category
        self.name = discord.ui.TextInput(label="行動名稱", max_length=100,
            placeholder="例如：服飾店店員／大額購物／穿搭課程")
        self.coins = discord.ui.TextInput(label="金幣（打工收入／其他為基本花費）", max_length=12,
            placeholder="例如 300 或 5000")
        self.stats = discord.ui.TextInput(label="數值獎勵（最多三項，可填無）", max_length=350,
            style=discord.TextStyle.paragraph, placeholder="容貌:10\n才智:隨機\n個人才藝:3", required=False)
        self.notes = discord.ui.TextInput(label="行動用途及說明（選填）", max_length=800,
            style=discord.TextStyle.paragraph, required=False)
        for field in (self.name, self.coins, self.stats, self.notes):
            self.add_item(field)

    async def on_submit(self, i):
        if not self.state.owns(i):
            return await i.response.send_message("不是你的申請。", ephemeral=True)
        try:
            coins = int(str(self.coins.value).strip())
            if not 0 <= coins <= 10**12:
                raise ProposalError("金幣必須是非負整數")
            rewards = parse_rewards(str(self.stats.value or ""))
            if self.category == "打工" and coins <= 0:
                raise ProposalError("打工必須設定正數金幣收入")
            if any(a["category"] == self.category and a["name"] == str(self.name.value).strip()
                   for a in self.state.actions):
                raise ProposalError("同一類別的行動名稱不得重複")
            estimate = preview_cost(self.category, coins, rewards)
            entry = {"category": self.category, "name": str(self.name.value).strip(),
                "base_coins": coins, "stat_rewards": rewards, "notes": str(self.notes.value or "").strip(),
                "price_min": estimate["min"], "price_max": estimate["max"]}
            self.state.actions.append(entry)
            await i.response.send_message(
                f"✅ 已新增{self.category}：**{entry['name']}**\n"
                + (f"金幣收入：{coins:,}" if self.category == "打工"
                   else f"最低花費範圍：{estimate['min']:,}～{estimate['max']:,} 金幣（按實際抽取點數計費）")
                + "\n可繼續新增行動或送審。", view=self.state, ephemeral=True)
        except (ValueError, ProposalError) as exc:
            await i.response.send_message(f"⚠️ 設定不合法：{exc}", ephemeral=True)


class ActionApplicationView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int, character_id: int | None, kind: str):
        super().__init__(timeout=1800)
        self.guild_id = guild_id
        self.user_id = user_id
        self.character_id = character_id
        self.kind = kind
        self.subject = None
        self.metadata = {}
        self.actions = []
        if kind == "action_expand":
            self.add_item(ChannelTarget())
            self.place.disabled = True

    def owns(self, i):
        return i.guild_id == self.guild_id and i.user.id == self.user_id

    async def interaction_check(self, i):
        if not self.owns(i):
            await i.response.send_message("不是你的申請畫面。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="設定地點資料", emoji="📍", style=discord.ButtonStyle.secondary, row=1)
    async def place(self, i, button):
        if self.kind == "action_expand":
            return await i.response.send_message("擴充行動請先從上方選擇現有頻道。", ephemeral=True)
        await i.response.send_modal(PlaceInformationModal(self))

    @discord.ui.button(label="＋打工", style=discord.ButtonStyle.primary, row=2)
    async def work(self, i, button):
        await i.response.send_modal(ActionInfoModal(self, "打工"))

    @discord.ui.button(label="＋活動", style=discord.ButtonStyle.primary, row=2)
    async def activity(self, i, button):
        await i.response.send_modal(ActionInfoModal(self, "活動"))

    @discord.ui.button(label="＋課程", style=discord.ButtonStyle.primary, row=2)
    async def course(self, i, button):
        await i.response.send_modal(ActionInfoModal(self, "課程"))

    @discord.ui.button(label="確認送審", emoji="✅", style=discord.ButtonStyle.success, row=3)
    async def submit(self, i, button):
        if not self.subject:
            return await i.response.send_message("請先設定地點資料或選擇現有頻道。", ephemeral=True)
        if not self.actions:
            return await i.response.send_message("請至少新增一項行動。", ephemeral=True)
        if self.kind != "action_expand" and not {"打工", "活動", "課程"}.issubset({a['category'] for a in self.actions}):
            return await i.response.send_message("新地點必須至少各有一項打工、活動、課程。", ephemeral=True)
        try:
            init_db()
            data = {"schema": "action_application_v1", "metadata": self.metadata,
                    "actions": self.actions, "pricing": "actual_roll_b"}
            with transaction() as db:
                if self.character_id is not None:
                    found = db.execute("SELECT 1 FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'",
                                       (str(self.guild_id), self.character_id, str(self.user_id))).fetchone()
                    if not found:
                        raise ProposalError("申請角色已失效")
                cur = db.execute("INSERT INTO rp_general_applications(guild_id,user_id,character_id,kind,title,details,reference) "
                    "VALUES(?,?,?,?,?,?,?)", (str(self.guild_id), str(self.user_id), self.character_id,
                    self.kind, self.subject, json.dumps(data, ensure_ascii=False, sort_keys=True), ""))
                case_id = cur.lastrowid
            self.stop()
            for child in self.children:
                child.disabled = True
            await i.response.edit_message(content=f"✅ 已提交申請 #{case_id}，共 {len(self.actions)} 項行動。\n"
                "待審核資料不會立即建立頻道或開放玩家執行。", view=self)
        except (sqlite3.Error, ProposalError) as exc:
            await i.response.send_message(f"⚠️ 送審失敗：{exc}", ephemeral=True)
