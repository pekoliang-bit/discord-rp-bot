
"""官方路人管理：照片素材、姓名職業庫、批次建立、排行榜總開關。"""
from __future__ import annotations
import re
import discord
from modules import extra_character_store as store

def owner(i):
    return bool(i.guild and i.user.id == i.guild.owner_id)

class PoolModal(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(
            title={
                'photos': '🖼️ 批次新增照片',
                'names': '🌏 新增姓名庫',
                'jobs': '🎭 新增職業庫'
            }[kind]
        )
        self.kind = kind

        if kind == 'photos':
            self.photo_group = discord.ui.TextInput(
                label='照片分類 male_adult 等四類',
                placeholder='female_adult',
                max_length=20
            )
            self.add_item(self.photo_group)

        if kind == 'names':
            self.nationality = discord.ui.TextInput(
                label='國籍（例：日本）',
                max_length=50
            )
            self.kind_field = discord.ui.TextInput(
                label='類型 surname 姓氏／given 名字',
                max_length=12,
                placeholder='surname'
            )
            self.add_item(self.nationality)
            self.add_item(self.kind_field)

        self.values = discord.ui.TextInput(
            label='每行一項（可貼上多行）',
            style=discord.TextStyle.paragraph,
            max_length=4000,
            placeholder='每行一筆照片網址、名字或職業'
        )
        self.add_item(self.values)

    async def on_submit(self, i):
        if not owner(i):
            return await i.response.send_message(
                '只有群主能操作。', ephemeral=True
            )

        values = [
            v.strip() for v in self.values.value.splitlines()
            if v.strip()
        ]

        try:
            if self.kind == 'photos':
                g, a = self.photo_group.value.strip().lower().split('_')
                n = store.add_photos(
                    i.guild_id, values, gender=g, age_group=a
                )
            elif self.kind == 'jobs':
                n = store.add_jobs(i.guild_id, values)
            else:
                n = store.add_names(
                    i.guild_id,
                    self.nationality.value.strip(),
                    self.kind_field.value.strip().lower(),
                    values
                )

            d = store.inventory(i.guild_id)
            await i.response.send_message(
                f'✅ 新增 {n} 筆。照片庫剩餘 **{d["photos"]}** 張。'
                + (' ⚠️ 只剩五張或更少！' if d['photos'] <= 5 else ''),
                ephemeral=True
            )
        except (ValueError, TypeError) as e:
            await i.response.send_message(
                f'⚠️ 匯入失敗：{e}', ephemeral=True
            )

class BatchModal(discord.ui.Modal, title='🎲 批次建立路人角色'):
    count = discord.ui.TextInput(
        label='建立人數（1～100）',
        placeholder='10',
        max_length=3
    )
    nation = discord.ui.TextInput(
        label='指定國籍（空白＝隨機）',
        required=False,
        max_length=50
    )
    level = discord.ui.TextInput(
        label='初始咖位 LV1～LV9（空白＝隨機）',
        required=False,
        max_length=2
    )
    gender = discord.ui.TextInput(
        label='性別 male/female（空白＝隨機）',
        required=False,
        max_length=8
    )
    age_group = discord.ui.TextInput(
        label='年齡 adult/minor（空白＝隨機）',
        required=False,
        max_length=8
    )

    async def on_submit(self, i):
        if not owner(i):
            return await i.response.send_message(
                '僅群主能操作。', ephemeral=True
            )

        try:
            count = int(self.count.value)
            level = (
                int(self.level.value)
                if self.level.value.strip()
                else None
            )

            if not 1 <= count <= 100:
                raise ValueError('生成數量必須為1～100')
            if level is not None and not 1 <= level <= 9:
                raise ValueError('初始咖位只能 LV1～LV9')

            gender = self.gender.value.strip().lower() or None
            age_group = self.age_group.value.strip().lower() or None

            if gender not in (None, 'male', 'female') or age_group not in (None, 'adult', 'minor'):
                raise ValueError('性別／年齡分類錯誤')

            kwargs = dict(
                nationality=self.nation.value.strip() or None,
                level=level,
                gender=gender,
                age_group=age_group
            )

            stock = store.photo_inventory(i.guild_id)
            total = sum(
                n for (g, a), n in stock.items()
                if (not gender or gender == g)
                and (not age_group or age_group == a)
            )

            if total < count:
                raise ValueError(
                    f'可用照片只有 {total} 張，不足建立 {count} 人'
                )

            await i.response.send_message(
                f'🎲 **批次建立設定預覽**\n'
                f'建立 {count} 位路人\n'
                f'國籍：{kwargs["nationality"] or "隨機"}\n'
                f'咖位：{level or "隨機 LV1～LV9"}\n'
                f'性別：{gender or "隨機"}\n'
                f'年齡類別：{age_group or "隨機"}\n'
                f'可用照片：{total} 張\n\n'
                '確認後才會正式抽取姓名與數值並占用照片。',
                view=BatchConfirmView(
                    i.guild_id, i.user.id, count, kwargs
                ),
                ephemeral=True
            )

        except (ValueError, TypeError) as e:
            await i.response.send_message(
                f'⚠️ 無法預覽：{e}', ephemeral=True
            )

class BatchConfirmView(discord.ui.View):
    def __init__(self, guild_id, user_id, count, kwargs):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.count = count
        self.kwargs = kwargs
        self.done = False

    async def interaction_check(self, i):
        if (
            not owner(i)
            or i.guild_id != self.guild_id
            or i.user.id != self.user_id
        ):
            await i.response.send_message(
                '只有建立此預覽的群主可以確認。',
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label='✅ 確認正式建立',
        style=discord.ButtonStyle.success
    )
    async def confirm(self, i, b):
        if self.done:
            return await i.response.send_message(
                '此批次已處理。', ephemeral=True
            )
        self.done = True
        await i.response.defer(ephemeral=True, thinking=True)

        try:
            created = store.generate(
                i.guild_id,
                i.guild.owner_id,
                self.count,
                **self.kwargs
            )
            remaining = store.inventory(i.guild_id)['photos']

            msg = f'✅ 已建立 {len(created)} 位路人\n'
            msg += '\n'.join(
                f'#{cid} {name}｜{job}｜LV{lv} {title}'
                for cid, name, job, lv, title in created[:12]
            )
            msg += f'\n剩餘照片：{remaining} 張'
            if remaining <= 5:
                msg += ' ⚠️ 庫存只剩五張或更少'

            await i.followup.send(
                msg[:1900], ephemeral=True
            )

        except Exception as e:
            self.done = False
            await i.followup.send(
                f'⚠️ 建立失敗：{type(e).__name__}：{e}',
                ephemeral=True
            )

    @discord.ui.button(
        label='取消',
        style=discord.ButtonStyle.secondary
    )
    async def cancel(self, i, b):
        self.done = True
        await i.response.edit_message(
            content='已取消批次建立，沒有占用照片。',
            view=None
        )

class PhotoChannelModal(
    discord.ui.Modal,
    title='🖼️ 指定照片附件頻道'
):
    channel = discord.ui.TextInput(
        label='文字頻道 ID',
        max_length=25
    )
    category = discord.ui.TextInput(
        label='分類 male_adult / male_minor / female_adult / female_minor',
        max_length=20
    )

    async def on_submit(self, i):
        if not owner(i):
            return await i.response.send_message(
                '僅群主可設定。', ephemeral=True
            )
        try:
            gender, age_group = (
                self.category.value.strip().lower().split('_')
            )
            channel = i.guild.get_channel(
                int(self.channel.value.strip())
            )

            if channel is None or not isinstance(
                channel, discord.TextChannel
            ):
                raise ValueError('找不到文字頻道')

            store.set_photo_channel(
                i.guild_id, channel.id, gender, age_group
            )
            await i.response.send_message(
                f'✅ 已設定 {channel.mention}：'
                f'{gender}／{age_group}。'
                '在此頻道上傳附件即可收錄。',
                ephemeral=True
            )
        except (ValueError, TypeError) as e:
            await i.response.send_message(
                f'⚠️ 設定失敗：{e}', ephemeral=True
            )

class ExtraCharacterView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)
        self.user_id = i.user.id
        self.guild_id = i.guild_id

    async def interaction_check(self, i):
        if (
            i.user.id != self.user_id
            or i.guild_id != self.guild_id
            or not owner(i)
        ):
            await i.response.send_message(
                '⛔ 僅群主可使用此面板。',
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label='🖼️ 匯入照片',
        style=discord.ButtonStyle.primary,
        row=0
    )
    async def photos(self, i, b):
        await i.response.send_modal(PoolModal('photos'))

    @discord.ui.button(
        label='🌏 姓氏／名字庫',
        style=discord.ButtonStyle.secondary,
        row=0
    )
    async def names(self, i, b):
        await i.response.send_modal(PoolModal('names'))

    @discord.ui.button(
        label='🎭 職業庫',
        style=discord.ButtonStyle.secondary,
        row=0
    )
    async def jobs(self, i, b):
        await i.response.send_modal(PoolModal('jobs'))

    @discord.ui.button(
        label='🎲 批次生成',
        style=discord.ButtonStyle.success,
        row=1
    )
    async def batch(self, i, b):
        await i.response.send_modal(BatchModal())

    @discord.ui.button(
        label='📊 庫存統計',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def stats(self, i, b):
        d = store.inventory(i.guild_id)
        cfg = store.get_config(i.guild_id)
        stock = store.photo_inventory(i.guild_id)

        message = (
            '🚶 **路人資料庫**\n'
            '四類庫存：'
            + ', '.join(
                f'{g}/{a}：{stock.get((g, a), 0)} 張'
                for g in ('male', 'female')
                for a in ('adult', 'minor')
            )
            + '\n未使用照片：'
            + str(d['photos'])
            + ' 張'
            + (
                ' ⚠️ 庫存僅剩 5 張或更少'
                if d['photos'] <= 5 else ''
            )
            + f'\n國籍數：{d["nations"]}，'
              f'姓名詞條：{d["names"]}，'
              f'職業數：{d["jobs"]}'
              f'\n已建立：{d["existing"]} 人'
              f'\n排行榜：'
              f'{"允許" if cfg["allow_ranking"] else "不允許"}'
        )
        await i.response.send_message(
            message, ephemeral=True
        )

    @discord.ui.button(
        label='🏆 切換排行榜資格',
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def rank(self, i, b):
        cfg = store.get_config(i.guild_id)
        enabled = not bool(cfg['allow_ranking'])
        store.set_ranking(i.guild_id, enabled)
        await i.response.send_message(
            f'✅ 路人角現在'
            f'{"允許" if enabled else "不允許"}'
            '參與排行榜。這不會刪除現有角色或數值。',
            ephemeral=True
        )

    @discord.ui.button(
        label='📁 設定附件頻道',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def channels(self, i, b):
        await i.response.send_modal(PhotoChannelModal())

    @discord.ui.button(
        label='🗂️ 匯入頻道舊照片',
        style=discord.ButtonStyle.secondary,
        row=3
    )
    async def backfill(self, i, b):
        await i.response.defer(
            ephemeral=True, thinking=True
        )
        from modules.extra_character_photo_backfill import (
            import_history
        )
        report = await import_history(
            i.guild, per_channel=1000
        )

        if not report:
            return await i.followup.send(
                '尚未綁定照片附件頻道。',
                ephemeral=True
            )

        lines = [
            f"<#{r['channel_id']}>：新增 {r['added']} 張"
            + (
                f"（{r['error']}）"
                if r['error'] else ''
            )
            for r in report
        ]

        await i.followup.send(
            '🗂️ 歷史照片匯入完成\n'
            + '\n'.join(lines),
            ephemeral=True
        )

    @discord.ui.button(
        label='◀️ 返回內容建立',
        style=discord.ButtonStyle.secondary,
        row=2
    )
    async def back(self, i, b):
        from modules.content_builder import ContentView
        await i.response.edit_message(
            content='✍️ **內容建立中心**',
            view=ContentView(i)
        )
