"""官方內容建立中心：路人角色建立／檢視。沿用 characters 表與既有路人分類標記。"""
from __future__ import annotations
import json
import sqlite3
import discord
from core.database import transaction, get_connection


def check_owner(i):
    return bool(i.guild and i.user.id == i.guild.owner_id)


class ExtraCharacterModal(discord.ui.Modal, title="🚶 建立路人角色"):
    name = discord.ui.TextInput(label="路人名稱", placeholder="例如：咖啡店店員 林小姐", max_length=80)
    age = discord.ui.TextInput(label="年齡（可留空）", required=False, max_length=3, placeholder="例如：25")
    occupation = discord.ui.TextInput(label="職業／身分", required=False, max_length=100, placeholder="例如：咖啡店店員")
    description = discord.ui.TextInput(label="人物介紹／出場設定", required=False, style=discord.TextStyle.paragraph, max_length=900, placeholder="例如：經常出現在商業街，與主線無直接關聯")
    portrait = discord.ui.TextInput(label="立繪網址（選填）", required=False, max_length=400, placeholder="https://...")

    async def on_submit(self, i: discord.Interaction):
        if not check_owner(i):
            return await i.response.send_message("⛔ 僅伺服器群主能建立路人角色。", ephemeral=True)
        try:
            name = self.name.value.strip()
            if not name:
                raise ValueError("路人名稱不能為空。")
            age = int(self.age.value.strip()) if self.age.value.strip() else None
            if age is not None and not 0 <= age <= 200:
                raise ValueError("年齡須介於 0～200。")
            portrait = self.portrait.value.strip()
            if portrait and not portrait.startswith(("https://", "http://")):
                raise ValueError("立繪網址須以 https:// 或 http:// 開頭。")
            settings = {
                "character_category": "extra", "public_category": "extra",
                "biography": self.description.value.strip(),
                "portrait_url": portrait,
                "created_as_extra": True,
            }
            with transaction() as db:
                cur = db.execute(
                    """INSERT INTO characters
                    (guild_id, owner_id, name, age, occupation, status, character_type, settings_json)
                    VALUES (?, ?, ?, ?, ?, 'active', 'official_npc', ?)""",
                    (str(i.guild_id), str(i.guild.owner_id), name, age,
                     self.occupation.value.strip(), json.dumps(settings, ensure_ascii=False)),
                )
                cid = cur.lastrowid
            await i.response.send_message(
                f"✅ 已建立路人角色 **{name}**（#{cid}）。\n可從 `/查看 → 路人角色` 找到。",
                ephemeral=True,
            )
        except (ValueError, sqlite3.Error) as exc:
            await i.response.send_message(f"⚠️ 建立失敗：{exc}", ephemeral=True)


class ExtraCharacterView(discord.ui.View):
    def __init__(self, i: discord.Interaction):
        super().__init__(timeout=600)
        self.owner_id = i.user.id
        self.guild_id = i.guild_id

    async def interaction_check(self, i):
        if i.user.id != self.owner_id or i.guild_id != self.guild_id or not check_owner(i):
            await i.response.send_message("⛔ 沒有此管理面板的權限。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="➕ 建立路人角色", style=discord.ButtonStyle.success)
    async def add(self, i, button):
        await i.response.send_modal(ExtraCharacterModal())

    @discord.ui.button(label="📋 最近建立的路人", style=discord.ButtonStyle.secondary)
    async def listing(self, i, button):
        with get_connection() as db:
            rows = db.execute(
                """SELECT id,name,occupation,status FROM characters
                WHERE guild_id=? AND json_extract(settings_json,'$.character_category')='extra'
                ORDER BY id DESC LIMIT 20""", (str(i.guild_id),)
            ).fetchall()
        lines = [f"#{r[0]}｜{r[1]}｜{r[2] or '身分未填'}｜{r[3]}" for r in rows]
        await i.response.send_message("🚶 **路人角色清單**\n" + ("\n".join(lines) if lines else "尚未建立路人角色。"), ephemeral=True)

    @discord.ui.button(label="◀️ 返回內容建立", style=discord.ButtonStyle.secondary)
    async def back(self, i, button):
        from modules.content_builder import ContentView
        await i.response.edit_message(content="✍️ **內容建立中心**", view=ContentView(i))
