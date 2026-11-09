"""路人角色管理中心：僅群主可操作，不增加 Slash 指令。"""
from __future__ import annotations

import asyncio

import discord

from modules import extra_character_store as store


def owner(interaction):
    return bool(
        interaction.guild
        and interaction.user.id
        == interaction.guild.owner_id
    )


class PoolModal(discord.ui.Modal):
    def __init__(self, kind):
        titles = {
            "photos": "批次新增路人照片",
            "names": "新增國籍姓名庫",
            "jobs": "新增職業庫",
        }
        super().__init__(title=titles[kind])
        self.kind = kind

        if kind == "photos":
            self.category = discord.ui.TextInput(
                label="分類 male_adult 等四類",
                placeholder="female_adult",
                max_length=20,
            )
            self.add_item(self.category)

        if kind == "names":
            self.nationality = discord.ui.TextInput(
                label="國籍",
                placeholder="日本",
                max_length=50,
            )
            self.name_kind = discord.ui.TextInput(
                label="surname姓氏／given名字",
                placeholder="surname",
                max_length=12,
            )
            self.add_item(self.nationality)
            self.add_item(self.name_kind)

        self.values = discord.ui.TextInput(
            label="每行一項",
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.values)

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await interaction.response.send_message(
                "只有群主可以操作。",
                ephemeral=True,
            )

        values = [
            line.strip()
            for line in self.values.value.splitlines()
            if line.strip()
        ]

        try:
            if self.kind == "photos":
                gender, age_group = (
                    self.category.value
                    .strip().lower().split("_")
                )
                count = await asyncio.to_thread(
                    store.add_photos,
                    interaction.guild_id,
                    values,
                    gender=gender,
                    age_group=age_group,
                )

            elif self.kind == "names":
                count = await asyncio.to_thread(
                    store.add_names,
                    interaction.guild_id,
                    self.nationality.value.strip(),
                    self.name_kind.value.strip().lower(),
                    values,
                )

            else:
                count = await asyncio.to_thread(
                    store.add_jobs,
                    interaction.guild_id,
                    values,
                )

            await interaction.response.send_message(
                f"✅ 成功匯入 {count} 筆資料。",
                ephemeral=True,
            )

        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(
                f"⚠️ 匯入失敗：{exc}",
                ephemeral=True,
            )


class BatchModal(
    discord.ui.Modal,
    title="批次建立路人角色",
):
    count = discord.ui.TextInput(
        label="建立人數（1～100）",
        placeholder="10",
        max_length=3,
    )
    nation = discord.ui.TextInput(
        label="指定國籍（空白隨機）",
        required=False,
        max_length=50,
    )
    level = discord.ui.TextInput(
        label="初始咖位 LV1～LV9（空白隨機）",
        required=False,
        max_length=2,
    )
    gender = discord.ui.TextInput(
        label="male/female（空白隨機）",
        required=False,
        max_length=8,
    )
    age_group = discord.ui.TextInput(
        label="adult/minor（空白隨機）",
        required=False,
        max_length=8,
    )

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await interaction.response.send_message(
                "只有群主可以建立路人。",
                ephemeral=True,
            )

        try:
            count = int(self.count.value)
            lv = (
                int(self.level.value)
                if self.level.value.strip()
                else None
            )
            gender = (
                self.gender.value.strip().lower()
                or None
            )
            age_group = (
                self.age_group.value.strip().lower()
                or None
            )

            if not 1 <= count <= 100:
                raise ValueError("數量必須為1～100")
            if lv is not None and not 1 <= lv <= 9:
                raise ValueError("初始咖位最高LV9")
            if gender not in (
                None, "male", "female",
            ):
                raise ValueError("性別分類不正確")
            if age_group not in (
                None, "adult", "minor",
            ):
                raise ValueError("年齡分類不正確")

            kwargs = {
                "nationality": (
                    self.nation.value.strip() or None
                ),
                "level": lv,
                "gender": gender,
                "age_group": age_group,
            }

            stock = await asyncio.to_thread(
                store.photo_inventory,
                interaction.guild_id,
            )

            available = sum(
                amount
                for (g, a), amount in stock.items()
                if (gender is None or gender == g)
                and (age_group is None or age_group == a)
            )

            if available < count:
                raise ValueError(
                    f"照片不足：需要{count}張，"
                    f"目前只有{available}張"
                )

            preview = (
                "🎲 **路人批次建立預覽**\n"
                f"人數：{count}\n"
                f"國籍：{kwargs['nationality'] or '隨機'}\n"
                f"咖位：{lv if lv is not None else 'LV1～LV9隨機'}\n"
                f"性別：{gender or '隨機'}\n"
                f"年齡分類：{age_group or '隨機'}\n"
                f"可用照片：{available}張\n\n"
                "確認後才正式抽取姓名、職業與數值。"
            )

            await interaction.response.send_message(
                preview,
                view=BatchConfirmView(
                    interaction.guild_id,
                    interaction.user.id,
                    count,
                    kwargs,
                ),
                ephemeral=True,
            )

        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(
                f"⚠️ 無法建立預覽：{exc}",
                ephemeral=True,
            )


class BatchConfirmView(discord.ui.View):
    def __init__(
        self, guild_id, user_id, count, kwargs
    ):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.count = count
        self.kwargs = kwargs
        self.done = False
        self.busy = False

    async def interaction_check(self, interaction):
        allowed = (
            owner(interaction)
            and interaction.guild_id == self.guild_id
            and interaction.user.id == self.user_id
        )
        if not allowed:
            await interaction.response.send_message(
                "只有此批次的群主可以確認。",
                ephemeral=True,
            )
        return allowed

    @discord.ui.button(
        label="確認正式建立",
        style=discord.ButtonStyle.success,
    )
    async def confirm(self, interaction, button):
        if self.done or self.busy:
            return await interaction.response.send_message(
                "此批次正在處理或已完成。",
                ephemeral=True,
            )

        self.busy = True
        await interaction.response.defer(
            ephemeral=True, thinking=True
        )

        try:
            created = await asyncio.to_thread(
                store.generate,
                self.guild_id,
                self.user_id,
                self.count,
                **self.kwargs,
            )

            self.done = True
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

            lines = [
                f"✅ 已建立 {len(created)} 位路人角色"
            ]

            for cid, name, job, level, title in created[:15]:
                lines.append(
                    f"#{cid}｜{name}｜{job}｜"
                    f"LV{level} {title}"
                )

            if len(created) > 15:
                lines.append(
                    f"其餘 {len(created)-15} 位已建立。"
                )

            await interaction.followup.send(
                "\n".join(lines)[:1900],
                ephemeral=True,
            )

        except Exception as exc:
            await interaction.followup.send(
                f"⚠️ 建立失敗：{type(exc).__name__}：{exc}",
                ephemeral=True,
            )

        finally:
            self.busy = False

    @discord.ui.button(
        label="取消",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(self, interaction, button):
        if self.busy:
            return await interaction.response.send_message(
                "批次正在建立，無法取消。",
                ephemeral=True,
            )
        self.done = True
        await interaction.response.edit_message(
            content="已取消建立，照片未占用。",
            view=None,
        )


class PhotoChannelModal(
    discord.ui.Modal,
    title="設定路人照片附件頻道",
):
    channel = discord.ui.TextInput(
        label="Discord文字頻道ID",
        max_length=25,
    )
    category = discord.ui.TextInput(
        label="male_adult等四種分類",
        placeholder="female_minor",
        max_length=20,
    )

    async def on_submit(self, interaction):
        if not owner(interaction):
            return await interaction.response.send_message(
                "只有群主可以操作。",
                ephemeral=True,
            )

        try:
            channel_id = int(
                self.channel.value.strip()
            )
            channel = interaction.guild.get_channel(
                channel_id
            )

            if not isinstance(
                channel, discord.TextChannel
            ):
                raise ValueError("找不到指定文字頻道")

            gender, age_group = (
                self.category.value
                .strip().lower().split("_")
            )

            await asyncio.to_thread(
                store.set_photo_channel,
                interaction.guild_id,
                channel.id,
                gender,
                age_group,
            )

            await interaction.response.send_message(
                f"✅ 已綁定 {channel.mention}，"
                f"分類為{gender}/{age_group}。",
                ephemeral=True,
            )

        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(
                f"⚠️ 綁定失敗：{exc}",
                ephemeral=True,
            )


class ExtraCharacterView(discord.ui.View):
    """分類路人管理：資料庫、批次生成；保留既有功能與權限。"""
    SECTIONS = {
        'resources': ('匯入照片', '姓名庫', '職業庫', '照片附件頻道', '匯入舊照片'),
        'production': ('批次生成', '庫存統計', '切換排行榜'),
    }

    def __init__(self, interaction, section='home'):
        super().__init__(timeout=600)
        self.user_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.section = section
        self._arrange()

    def _arrange(self):
        original = list(self.children)
        self.clear_items()
        if self.section == 'home':
            for idx, (key, label) in enumerate((
                ('resources', '📚 照片／姓名／職業資料'),
                ('production', '🎲 生成／庫存／排行'),
            )):
                item = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, row=idx)
                async def selected(interaction, chosen=key):
                    await interaction.response.edit_message(
                        content=f'🚶 **路人角色管理｜{"資料庫" if chosen == "resources" else "生成與統計"}**',
                        view=ExtraCharacterView(interaction, section=chosen))
                item.callback = selected
                self.add_item(item)
        else:
            if self.section not in self.SECTIONS:
                raise ValueError('未知路人管理分類')
            selected = [x for x in original if getattr(x, 'label', None) in self.SECTIONS[self.section]]
            for idx, item in enumerate(selected):
                item.row = idx // 4
                self.add_item(item)
        back = discord.ui.Button(
            label='⬅️ 返回內容建立' if self.section == 'home' else '⬅️ 返回路人管理',
            style=discord.ButtonStyle.secondary, row=4)
        async def go_back(interaction):
            if self.section == 'home':
                from modules.content_builder import ContentView
                await interaction.response.edit_message(
                    content='✍️ **內容建立中心**', view=ContentView(interaction))
            else:
                await interaction.response.edit_message(
                    content='🚶 **路人角色管理中心**', view=ExtraCharacterView(interaction))
        back.callback = go_back
        self.add_item(back)

    async def interaction_check(self, interaction):
        allowed = (
            owner(interaction)
            and interaction.user.id == self.user_id
            and interaction.guild_id == self.guild_id
        )
        if not allowed:
            await interaction.response.send_message(
                "只有群主可以使用此面板。",
                ephemeral=True,
            )
        return allowed

    @discord.ui.button(
        label="匯入照片",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def photos(self, interaction, button):
        await interaction.response.send_modal(
            PoolModal("photos")
        )

    @discord.ui.button(
        label="姓名庫",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def names(self, interaction, button):
        await interaction.response.send_modal(
            PoolModal("names")
        )

    @discord.ui.button(
        label="職業庫",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def jobs(self, interaction, button):
        await interaction.response.send_modal(
            PoolModal("jobs")
        )

    @discord.ui.button(
        label="批次生成",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def batch(self, interaction, button):
        await interaction.response.send_modal(
            BatchModal()
        )

    @discord.ui.button(
        label="庫存統計",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def stats(self, interaction, button):
        data = await asyncio.to_thread(
            store.inventory,
            interaction.guild_id,
        )
        cfg = await asyncio.to_thread(
            store.get_config,
            interaction.guild_id,
        )
        stock = await asyncio.to_thread(
            store.photo_inventory,
            interaction.guild_id,
        )

        lines = [
            "🚶 **路人角色資料庫**",
            f"已建立：{data['existing']}人",
            f"未使用照片：{data['photos']}張",
            f"國籍：{data['nations']}種",
            f"姓名詞條：{data['names']}筆",
            f"職業：{data['jobs']}種",
            "排行榜：" + (
                "允許" if cfg["allow_ranking"]
                else "停用"
            ),
        ]

        for gender in ("male", "female"):
            for age in ("adult", "minor"):
                lines.append(
                    f"{gender}/{age}："
                    f"{stock.get((gender,age),0)}張"
                )

        if data["photos"] <= 5:
            lines.append("⚠️ 照片庫存低於六張！")

        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=True,
        )

    @discord.ui.button(
        label="切換排行榜",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def rank(self, interaction, button):
        cfg = await asyncio.to_thread(
            store.get_config,
            interaction.guild_id,
        )

        enabled = not bool(cfg["allow_ranking"])

        await asyncio.to_thread(
            store.set_ranking,
            interaction.guild_id,
            enabled,
        )

        await interaction.response.send_message(
            "✅ 路人排行榜總開關已"
            + ("開啟。" if enabled else "關閉。"),
            ephemeral=True,
        )

    @discord.ui.button(
        label="照片附件頻道",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def channels(self, interaction, button):
        await interaction.response.send_modal(
            PhotoChannelModal()
        )

    @discord.ui.button(
        label="匯入舊照片",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def backfill(self, interaction, button):
        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        from modules.extra_character_photo_backfill import (
            import_history,
        )

        report = await import_history(
            interaction.guild,
            per_channel=1000,
        )

        if not report:
            return await interaction.followup.send(
                "目前沒有綁定照片頻道。",
                ephemeral=True,
            )

        lines = [
            "🖼️ 歷史照片匯入結果"
        ]

        for entry in report:
            lines.append(
                f"<#{entry['channel_id']}>："
                f"新增{entry['added']}張"
            )
            if entry.get("error"):
                lines.append(
                    f"錯誤：{entry['error']}"
                )

        await interaction.followup.send(
            "\n".join(lines)[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="返回內容建立",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def back(self, interaction, button):
        from modules.content_builder import ContentView

        await interaction.response.edit_message(
            content="✍️ **內容建立中心**",
            view=ContentView(interaction),
        )
