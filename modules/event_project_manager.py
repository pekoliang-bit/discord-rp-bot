"""Discord RP｜活動企劃、審核與限權主辦管理。

玩家使用 /活動企劃 投稿。
群主／授權管理員使用 /活動管理。

臨時主辦身分組只有辨識用途，
不授予 Discord 管理權限。
實際權限逐活動驗證資料庫。

獎勵與稱號先保存待審設定，
不會因企劃建立而自動發放。
"""

from __future__ import annotations

import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction, get_connection


LOG = logging.getLogger(__name__)

KINDS = {
    "normal": "一般活動",
    "main": "世界／主線事件",
    "competition": "競賽",
    "limited": "限時活動",
}

STATES = {
    "draft": "草稿",
    "submitted": "待審核",
    "approved": "已核准",
    "rejected": "已駁回",
    "open": "報名中",
    "running": "進行中",
    "closed": "已結束",
    "cancelled": "已取消",
}

ACTIVE_STATES = (
    "approved",
    "open",
    "running",
)


# ==========================================
# 資料庫
# ==========================================

def init_db():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                creator_id TEXT NOT NULL,
                organizer_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                kind TEXT NOT NULL,
                eligibility TEXT NOT NULL,
                capacity INTEGER NOT NULL
                    CHECK(capacity BETWEEN 1 AND 100000),

                channel_mode TEXT NOT NULL
                    DEFAULT 'existing',
                channel_id TEXT,
                category_id TEXT,

                rewards_text TEXT NOT NULL DEFAULT '',
                title_reward_text TEXT NOT NULL DEFAULT '',
                schedule_text TEXT NOT NULL DEFAULT '',

                status TEXT NOT NULL DEFAULT 'draft',
                review_by TEXT,
                review_note TEXT,

                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_admin_grants (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                granted_by TEXT NOT NULL,

                PRIMARY KEY(guild_id, user_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_settings (
                guild_id TEXT PRIMARY KEY,
                organizer_role_id TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_organizers (
                guild_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                granted_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY(guild_id, event_id, user_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_enrollments (
                guild_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                completed INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                reviewed_by TEXT,

                PRIMARY KEY(guild_id, event_id, user_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_project_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                event_id INTEGER,
                actor_id TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)


# ==========================================
# 操作紀錄
# ==========================================

def audit(db, gid, eid, uid, action, detail=""):
    db.execute(
        """
        INSERT INTO rp_event_project_audit (
            guild_id,
            event_id,
            actor_id,
            action,
            detail
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(gid),
            eid,
            str(uid),
            action,
            str(detail)[:500],
        ),
    )


# ==========================================
# 權限
# ==========================================

def is_owner(i):
    return (
        i.guild is not None
        and i.user.id == i.guild.owner_id
    )


def is_event_admin(i):
    if is_owner(i):
        return True

    if i.guild is None:
        return False

    permissions = i.user.guild_permissions

    if (
        permissions.administrator
        or permissions.manage_guild
    ):
        return True

    with get_connection() as db:
        row = db.execute(
            """
            SELECT 1
            FROM rp_event_admin_grants
            WHERE guild_id=?
              AND user_id=?
            """,
            (
                str(i.guild_id),
                str(i.user.id),
            ),
        ).fetchone()

    return row is not None


def fetch_event(db, gid, eid):
    row = db.execute(
        """
        SELECT *
        FROM rp_event_projects
        WHERE guild_id=?
          AND id=?
        """,
        (
            str(gid),
            int(eid),
        ),
    ).fetchone()

    if row is None:
        raise ValueError(
            "活動不存在，或不屬於此伺服器。"
        )

    return row


def can_manage(i, event):
    if is_event_admin(i):
        return True

    if (
        str(event["organizer_id"]) != str(i.user.id)
        or event["status"] not in ACTIVE_STATES
    ):
        return False

    with get_connection() as db:
        row = db.execute(
            """
            SELECT revoked
            FROM rp_event_organizers
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(i.guild_id),
                int(event["id"]),
                str(i.user.id),
            ),
        ).fetchone()

    return (
        row is not None
        and not bool(row["revoked"])
    )


# ==========================================
# 建立活動企劃
# ==========================================

def submit_project(
    gid,
    uid,
    title,
    description,
    kind,
    eligibility,
    capacity,
):
    if kind not in KINDS:
        raise ValueError("活動類型不正確。")

    title = title.strip()
    description = description.strip()
    eligibility = eligibility.strip()

    if not 1 <= len(title) <= 100:
        raise ValueError("活動名稱需為1～100字。")

    if not 1 <= len(description) <= 3500:
        raise ValueError("活動內容需為1～3500字。")

    if not 1 <= len(eligibility) <= 1000:
        raise ValueError("參與資格需為1～1000字。")

    if (
        type(capacity) is not int
        or not 1 <= capacity <= 100000
    ):
        raise ValueError(
            "名額必須是1～100000的整數。"
        )

    with transaction() as db:
        cur = db.execute(
            """
            INSERT INTO rp_event_projects (
                guild_id,
                creator_id,
                organizer_id,
                title,
                description,
                kind,
                eligibility,
                capacity,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                str(gid),
                str(uid),
                str(uid),
                title,
                description,
                kind,
                eligibility,
                capacity,
            ),
        )

        eid = cur.lastrowid

        audit(
            db,
            gid,
            eid,
            uid,
            "create",
        )

    return eid


# ==========================================
# 補充活動企劃
# ==========================================

def set_details(
    gid,
    uid,
    eid,
    mode,
    channel_id,
    category_id,
    rewards,
    title_rewards,
    schedule,
):
    if mode not in ("existing", "new"):
        raise ValueError(
            "頻道模式只能為existing或new。"
        )

    if (
        mode == "existing"
        and not str(channel_id).isdigit()
    ):
        raise ValueError(
            "使用現有頻道時，請填入頻道ID。"
        )

    if (
        mode == "new"
        and category_id
        and not str(category_id).isdigit()
    ):
        raise ValueError("新頻道分類ID無效。")

    if (
        len(rewards) > 1500
        or len(title_rewards) > 400
        or len(schedule) > 500
    ):
        raise ValueError(
            "獎勵、稱號或活動時間文字過長。"
        )

    with transaction() as db:
        event = fetch_event(db, gid, eid)

        if (
            event["status"] not in ("draft", "rejected")
            or str(event["creator_id"]) != str(uid)
        ):
            raise PermissionError(
                "只有企劃提交者能修改草稿或退件稿。"
            )

        db.execute(
            """
            UPDATE rp_event_projects
            SET
                channel_mode=?,
                channel_id=?,
                category_id=?,
                rewards_text=?,
                title_reward_text=?,
                schedule_text=?,
                status='draft',
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
            """,
            (
                mode,
                str(channel_id).strip() or None,
                str(category_id).strip() or None,
                rewards.strip(),
                title_rewards.strip(),
                schedule.strip(),
                str(gid),
                int(eid),
            ),
        )

        audit(
            db,
            gid,
            eid,
            uid,
            "details",
        )


# ==========================================
# 送審
# ==========================================

def submit_for_review(gid, uid, eid):
    with transaction() as db:
        event = fetch_event(db, gid, eid)

        if (
            str(event["creator_id"]) != str(uid)
            or event["status"] not in ("draft", "rejected")
        ):
            raise PermissionError(
                "只能送審自己的草稿或退件企劃。"
            )

        if (
            not event["rewards_text"]
            and not event["title_reward_text"]
        ):
            raise ValueError(
                "請先填寫獎勵，"
                "無獎勵也請明確填寫「無」。"
            )

        if (
            event["channel_mode"] == "existing"
            and not event["channel_id"]
        ):
            raise ValueError(
                "請先設定活動頻道。"
            )

        db.execute(
            """
            UPDATE rp_event_projects
            SET status='submitted',
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
            """,
            (
                str(gid),
                int(eid),
            ),
        )

        audit(
            db,
            gid,
            eid,
            uid,
            "submit",
        )


# ==========================================
# 官方審核
# ==========================================

def review(
    gid,
    reviewer,
    eid,
    approve,
    note,
    owner_override=False,
):
    with transaction() as db:
        event = fetch_event(db, gid, eid)

        if event["status"] != "submitted":
            raise ValueError(
                "只能審核待審核的企劃。"
            )

        if (
            not owner_override
            and str(event["creator_id"]) == str(reviewer)
        ):
            raise PermissionError(
                "管理員不能審核自己的企劃，"
                "群主除外。"
            )

        state = (
            "approved"
            if approve
            else "rejected"
        )

        db.execute(
            """
            UPDATE rp_event_projects
            SET status=?,
                review_by=?,
                review_note=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
            """,
            (
                state,
                str(reviewer),
                note[:500],
                str(gid),
                int(eid),
            ),
        )

        if approve:
            db.execute(
                """
                INSERT INTO rp_event_organizers (
                    guild_id,
                    event_id,
                    user_id,
                    revoked
                )
                VALUES (?, ?, ?, 0)

                ON CONFLICT(
                    guild_id,
                    event_id,
                    user_id
                )
                DO UPDATE SET revoked=0
                """,
                (
                    str(gid),
                    int(eid),
                    str(event["organizer_id"]),
                ),
            )

        audit(
            db,
            gid,
            eid,
            reviewer,
            "approve" if approve else "reject",
            note,
        )

        return dict(event)


# ==========================================
# 活動進程
# ==========================================

def change_state(
    gid,
    uid,
    eid,
    state,
):
    allowed = {
        "approved": ("open", "cancelled"),
        "open": ("running", "closed", "cancelled"),
        "running": ("closed", "cancelled"),
    }

    with transaction() as db:
        event = fetch_event(db, gid, eid)

        if state not in allowed.get(
            event["status"],
            (),
        ):
            raise ValueError(
                "這個活動狀態不能這樣轉換。"
            )

        db.execute(
            """
            UPDATE rp_event_projects
            SET status=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
            """,
            (
                state,
                str(gid),
                int(eid),
            ),
        )

        if state in ("closed", "cancelled"):
            db.execute(
                """
                UPDATE rp_event_organizers
                SET revoked=1
                WHERE guild_id=?
                  AND event_id=?
                """,
                (
                    str(gid),
                    int(eid),
                ),
            )

        audit(
            db,
            gid,
            eid,
            uid,
            "state",
            f"{event['status']} -> {state}",
        )

        return dict(event)


# ==========================================
# 玩家報名
# ==========================================

def apply_join(
    gid,
    eid,
    uid,
):
    with transaction() as db:
        event = fetch_event(db, gid, eid)

        if event["status"] != "open":
            raise ValueError(
                "活動尚未開放報名。"
            )

        old = db.execute(
            """
            SELECT status
            FROM rp_event_enrollments
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(gid),
                int(eid),
                str(uid),
            ),
        ).fetchone()

        if old is not None:
            raise ValueError(
                "你已經申請或報名過這場活動。"
            )

        db.execute(
            """
            INSERT INTO rp_event_enrollments (
                guild_id,
                event_id,
                user_id
            )
            VALUES (?, ?, ?)
            """,
            (
                str(gid),
                int(eid),
                str(uid),
            ),
        )

        audit(
            db,
            gid,
            eid,
            uid,
            "join_request",
        )


# ==========================================
# 報名審核
# ==========================================

def decide_join(
    gid,
    eid,
    uid,
    reviewer,
    approve,
):
    with transaction() as db:
        event = fetch_event(db, gid, eid)

        if event["status"] not in ("open", "running"):
            raise ValueError(
                "活動目前不接受報名審核。"
            )

        row = db.execute(
            """
            SELECT status
            FROM rp_event_enrollments
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(gid),
                int(eid),
                str(uid),
            ),
        ).fetchone()

        if row is None or row["status"] != "pending":
            raise ValueError(
                "沒有待審核的報名。"
            )

        if approve:
            count = db.execute(
                """
                SELECT COUNT(*) AS n
                FROM rp_event_enrollments
                WHERE guild_id=?
                  AND event_id=?
                  AND status='accepted'
                """,
                (
                    str(gid),
                    int(eid),
                ),
            ).fetchone()["n"]

            if count >= event["capacity"]:
                raise ValueError(
                    "活動人數已達上限。"
                )

        db.execute(
            """
            UPDATE rp_event_enrollments
            SET status=?,
                reviewed_by=?
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                "accepted" if approve else "rejected",
                str(reviewer),
                str(gid),
                int(eid),
                str(uid),
            ),
        )

        audit(
            db,
            gid,
            eid,
            reviewer,
            "accept_join" if approve else "reject_join",
            uid,
        )


# ==========================================
# Discord 回覆工具
# ==========================================

async def respond(i, msg):
    if i.response.is_done():
        await i.followup.send(
            str(msg)[:1900],
            ephemeral=True,
        )
    else:
        await i.response.send_message(
            str(msg)[:1900],
            ephemeral=True,
        )


# ==========================================
# 臨時主辦身分組同步
# ==========================================

async def sync_role(i, user_id, add):
    """身分組只是識別，權限由資料庫判斷。"""

    with get_connection() as db:
        setting = db.execute(
            """
            SELECT organizer_role_id
            FROM rp_event_settings
            WHERE guild_id=?
            """,
            (
                str(i.guild_id),
            ),
        ).fetchone()

    if (
        setting is None
        or not setting["organizer_role_id"]
    ):
        return

    role = i.guild.get_role(
        int(setting["organizer_role_id"])
    )

    if (
        role is None
        or role.permissions.value != 0
    ):
        LOG.warning(
            "臨時主辦身分組不存在或帶有"
            "Discord 原生權限，拒絕同步。"
        )
        return

    member = i.guild.get_member(
        int(user_id)
    )

    if member is None:
        try:
            member = await i.guild.fetch_member(
                int(user_id)
            )
        except discord.HTTPException:
            return

    try:
        if add:
            await member.add_roles(
                role,
                reason="企劃審核通過：臨時主辦識別",
            )

        else:
            # 同一玩家可能同時主辦多場活動。
            # 只有所有有效主辦資格都失效，
            # 才能移除識別身分組。
            with get_connection() as db:
                remaining = db.execute(
                    """
                    SELECT 1
                    FROM rp_event_organizers o
                    JOIN rp_event_projects p
                      ON p.id=o.event_id
                     AND p.guild_id=o.guild_id

                    WHERE o.guild_id=?
                      AND o.user_id=?
                      AND o.revoked=0
                      AND p.status IN (
                          'approved',
                          'open',
                          'running'
                      )
                    LIMIT 1
                    """,
                    (
                        str(i.guild_id),
                        str(user_id),
                    ),
                ).fetchone()

            if remaining is None:
                await member.remove_roles(
                    role,
                    reason="臨時主辦活動結束",
                )

    except discord.HTTPException:
        LOG.exception(
            "無法同步臨時主辦身分組。"
        )


# ==========================================
# 活動企劃第一步
# ==========================================

class ProjectModal(
    discord.ui.Modal,
    title="建立活動企劃（第一步）",
):
    name = discord.ui.TextInput(
        label="活動名稱",
        max_length=100,
    )

    content = discord.ui.TextInput(
        label="活動內容與玩法",
        style=discord.TextStyle.paragraph,
        max_length=3500,
    )

    kind = discord.ui.TextInput(
        label="類型：normal/main/competition/limited",
        max_length=20,
        default="normal",
    )

    qualification = discord.ui.TextInput(
        label="參與資格／限制",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    capacity = discord.ui.TextInput(
        label="參加人數上限",
        max_length=6,
        default="30",
    )

    async def on_submit(self, i):
        try:
            eid = submit_project(
                i.guild_id,
                i.user.id,
                str(self.name),
                str(self.content),
                str(self.kind).strip().lower(),
                str(self.qualification),
                int(str(self.capacity)),
            )

            await i.response.send_message(
                f"✅ 企劃 #{eid} 已建立草稿。\n"
                "下一步選擇活動參加頻道，然後填寫時間與獎勵；填完會自動送審。",
                view=ProjectChannelView(eid, i.user.id), ephemeral=True,
            )

        except (ValueError, sqlite3.Error) as exc:
            await respond(
                i,
                f"⚠️ {exc}",
            )


# ==========================================
# 活動企劃第二步
# ==========================================

class DetailModal(
    discord.ui.Modal,
    title="活動企劃補充（第二步）",
):
    mode = discord.ui.TextInput(
        label="頻道模式 existing 或 new",
        default="existing",
        max_length=8,
    )

    channel = discord.ui.TextInput(
        label="現有頻道ID／新頻道分類ID",
        required=False,
        max_length=22,
    )

    schedule = discord.ui.TextInput(
        label="活動時間及時區／報名截止日",
        required=False,
        max_length=500,
    )

    rewards = discord.ui.TextInput(
        label="獎勵（無獎勵請填「無」）",
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    titles = discord.ui.TextInput(
        label="活動稱號名稱與取得條件",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
    )

    def __init__(self, eid, mode=None, channel_id=None):
        super().__init__()
        self.eid = eid
        if mode is not None:
            self.mode.default = mode
        if channel_id is not None:
            self.channel.default = str(channel_id)

    async def on_submit(self, i):
        try:
            mode = str(self.mode).strip().lower()
            field = str(self.channel).strip()

            set_details(
                i.guild_id,
                i.user.id,
                self.eid,
                mode,
                field if mode == "existing" else "",
                field if mode == "new" else "",
                str(self.rewards),
                str(self.titles),
                str(self.schedule),
            )

            submit_for_review(i.guild_id, i.user.id, self.eid)
            await respond(
                i,
                f"✅ 企劃 #{self.eid} 已補齊活動頻道、時間與獎勵，並自動送交審核。"
                "審核通過後，可由官方活動管理設定截止時間、公告頻道與報名資格。",
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(i, f"⚠️ {exc}")


class ProjectChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, eid, uid, use_category=False):
        super().__init__(
            placeholder="選擇新頻道的分類" if use_category else "選擇活動參加頻道",
            channel_types=[discord.ChannelType.category] if use_category else [discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        self.eid = eid
        self.uid = uid
        self.use_category = use_category

    async def callback(self, i):
        if i.user.id != self.uid:
            return await i.response.send_message("這不是你的企劃選單。", ephemeral=True)
        mode = "new" if self.use_category else "existing"
        await i.response.send_modal(DetailModal(self.eid, mode, self.values[0].id))


class ProjectChannelView(discord.ui.View):
    def __init__(self, eid, uid):
        super().__init__(timeout=600)
        self.add_item(ProjectChannelSelect(eid, uid, False))
        self.add_item(ProjectChannelSelect(eid, uid, True))

# ==========================================
# 活動管理介面
# ==========================================

class EventAdminView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)

        self.uid = i.user.id
        self.gid = i.guild_id

    async def interaction_check(self, i):
        if (
            i.user.id != self.uid
            or i.guild_id != self.gid
            or not is_event_admin(i)
        ):
            await respond(
                i,
                "你沒有使用此活動管理面板的權限。",
            )
            return False

        return True

    @discord.ui.button(
        label="📋 待審與活動列表",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def listing(self, i, button):
        with get_connection() as db:
            rows = db.execute(
                """
                SELECT
                    id,
                    title,
                    status,
                    organizer_id
                FROM rp_event_projects
                WHERE guild_id=?
                ORDER BY
                    CASE
                        WHEN status='submitted'
                        THEN 0 ELSE 1
                    END,
                    id DESC
                LIMIT 20
                """,
                (str(i.guild_id),),
            ).fetchall()

        lines = [
            f"#{r['id']}｜"
            f"{r['title'][:35]}｜"
            f"{STATES.get(r['status'], r['status'])}"
            f"｜主辦 <@{r['organizer_id']}>"
            for r in rows
        ]

        await respond(
            i,
            "📋 **活動企劃**\n"
            + (
                "\n".join(lines)
                if lines
                else "尚無活動。"
            ),
        )

    @discord.ui.button(
        label="➕ 建立活動",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def creating(self, i, button):
        await i.response.send_modal(
            ProjectModal()
        )

    @discord.ui.button(
        label="🎟️ 報名／公告／抽籤",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def registration(self, i, button):
        from modules.event_registration_flow import EventRegistrationAdminView
        await i.response.send_message(
            "🎟️ **活動報名管理**\n可以設定截止、資格、公告頻道、審核及抽籤。",
            view=EventRegistrationAdminView(i), ephemeral=True,
        )

    @discord.ui.button(
        label="↩️ 返回",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(self, i, button):
        from modules.content_builder import ContentView
        await i.response.edit_message(
            content="✍️ **內容建立中心**",
            view=ContentView(i),
        )


# ==========================================
# Discord 活動指令
# ==========================================

class EventProject(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="活動企劃",
        description="玩家提交活動企劃書",
    )
    @app_commands.guild_only()
    async def project(self, i: discord.Interaction):
        await i.response.send_modal(
            ProjectModal()
        )

    @app_commands.command(
        name="活動企劃補充",
        description="補齊活動頻道、時間、獎勵與稱號",
    )
    @app_commands.guild_only()
    async def supplement(
        self,
        i: discord.Interaction,
        企劃編號: int,
    ):
        try:
            with get_connection() as db:
                event = fetch_event(
                    db,
                    i.guild_id,
                    企劃編號,
                )

            if (
                str(event["creator_id"]) != str(i.user.id)
                or event["status"] not in ("draft", "rejected")
            ):
                return await respond(
                    i,
                    "你只能補充自己的草稿或退件企劃。",
                )

            await i.response.send_modal(
                DetailModal(企劃編號)
            )

        except ValueError as exc:
            await respond(i, f"⚠️ {exc}")

    @app_commands.command(
        name="活動企劃送審",
        description="將活動企劃交給管理員審核",
    )
    @app_commands.guild_only()
    async def submit(
        self,
        i: discord.Interaction,
        企劃編號: int,
    ):
        try:
            submit_for_review(
                i.guild_id,
                i.user.id,
                企劃編號,
            )

            await respond(
                i,
                "✅ 企劃已送審。",
            )

        except (
            ValueError,
            PermissionError,
        ) as exc:
            await respond(i, f"⚠️ {exc}")

    @app_commands.command(
        name="活動管理",
        description="活動管理員審核及管理企劃",
    )
    @app_commands.guild_only()
    async def management(self, i: discord.Interaction):
        if not is_event_admin(i):
            return await respond(
                i,
                "此功能只限群主或正式活動管理員。",
            )

        await i.response.send_message(
            "🎪 活動管理中心。\n"
            "管理操作請使用活動管理指令。",
            view=EventAdminView(i),
            ephemeral=True,
        )

    @app_commands.command(
        name="活動企劃審核",
        description="批准或退回活動企劃",
    )
    @app_commands.guild_only()
    async def approval(
        self,
        i: discord.Interaction,
        企劃編號: int,
        通過: bool,
        審核意見: str = "",
    ):
        if not is_event_admin(i):
            return await respond(
                i,
                "沒有活動審核權限。",
            )

        try:
            event = review(
                i.guild_id,
                i.user.id,
                企劃編號,
                通過,
                審核意見,
                is_owner(i),
            )

            if 通過:
                await sync_role(
                    i,
                    event["organizer_id"],
                    True,
                )

            await respond(
                i,
                f"✅ 企劃 #{企劃編號} "
                f"已{'核准' if 通過 else '退回'}。\n"
                "稱號與數值獎勵仍需另行配置，"
                "尚未自動發放。",
            )

        except (
            ValueError,
            PermissionError,
        ) as exc:
            await respond(i, f"⚠️ {exc}")

    @app_commands.command(
        name="活動進程",
        description="開放報名、開始、結束或取消活動",
    )
    @app_commands.guild_only()
    @app_commands.choices(
        操作=[
            app_commands.Choice(
                name="開放報名",
                value="open",
            ),
            app_commands.Choice(
                name="開始活動",
                value="running",
            ),
            app_commands.Choice(
                name="結束活動",
                value="closed",
            ),
            app_commands.Choice(
                name="取消活動",
                value="cancelled",
            ),
        ]
    )
    async def progress(
        self,
        i: discord.Interaction,
        活動編號: int,
        操作: app_commands.Choice[str],
    ):
        try:
            with get_connection() as db:
                event = fetch_event(
                    db,
                    i.guild_id,
                    活動編號,
                )

            if not can_manage(i, event):
                raise PermissionError(
                    "只能管理自己受授權的活動。"
                )

            previous = change_state(
                i.guild_id,
                i.user.id,
                活動編號,
                操作.value,
            )

            if 操作.value in ("closed", "cancelled"):
                await sync_role(
                    i,
                    previous["organizer_id"],
                    False,
                )

            await respond(
                i,
                f"✅ 活動 #{活動編號} "
                f"已更新為 {STATES[操作.value]}。",
            )

        except (
            ValueError,
            PermissionError,
        ) as exc:
            await respond(i, f"⚠️ {exc}")

    @app_commands.command(
        name="活動專屬頻道",
        description="為核准活動建立專用頻道",
    )
    @app_commands.guild_only()
    async def make_channel(
        self,
        i: discord.Interaction,
        活動編號: int,
    ):
        try:
            with get_connection() as db:
                event = dict(
                    fetch_event(
                        db,
                        i.guild_id,
                        活動編號,
                    )
                )

            if not can_manage(i, event):
                raise PermissionError(
                    "你沒有這場活動的管理權限。"
                )

            if event["channel_mode"] != "new":
                raise ValueError(
                    "這份企劃選擇使用現有頻道。"
                )

            if event["channel_id"]:
                raise ValueError(
                    "此活動已有專屬頻道，"
                    "不能重複建立。"
                )

            category = None

            if event["category_id"]:
                category = i.guild.get_channel(
                    int(event["category_id"])
                )

                if not isinstance(
                    category,
                    discord.CategoryChannel,
                ):
                    raise ValueError(
                        "指定的頻道分類不存在。"
                    )

            await i.response.defer(ephemeral=True)

            # 預設不對全體玩家開放。
            # 僅機器人及主辦人可見。
            overwrites = {
                i.guild.default_role:
                    discord.PermissionOverwrite(
                        view_channel=False,
                    ),
                i.guild.me:
                    discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        manage_channels=True,
                    ),
            }

            member = i.guild.get_member(
                int(event["organizer_id"])
            )

            if member is None:
                try:
                    member = await i.guild.fetch_member(
                        int(event["organizer_id"])
                    )
                except discord.HTTPException:
                    member = None

            if member:
                overwrites[member] = (
                    discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    )
                )

            channel = await i.guild.create_text_channel(
                name=(
                    f"活動-{活動編號}-"
                    f"{event['title']}"
                )[:90],
                category=category,
                overwrites=overwrites,
                reason=(
                    f"已核准活動 #{活動編號} "
                    "專屬頻道"
                ),
            )

            try:
                with transaction() as db:
                    current = fetch_event(
                        db,
                        i.guild_id,
                        活動編號,
                    )

                    if (
                        current["channel_id"]
                        or current["status"]
                        not in ACTIVE_STATES
                    ):
                        raise ValueError(
                            "活動狀態已變更或頻道已存在。"
                        )

                    db.execute(
                        """
                        UPDATE rp_event_projects
                        SET channel_id=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE guild_id=?
                          AND id=?
                        """,
                        (
                            str(channel.id),
                            str(i.guild_id),
                            活動編號,
                        ),
                    )

                    audit(
                        db,
                        i.guild_id,
                        活動編號,
                        i.user.id,
                        "create_channel",
                        str(channel.id),
                    )

            except Exception:
                try:
                    await channel.delete(
                        reason="未能完成活動資料登記",
                    )
                except discord.HTTPException:
                    LOG.exception(
                        "活動頻道回滾失敗。"
                    )
                raise

            await respond(
                i,
                f"✅ 已建立活動專屬頻道："
                f"{channel.mention}。\n"
                "預設不向全體公開。",
            )

        except (
            ValueError,
            PermissionError,
            discord.HTTPException,
            sqlite3.Error,
        ) as exc:
            await respond(i, f"⚠️ {exc}")

    @app_commands.command(
        name="我的活動企劃",
        description="查詢自己的企劃及審核結果",
    )
    @app_commands.guild_only()
    async def my_projects(self, i: discord.Interaction):
        with get_connection() as db:
            rows = db.execute(
                """
                SELECT
                    id,
                    title,
                    status,
                    review_note
                FROM rp_event_projects
                WHERE guild_id=?
                  AND creator_id=?
                ORDER BY id DESC
                LIMIT 15
                """,
                (
                    str(i.guild_id),
                    str(i.user.id),
                ),
            ).fetchall()

        lines = []

        for row in rows:
            line = (
                f"#{row['id']}｜"
                f"{row['title'][:35]}｜"
                f"{STATES.get(row['status'], row['status'])}"
            )

            if row["review_note"]:
                line += (
                    f"｜意見："
                    f"{row['review_note'][:80]}"
                )

            lines.append(line)

        await respond(
            i,
            "📋 **我的活動企劃**\n"
            + (
                "\n".join(lines)
                if lines
                else "尚未提交企劃。"
            ),
        )

    @app_commands.command(
        name="活動報名",
        description="申請參與已開放報名的活動",
    )
    @app_commands.guild_only()
    async def join(
        self,
        i: discord.Interaction,
        活動編號: int,
    ):
        try:
            apply_join(
                i.guild_id,
                活動編號,
                i.user.id,
            )

            await respond(
                i,
                "✅ 已送出報名，"
                "待主辦確認參與資格。",
            )

        except ValueError as exc:
            await respond(i, f"⚠️ {exc}")

    @app_commands.command(
        name="活動報名審核",
        description="主辦審核報名與名額",
    )
    @app_commands.guild_only()
    async def join_review(
        self,
        i: discord.Interaction,
        活動編號: int,
        玩家: discord.Member,
        通過: bool,
    ):
        try:
            with get_connection() as db:
                event = fetch_event(
                    db,
                    i.guild_id,
                    活動編號,
                )

            if not can_manage(i, event):
                raise PermissionError(
                    "僅本活動主辦或正式管理員可審核。"
                )

            decide_join(
                i.guild_id,
                活動編號,
                玩家.id,
                i.user.id,
                通過,
            )

            await respond(
                i,
                "✅ 報名審核完成。\n"
                "請依企劃參與資格人工核實。",
            )

        except (
            ValueError,
            PermissionError,
        ) as exc:
            await respond(i, f"⚠️ {exc}")

    @app_commands.command(
        name="活動管理員授權",
        description="群主設定正式活動管理員",
    )
    @app_commands.guild_only()
    async def admin_grant(
        self,
        i: discord.Interaction,
        玩家: discord.Member,
        授權: bool,
    ):
        if not is_owner(i):
            return await respond(
                i,
                "只有群主可以設定活動管理員。",
            )

        with transaction() as db:
            if 授權:
                db.execute(
                    """
                    INSERT OR REPLACE INTO
                    rp_event_admin_grants (
                        guild_id,
                        user_id,
                        granted_by
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        str(i.guild_id),
                        str(玩家.id),
                        str(i.user.id),
                    ),
                )
            else:
                db.execute(
                    """
                    DELETE FROM rp_event_admin_grants
                    WHERE guild_id=?
                      AND user_id=?
                    """,
                    (
                        str(i.guild_id),
                        str(玩家.id),
                    ),
                )

            audit(
                db,
                i.guild_id,
                None,
                i.user.id,
                (
                    "admin_grant"
                    if 授權
                    else "admin_revoke"
                ),
                str(玩家.id),
            )

        await respond(
            i,
            "✅ 正式活動管理權限已更新。",
        )

    @app_commands.command(
        name="活動主辦身分組",
        description="設定零權限的臨時主辦身分組",
    )
    @app_commands.guild_only()
    async def role_config(
        self,
        i: discord.Interaction,
        身分組: discord.Role,
    ):
        if not is_owner(i):
            return await respond(
                i,
                "只有群主可以設定主辦身分組。",
            )

        if (
            身分組.permissions.value != 0
            or 身分組.is_default()
            or 身分組.managed
        ):
            return await respond(
                i,
                "請選擇沒有任何 Discord "
                "原生權限、非託管的普通身分組。",
            )

        with transaction() as db:
            db.execute(
                """
                INSERT INTO rp_event_settings (
                    guild_id,
                    organizer_role_id
                )
                VALUES (?, ?)

                ON CONFLICT(guild_id)
                DO UPDATE SET
                    organizer_role_id=
                        excluded.organizer_role_id
                """,
                (
                    str(i.guild_id),
                    str(身分組.id),
                ),
            )

            audit(
                db,
                i.guild_id,
                None,
                i.user.id,
                "organizer_role",
                str(身分組.id),
            )

        await respond(
            i,
            "✅ 已設定臨時主辦識別身分組。\n"
            "主辦權限僅限獲核准的活動，"
            "不包含其他管理功能。",
        )


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    init_db()
    await bot.add_cog(
        EventProject(bot)
    )
