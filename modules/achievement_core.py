
"""Discord RP｜成就稱號系統核心。

功能：
1. 建立 12 個固定成就稱號。
2. 支援可持續新增的活動專屬稱號。
3. 角色獨立解鎖與穿戴。
4. 首次穿戴啟動 168 小時計時。
5. 卸下與重新穿戴不重置倒數。
6. 穿戴數量不限，角色面板最多展示 3 個。
7. 紀錄穿戴歷史，供每日效果結算。
8. 紀錄一次性獎勵與每日效果的處理狀態。

注意：
本模組不直接發放金錢、屬性、物品或房產。
實際發獎由後續 achievement_rewards.py 處理。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from discord.ext import commands

from core.database import (
    transaction,
    get_connection,
)


EFFECT_HOURS = 168
MAX_DISPLAY_TITLES = 3


# ==========================================
# 十二個固定稱號
# ==========================================

# 欄位：
# code
# name
# condition
# reward
# effect

FIXED_TITLES = [

    {
        "code": "wealth",
        "name": "財運亨通",
        "condition": {
            "type": "total_assets",
            "amount": 500000,
            "include": [
                "wallet",
                "bank",
                "fixed_deposit",
            ],
        },
        "reward": {
            "free_points": 100,
            "business_value": 30,
            "heat": 30,
            "influence": 10,
        },
        "effect": {
            "type": "random_daily",
            "amount": 5,
            "pool": [
                "heat",
                "pr",
                "business_value",
                "fans",
            ],
        },
    },

    {
        "code": "spender",
        "name": "揮金如土",
        "condition": {
            "type": "shop_spent",
            "amount": 1000000,
        },
        "reward": {
            "heat": 50,
            "fans": 50,
            "business_value": 100,
            "influence": 15,
        },
        "effect": {
            "type": "shop_discount",
            "rate": 0.9,
        },
    },

    {
        "code": "spotlight",
        "name": "閃閃發光",
        "condition": {
            "type": "attribute",
            "name": "heat",
            "amount": 2000,
        },
        "reward": {
            "recognition": 15,
            "heat": 50,
            "favor": 50,
            "appearance": 30,
            "influence": 10,
        },
        "effect": {
            "type": "daily",
            "stats": {
                "heat": 10,
            },
        },
    },

    {
        "code": "popular",
        "name": "人氣王",
        "condition": {
            "type": "attribute",
            "name": "favor",
            "amount": 2000,
        },
        "reward": {
            "favor": 30,
            "pr": 30,
            "influence": 10,
            "free_points": 100,
            "appearance": 30,
        },
        "effect": {
            "type": "daily",
            "stats": {
                "fans": 10,
                "recognition": 5,
            },
        },
    },

    {
        "code": "eloquent",
        "name": "舌燦蓮花",
        "condition": {
            "type": "attribute",
            "name": "pr",
            "amount": 1500,
        },
        "reward": {
            "pr": 40,
            "intelligence": 50,
            "fans": 20,
            "recognition": 50,
            "influence": 15,
        },
        "effect": {
            "type": "daily",
            "stats": {
                "pr": 10,
            },
        },
    },

    {
        "code": "commerce",
        "name": "商貿新手",
        "condition": {
            "type": "finished_contracts",
            "count": 10,
        },
        "reward": {
            "business_value": 50,
            "pr": 30,
            "influence": 20,
            "free_points": 150,
        },
        "effect": {
            "type": "daily",
            "stats": {
                "business_value": 20,
            },
        },
    },

    {
        "code": "faction_star",
        "name": "眾望所歸",
        "condition": {
            "type": "faction_monthly_first",
        },
        "reward": {
            "influence": 15,
            "favor": 50,
            "fans": 100,
            "heat": 50,
        },
        "effect": {
            "type": "daily",
            "stats": {
                "fans": 20,
            },
        },
    },

    {
        "code": "fate",
        "name": "命運改寫者",
        "condition": {
            "type": "npc_story_chapters",
            "count": 5,
            "distinct_npcs": True,
        },
        "reward": {
            "free_points": 100,
            "intelligence": 30,
            "favor": 50,
            "all_performance_stats": 10,
            "influence": 10,
        },
        "effect": {
            "type": "random_daily",
            "amount": 10,
            "exclude": [
                "influence",
                "tracking",
            ],
        },
    },

    {
        "code": "founder",
        "name": "開城元老",
        "condition": {
            "type": "created_in_first_month",
        },
        "reward": {
            "free_points": 200,
            "action_item": 20,
            "money": 5000,
            "ordinary_apartment_room": 1,
        },
        "effect": {
            "type": "none",
        },
    },

    {
        "code": "champion",
        "name": "金榜題名",
        "condition": {
            "type": "official_competition_first",
        },
        "reward": {
            "free_points": 100,
            "recognition": 50,
            "heat": 50,
            "influence": 15,
            "fans": 50,
        },
        "effect": {
            "type": "daily",
            "stats": {
                "recognition": 10,
            },
        },
    },

    {
        "code": "night",
        "name": "夜幕來客",
        "condition": {
            "type": "hidden_quest_or_night_story",
        },
        "reward": {
            "free_points": 50,
            "intelligence": 100,
            "influence": 15,
        },
        "effect": {
            "type": "random_daily",
            "amount": 5,
            "exclude": [
                "influence",
                "tracking",
            ],
        },
    },

    {
        "code": "legend",
        "name": "傳奇見證者",
        "condition": {
            "type": "event_dual_approval",
            "requires_task_completion": True,
            "requires_organizer": True,
            "requires_owner": True,
            "spectators_eligible": False,
        },
        "reward": {
            "free_points": 200,
            "influence": 30,
            "recognition": 100,
            "fans": 100,
        },
        "effect": {
            "type": "daily",
            "stats": {
                "heat": 20,
            },
        },
    },
]


# ==========================================
# 時間工具
# ==========================================

def utc_now():
    return datetime.now(
        timezone.utc
    )


def to_iso(value):
    if value.tzinfo is None:
        raise ValueError(
            "時間必須包含時區。"
        )

    return value.astimezone(
        timezone.utc
    ).isoformat(
        timespec="seconds"
    )


def from_iso(value):
    if not value:
        return None

    parsed = datetime.fromisoformat(
        value
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(
        timezone.utc
    )


# ==========================================
# 資料庫
# ==========================================

def init_db():

    with transaction() as db:

        # 稱號定義
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_definitions (

                id INTEGER PRIMARY KEY
                    AUTOINCREMENT,

                guild_id TEXT NOT NULL,

                code TEXT NOT NULL,

                name TEXT NOT NULL,

                category TEXT NOT NULL
                    CHECK (
                        category IN (
                            'fixed',
                            'event'
                        )
                    ),

                condition_json TEXT NOT NULL,

                reward_json TEXT NOT NULL,

                effect_json TEXT NOT NULL,

                enabled INTEGER NOT NULL
                    DEFAULT 1,

                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                UNIQUE (
                    guild_id,
                    code
                )
            )
        """)

        # 角色取得紀錄
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_character_achievements (

                guild_id TEXT NOT NULL,

                character_id INTEGER NOT NULL,

                achievement_id INTEGER NOT NULL,

                obtained_at TEXT NOT NULL,

                reward_state TEXT NOT NULL
                    DEFAULT 'pending'
                    CHECK (
                        reward_state IN (
                            'pending',
                            'done'
                        )
                    ),

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    achievement_id
                ),

                FOREIGN KEY (
                    achievement_id
                )
                REFERENCES
                    rp_achievement_definitions(id)
            )
        """)

        # 穿戴狀態與首次啟動時間
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_title_equipment (

                guild_id TEXT NOT NULL,

                character_id INTEGER NOT NULL,

                achievement_id INTEGER NOT NULL,

                equipped INTEGER NOT NULL
                    DEFAULT 0,

                first_activated_at TEXT,

                expires_at TEXT,

                display_order INTEGER,

                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    achievement_id
                ),

                CHECK (
                    display_order IS NULL
                    OR display_order BETWEEN 1 AND 3
                )
            )
        """)

        # 同角色的三個展示欄位不可重複
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            rp_title_display_unique

            ON rp_title_equipment (
                guild_id,
                character_id,
                display_order
            )

            WHERE display_order IS NOT NULL
        """)

        # 穿戴歷史
        # 供每日整點結算時判斷是否穿戴
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_title_wear_history (

                id INTEGER PRIMARY KEY
                    AUTOINCREMENT,

                guild_id TEXT NOT NULL,

                character_id INTEGER NOT NULL,

                achievement_id INTEGER NOT NULL,

                worn_from TEXT NOT NULL,

                worn_until TEXT
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            rp_title_wear_lookup

            ON rp_title_wear_history (
                guild_id,
                character_id,
                achievement_id,
                worn_from
            )
        """)

        # 每日效果結算紀錄
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_title_daily_claims (

                guild_id TEXT NOT NULL,

                character_id INTEGER NOT NULL,

                cycle_key TEXT NOT NULL,

                stat_key TEXT NOT NULL,

                granted_amount INTEGER NOT NULL,

                source_achievement_id INTEGER
                    NOT NULL,

                state TEXT NOT NULL
                    DEFAULT 'pending'
                    CHECK (
                        state IN (
                            'pending',
                            'done'
                        )
                    ),

                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    cycle_key,
                    stat_key
                )
            )
        """)

        # 傳奇見證者雙重確認
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_approvals (

                guild_id TEXT NOT NULL,

                character_id INTEGER NOT NULL,

                achievement_id INTEGER NOT NULL,

                organizer_id TEXT,

                owner_id TEXT,

                organizer_at TEXT,

                owner_at TEXT,

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    achievement_id
                )
            )
        """)


# ==========================================
# 初始化十二個固定稱號
# ==========================================

def seed_fixed_titles(guild_id):

    init_db()

    with transaction() as db:

        for item in FIXED_TITLES:

            db.execute(
                """
                INSERT OR IGNORE INTO
                rp_achievement_definitions (

                    guild_id,
                    code,
                    name,
                    category,
                    condition_json,
                    reward_json,
                    effect_json
                )

                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(guild_id),

                    item["code"],

                    item["name"],

                    "fixed",

                    json.dumps(
                        item["condition"],
                        ensure_ascii=False,
                    ),

                    json.dumps(
                        item["reward"],
                        ensure_ascii=False,
                    ),

                    json.dumps(
                        item["effect"],
                        ensure_ascii=False,
                    ),
                ),
            )

    return len(FIXED_TITLES)


# ==========================================
# 建立活動專屬稱號
# ==========================================

def create_event_title(
    guild_id,
    code,
    name,
    condition,
    reward,
    effect,
):

    init_db()

    if (
        not code.startswith("event:")
        or len(code) > 100
    ):
        raise ValueError(
            "活動稱號代碼必須以 "
            "event: 開頭，且不超過 100 字。"
        )

    name = name.strip()

    if not 1 <= len(name) <= 100:
        raise ValueError(
            "稱號名稱須為 1～100 字。"
        )

    if not all(
        isinstance(value, dict)
        for value in (
            condition,
            reward,
            effect,
        )
    ):
        raise ValueError(
            "條件、獎勵及效果必須是資料字典。"
        )

    with transaction() as db:

        cursor = db.execute(
            """
            INSERT INTO
            rp_achievement_definitions (

                guild_id,
                code,
                name,
                category,
                condition_json,
                reward_json,
                effect_json
            )

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                code,
                name,
                "event",

                json.dumps(
                    condition,
                    ensure_ascii=False,
                ),

                json.dumps(
                    reward,
                    ensure_ascii=False,
                ),

                json.dumps(
                    effect,
                    ensure_ascii=False,
                ),
            ),
        )

        return cursor.lastrowid


# ==========================================
# 稱號解鎖
# ==========================================

def unlock_title(
    guild_id,
    character_id,
    code,
    *,
    eligibility_verified=False,
):
    """
    只有後端確認符合條件後才能呼叫。

    回傳 True：
        第一次取得稱號。

    回傳 False：
        曾經取得，不重複發獎。

    注意：
        本函式只建立待發獎紀錄，
        不實際修改角色屬性。
    """

    if not eligibility_verified:
        raise PermissionError(
            "尚未完成稱號取得條件驗證。"
        )

    init_db()

    with transaction() as db:

        title = db.execute(
            """
            SELECT id
            FROM rp_achievement_definitions

            WHERE guild_id=?
              AND code=?
              AND enabled=1
            """,
            (
                str(guild_id),
                code,
            ),
        ).fetchone()

        if title is None:
            raise ValueError(
                "稱號不存在或已停用。"
            )

        character = db.execute(
            """
            SELECT id
            FROM characters

            WHERE guild_id=?
              AND id=?
              AND status IN (
                  'approved',
                  'active'
              )
            """,
            (
                str(guild_id),
                int(character_id),
            ),
        ).fetchone()

        if character is None:
            raise ValueError(
                "找不到已核准或啟用的角色。"
            )

        cursor = db.execute(
            """
            INSERT OR IGNORE INTO
            rp_character_achievements (

                guild_id,
                character_id,
                achievement_id,
                obtained_at
            )

            VALUES (?, ?, ?, ?)
            """,
            (
                str(guild_id),
                int(character_id),
                int(title["id"]),
                to_iso(utc_now()),
            ),
        )

        return cursor.rowcount == 1


# ==========================================
# 穿戴與卸下
# ==========================================

def equip_title(
    guild_id,
    character_id,
    code,
    *,
    equip=True,
    now=None,
):
    """
    第一次穿戴才啟動 168 小時。

    卸下：
        不會暫停、不會重置計時。

    重新穿戴：
        使用原本的到期時間。

    沒有每日效果的稱號：
        不需要 168 小時計時。
    """

    moment = now or utc_now()

    if moment.tzinfo is None:
        raise ValueError(
            "時間必須包含時區。"
        )

    moment = moment.astimezone(
        timezone.utc
    )

    gid = str(guild_id)
    cid = int(character_id)

    with transaction() as db:

        row = db.execute(
            """
            SELECT
                a.achievement_id,
                d.effect_json,
                d.enabled,
                e.first_activated_at,
                e.expires_at,
                e.equipped

            FROM rp_character_achievements a

            JOIN rp_achievement_definitions d
              ON d.id = a.achievement_id

            LEFT JOIN rp_title_equipment e
              ON e.guild_id = a.guild_id
             AND e.character_id = a.character_id
             AND e.achievement_id = a.achievement_id

            WHERE a.guild_id=?
              AND a.character_id=?
              AND d.code=?
            """,
            (
                gid,
                cid,
                code,
            ),
        ).fetchone()

        if row is None:
            raise ValueError(
                "角色尚未取得此稱號。"
            )

        if equip and not row["enabled"]:
            raise ValueError(
                "此稱號已停用，無法穿戴。"
            )

        achievement_id = int(
            row["achievement_id"]
        )

        was_equipped = bool(
            row["equipped"]
        )

        first = row[
            "first_activated_at"
        ]

        expires = row[
            "expires_at"
        ]

        effect = json.loads(
            row["effect_json"]
        )

        if (
            equip
            and first is None
            and effect.get("type") != "none"
        ):

            first = to_iso(moment)

            expires = to_iso(
                moment + timedelta(
                    hours=EFFECT_HOURS
                )
            )

        db.execute(
            """
            INSERT INTO rp_title_equipment (

                guild_id,
                character_id,
                achievement_id,
                equipped,
                first_activated_at,
                expires_at
            )

            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT (
                guild_id,
                character_id,
                achievement_id
            )

            DO UPDATE SET

                equipped=excluded.equipped,

                first_activated_at=
                    excluded.first_activated_at,

                expires_at=excluded.expires_at,

                updated_at=CURRENT_TIMESTAMP
            """,
            (
                gid,
                cid,
                achievement_id,
                int(bool(equip)),
                first,
                expires,
            ),
        )

        # 從未穿戴變成穿戴
        if equip and not was_equipped:

            db.execute(
                """
                INSERT INTO
                rp_title_wear_history (

                    guild_id,
                    character_id,
                    achievement_id,
                    worn_from
                )

                VALUES (?, ?, ?, ?)
                """,
                (
                    gid,
                    cid,
                    achievement_id,
                    to_iso(moment),
                ),
            )

        # 從穿戴變成卸下
        elif not equip and was_equipped:

            db.execute(
                """
                UPDATE rp_title_wear_history

                SET worn_until=?

                WHERE id=(
                    SELECT id
                    FROM rp_title_wear_history

                    WHERE guild_id=?
                      AND character_id=?
                      AND achievement_id=?
                      AND worn_until IS NULL

                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (
                    to_iso(moment),
                    gid,
                    cid,
                    achievement_id,
                ),
            )

            # 卸下的稱號不能繼續展示
            db.execute(
                """
                UPDATE rp_title_equipment

                SET display_order=NULL

                WHERE guild_id=?
                  AND character_id=?
                  AND achievement_id=?
                """,
                (
                    gid,
                    cid,
                    achievement_id,
                ),
            )

        return {
            "equipped": bool(equip),
            "first_activated_at": first,
            "expires_at": expires,
        }


# ==========================================
# 設定角色面板展示稱號
# ==========================================

def set_display_titles(
    guild_id,
    character_id,
    codes,
):
    """
    可以穿戴無限個稱號，
    但是角色面板最多展示三個。
    """

    codes = list(codes)

    if len(codes) > MAX_DISPLAY_TITLES:
        raise ValueError(
            "角色面板最多展示三個稱號。"
        )

    if len(codes) != len(set(codes)):
        raise ValueError(
            "展示稱號不能重複。"
        )

    gid = str(guild_id)
    cid = int(character_id)

    with transaction() as db:

        rows = db.execute(
            """
            SELECT d.code

            FROM rp_title_equipment e

            JOIN rp_achievement_definitions d
              ON d.id=e.achievement_id

            WHERE e.guild_id=?
              AND e.character_id=?
              AND e.equipped=1
              AND d.enabled=1
            """,
            (
                gid,
                cid,
            ),
        ).fetchall()

        allowed = {
            row["code"]
            for row in rows
        }

        if not set(codes).issubset(
            allowed
        ):
            raise ValueError(
                "只能展示目前已穿戴的稱號。"
            )

        # 先清除原本排序
        db.execute(
            """
            UPDATE rp_title_equipment

            SET display_order=NULL

            WHERE guild_id=?
              AND character_id=?
            """,
            (
                gid,
                cid,
            ),
        )

        # 寫入新的三格展示位置
        for index, code in enumerate(
            codes,
            start=1,
        ):

            db.execute(
                """
                UPDATE rp_title_equipment

                SET display_order=?

                WHERE guild_id=?
                  AND character_id=?
                  AND achievement_id=(
                      SELECT id

                      FROM rp_achievement_definitions

                      WHERE guild_id=?
                        AND code=?
                  )
                """,
                (
                    index,
                    gid,
                    cid,
                    gid,
                    code,
                ),
            )


# ==========================================
# 查詢角色已取得稱號
# ==========================================

def list_character_titles(
    guild_id,
    character_id,
):

    init_db()

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT

                d.code,
                d.name,
                d.category,
                d.enabled,

                a.reward_state,
                a.obtained_at,

                e.equipped,
                e.first_activated_at,
                e.expires_at,
                e.display_order

            FROM rp_character_achievements a

            JOIN rp_achievement_definitions d
              ON d.id=a.achievement_id

            LEFT JOIN rp_title_equipment e
              ON e.guild_id=a.guild_id
             AND e.character_id=a.character_id
             AND e.achievement_id=a.achievement_id

            WHERE a.guild_id=?
              AND a.character_id=?

            ORDER BY
                a.obtained_at,
                d.id
            """,
            (
                str(guild_id),
                int(character_id),
            ),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


# ==========================================
# 查詢全部稱號定義
# ==========================================

def list_title_definitions(
    guild_id,
    *,
    include_disabled=False,
):

    seed_fixed_titles(guild_id)

    query = """
        SELECT *
        FROM rp_achievement_definitions
        WHERE guild_id=?
    """

    if not include_disabled:
        query += " AND enabled=1"

    query += """
        ORDER BY
            CASE category
                WHEN 'fixed' THEN 0
                ELSE 1
            END,
            id
    """

    with get_connection() as db:

        rows = db.execute(
            query,
            (str(guild_id),),
        ).fetchall()

    result = []

    for row in rows:

        item = dict(row)

        item["condition"] = json.loads(
            item.pop("condition_json")
        )

        item["reward"] = json.loads(
            item.pop("reward_json")
        )

        item["effect"] = json.loads(
            item.pop("effect_json")
        )

        result.append(item)

    return result


# ==========================================
# 確認某時刻是否正在穿戴
# ==========================================

def was_equipped_at(
    guild_id,
    character_id,
    achievement_id,
    timestamp,
):
    """
    每日效果模組使用。

    只有指定時刻確實穿戴，
    才符合當次效果領取資格。

    卸下當下視為未穿戴。
    """

    target = to_iso(timestamp)

    with get_connection() as db:

        row = db.execute(
            """
            SELECT id

            FROM rp_title_wear_history

            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?

              AND worn_from <= ?

              AND (
                  worn_until IS NULL
                  OR worn_until > ?
              )

            LIMIT 1
            """,
            (
                str(guild_id),
                int(character_id),
                int(achievement_id),
                target,
                target,
            ),
        ).fetchone()

    return row is not None


# ==========================================
# 剩餘有效時間
# ==========================================

def title_remaining_seconds(
    guild_id,
    character_id,
    code,
    *,
    now=None,
):

    moment = now or utc_now()

    titles = list_character_titles(
        guild_id,
        character_id,
    )

    for item in titles:

        if item["code"] != code:
            continue

        if not item["expires_at"]:
            return None

        expires = from_iso(
            item["expires_at"]
        )

        remaining = (
            expires - moment
        ).total_seconds()

        return max(
            0,
            int(remaining),
        )

    raise ValueError(
        "角色尚未取得此稱號。"
    )


# ==========================================
# Discord 模組入口
# ==========================================

async def setup(
    bot: commands.Bot,
):
    init_db()
