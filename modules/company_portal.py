"""/公司 身分入口；人事與陣營完全分離。"""
from __future__ import annotations

import asyncio
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from modules.company_workforce import (
    apply_job, configure_position, pending_reviews, resign,
    review_application, review_work, review_promotion, submit_work,
)


def company_context(gid: str, uid: str):
    with get_connection() as db:
        owned = db.execute("""SELECT company_id FROM rp_approved_company_authorizations
            WHERE guild_id=? AND original_applicant_id=? AND status='approved'
            ORDER BY company_id""", (gid, uid)).fetchall()
        employees = db.execute("""SELECT e.company_id,e.character_id,e.rank,e.exp,
            COALESCE(p.title,'職位未設定') AS title FROM company_employees e
            JOIN characters c ON c.guild_id=e.guild_id AND c.id=e.character_id
            LEFT JOIN company_job_positions p ON p.guild_id=e.guild_id
                AND p.company_id=e.company_id AND p.rank=e.rank
            WHERE e.guild_id=? AND c.owner_id=? AND e.status='active'
            ORDER BY e.company_id,e.character_id""", (gid, uid)).fetchall()
        lines = []
        for e in employees:
            positions = db.execute("""SELECT rank,title,exp_required,promotion_mode
                FROM company_job_positions WHERE guild_id=? AND company_id=?
                AND rank>? ORDER BY rank LIMIT 1""",
                (gid, e['company_id'], e['rank'])).fetchone()
            next_text = (f"下一級「{positions['title']}」需累積 {positions['exp_required']} EXP，"
                         f"還差 {max(0,positions['exp_required']-e['exp'])} EXP，"
                         f"{'達標自動升職' if positions['promotion_mode']=='auto' else '達標後由老闆審核'}"
                         if positions else '已達目前最高職位')
            all_positions = db.execute("""SELECT rank,title,exp_required FROM company_job_positions
                WHERE guild_id=? AND company_id=? ORDER BY rank LIMIT 20""",
                (gid,e['company_id'])).fetchall()
            track = '、'.join(f"{p['title']} {p['exp_required']}" for p in all_positions)
            lines.append(f"• {e['company_id']}｜角色 #{e['character_id']}｜{e['title']}"
                         f"（LV.{e['rank']}）｜累積 {e['exp']} EXP\n  {next_text}"
                         f"\n  各職位累積門檻：{track or '未設定'}")
        return [r['company_id'] for r in owned], '\n'.join(lines)


def company_positions(gid: str, company: str):
    with get_connection() as db:
        rows = db.execute("""SELECT rank,title,exp_required,exp_per_work,promotion_mode
            FROM company_job_positions WHERE guild_id=? AND company_id=?
            ORDER BY rank""", (gid, company)).fetchall()
        return ('\n'.join(f"LV.{r['rank']} {r['title']}｜累積 {r['exp_required']} EXP"
            f"｜工作 +{r['exp_per_work']}｜{'自動' if r['promotion_mode']=='auto' else '審核'}升職"
            for r in rows) or '公司尚未設定職位。')


async def respond(i, action):
    await i.response.defer(ephemeral=True)
    try:
        result = await asyncio.to_thread(action)
    except (ValueError, PermissionError, sqlite3.Error) as exc:
        result = f"⚠️ {exc}"
    await i.followup.send(str(result)[:1900], ephemeral=True)


class OwnedModal(discord.ui.Modal):
    def __init__(self, uid, gid, title):
        super().__init__(title=title, timeout=600)
        self.uid, self.gid = uid, gid

    async def submit_job(self, i, job):
        if str(i.user.id) != self.uid or str(i.guild_id) != self.gid:
            return await i.response.send_message('這不是你的操作表單。', ephemeral=True)
        await respond(i, job)


class ApplyModal(OwnedModal):
    def __init__(self, uid, gid):
        super().__init__(uid, gid, '公司職位應徵')
        self.company = discord.ui.TextInput(label='公司編號', max_length=100)
        self.character = discord.ui.TextInput(label='自己角色的編號', max_length=20)
        self.add_item(self.company)
        self.add_item(self.character)

    async def on_submit(self, i):
        await self.submit_job(i, lambda: apply_job(self.gid, str(self.company.value).strip(),
                                                   self.uid, int(self.character.value)))


class ResignModal(OwnedModal):
    def __init__(self, uid, gid):
        super().__init__(uid, gid, '辭職確認')
        self.company = discord.ui.TextInput(label='公司編號', max_length=100)
        self.character = discord.ui.TextInput(label='自己角色的編號', max_length=20)
        self.confirm = discord.ui.TextInput(label='輸入「確認辭職」', max_length=10)
        for control in (self.company,self.character,self.confirm):
            self.add_item(control)

    async def on_submit(self, i):
        if str(self.confirm.value).strip() != '確認辭職':
            return await i.response.send_message('尚未確認辭職，沒有更動任職狀態。', ephemeral=True)
        await self.submit_job(i, lambda: resign(self.gid, str(self.company.value).strip(),
                                                self.uid, int(self.character.value)))


class PositionModal(OwnedModal):
    def __init__(self, uid, gid):
        super().__init__(uid, gid, '公司職位與經驗設定')
        self.company = discord.ui.TextInput(label='公司編號', max_length=100)
        self.rank = discord.ui.TextInput(label='階級（從 0 開始）', max_length=3)
        self.title_input = discord.ui.TextInput(label='職位名稱', max_length=60)
        self.required = discord.ui.TextInput(label='累積升職 EXP 門檻', max_length=12)
        self.exp_mode = discord.ui.TextInput(label='工作EXP,升職方式,薪資',
                                             placeholder='例如：20,自動,5000', max_length=45)
        for control in (self.company,self.rank,self.title_input,self.required,self.exp_mode):
            self.add_item(control)

    async def on_submit(self, i):
        try:
            parts = [x.strip() for x in str(self.exp_mode.value).split(',')]
            if len(parts) != 3:
                raise ValueError
            points, mode, salary = parts
            if mode not in ('自動','審核'):
                raise ValueError
            rank, required, points, salary = int(self.rank.value), int(self.required.value), int(points), int(salary)
            if not (0 <= rank <= 30 and 0 <= required <= 100000000
                    and 0 <= points <= 100000 and 0 <= salary <= 100000000):
                raise ValueError
        except ValueError:
            return await i.response.send_message('格式錯誤：例如 20,自動,5000；數值須為非負整數。', ephemeral=True)
        await self.submit_job(i, lambda: configure_position(self.gid,
            str(self.company.value).strip(), self.uid, rank, str(self.title_input.value),
            required, points, 'auto' if mode == '自動' else 'approval', salary))


class ReviewModal(OwnedModal):
    def __init__(self, uid, gid):
        super().__init__(uid, gid, '公司人事審核')
        self.kind = discord.ui.TextInput(label='種類：應徵／工作／升職', max_length=10)
        self.number = discord.ui.TextInput(label='待審編號', max_length=20)
        self.choice = discord.ui.TextInput(label='處理：通過／拒絕', max_length=10)
        for control in (self.kind,self.number,self.choice):
            self.add_item(control)

    async def on_submit(self, i):
        kind, decision = str(self.kind.value).strip(), str(self.choice.value).strip()
        if kind not in ('應徵','工作','升職') or decision not in ('通過','拒絕'):
            return await i.response.send_message('請輸入種類：應徵／工作／升職，結果：通過／拒絕。', ephemeral=True)
        try:
            rid = int(self.number.value)
        except ValueError:
            return await i.response.send_message('待審編號必須是數字。', ephemeral=True)
        actions = {'應徵':review_application,'工作':review_work,'升職':review_promotion}
        await self.submit_job(i, lambda: actions[kind](self.gid, self.uid, rid, decision=='通過'))


class OwnerLookupModal(OwnedModal):
    def __init__(self, uid, gid):
        super().__init__(uid, gid, '公司營運與待審')
        self.company = discord.ui.TextInput(label='公司編號', max_length=100)
        self.add_item(self.company)

    async def on_submit(self, i):
        await self.submit_job(i, lambda: pending_reviews(self.gid,
                                                          str(self.company.value).strip(),self.uid))


class WorkModal(OwnedModal):
    def __init__(self, uid, gid):
        super().__init__(uid, gid, '提交公司工作')
        self.company = discord.ui.TextInput(label='公司編號', max_length=100)
        self.character = discord.ui.TextInput(label='自己的角色編號', max_length=20)
        self.description = discord.ui.TextInput(label='工作內容', style=discord.TextStyle.paragraph,
                                                max_length=1000)
        self.evidence = discord.ui.TextInput(label='認證紀錄連結（選填）', required=False,
                                             max_length=500)
        for control in (self.company,self.character,self.description,self.evidence):
            self.add_item(control)

    async def on_submit(self, i):
        await self.submit_job(i, lambda: submit_work(self.gid, str(self.company.value).strip(),
            self.uid, int(self.character.value), str(self.description.value), str(self.evidence.value or '')))


class CompanyPanel(discord.ui.View):
    def __init__(self, uid, gid, is_owner):
        super().__init__(timeout=600)
        self.uid, self.gid, self.is_owner = uid, gid, is_owner
        if is_owner:
            for label, modal, style in (
                ('⚙️ 職位設定',PositionModal,discord.ButtonStyle.primary),
                ('📋 待審清單',OwnerLookupModal,discord.ButtonStyle.secondary),
                ('✅ 人事審核',ReviewModal,discord.ButtonStyle.success),
            ):
                button = discord.ui.Button(label=label,style=style)
                async def callback(i, modal_class=modal):
                    await i.response.send_modal(modal_class(self.uid,self.gid))
                button.callback=callback
                self.add_item(button)

    async def interaction_check(self, i):
        if str(i.user.id)!=self.uid or str(i.guild_id)!=self.gid:
            await i.response.send_message('這不是你的公司介面。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='👔 應徵', style=discord.ButtonStyle.primary, row=1)
    async def apply(self,i,button):
        await i.response.send_modal(ApplyModal(self.uid,self.gid))

    @discord.ui.button(label='🚪 辭職', style=discord.ButtonStyle.secondary, row=1)
    async def leave(self,i,button):
        await i.response.send_modal(ResignModal(self.uid,self.gid))

    @discord.ui.button(label='📝 工作提交', style=discord.ButtonStyle.secondary, row=1)
    async def submit(self,i,button):
        await i.response.send_modal(WorkModal(self.uid,self.gid))

    @discord.ui.button(label='🔄 更新資料', style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self,i,button):
        owned, employees = await asyncio.to_thread(company_context,self.gid,self.uid)
        await i.response.edit_message(content=panel_text(owned,employees),
                                      view=CompanyPanel(self.uid,self.gid,bool(owned)))


def panel_text(owned, employees):
    if owned:
        return ("🏢 **公司營運中心**\n你持有的正式公司：" + ', '.join(owned) +
                "\n可管理職位、待審案件與人事。下方亦可操作個人的應徵、工作與辭職。"
                "\n\n**我的任職公司與累積經驗**\n" + (employees or '目前沒有任職公司。') +
                "\n\n注意：公司僱傭身分不會改變任何陣營身分。")
    return ("👔 **玩家公司中心**\n可應徵、辭職、提交工作，並查看所屬公司與職位進度。"
            "\n\n**我的任職公司與各職位經驗**\n" + (employees or '目前沒有任職公司。') +
            "\n\nEXP 一律累加，升職和辭職都不會歸零。公司任職不代表加入該公司陣營。")


class CompanyPortal(commands.Cog):
    @app_commands.command(name='公司',description='依身分進入公司營運或員工管理中心')
    @app_commands.guild_only()
    async def company(self,i:discord.Interaction):
        await i.response.defer(ephemeral=True)
        try:
            owned, employees = await asyncio.to_thread(company_context,
                                                        str(i.guild_id),str(i.user.id))
        except sqlite3.Error:
            return await i.followup.send('公司資料讀取失敗，請聯絡管理員。',ephemeral=True)
        await i.followup.send(panel_text(owned,employees)[:1900],
            view=CompanyPanel(str(i.user.id),str(i.guild_id),bool(owned)),ephemeral=True)


async def setup(bot):
    await bot.add_cog(CompanyPortal())
