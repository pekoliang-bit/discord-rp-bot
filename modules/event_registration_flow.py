"""Activity registration: per-character eligibility, review, lottery and public notices.

No rewards are paid automatically. Official rewards remain subject to approved settlement.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import transaction, get_connection
from modules.event_project_manager import fetch_event, is_event_admin, can_manage

LOG = logging.getLogger(__name__)


def init_db():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_event_registration_config (
            guild_id TEXT NOT NULL, event_id INTEGER NOT NULL,
            deadline TEXT NOT NULL, review_mode TEXT NOT NULL DEFAULT 'system',
            min_age INTEGER, max_age INTEGER, occupation TEXT NOT NULL DEFAULT '',
            notice_channel_id TEXT, published_message_id TEXT,
            finalized_at TEXT, result_message_id TEXT,
            PRIMARY KEY(guild_id,event_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_event_character_entries (
            guild_id TEXT NOT NULL, event_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL, owner_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,event_id,character_id))''')
        db.execute('''CREATE INDEX IF NOT EXISTS idx_event_entries_status
            ON rp_event_character_entries(guild_id,event_id,status)''')


def now_utc():
    return datetime.now(timezone.utc)


def parse_deadline(value):
    try:
        result = datetime.fromisoformat(value.strip())
        if result.tzinfo is None:
            raise ValueError()
        result = result.astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError('截止時間請填 2026-12-01T20:00+08:00 這類含時區的格式。') from exc
    if result <= now_utc():
        raise ValueError('截止時間必須是未來。')
    return result.isoformat()


def event_config(db, gid, eid):
    row = db.execute('SELECT * FROM rp_event_registration_config WHERE guild_id=? AND event_id=?',
                     (str(gid), int(eid))).fetchone()
    return dict(row) if row else None


def setup_registration(gid, eid, uid, deadline, review_mode, min_age, max_age, occupation, channel_id):
    if review_mode not in ('system','organizer'):
        raise ValueError('審核方式必須是 system 或 organizer。')
    if min_age is not None and not 0 <= min_age <= 200:
        raise ValueError('最低年齡不正確。')
    if max_age is not None and not 0 <= max_age <= 200:
        raise ValueError('最高年齡不正確。')
    if min_age is not None and max_age is not None and min_age > max_age:
        raise ValueError('最低年齡不能大於最高年齡。')
    deadline = parse_deadline(deadline)
    with transaction() as db:
        e = fetch_event(db, gid, eid)
        if e['status'] not in ('draft','rejected','submitted','approved','open'):
            raise ValueError('目前活動狀態不可調整報名設定。')
        if e['status'] == 'open':
            existing = db.execute('SELECT COUNT(*) n FROM rp_event_character_entries WHERE guild_id=? AND event_id=?',
                                  (str(gid), eid)).fetchone()['n']
            if existing:
                raise ValueError('已有報名資料，不能改變資格或截止時間。')
        db.execute('''INSERT INTO rp_event_registration_config
            (guild_id,event_id,deadline,review_mode,min_age,max_age,occupation,notice_channel_id)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,event_id) DO UPDATE SET
            deadline=excluded.deadline, review_mode=excluded.review_mode,
            min_age=excluded.min_age,max_age=excluded.max_age,
            occupation=excluded.occupation,notice_channel_id=excluded.notice_channel_id''',
            (str(gid), eid, deadline, review_mode,min_age,max_age,occupation.strip(),str(channel_id)))


def eligible_characters(db, gid, eid, owner_id):
    config = event_config(db,gid,eid)
    if not config:
        raise ValueError('活動尚未設定報名截止、資格及公告頻道。')
    rows = db.execute('''SELECT id,name,age,occupation FROM characters
                       WHERE guild_id=? AND owner_id=? AND status='active'
                       ORDER BY id''',(str(gid),str(owner_id))).fetchall()
    good = []
    for r in rows:
        if config['min_age'] is not None and (r['age'] is None or r['age'] < config['min_age']):
            continue
        if config['max_age'] is not None and (r['age'] is None or r['age'] > config['max_age']):
            continue
        if config['occupation'] and r['occupation'].strip() != config['occupation'].strip():
            continue
        good.append(dict(r))
    return good


def register(gid,eid,owner_id,char_ids):
    char_ids = list(dict.fromkeys(int(x) for x in char_ids))
    if not char_ids:
        raise ValueError('至少選擇一名角色。')
    with transaction() as db:
        event = fetch_event(db,gid,eid)
        cfg = event_config(db,gid,eid)
        if event['status'] != 'open' or not cfg or cfg['finalized_at']:
            raise ValueError('目前沒有開放報名。')
        if now_utc() >= datetime.fromisoformat(cfg['deadline']):
            raise ValueError('報名已截止。')
        valid = {r['id']:r for r in eligible_characters(db,gid,eid,owner_id)}
        if any(x not in valid for x in char_ids):
            raise ValueError('選擇了非本人、未啟用或不符合基本資格的角色。')
        for cid in char_ids:
            if db.execute('SELECT 1 FROM rp_event_character_entries WHERE guild_id=? AND event_id=? AND character_id=?',
                          (str(gid),eid,cid)).fetchone():
                raise ValueError(f'角色 #{cid} 已報名，不可重複。')
        for cid in char_ids:
            # System review validates database conditions. Text-only qualification
            # remains subject to organizer verification if selected.
            state = 'qualified' if cfg['review_mode']=='system' else 'pending'
            db.execute('''INSERT INTO rp_event_character_entries
                          (guild_id,event_id,character_id,owner_id,status)
                          VALUES (?,?,?,?,?)''',(str(gid),eid,cid,str(owner_id),state))
    return len(char_ids)


def registration_rows(gid,eid):
    with get_connection() as db:
        return [dict(r) for r in db.execute('''SELECT r.character_id,r.owner_id,r.status,c.name
           FROM rp_event_character_entries r JOIN characters c
           ON c.guild_id=r.guild_id AND c.id=r.character_id
           WHERE r.guild_id=? AND r.event_id=? ORDER BY r.created_at,r.character_id''',
           (str(gid),eid)).fetchall()]


def review_entry(gid,eid,character_id,reviewer,approve):
    with transaction() as db:
        event = fetch_event(db,gid,eid)
        cfg = event_config(db,gid,eid)
        if event['status'] != 'open' or not cfg or cfg['finalized_at']:
            raise ValueError('活動不可再審核報名。')
        row=db.execute('SELECT * FROM rp_event_character_entries WHERE guild_id=? AND event_id=? AND character_id=?',
                       (str(gid),eid,character_id)).fetchone()
        if not row or row['status'] not in ('pending','qualified'):
            raise ValueError('找不到可審核的角色報名。')
        db.execute('UPDATE rp_event_character_entries SET status=?,reviewed_by=? WHERE guild_id=? AND event_id=? AND character_id=?',
                   ('qualified' if approve else 'rejected',str(reviewer),str(gid),eid,character_id))


def finalize(gid,eid,actor):
    """Idempotent, single-transaction, fair drawing after deadline."""
    with transaction() as db:
        event=fetch_event(db,gid,eid)
        cfg=event_config(db,gid,eid)
        if not cfg or event['status'] != 'open':
            raise ValueError('活動沒有開放中的報名設定。')
        if cfg['finalized_at']:
            raise ValueError('此活動已完成抽籤，不可重抽。')
        if now_utc() < datetime.fromisoformat(cfg['deadline']):
            raise ValueError('尚未到報名截止時間。')
        waiting=db.execute('SELECT COUNT(*) n FROM rp_event_character_entries WHERE guild_id=? AND event_id=? AND status=?',
                           (str(gid),eid,'pending')).fetchone()['n']
        if waiting:
            raise ValueError(f'還有 {waiting} 名角色等待人工資格確認，請先完成審核。')
        entries=[r['character_id'] for r in db.execute('''SELECT character_id FROM rp_event_character_entries
                    WHERE guild_id=? AND event_id=? AND status='qualified' ORDER BY character_id''',
                    (str(gid),eid)).fetchall()]
        secrets.SystemRandom().shuffle(entries)
        winners=set(entries[:event['capacity']])
        for cid in entries:
            db.execute('UPDATE rp_event_character_entries SET status=? WHERE guild_id=? AND event_id=? AND character_id=?',
                       ('accepted' if cid in winners else 'waitlist',str(gid),eid,cid))
        # Synchronize the legacy user-level enrollment for existing event-task
        # and settlement services; character-level authorization remains stricter.
        for owner in db.execute('''SELECT DISTINCT owner_id FROM rp_event_character_entries
                     WHERE guild_id=? AND event_id=? AND status='accepted' ''',(str(gid),eid)).fetchall():
            db.execute('''INSERT INTO rp_event_enrollments (guild_id,event_id,user_id,status)
                   VALUES (?,?,?,'accepted')
                   ON CONFLICT(guild_id,event_id,user_id) DO UPDATE SET status='accepted' ''',
                   (str(gid),eid,str(owner['owner_id'])))
        db.execute('UPDATE rp_event_registration_config SET finalized_at=? WHERE guild_id=? AND event_id=?',
                   (now_utc().isoformat(),str(gid),eid))
        db.execute('UPDATE rp_event_projects SET status=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?',
                   ('running',str(gid),eid))
        db.execute('INSERT INTO rp_event_project_audit (guild_id,event_id,actor_id,action,detail) VALUES (?,?,?,?,?)',
                   (str(gid),eid,str(actor),'lottery_finalize',f'qualified={len(entries)}, accepted={len(winners)}'))
    return len(entries),len(winners)


def roster_text(gid,eid,limit=1750):
    rows=registration_rows(gid,eid)
    passers=[f"{r['name']} (#{r['character_id']})" for r in rows if r['status']=='accepted']
    waiting=[f"{r['name']} (#{r['character_id']})" for r in rows if r['status'] in ('pending','qualified')]
    alternate=[f"{r['name']} (#{r['character_id']})" for r in rows if r['status']=='waitlist']
    text='✅ 已通過：'+('、'.join(passers) or '無')+'\n⏳ 審核中／待抽籤：'+('、'.join(waiting) or '無')
    if alternate:
        text+='\n🎟️ 候補：'+'、'.join(alternate)
    return text[:limit] + ('…（名單過長，請由管理員查詢）' if len(text)>limit else '')


class CharacterPicker(discord.ui.Select):
    def __init__(self, eid, owner_id, characters):
        self.eid=eid
        self.owner_id=owner_id
        options=[discord.SelectOption(label=r['name'][:90],value=str(r['id']),
                                      description=f"角色 #{r['id']}｜{r['occupation'][:45]}") for r in characters[:25]]
        super().__init__(placeholder='勾選符合資格的角色（最多 25 名）',options=options,
                         min_values=1,max_values=len(options))

    async def callback(self,i):
        if i.user.id!=self.owner_id:
            return await i.response.send_message('這不是你的報名選單。',ephemeral=True)
        try:
            n=register(i.guild_id,self.eid,i.user.id,self.values)
            await i.response.send_message(f'✅ 已提交 {n} 名角色的報名。最終名單將於截止抽籤後公布。',ephemeral=True)
        except (ValueError,PermissionError) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)


class CharacterPickerView(discord.ui.View):
    def __init__(self,eid,uid,characters):
        super().__init__(timeout=180)
        self.add_item(CharacterPicker(eid,uid,characters))


class PublicEventView(discord.ui.View):
    def __init__(self,eid,gid):
        super().__init__(timeout=None)
        self.eid=eid
        self.gid=int(gid)
        # custom IDs are unique per event, and the view is re-registered at boot.
        join=discord.ui.Button(label='🎟️ 報名活動',style=discord.ButtonStyle.primary,
                               custom_id=f'rp:event:{gid}:{eid}:join')
        roster=discord.ui.Button(label='📋 參與角色名單',style=discord.ButtonStyle.secondary,
                                 custom_id=f'rp:event:{gid}:{eid}:roster')
        join.callback=self.join_callback
        roster.callback=self.roster_callback
        self.add_item(join)
        self.add_item(roster)

    async def join_callback(self,i):
        try:
            if i.guild_id != self.gid:
                raise ValueError('此活動不屬於本伺服器。')
            with get_connection() as db:
                event=fetch_event(db,i.guild_id,self.eid)
                people=eligible_characters(db,i.guild_id,self.eid,i.user.id)
            if event['status']!='open':
                raise ValueError('此活動已截止或尚未開放。')
            if not people:
                raise ValueError('你沒有符合資料庫基本資格的已啟用角色。')
            await i.response.send_message('請勾選參加角色；特殊文字條件須由主辦確認。',
                                          ephemeral=True,view=CharacterPickerView(self.eid,i.user.id,people))
        except ValueError as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

    async def roster_callback(self,i):
        try:
            if i.guild_id != self.gid:
                raise ValueError('此活動不屬於本伺服器。')
            with get_connection() as db:
                fetch_event(db,i.guild_id,self.eid)
            await i.response.send_message('📋 活動參與名單\n'+roster_text(i.guild_id,self.eid),ephemeral=True)
        except ValueError as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)


class RegistrationModal(discord.ui.Modal,title='設定活動報名、審核與公告'):
    deadline=discord.ui.TextInput(label='報名截止（須含時區）',placeholder='2026-12-01T20:00+08:00',max_length=35)
    review=discord.ui.TextInput(label='審核模式 system／organizer',default='system',max_length=12)
    minage=discord.ui.TextInput(label='最低年齡（可空白）',required=False,max_length=3)
    maxage=discord.ui.TextInput(label='最高年齡（可空白）',required=False,max_length=3)
    occupation=discord.ui.TextInput(label='限制職業（空白不限）',required=False,max_length=100)

    def __init__(self,eid,notice_channel):
        super().__init__()
        self.eid=eid
        self.notice_channel=notice_channel

    async def on_submit(self,i):
        try:
            setup_registration(i.guild_id,self.eid,i.user.id,str(self.deadline),str(self.review).strip().lower(),
                               int(str(self.minage)) if str(self.minage).strip() else None,
                               int(str(self.maxage)) if str(self.maxage).strip() else None,
                               str(self.occupation),self.notice_channel)
            await i.response.send_message(f'✅ 活動 #{self.eid} 報名設定完成。獎勵及活動參加頻道仍以企劃資料為準。',ephemeral=True)
        except (ValueError,PermissionError) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)


class EventRegistration(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self.deadline_worker.start()

    def cog_unload(self):
        self.deadline_worker.cancel()

    @tasks.loop(minutes=2)
    async def deadline_worker(self):
        # Poll only eligible completed reviews; pending manual cases remain open.
        with get_connection() as db:
            rows=[dict(r) for r in db.execute('''SELECT p.id,p.guild_id,c.deadline,c.finalized_at,c.result_message_id,c.notice_channel_id
                FROM rp_event_projects p JOIN rp_event_registration_config c
                ON p.guild_id=c.guild_id AND p.id=c.event_id
                WHERE p.status='open' OR (c.finalized_at IS NOT NULL AND c.result_message_id IS NULL)''').fetchall()]
        for row in rows:
            guild=self.bot.get_guild(int(row['guild_id']))
            if guild is None:
                continue
            channel=guild.get_channel(int(row['notice_channel_id'])) if row['notice_channel_id'] else None
            if not isinstance(channel,discord.TextChannel):
                continue
            try:
                with get_connection() as db:
                    event=dict(fetch_event(db,row['guild_id'],row['id']))
                if not row['finalized_at']:
                    if now_utc()<datetime.fromisoformat(row['deadline']):
                        continue
                    with get_connection() as db:
                        pending=db.execute('SELECT COUNT(*) n FROM rp_event_character_entries WHERE guild_id=? AND event_id=? AND status=?',
                                           (str(row['guild_id']),row['id'],'pending')).fetchone()['n']
                    if pending:
                        continue
                    finalize(row['guild_id'],row['id'],self.bot.user.id)
                if not row['result_message_id']:
                    await publish_result(guild,row['id'],event,channel)
            except Exception:
                LOG.exception('Activity deadline processing failed: %s/%s',row['guild_id'],row['id'])

    @deadline_worker.before_loop
    async def before_deadlines(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name='活動報名設定',description='設定截止時間、角色資格、審核方式及活動公告頻道')
    @app_commands.guild_only()
    async def configure(self,i:discord.Interaction,活動編號:int,公告頻道:discord.TextChannel):
        try:
            with get_connection() as db:
                event=fetch_event(db,i.guild_id,活動編號)
            if not (is_event_admin(i) or str(event['creator_id'])==str(i.user.id)):
                raise PermissionError('只有企劃建立者或管理員可設定。')
            await i.response.send_modal(RegistrationModal(活動編號,公告頻道.id))
        except (ValueError,PermissionError) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

    @app_commands.command(name='活動發布報名',description='在指定活動公告頻道發布附有報名及名單按鈕的公告')
    @app_commands.guild_only()
    async def announce(self,i:discord.Interaction,活動編號:int):
        try:
            with get_connection() as db:
                e=fetch_event(db,i.guild_id,活動編號)
                cfg=event_config(db,i.guild_id,活動編號)
            if not can_manage(i,e):
                raise PermissionError('只有本活動主辦及正式管理員可公告。')
            if e['status']!='open' or not cfg:
                raise ValueError('活動須先獲核准、開放報名並設定報名資訊。')
            if cfg['published_message_id']:
                raise ValueError('此活動報名公告已發布，避免重複通知。')
            channel=i.guild.get_channel(int(cfg['notice_channel_id']))
            if not isinstance(channel,discord.TextChannel):
                raise ValueError('找不到公告頻道。')
            await i.response.defer(ephemeral=True)
            # Mention each owner of at least one active character; do not ping nonplayers.
            with get_connection() as db:
                owners=[str(r['owner_id']) for r in db.execute('''SELECT DISTINCT owner_id FROM characters
                          WHERE guild_id=? AND status='active' ''',(str(i.guild_id),)).fetchall()]
            activity_channel=(f'<#{e["channel_id"]}>' if e['channel_id'] else '待管理員建立專用頻道')
            await channel.send(f'🎪 **活動開放報名｜#{活動編號} {e["title"]}**\n'
                f'主辦：<@{e["organizer_id"]}>｜名額：{e["capacity"]}\n'
                f'報名截止：<t:{int(datetime.fromisoformat(cfg["deadline"]).timestamp())}:F>\n'
                f'資格：{e["eligibility"][:500]}\n活動參加頻道：{activity_channel}\n'
                f'獎勵：{e["rewards_text"][:500]}\n稱號：{e["title_reward_text"][:200] or "無"}\n'
                f'審核方式：{"系統基本資格判定" if cfg["review_mode"]=="system" else "主辦審核"}',
                view=PublicEventView(活動編號,i.guild_id),allowed_mentions=discord.AllowedMentions.none())
            with transaction() as db:
                db.execute('UPDATE rp_event_registration_config SET published_message_id=? WHERE guild_id=? AND event_id=?',
                           ('published',str(i.guild_id),活動編號))
            # Chunk mentions under Discord message size; mention only validated active owners.
            for start in range(0,len(owners),60):
                await channel.send('📣 活動報名提醒： '+' '.join(f'<@{uid}>' for uid in owners[start:start+60]),
                                   allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
            await i.followup.send('✅ 已發布活動報名及正式角色玩家通知。',ephemeral=True)
        except (ValueError,PermissionError,discord.HTTPException) as exc:
            if i.response.is_done():
                await i.followup.send(f'⚠️ {exc}',ephemeral=True)
            else:
                await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

    @app_commands.command(name='活動角色報名審核',description='審核單一角色參與活動的資格（不是玩家帳號）')
    @app_commands.guild_only()
    async def inspect(self,i:discord.Interaction,活動編號:int,角色編號:int,通過:bool):
        try:
            with get_connection() as db:
                e=fetch_event(db,i.guild_id,活動編號)
                cfg=event_config(db,i.guild_id,活動編號)
            if not cfg or not (is_event_admin(i) or (cfg['review_mode']=='organizer' and can_manage(i,e))):
                raise PermissionError('目前僅管理員或獲授權的主辦可審核。')
            review_entry(i.guild_id,活動編號,角色編號,i.user.id,通過)
            await i.response.send_message('✅ 已更新角色報名資格。',ephemeral=True)
        except (ValueError,PermissionError) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

    @app_commands.command(name='活動截止抽籤',description='截止後統一抽籤，發布已通過及候補名單')
    @app_commands.guild_only()
    async def finish(self,i:discord.Interaction,活動編號:int):
        try:
            with get_connection() as db:
                e=fetch_event(db,i.guild_id,活動編號)
                cfg=event_config(db,i.guild_id,活動編號)
            if not can_manage(i,e):
                raise PermissionError('只有主辦或活動管理員可以結算。')
            if not cfg:
                raise ValueError('尚未設定報名。')
            channel=i.guild.get_channel(int(cfg['notice_channel_id']))
            if not isinstance(channel,discord.TextChannel):
                raise ValueError('公告頻道不存在。')
            count,won=finalize(i.guild_id,活動編號,i.user.id)
            await i.response.defer(ephemeral=True)
            msg=await channel.send(f'📣 **活動報名截止｜#{活動編號} {e["title"]}**\n'
                                   f'有效報名：{count} 人，錄取：{won} 人。\n'+roster_text(i.guild_id,活動編號))
            with transaction() as db:
                db.execute('UPDATE rp_event_registration_config SET result_message_id=? WHERE guild_id=? AND event_id=?',
                           (str(msg.id),str(i.guild_id),活動編號))
            await i.followup.send('✅ 抽籤完成並公布名單。',ephemeral=True)
        except (ValueError,PermissionError,discord.HTTPException) as exc:
            if i.response.is_done():
                await i.followup.send(f'⚠️ {exc}',ephemeral=True)
            else:
                await i.response.send_message(f'⚠️ {exc}',ephemeral=True)

    @app_commands.command(name='活動角色名單',description='查看活動已錄取、待審核及候補名單')
    @app_commands.guild_only()
    async def roster(self,i:discord.Interaction,活動編號:int):
        try:
            with get_connection() as db:
                fetch_event(db,i.guild_id,活動編號)
            await i.response.send_message(roster_text(i.guild_id,活動編號),ephemeral=True)
        except ValueError as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)


async def setup(bot):
    init_db()
    await bot.add_cog(EventRegistration(bot))
    with get_connection() as db:
        rows=db.execute('''SELECT p.id,p.guild_id FROM rp_event_projects p JOIN rp_event_registration_config c
            ON p.guild_id=c.guild_id AND p.id=c.event_id
            WHERE p.status='open' AND c.published_message_id IS NOT NULL''').fetchall()
    for row in rows:
        bot.add_view(PublicEventView(row['id'],row['guild_id']))

# The public slash commands above are intentionally not relied on as navigation:
# the bot's official command filter removes standalone activity commands.
# All management actions are also accessible through /官方 > 內容建立.
class AdminEventIdModal(discord.ui.Modal,title='活動編號'):
    eid=discord.ui.TextInput(label='活動企劃編號',max_length=12)
    def __init__(self,action):
        super().__init__()
        self.action=action
    async def on_submit(self,i):
        try:
            eid=int(str(self.eid).strip())
            with get_connection() as db:
                e=fetch_event(db,i.guild_id,eid)
                cfg=event_config(db,i.guild_id,eid)
            if not can_manage(i,e) and not (self.action=='config' and str(e['creator_id'])==str(i.user.id)):
                raise PermissionError('你沒有這場活動的操作權限。')
            if self.action=='config':
                await i.response.send_message('先選擇活動公告頻道（活動實際參與頻道另於企劃內選擇）。',
                                              ephemeral=True,view=NoticeChannelPicker(eid,i.user.id))
            elif self.action=='announce':
                await publish_registration(i,eid,e,cfg)
            elif self.action=='finish':
                await close_registration(i,eid,e,cfg)
            elif self.action=='review':
                await i.response.send_modal(EntryReviewModal(eid))
            else:
                raise ValueError('未知操作。')
        except (ValueError,PermissionError) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)


class NoticeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,eid,uid):
        super().__init__(placeholder='選擇活動報名公告頻道',channel_types=[discord.ChannelType.text])
        self.eid=eid
        self.uid=uid
    async def callback(self,i):
        if i.user.id!=self.uid:
            return await i.response.send_message('這不是你的設定選單。',ephemeral=True)
        await i.response.send_modal(RegistrationModal(self.eid,self.values[0].id))

class NoticeChannelPicker(discord.ui.View):
    def __init__(self,eid,uid):
        super().__init__(timeout=180)
        self.add_item(NoticeChannelSelect(eid,uid))


class EntryReviewModal(discord.ui.Modal,title='審核活動角色資格'):
    cid=discord.ui.TextInput(label='報名角色編號',max_length=12)
    decision=discord.ui.TextInput(label='決定：通過／拒絕',placeholder='通過',max_length=5)
    def __init__(self,eid):
        super().__init__()
        self.eid=eid
    async def on_submit(self,i):
        try:
            with get_connection() as db:
                e=fetch_event(db,i.guild_id,self.eid)
                cfg=event_config(db,i.guild_id,self.eid)
            if not cfg or not (is_event_admin(i) or (cfg['review_mode']=='organizer' and can_manage(i,e))):
                raise PermissionError('沒有活動報名資格審核權限。')
            choice=str(self.decision).strip()
            if choice not in ('通過','拒絕'):
                raise ValueError('請填「通過」或「拒絕」。')
            review_entry(i.guild_id,self.eid,int(str(self.cid)),i.user.id,choice=='通過')
            await i.response.send_message('✅ 報名資格判定已儲存。',ephemeral=True)
        except (ValueError,PermissionError) as exc:
            await i.response.send_message(f'⚠️ {exc}',ephemeral=True)


class EventRegistrationAdminView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600)
        self.owner_id=i.user.id
    async def interaction_check(self,i):
        if i.user.id!=self.owner_id:
            await i.response.send_message('這不是你的活動管理選單。',ephemeral=True)
            return False
        return True
    @discord.ui.button(label='⚙️ 報名設定／公告頻道',style=discord.ButtonStyle.primary,row=0)
    async def config(self,i,button):
        await i.response.send_modal(AdminEventIdModal('config'))
    @discord.ui.button(label='📣 發布報名公告',style=discord.ButtonStyle.primary,row=0)
    async def announce(self,i,button):
        await i.response.send_modal(AdminEventIdModal('announce'))
    @discord.ui.button(label='✅ 報名資格審核',style=discord.ButtonStyle.secondary,row=1)
    async def review(self,i,button):
        await i.response.send_modal(AdminEventIdModal('review'))
    @discord.ui.button(label='🎲 截止抽籤與公布',style=discord.ButtonStyle.success,row=1)
    async def finish(self,i,button):
        await i.response.send_modal(AdminEventIdModal('finish'))


async def publish_registration(i,eid,e,cfg):
    if e['status']!='open' or not cfg:
        raise ValueError('須先審核通過、開放報名並設定公告頻道。')
    if cfg['published_message_id']:
        raise ValueError('已發布報名公告，不可重複推播。')
    channel=i.guild.get_channel(int(cfg['notice_channel_id']))
    if not isinstance(channel,discord.TextChannel):
        raise ValueError('公告頻道不存在。')
    await i.response.defer(ephemeral=True)
    await _publish(i.guild,eid,e,cfg,channel)
    await i.followup.send('✅ 已發布報名公告及已啟用角色玩家提醒。',ephemeral=True)

async def _publish(guild,eid,e,cfg,channel):
    activity_channel=f'<#{e["channel_id"]}>' if e['channel_id'] else '待管理員建立活動專用頻道'
    msg=await channel.send(f'🎪 **活動開放報名｜#{eid} {e["title"]}**\n'
        f'主辦：<@{e["organizer_id"]}>｜名額：{e["capacity"]}\n'
        f'報名截止：<t:{int(datetime.fromisoformat(cfg["deadline"]).timestamp())}:F>\n'
        f'資格：{e["eligibility"][:400]}\n活動參加頻道：{activity_channel}\n'
        f'活動時間：{e["schedule_text"][:200]}\n獎勵：{e["rewards_text"][:400]}\n'
        f'稱號：{e["title_reward_text"][:200] or "無"}',
        view=PublicEventView(eid,guild.id),allowed_mentions=discord.AllowedMentions.none())
    with transaction() as db:
        db.execute('UPDATE rp_event_registration_config SET published_message_id=? WHERE guild_id=? AND event_id=?',
                   (str(msg.id),str(guild.id),eid))
    with get_connection() as db:
        owners=[str(r['owner_id']) for r in db.execute('SELECT DISTINCT owner_id FROM characters WHERE guild_id=? AND status=?',
                                                        (str(guild.id),'active')).fetchall()]
    for start in range(0,len(owners),60):
        await channel.send('📣 活動報名提醒：'+' '.join(f'<@{uid}>' for uid in owners[start:start+60]),
                           allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))


async def close_registration(i,eid,e,cfg):
    if not cfg:
        raise ValueError('尚未設定活動報名。')
    channel=i.guild.get_channel(int(cfg['notice_channel_id']))
    if not isinstance(channel,discord.TextChannel):
        raise ValueError('公告頻道不存在。')
    n,won=finalize(i.guild_id,eid,i.user.id)
    await i.response.defer(ephemeral=True)
    await publish_result(i.guild,eid,e,channel,n,won)
    await i.followup.send('✅ 報名截止、統一抽籤並公布名單。',ephemeral=True)


async def publish_result(guild,eid,e,channel,n=None,won=None):
    rows=registration_rows(guild.id,eid)
    if n is None:
        n=sum(r['status'] in ('accepted','waitlist') for r in rows)
    if won is None:
        won=sum(r['status']=='accepted' for r in rows)
    msg=await channel.send(f'📣 **活動報名截止｜#{eid} {e["title"]}**\n'
                           f'有效報名：{n} 名角色；錄取：{won} 名。\n'+roster_text(guild.id,eid))
    with transaction() as db:
        db.execute('UPDATE rp_event_registration_config SET result_message_id=? WHERE guild_id=? AND event_id=?',
                   (str(msg.id),str(guild.id),eid))
