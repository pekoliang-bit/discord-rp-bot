
"""Discord RP｜稱號取得條件偵測與官方確認。

搭配：
    modules.achievement_core
    modules.achievement_rewards

功能：
1. 偵測角色屬性門檻。
2. 記錄金融、商城、合約及故事事件。
3. 防止相同事件重複累計。
4. 支援五位不同官方 NPC 的劇情條件。
5. 支援開服一個月內建立角色的判定。
6. 支援競賽冠軍、陣營第一名及隱藏任務。
7. 傳奇見證者：任務完成 + 指定主辦 + 群主確認。
8. 取得後呼叫既有稱號核心及一次性獎勵。

安全限制：
- 這個模組不提供玩家自行回報進度的指令。
- 事件只能由其他可信任的機器人模組呼叫。
- 金錢、合約、劇情等資料必須由對應模組確認。
- 只有群主能指定大型活動主辦人。
- 一般官方主辦人不能取代群主最終確認。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from datetime import datetime, timedelta, timezone

from discord.ext import commands

from core.database import get_connection, transaction

from modules.achievement_core import (
    init_db as init_core,
    seed_fixed_titles,
    unlock_title,
)

from modules.achievement_rewards import (
    grant_title_rewards,
)


LOG = logging.getLogger(__name__)


# ==========================================
# 可信任事件類型
# ==========================================

EVENT_TYPES = {
    "shop_purchase",
    "finished_contract",
    "npc_story_chapter",
    "faction_monthly_first",
    "official_competition_first",
    "hidden_quest",
    "night_story",
}

# 某些稱號的進度要求不同事件不得重複。
# event_id 應使用來源系統中的真實唯一 ID。
EVENT_TITLE_MAP = {
    "finished_contract": "commerce",
    "faction_monthly_first": "faction_star",
    "official_competition_first": "champion",
    "hidden_quest": "night",
    "night_story": "night",
}

ATTRIBUTE_THRESHOLDS = {
    "spotlight": ("流量熱度", 2000),
    "popular": ("路人緣", 2000),
    "eloquent": ("公關", 1500),
}


# ==========================================
# 基礎工具
# ==========================================

def utc_now():
    return datetime.now(timezone.utc)


def as_iso(value=None):
    value = value or utc_now()

    if value.tzinfo is None:
        raise ValueError("時間必須包含時區。")

    return value.astimezone(
        timezone.utc
    ).isoformat(timespec="seconds")


def parse_iso(value):
    if not value:
        return None

    result = datetime.fromisoformat(value)

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def owner_only(actor_id, guild_owner_id):
    if str(actor_id) != str(guild_owner_id):
        raise PermissionError("此操作僅限伺服器群主。")


def valid_positive_id(value, field="ID"):
    text = str(value).strip()

    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"{field}必須是有效的正整數。")

    return text


def check_character(db, guild_id, character_id):
    row = db.execute(
        """
        SELECT id
        FROM characters
        WHERE guild_id=?
          AND id=?
          AND status IN ('approved', 'active')
        """,
        (str(guild_id), int(character_id)),
    ).fetchone()

    if row is None:
        raise ValueError("角色不存在，或尚未完成審核。")


# ==========================================
# 資料庫
# ==========================================

def init_db():
    init_core()

    with transaction() as db:

        # 單次可信任事件，不可重複記錄。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_events (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_id TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 1,
                subject_id TEXT,
                occurred_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',

                PRIMARY KEY (
                    guild_id,
                    event_type,
                    event_id,
                    character_id
                )
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_rp_achievement_events_character
            ON rp_achievement_events (
                guild_id,
                character_id,
                event_type
            )
        """)

        # 角色財富快照。
        # 必須由銀行／資產模組回報，不由玩家輸入。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_financial_snapshots (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                wallet INTEGER NOT NULL DEFAULT 0,
                bank INTEGER NOT NULL DEFAULT 0,
                fixed_deposit INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,

                PRIMARY KEY (guild_id, character_id)
            )
        """)

        # 開服日期：由群主設定。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_guild_settings (
                guild_id TEXT PRIMARY KEY,
                opened_at TEXT,
                updated_by TEXT,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 重大活動與指定主辦人。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_legend_events (
                guild_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                title TEXT NOT NULL,
                organizer_id TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,

                PRIMARY KEY (guild_id, event_id)
            )
        """)

        # 重大活動角色參與、任務及雙重確認。
        # 群主特別覆核另外記錄，避免偽裝成主辦人簽名。
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_legend_participants (
                guild_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,

                participated INTEGER NOT NULL DEFAULT 0,
                task_completed INTEGER NOT NULL DEFAULT 0,

                organizer_approved_by TEXT,
                organizer_approved_at TEXT,

                owner_approved_by TEXT,
                owner_approved_at TEXT,

                owner_override INTEGER NOT NULL DEFAULT 0,

                updated_at TEXT NOT NULL,

                PRIMARY KEY (
                    guild_id,
                    event_id,
                    character_id
                )
            )
        """)


# ==========================================
# 開服日期
# ==========================================

def set_server_opening(
    guild_id,
    actor_id,
    guild_owner_id,
    opened_at,
):
    """只有群主可以設定正式開服日期。"""

    owner_only(actor_id, guild_owner_id)
    init_db()

    date_text = as_iso(opened_at)

    with transaction() as db:
        db.execute(
            """
            INSERT INTO rp_achievement_guild_settings (
                guild_id,
                opened_at,
                updated_by
            )
            VALUES (?, ?, ?)

            ON CONFLICT(guild_id)
            DO UPDATE SET
                opened_at=excluded.opened_at,
                updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                date_text,
                str(actor_id),
            ),
        )


# ==========================================
# 金融資料快照
# ==========================================

def update_wealth_snapshot(
    guild_id,
    character_id,
    *,
    wallet,
    bank,
    fixed_deposit,
):
    """供銀行模組在實際餘額更新後呼叫。

    僅計入：
    1. 角色現金
    2. 銀行存款
    3. 定存

    不計入公司帳戶、借款額度或商城估價。
    """

    values = (wallet, bank, fixed_deposit)

    if any(
        type(value) is not int or value < 0
        for value in values
    ):
        raise ValueError("資產快照必須使用非負整數。")

    init_db()

    with transaction() as db:
        check_character(db, guild_id, character_id)

        db.execute(
            """
            INSERT INTO rp_achievement_financial_snapshots (
                guild_id,
                character_id,
                wallet,
                bank,
                fixed_deposit,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(guild_id, character_id)
            DO UPDATE SET
                wallet=excluded.wallet,
                bank=excluded.bank,
                fixed_deposit=excluded.fixed_deposit,
                updated_at=excluded.updated_at
            """,
            (
                str(guild_id),
                int(character_id),
                wallet,
                bank,
                fixed_deposit,
                as_iso(),
            ),
        )


# ==========================================
# 記錄可信任事件
# ==========================================

def record_event(
    guild_id,
    character_id,
    event_type,
    event_id,
    *,
    amount=1,
    subject_id=None,
    metadata=None,
    occurred_at=None,
):
    """從商城、合約、劇情、陣營或比賽模組呼叫。

    同一角色的同一 event_type + event_id
    只會記錄一次。

    回傳 True：新增成功。
    回傳 False：事件已存在。
    """

    if event_type not in EVENT_TYPES:
        raise ValueError("不支援的稱號事件類型。")

    event_id = str(event_id).strip()

    if not event_id or len(event_id) > 150:
        raise ValueError("事件 ID 不可空白或超過 150 字。")

    if type(amount) is not int or amount <= 0:
        raise ValueError("事件數值必須是正整數。")

    if event_type != "shop_purchase" and amount != 1:
        raise ValueError("此事件只能累計一次。")

    if (
        event_type == "npc_story_chapter"
        and not subject_id
    ):
        raise ValueError(
            "劇情事件必須包含官方 NPC 的 ID。"
        )

    init_db()

    with transaction() as db:
        check_character(db, guild_id, character_id)

        cursor = db.execute(
            """
            INSERT OR IGNORE INTO rp_achievement_events (
                guild_id,
                character_id,
                event_type,
                event_id,
                amount,
                subject_id,
                occurred_at,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                int(character_id),
                event_type,
                event_id,
                amount,
                (
                    str(subject_id)
                    if subject_id is not None
                    else None
                ),
                as_iso(occurred_at),
                json.dumps(
                    metadata or {},
                    ensure_ascii=False,
                ),
            ),
        )

        return cursor.rowcount == 1


# ==========================================
# 取得事件進度
# ==========================================

def event_count(db, guild_id, character_id, event_type):
    row = db.execute(
        """
        SELECT COUNT(*) AS total
        FROM rp_achievement_events
        WHERE guild_id=?
          AND character_id=?
          AND event_type=?
        """,
        (
            str(guild_id),
            int(character_id),
            event_type,
        ),
    ).fetchone()

    return int(row["total"])


def event_sum(db, guild_id, character_id, event_type):
    row = db.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM rp_achievement_events
        WHERE guild_id=?
          AND character_id=?
          AND event_type=?
        """,
        (
            str(guild_id),
            int(character_id),
            event_type,
        ),
    ).fetchone()

    return int(row["total"])


def distinct_npc_count(db, guild_id, character_id):
    row = db.execute(
        """
        SELECT COUNT(DISTINCT subject_id) AS total
        FROM rp_achievement_events
        WHERE guild_id=?
          AND character_id=?
          AND event_type='npc_story_chapter'
          AND subject_id IS NOT NULL
        """,
        (
            str(guild_id),
            int(character_id),
        ),
    ).fetchone()

    return int(row["total"])


# ==========================================
# 自動稱號達成判定
# ==========================================

def eligible_fixed_titles(guild_id, character_id):
    """回傳目前已驗證達成的固定稱號代碼。"""

    init_db()
    seed_fixed_titles(guild_id)

    gid = str(guild_id)
    cid = int(character_id)

    completed = set()

    with get_connection() as db:
        check_character(db, gid, cid)

        # 財運亨通：三類資產合計 50 萬。
        row = db.execute(
            """
            SELECT wallet, bank, fixed_deposit
            FROM rp_achievement_financial_snapshots
            WHERE guild_id=? AND character_id=?
            """,
            (gid, cid),
        ).fetchone()

        if row is not None:
            total = (
                int(row["wallet"])
                + int(row["bank"])
                + int(row["fixed_deposit"])
            )

            if total >= 500000:
                completed.add("wealth")

        # 揮金如土：累計商城實際支出 100 萬。
        if event_sum(
            db, gid, cid, "shop_purchase"
        ) >= 1000000:
            completed.add("spender")

        # 三項屬性達標稱號。
        for code, (attribute, minimum) in (
            ATTRIBUTE_THRESHOLDS.items()
        ):
            stat = db.execute(
                """
                SELECT value
                FROM attributes
                WHERE guild_id=?
                  AND character_id=?
                  AND name=?
                """,
                (gid, cid, attribute),
            ).fetchone()

            if (
                stat is not None
                and int(stat["value"]) >= minimum
            ):
                completed.add(code)

        # 商貿新手：十次獨立合作或正式合約。
        if event_count(
            db, gid, cid, "finished_contract"
        ) >= 10:
            completed.add("commerce")

        # 眾望所歸：至少一次陣營月榜第一。
        if event_count(
            db, gid, cid, "faction_monthly_first"
        ) >= 1:
            completed.add("faction_star")

        # 命運改寫者：五位不同官方 NPC 的劇情。
        if distinct_npc_count(db, gid, cid) >= 5:
            completed.add("fate")

        # 金榜題名：一次指定官方競賽冠軍。
        if event_count(
            db, gid, cid, "official_competition_first"
        ) >= 1:
            completed.add("champion")

        # 夜幕來客：隱藏任務或指定夜間劇情。
        if (
            event_count(
                db, gid, cid, "hidden_quest"
            ) >= 1
            or event_count(
                db, gid, cid, "night_story"
            ) >= 1
        ):
            completed.add("night")

        # 開城元老：角色建立與啟用均需符合開服期限。
        setting = db.execute(
            """
            SELECT opened_at
            FROM rp_achievement_guild_settings
            WHERE guild_id=?
            """,
            (gid,),
        ).fetchone()

        if setting and setting["opened_at"]:
            # 此處不擅自推斷 characters 的建立與
            # 啟用時間欄位。開城元老的日期驗證
            # 交由下方 trusted_founder_unlock()。
            pass

        # 傳奇見證者需要獨立的雙重審核。
        # 不在普通自動偵測中直接授予。

    return sorted(completed)


# ==========================================
# 統一解鎖及獎勵
# ==========================================

def complete_unlock(guild_id, character_id, code):
    """此函式只供已完成驗證的後端流程呼叫。"""

    new_unlock = unlock_title(
        guild_id,
        character_id,
        code,
        eligibility_verified=True,
    )

    with get_connection() as db:
        row = db.execute(
            """
            SELECT id
            FROM rp_achievement_definitions
            WHERE guild_id=? AND code=?
            """,
            (str(guild_id), code),
        ).fetchone()

    if row is None:
        raise ValueError("稱號資料不存在。")

    rewards = grant_title_rewards(
        guild_id,
        character_id,
        int(row["id"]),
    )

    return {
        "title": code,
        "new_unlock": new_unlock,
        "rewards": rewards,
    }


def check_and_unlock(guild_id, character_id):
    """檢查角色目前已符合的普通稱號條件。"""

    eligible = eligible_fixed_titles(
        guild_id,
        character_id,
    )

    results = []

    for code in eligible:
        results.append(
            complete_unlock(
                guild_id,
                character_id,
                code,
            )
        )

    return results


# ==========================================
# 開城元老專用檢查
# ==========================================

def trusted_founder_unlock(
    guild_id,
    character_id,
    *,
    created_at,
    activated_at,
):
    """供正式角色建立／啟用模組傳入可信任時間。

    不接受玩家自行填寫的日期。
    """

    init_db()

    with get_connection() as db:
        setting = db.execute(
            """
            SELECT opened_at
            FROM rp_achievement_guild_settings
            WHERE guild_id=?
            """,
            (str(guild_id),),
        ).fetchone()

    if setting is None or not setting["opened_at"]:
        raise ValueError("尚未設定正式開服日期。")

    opening = parse_iso(setting["opened_at"])
    created = parse_iso(created_at)
    activated = parse_iso(activated_at)

    if created is None or activated is None:
        raise ValueError("缺少角色建立或啟用時間。")

    # 此處以「開服日起一個月」的曆月範圍計算，
    # 避免直接假設每個月都有三十天。
    from calendar import monthrange

    year = opening.year
    month = opening.month + 1

    if month == 13:
        year += 1
        month = 1

    last_day = monthrange(year, month)[1]

    deadline = opening.replace(
        year=year,
        month=month,
        day=min(opening.day, last_day),
    )

    if not (
        opening <= created < deadline
        and opening <= activated < deadline
    ):
        return {
            "eligible": False,
            "reason": "角色不在開服首月建立並啟用。",
        }

    result = complete_unlock(
        guild_id,
        character_id,
        "founder",
    )

    return {
        "eligible": True,
        "result": result,
    }


# ==========================================
# 建立指定大型世界活動
# ==========================================

def create_legend_event(
    guild_id,
    actor_id,
    guild_owner_id,
    event_id,
    title,
    organizer_id,
):
    """群主指定活動與官方主辦人。"""

    owner_only(actor_id, guild_owner_id)
    init_db()

    event_id = str(event_id).strip()
    title = str(title).strip()

    if not 1 <= len(event_id) <= 100:
        raise ValueError("活動代碼須為 1～100 字。")

    if not 1 <= len(title) <= 150:
        raise ValueError("活動名稱須為 1～150 字。")

    organizer_id = valid_positive_id(
        organizer_id, "主辦人 ID"
    )

    with transaction() as db:
        db.execute(
            """
            INSERT INTO rp_legend_events (
                guild_id,
                event_id,
                title,
                organizer_id,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                event_id,
                title,
                organizer_id,
                str(actor_id),
                as_iso(),
            ),
        )


def get_legend_event(db, guild_id, event_id):
    row = db.execute(
        """
        SELECT *
        FROM rp_legend_events
        WHERE guild_id=?
          AND event_id=?
          AND active=1
        """,
        (str(guild_id), str(event_id)),
    ).fetchone()

    if row is None:
        raise ValueError("重大活動不存在或已停用。")

    return row


# ==========================================
# 記錄參與及指定任務完成
# ==========================================

def record_legend_progress(
    guild_id,
    event_id,
    character_id,
    actor_id,
    guild_owner_id,
    *,
    participated=False,
    task_completed=False,
):
    """活動指定主辦人或群主可以記錄進度。

    旁觀不能取得稱號。
    必須同時達成參與及任務完成。
    """

    init_db()

    with transaction() as db:
        event = get_legend_event(
            db, guild_id, event_id
        )

        check_character(
            db, guild_id, character_id
        )

        authorized = {
            str(event["organizer_id"]),
            str(guild_owner_id),
        }

        if str(actor_id) not in authorized:
            raise PermissionError(
                "只有指定主辦人或群主能更新活動進度。"
            )

        # 任務完成代表確實參與。
        participated = participated or task_completed

        db.execute(
            """
            INSERT INTO rp_legend_participants (
                guild_id,
                event_id,
                character_id,
                participated,
                task_completed,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(
                guild_id, event_id, character_id
            )
            DO UPDATE SET
                participated=MAX(
                    participated,
                    excluded.participated
                ),
                task_completed=MAX(
                    task_completed,
                    excluded.task_completed
                ),
                updated_at=excluded.updated_at
            """,
            (
                str(guild_id),
                str(event_id),
                int(character_id),
                int(bool(participated)),
                int(bool(task_completed)),
                as_iso(),
            ),
        )


# ==========================================
# 主辦人／群主核准
# ==========================================

def approve_legend(
    guild_id,
    event_id,
    character_id,
    actor_id,
    guild_owner_id,
    *,
    role,
    owner_override=False,
):
    """role='organizer' 或 'owner'。

    正常情況：
        指定主辦人核准。
        群主再核准。
        兩項確認完成後才解鎖。

    群主例外：
        owner_override=True 可行使最高權限。
        例外操作獨立保存，不偽造主辦人簽名。
    """

    if role not in ("organizer", "owner"):
        raise ValueError("核准身分不正確。")

    init_db()

    gid = str(guild_id)
    cid = int(character_id)
    actor = str(actor_id)
    owner = str(guild_owner_id)

    with transaction() as db:
        event = get_legend_event(
            db, gid, event_id
        )

        participant = db.execute(
            """
            SELECT *
            FROM rp_legend_participants
            WHERE guild_id=?
              AND event_id=?
              AND character_id=?
            """,
            (gid, str(event_id), cid),
        ).fetchone()

        if participant is None:
            raise ValueError("角色尚未登記活動進度。")

        if (
            not participant["participated"]
            or not participant["task_completed"]
        ):
            raise ValueError(
                "角色必須實際參與並完成指定任務。"
            )

        organizer = str(event["organizer_id"])

        if owner_override:
            owner_only(actor, owner)

            db.execute(
                """
                UPDATE rp_legend_participants
                SET owner_override=1,
                    owner_approved_by=?,
                    owner_approved_at=?,
                    updated_at=?
                WHERE guild_id=?
                  AND event_id=?
                  AND character_id=?
                """,
                (
                    actor,
                    as_iso(),
                    as_iso(),
                    gid,
                    str(event_id),
                    cid,
                ),
            )

        elif role == "organizer":
            if actor != organizer:
                raise PermissionError(
                    "只有活動指定主辦人能進行主辦確認。"
                )

            db.execute(
                """
                UPDATE rp_legend_participants
                SET organizer_approved_by=?,
                    organizer_approved_at=?,
                    updated_at=?
                WHERE guild_id=?
                  AND event_id=?
                  AND character_id=?
                """,
                (
                    actor,
                    as_iso(),
                    as_iso(),
                    gid,
                    str(event_id),
                    cid,
                ),
            )

        else:
            owner_only(actor, owner)

            db.execute(
                """
                UPDATE rp_legend_participants
                SET owner_approved_by=?,
                    owner_approved_at=?,
                    updated_at=?
                WHERE guild_id=?
                  AND event_id=?
                  AND character_id=?
                """,
                (
                    actor,
                    as_iso(),
                    as_iso(),
                    gid,
                    str(event_id),
                    cid,
                ),
            )

        current = db.execute(
            """
            SELECT *
            FROM rp_legend_participants
            WHERE guild_id=?
              AND event_id=?
              AND character_id=?
            """,
            (gid, str(event_id), cid),
        ).fetchone()

        completed = bool(
            current["participated"]
            and current["task_completed"]
            and (
                current["owner_override"]
                or (
                    current["organizer_approved_by"]
                    and current["owner_approved_by"]
                )
            )
        )

    if not completed:
        return {
            "completed": False,
            "unlocked": False,
        }

    result = complete_unlock(
        guild_id,
        character_id,
        "legend",
    )

    return {
        "completed": True,
        "unlocked": result["new_unlock"],
        "rewards": result["rewards"],
    }


# ==========================================
# 從事件觸發檢查
# ==========================================

def process_trusted_event(
    guild_id,
    character_id,
    event_type,
    event_id,
    *,
    amount=1,
    subject_id=None,
    metadata=None,
):
    """提供給其他可信任模組的整合入口。

    例：
    商城實際扣款成功後：
        process_trusted_event(
            guild_id, character_id,
            'shop_purchase',
            purchase_id,
            amount=actual_paid,
        )

    合約完成後：
        process_trusted_event(
            guild_id, character_id,
            'finished_contract',
            contract_id,
        )

    官方 NPC 劇情章節完成後：
        process_trusted_event(
            guild_id, character_id,
            'npc_story_chapter',
            completion_id,
            subject_id=official_npc_id,
        )
    """

    inserted = record_event(
        guild_id,
        character_id,
        event_type,
        event_id,
        amount=amount,
        subject_id=subject_id,
        metadata=metadata,
    )

    # 重複事件不再次計算進度。
    if not inserted:
        return {
            "new_event": False,
            "titles": [],
        }

    titles = check_and_unlock(
        guild_id,
        character_id,
    )

    return {
        "new_event": True,
        "titles": titles,
    }


# ==========================================
# Discord 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    LOG.info(
        "稱號條件偵測與大型活動確認模組已載入。"
    )
