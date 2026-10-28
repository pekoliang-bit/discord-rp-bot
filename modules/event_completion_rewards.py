
"""Discord RP｜活動完成驗證與正式獎勵結算。

檔案：
modules/event_completion_rewards.py

功能：
1. 官方設定核准活動的固定獎勵。
2. 活動主辦人回報參加者完成情況。
3. 正式管理員覆核完成證明。
4. 世界／主線事件需要群主最終確認。
5. 系統發放正式角色數值。
6. 支援活動專屬稱號。
7. 使用統一獎勵核心防止重複發獎。
8. 部分獎勵失敗時允許安全重試。

限制：
- 臨時主辦人不可自行核發獎勵。
- 不得將購買、隨機獎勵冒充正式活動獎勵。
- 未經驗證的玩家輸入不能直接觸發發獎。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    transaction,
    get_connection,
)

from core.stat_rewards import (
    grant_stats,
    ATTRIBUTES,
    METRICS,
)

from modules.event_project_manager import (
    init_db as init_events,
    fetch_event,
    is_event_admin,
    is_owner,
    can_manage,
    audit,
    respond,
)


LOG = logging.getLogger(__name__)

VALID_STAT_NAMES = (
    set(ATTRIBUTES)
    | {"粉絲", "大眾認知度"}
) - {"CP好感度"}

MAX_STAT_AMOUNT = 100000


# ==========================================
# 資料庫
# ==========================================

def init_db():
    init_events()

    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_reward_plans (
                guild_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                stats_json TEXT NOT NULL DEFAULT '{}',
                title_code TEXT,
                configured_by TEXT NOT NULL,
                configured_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY(guild_id, event_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_event_completion_reviews (
                guild_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                evidence TEXT NOT NULL,
                organizer_id TEXT NOT NULL,
                official_id TEXT,
                owner_id TEXT,

                status TEXT NOT NULL DEFAULT 'reported'
                    CHECK(status IN (
                        'reported',
                        'official_checked',
                        'ready',
                        'rejected',
                        'paid'
                    )),

                paid_at TEXT,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY(
                    guild_id,
                    event_id,
                    user_id
                ),

                UNIQUE(
                    guild_id,
                    event_id,
                    character_id
                )
            )
        """)


# ==========================================
# 讀取活動獎勵方案
# ==========================================

def get_reward_plan(db, guild_id, event_id):
    return db.execute(
        """
        SELECT *
        FROM rp_event_reward_plans
        WHERE guild_id=?
          AND event_id=?
        """,
        (
            str(guild_id),
            int(event_id),
        ),
    ).fetchone()


# ==========================================
# 官方設定獎勵
# ==========================================

def configure_plan(
    guild_id,
    event_id,
    user_id,
    stats_json,
    title_code,
):
    """建立活動的正式獎勵方案。

    呼叫此函式前，
    Discord 指令必須驗證正式管理權限。

    有任何玩家回報完成後，
    原獎勵設定便不能修改。
    """

    try:
        parsed = json.loads(
            stats_json or "{}"
        )
    except (
        ValueError,
        TypeError,
    ) as exc:
        raise ValueError(
            "數值獎勵 JSON 格式錯誤。"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            "數值獎勵必須是 JSON 物件。"
        )

    for name, amount in parsed.items():

        if name not in VALID_STAT_NAMES:
            raise ValueError(
                f"不支援的活動獎勵數值：{name}"
            )

        if (
            type(amount) is not int
            or not 0 < amount <= MAX_STAT_AMOUNT
        ):
            raise ValueError(
                f"「{name}」獎勵必須是"
                f"1～{MAX_STAT_AMOUNT} 的整數。"
            )

    code = (
        str(title_code).strip()
        if title_code
        else None
    )

    with transaction() as db:
        event = fetch_event(
            db,
            guild_id,
            event_id,
        )

        if event["status"] not in (
            "approved",
            "open",
            "running",
        ):
            raise ValueError(
                "只能為已核准且尚未結束的"
                "活動設定正式獎勵。"
            )

        exists = db.execute(
            """
            SELECT 1
            FROM rp_event_completion_reviews
            WHERE guild_id=?
              AND event_id=?
            LIMIT 1
            """,
            (
                str(guild_id),
                int(event_id),
            ),
        ).fetchone()

        if exists:
            raise ValueError(
                "活動已有完成回報，"
                "正式獎勵設定已鎖定。"
            )

        # 只允許活動專屬稱號。
        # 固定成就稱號有自己的取得條件。
        if code:
            title = db.execute(
                """
                SELECT
                    category,
                    enabled
                FROM rp_achievement_definitions
                WHERE guild_id=?
                  AND code=?
                """,
                (
                    str(guild_id),
                    code,
                ),
            ).fetchone()

            if (
                title is None
                or title["category"] != "event"
                or not title["enabled"]
            ):
                raise ValueError(
                    "活動只能授予已建立、"
                    "已啟用的活動專屬稱號。"
                )

        if not parsed and not code:
            raise ValueError(
                "沒有任何正式獎勵。"
                "無獎勵活動不需要設定發獎方案。"
            )

        db.execute(
            """
            INSERT INTO rp_event_reward_plans (
                guild_id,
                event_id,
                stats_json,
                title_code,
                configured_by
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(
                guild_id,
                event_id
            )
            DO UPDATE SET
                stats_json=excluded.stats_json,
                title_code=excluded.title_code,
                configured_by=excluded.configured_by,
                configured_at=CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                int(event_id),
                json.dumps(
                    parsed,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                code,
                str(user_id),
            ),
        )

        audit(
            db,
            guild_id,
            event_id,
            user_id,
            "reward_plan",
            json.dumps(
                {
                    "stats": parsed,
                    "title": code,
                },
                ensure_ascii=False,
            ),
        )


# ==========================================
# 主辦人回報完成
# ==========================================

def report_completed(
    guild_id,
    event_id,
    user_id,
    player_id,
    character_id,
    evidence,
):
    evidence = str(
        evidence
    ).strip()

    if not 5 <= len(evidence) <= 1000:
        raise ValueError(
            "完成證明請填寫5～1000字。"
        )

    with transaction() as db:
        event = fetch_event(
            db,
            guild_id,
            event_id,
        )

        if event["status"] not in (
            "running",
            "closed",
        ):
            raise ValueError(
                "活動尚未開始或已取消，"
                "無法回報完成。"
            )

        if get_reward_plan(
            db,
            guild_id,
            event_id,
        ) is None:
            raise ValueError(
                "此活動尚未核定正式獎勵方案。"
            )

        enrollment = db.execute(
            """
            SELECT status
            FROM rp_event_enrollments
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        ).fetchone()

        if (
            enrollment is None
            or enrollment["status"] != "accepted"
        ):
            raise ValueError(
                "參與者尚未通過活動報名。"
            )

        character = db.execute(
            """
            SELECT
                owner_id,
                status
            FROM characters
            WHERE guild_id=?
              AND id=?
            """,
            (
                str(guild_id),
                int(character_id),
            ),
        ).fetchone()

        if (
            character is None
            or str(character["owner_id"])
            != str(player_id)
            or character["status"] not in (
                "approved",
                "active",
            )
        ):
            raise ValueError(
                "領獎角色必須是該參加者本人"
                "持有、已核准的正式角色。"
            )

        prior = db.execute(
            """
            SELECT 1
            FROM rp_event_completion_reviews
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        ).fetchone()

        if prior:
            raise ValueError(
                "此參與者已有完成回報，"
                "不能重複建立。"
            )

        db.execute(
            """
            INSERT INTO rp_event_completion_reviews (
                guild_id,
                event_id,
                user_id,
                character_id,
                evidence,
                organizer_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                int(event_id),
                str(player_id),
                int(character_id),
                evidence,
                str(user_id),
            ),
        )

        audit(
            db,
            guild_id,
            event_id,
            user_id,
            "completion_report",
            (
                f"player={player_id},"
                f"character={character_id}"
            ),
        )


# ==========================================
# 正式管理員覆核
# ==========================================

def official_review(
    guild_id,
    event_id,
    player_id,
    reviewer_id,
    approve,
    owner_override=False,
):
    with transaction() as db:
        event = fetch_event(
            db,
            guild_id,
            event_id,
        )

        record = db.execute(
            """
            SELECT *
            FROM rp_event_completion_reviews
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        ).fetchone()

        if (
            record is None
            or record["status"] != "reported"
        ):
            raise ValueError(
                "沒有待正式覆核的完成回報。"
            )

        if (
            not owner_override
            and str(reviewer_id) in {
                str(record["organizer_id"]),
                str(player_id),
                str(event["creator_id"]),
            }
        ):
            raise PermissionError(
                "非群主不能覆核自己主辦、"
                "提交或參與的活動成果。"
            )

        if approve:
            state = (
                "official_checked"
                if event["kind"] == "main"
                else "ready"
            )
        else:
            state = "rejected"

        db.execute(
            """
            UPDATE rp_event_completion_reviews
            SET
                status=?,
                official_id=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                state,
                str(reviewer_id),
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        )

        audit(
            db,
            guild_id,
            event_id,
            reviewer_id,
            (
                "official_approve"
                if approve
                else "official_reject"
            ),
            str(player_id),
        )

        return state


# ==========================================
# 群主最終確認
# ==========================================

def owner_confirm(
    guild_id,
    event_id,
    player_id,
    owner_id,
    approve,
):
    with transaction() as db:
        event = fetch_event(
            db,
            guild_id,
            event_id,
        )

        if event["kind"] != "main":
            raise ValueError(
                "只有世界／主線事件"
                "需要群主最終確認。"
            )

        record = db.execute(
            """
            SELECT status
            FROM rp_event_completion_reviews
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        ).fetchone()

        if (
            record is None
            or record["status"]
            != "official_checked"
        ):
            raise ValueError(
                "必須先完成正式管理員覆核。"
            )

        state = (
            "ready"
            if approve
            else "rejected"
        )

        db.execute(
            """
            UPDATE rp_event_completion_reviews
            SET
                status=?,
                owner_id=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                state,
                str(owner_id),
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        )

        audit(
            db,
            guild_id,
            event_id,
            owner_id,
            (
                "owner_confirm"
                if approve
                else "owner_reject"
            ),
            str(player_id),
        )


# ==========================================
# 正式獎勵結算
# ==========================================

def settle_one(
    guild_id,
    event_id,
    player_id,
):
    """結算通過全部審核的活動獎勵。

    固定數值使用唯一 source_id，
    避免重試時重複增加數值。

    如果稱號特殊獎勵未完成，
    保留可重試狀態。
    """

    init_db()

    with get_connection() as db:
        event = dict(
            fetch_event(
                db,
                guild_id,
                event_id,
            )
        )

        record = db.execute(
            """
            SELECT *
            FROM rp_event_completion_reviews
            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
            """,
            (
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        ).fetchone()

        plan = get_reward_plan(
            db,
            guild_id,
            event_id,
        )

        if (
            record is None
            or record["status"] not in (
                "ready",
                "paid",
            )
        ):
            raise ValueError(
                "參與者尚未取得全部完成確認。"
            )

        if plan is None:
            raise ValueError(
                "活動沒有正式獎勵方案。"
            )

        if event["status"] not in (
            "running",
            "closed",
        ):
            raise ValueError(
                "只能結算進行中或已結束的活動。"
            )

        character_id = int(
            record["character_id"]
        )

        title_code = plan[
            "title_code"
        ]

        stat_changes = json.loads(
            plan["stats_json"]
        )

        if record["status"] == "paid":
            return "already_paid"

    # --------------------------------------
    # 正式數值獎勵
    # --------------------------------------

    if stat_changes:
        grant_stats(
            guild_id,
            character_id,
            stat_changes,
            source="event_reward",
            source_id=(
                f"event:{guild_id}:"
                f"{event_id}:"
                f"character:{character_id}"
            ),
            reason=(
                f"活動 #{event_id}"
                f"「{event['title']}」"
                "正式完成獎勵"
            ),
            random=False,
            purchased=False,
        )

    # --------------------------------------
    # 活動專屬稱號
    # --------------------------------------

    if title_code:
        from modules.achievement_core import (
            unlock_title,
        )

        from modules.achievement_rewards import (
            grant_title_rewards,
        )

        unlock_title(
            guild_id,
            character_id,
            title_code,
            eligibility_verified=True,
        )

        with get_connection() as db:
            title = db.execute(
                """
                SELECT id
                FROM rp_achievement_definitions
                WHERE guild_id=?
                  AND code=?
                """,
                (
                    str(guild_id),
                    title_code,
                ),
            ).fetchone()

        if title is None:
            raise ValueError(
                "指定的活動稱號不存在。"
            )

        outcome = grant_title_rewards(
            guild_id,
            character_id,
            int(title["id"]),
        )

        if (
            outcome.get("errors")
            or outcome.get("pending")
        ):
            return "title_reward_pending"

    # --------------------------------------
    # 標記玩家完成活動
    # --------------------------------------

    with transaction() as db:
        cursor = db.execute(
            """
            UPDATE rp_event_completion_reviews
            SET
                status='paid',
                paid_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP

            WHERE guild_id=?
              AND event_id=?
              AND user_id=?
              AND status='ready'
            """,
            (
                str(guild_id),
                int(event_id),
                str(player_id),
            ),
        )

        if cursor.rowcount:
            db.execute(
                """
                UPDATE rp_event_enrollments
                SET completed=1
                WHERE guild_id=?
                  AND event_id=?
                  AND user_id=?
                """,
                (
                    str(guild_id),
                    int(event_id),
                    str(player_id),
                ),
            )

            audit(
                db,
                guild_id,
                event_id,
                "system",
                "reward_settled",
                (
                    f"player={player_id},"
                    f"character={character_id}"
                ),
            )

    return "paid"


# ==========================================
# Discord 指令
# ==========================================

class EventCompletion(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    # --------------------------------------
    # 活動獎勵設定
    # --------------------------------------

    @app_commands.command(
        name="活動獎勵設定",
        description="官方設定活動固定獎勵與專屬稱號",
    )
    @app_commands.guild_only()
    async def reward_config(
        self,
        i: discord.Interaction,
        活動編號: int,
        數值獎勵_json: str = "{}",
        活動稱號代碼: str = "",
    ):
        if not is_event_admin(i):
            return await respond(
                i,
                "⛔ 僅群主或正式活動管理員"
                "可以設定正式活動獎勵。",
            )

        try:
            configure_plan(
                i.guild_id,
                活動編號,
                i.user.id,
                數值獎勵_json,
                活動稱號代碼,
            )

            await respond(
                i,
                "✅ 活動獎勵方案已保存。\n"
                "首次完成回報後獎勵方案鎖定。",
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                i,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 活動完成回報
    # --------------------------------------

    @app_commands.command(
        name="活動完成回報",
        description="主辦人回報參與者完成活動的證明",
    )
    @app_commands.guild_only()
    async def completion(
        self,
        i: discord.Interaction,
        活動編號: int,
        玩家: discord.Member,
        角色編號: int,
        完成證明: str,
    ):
        try:
            with get_connection() as db:
                event = fetch_event(
                    db,
                    i.guild_id,
                    活動編號,
                )

            if not can_manage(
                i,
                event,
            ):
                raise PermissionError(
                    "只有本場活動主辦人"
                    "或正式活動管理員"
                    "可以回報完成。"
                )

            report_completed(
                i.guild_id,
                活動編號,
                i.user.id,
                玩家.id,
                角色編號,
                完成證明,
            )

            await respond(
                i,
                "✅ 完成紀錄已送交官方覆核。\n"
                "目前尚未發放任何獎勵。",
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                i,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 官方成果覆核
    # --------------------------------------

    @app_commands.command(
        name="活動成果覆核",
        description="正式管理員覆核完成證明",
    )
    @app_commands.guild_only()
    async def review(
        self,
        i: discord.Interaction,
        活動編號: int,
        玩家: discord.Member,
        通過: bool,
    ):
        if not is_event_admin(i):
            return await respond(
                i,
                "⛔ 只有正式活動管理員"
                "可以覆核活動成果。",
            )

        try:
            state = official_review(
                i.guild_id,
                活動編號,
                玩家.id,
                i.user.id,
                通過,
                is_owner(i),
            )

            message = "✅ 官方覆核結果已保存。"

            if state == "official_checked":
                message += (
                    "\n此為世界／主線事件，"
                    "仍需要群主最終確認。"
                )

            await respond(
                i,
                message,
            )

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            await respond(
                i,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 群主最終確認
    # --------------------------------------

    @app_commands.command(
        name="活動群主確認",
        description="世界／主線事件的最終確認",
    )
    @app_commands.guild_only()
    async def confirm(
        self,
        i: discord.Interaction,
        活動編號: int,
        玩家: discord.Member,
        通過: bool,
    ):
        if not is_owner(i):
            return await respond(
                i,
                "⛔ 只有群主可以進行"
                "世界／主線事件最終確認。",
            )

        try:
            owner_confirm(
                i.guild_id,
                活動編號,
                玩家.id,
                i.user.id,
                通過,
            )

            await respond(
                i,
                "✅ 群主確認已保存。\n"
                "通過後可正式結算活動獎勵。",
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await respond(
                i,
                f"⚠️ {exc}",
            )

    # --------------------------------------
    # 正式獎勵結算
    # --------------------------------------

    @app_commands.command(
        name="活動獎勵結算",
        description="正式發放已通過覆核的活動獎勵",
    )
    @app_commands.guild_only()
    async def payout(
        self,
        i: discord.Interaction,
        活動編號: int,
        玩家: discord.Member,
    ):
        if not is_event_admin(i):
            return await respond(
                i,
                "⛔ 只有正式活動管理員"
                "可以執行獎勵結算。",
            )

        await i.response.defer(
            ephemeral=True
        )

        try:
            result = await asyncio.to_thread(
                settle_one,
                i.guild_id,
                活動編號,
                玩家.id,
            )

            messages = {
                "paid": (
                    "✅ 已完成活動獎勵發放"
                    "與角色完成紀錄。"
                ),
                "already_paid": (
                    "✅ 此活動獎勵先前已結算，"
                    "沒有重複發放。"
                ),
                "title_reward_pending": (
                    "⚠️ 活動固定數值已入帳。\n"
                    "稱號的部分特殊獎勵"
                    "仍在等待正式發放，"
                    "可以稍後重試。"
                ),
            }

            await respond(
                i,
                messages.get(
                    result,
                    "活動結算已執行。",
                ),
            )

        except Exception as exc:
            LOG.exception(
                "活動獎勵結算失敗："
                "event=%s player=%s",
                活動編號,
                玩家.id,
            )

            await respond(
                i,
                "⚠️ 活動結算失敗。\n"
                f"{type(exc).__name__}: "
                f"{str(exc)[:300]}",
            )


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    await bot.add_cog(
        EventCompletion(bot)
    )
