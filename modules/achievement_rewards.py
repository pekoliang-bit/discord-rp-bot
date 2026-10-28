
"""Discord RP｜稱號一次性獎勵發放系統。

搭配 modules.achievement_core 使用。

支援：
- 自由點數
- 十三項正式屬性
- 全部藝能屬性加成
- 系統金幣獎勵
- 分項發獎紀錄
- 重複發放保護
- 待接入外部系統獎勵

安全原則：
1. 只有已正式解鎖的稱號可以發獎。
2. 同一項獎勵只能實際發放一次。
3. 無法發放的獎勵保持 pending。
4. 不會用虛構商品或房產代替實際資產。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)

from modules.achievement_core import init_db as init_core


LOG = logging.getLogger(__name__)


# ==========================================
# 正式十三項屬性對應
# ==========================================

ATTRIBUTE_MAP = {
    "heat": "流量熱度",
    "business_value": "商業價值",
    "favor": "路人緣",
    "pr": "公關",
    "influence": "影響指數",
    "appearance": "容貌",
    "intelligence": "才智",
}

PERFORMANCE_ATTRIBUTES = [
    "歌唱",
    "舞蹈",
    "演繹",
    "主持",
    "綜藝",
    "個人才藝",
]


# 尚未直接寫入的獎勵
#
# fans、recognition 屬於追蹤數據，
# 不是十三項正式屬性。
#
# action_item 必須對應正式商城商品。
# ordinary_apartment_room 必須建立真實房產紀錄。

EXTERNAL_REWARDS = {
    "fans",
    "recognition",
    "action_item",
    "ordinary_apartment_room",
}


# ==========================================
# 資料庫
# ==========================================

def init_db():
    init_core()

    with transaction() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_achievement_reward_items (

                guild_id TEXT NOT NULL,

                character_id INTEGER NOT NULL,

                achievement_id INTEGER NOT NULL,

                reward_key TEXT NOT NULL,

                amount INTEGER NOT NULL,

                status TEXT NOT NULL
                    DEFAULT 'pending'
                    CHECK (
                        status IN (
                            'pending',
                            'done'
                        )
                    ),

                applied_at TEXT,

                PRIMARY KEY (
                    guild_id,
                    character_id,
                    achievement_id,
                    reward_key
                )
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_achievement_reward_pending

            ON rp_achievement_reward_items (
                guild_id,
                status
            )
        """)


# ==========================================
# 獎勵資料讀取
# ==========================================

def get_unlocked_title(
    guild_id,
    character_id,
    achievement_id,
):

    with get_connection() as db:

        row = db.execute(
            """
            SELECT
                d.id,
                d.code,
                d.name,
                d.reward_json,
                a.reward_state

            FROM rp_character_achievements a

            JOIN rp_achievement_definitions d
              ON d.id = a.achievement_id

            WHERE a.guild_id=?
              AND a.character_id=?
              AND a.achievement_id=?
            """,
            (
                str(guild_id),
                int(character_id),
                int(achievement_id),
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "角色尚未取得此稱號。"
        )

    return dict(row)


def expand_rewards(reward_data):
    """把稱號獎勵拆成獨立項目。"""

    result = {}

    for key, value in reward_data.items():

        if key == "all_performance_stats":

            if type(value) is not int or value < 0:
                raise ValueError(
                    "藝能獎勵數值錯誤。"
                )

            for name in PERFORMANCE_ATTRIBUTES:
                result[
                    "attribute:" + name
                ] = value

            continue

        if type(value) is not int or value < 0:
            raise ValueError(
                f"獎勵 {key} 必須是非負整數。"
            )

        if key in ATTRIBUTE_MAP:

            result[
                "attribute:" + ATTRIBUTE_MAP[key]
            ] = value

        else:
            result[key] = value

    return {
        key: value
        for key, value in result.items()
        if value > 0
    }


# ==========================================
# 建立待發獎項目
# ==========================================

def prepare_rewards(
    guild_id,
    character_id,
    achievement_id,
):
    """必須已取得稱號，才能建立獎勵。"""

    init_db()

    title = get_unlocked_title(
        guild_id,
        character_id,
        achievement_id,
    )

    reward_data = json.loads(
        title["reward_json"]
    )

    expanded = expand_rewards(
        reward_data
    )

    with transaction() as db:

        for key, amount in expanded.items():

            db.execute(
                """
                INSERT OR IGNORE INTO
                rp_achievement_reward_items (

                    guild_id,
                    character_id,
                    achievement_id,
                    reward_key,
                    amount
                )

                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(guild_id),
                    int(character_id),
                    int(achievement_id),
                    key,
                    amount,
                ),
            )

        if not expanded:

            db.execute(
                """
                UPDATE rp_character_achievements
                SET reward_state='done'

                WHERE guild_id=?
                  AND character_id=?
                  AND achievement_id=?
                """,
                (
                    str(guild_id),
                    int(character_id),
                    int(achievement_id),
                ),
            )

    return expanded


# ==========================================
# 自由點數
# ==========================================

def grant_free_points(
    db,
    guild_id,
    character_id,
    amount,
):

    # 若尚未初始化黑卡／VIP 資料表，
    # 則建立相容的基礎餘額表。
    db.execute("""
        CREATE TABLE IF NOT EXISTS vip_balances (

            guild_id TEXT NOT NULL,

            character_id INTEGER NOT NULL,

            free_points INTEGER NOT NULL
                DEFAULT 0,

            bonus_actions INTEGER NOT NULL
                DEFAULT 0,

            PRIMARY KEY (
                guild_id,
                character_id
            ),

            CHECK(free_points >= 0),
            CHECK(bonus_actions >= 0)
        )
    """)

    db.execute(
        """
        INSERT INTO vip_balances (
            guild_id,
            character_id,
            free_points
        )

        VALUES (?, ?, ?)

        ON CONFLICT(
            guild_id,
            character_id
        )

        DO UPDATE SET
            free_points =
                free_points + excluded.free_points
        """,
        (
            str(guild_id),
            int(character_id),
            int(amount),
        ),
    )


# ==========================================
# 屬性獎勵
# ==========================================

def grant_attribute(
    db,
    guild_id,
    character_id,
    attribute_name,
    amount,
):

    # 避免獎勵發到不存在的屬性。
    official_attributes = set(
        ATTRIBUTE_MAP.values()
    ) | set(
        PERFORMANCE_ATTRIBUTES
    )

    if attribute_name not in official_attributes:
        raise ValueError(
            f"未知正式屬性：{attribute_name}"
        )

    db.execute(
        """
        INSERT INTO attributes (

            guild_id,
            character_id,
            name,
            value
        )

        VALUES (?, ?, ?, ?)

        ON CONFLICT (
            guild_id,
            character_id,
            name
        )

        DO UPDATE SET

            value =
                value + excluded.value,

            version =
                version + 1
        """,
        (
            str(guild_id),
            int(character_id),
            attribute_name,
            int(amount),
        ),
    )


# ==========================================
# 更新稱號總獎勵狀態
# ==========================================

def refresh_reward_state(
    db,
    guild_id,
    character_id,
    achievement_id,
):

    pending = db.execute(
        """
        SELECT COUNT(*) AS total

        FROM rp_achievement_reward_items

        WHERE guild_id=?
          AND character_id=?
          AND achievement_id=?
          AND status='pending'
        """,
        (
            str(guild_id),
            int(character_id),
            int(achievement_id),
        ),
    ).fetchone()["total"]

    new_state = (
        "done"
        if pending == 0
        else "pending"
    )

    db.execute(
        """
        UPDATE rp_character_achievements

        SET reward_state=?

        WHERE guild_id=?
          AND character_id=?
          AND achievement_id=?
        """,
        (
            new_state,
            str(guild_id),
            int(character_id),
            int(achievement_id),
        ),
    )

    return new_state


# ==========================================
# 單項獎勵發放
# ==========================================

def grant_one_item(
    guild_id,
    character_id,
    achievement_id,
    reward_key,
):
    """回傳 done、already_done 或 pending。

    金幣使用 core.economy 的唯一交易編號防重。
    其他本地獎勵在同一筆 SQLite 交易完成。
    """

    gid = str(guild_id)
    cid = int(character_id)
    aid = int(achievement_id)

    # 先處理金幣：
    # 財務系統有自己的交易與防重複機制。
    if reward_key == "money":

        from core.economy import (
            issue_system_reward,
        )

        with get_connection() as db:

            row = db.execute(
                """
                SELECT amount, status
                FROM rp_achievement_reward_items

                WHERE guild_id=?
                  AND character_id=?
                  AND achievement_id=?
                  AND reward_key='money'
                """,
                (gid, cid, aid),
            ).fetchone()

        if row is None:
            raise ValueError(
                "找不到金幣獎勵項目。"
            )

        if row["status"] == "done":
            return "already_done"

        unique_key = (
            f"achievement:"
            f"{gid}:{cid}:{aid}:money"
        )

        # 即使機器人在金幣入帳後、
        # 更新獎勵紀錄前中斷，
        # 重試時也不會再次發放相同交易。
        issue_system_reward(
            gid,
            unique_key,
            "player",
            cid,
            int(row["amount"]),
            "稱號一次性獎勵",
        )

        with transaction() as db:

            db.execute(
                """
                UPDATE rp_achievement_reward_items

                SET
                    status='done',
                    applied_at=CURRENT_TIMESTAMP

                WHERE guild_id=?
                  AND character_id=?
                  AND achievement_id=?
                  AND reward_key='money'
                """,
                (gid, cid, aid),
            )

            refresh_reward_state(
                db, gid, cid, aid
            )

        return "done"

    # 其餘獎勵在同一筆交易中完成。
    with transaction() as db:

        row = db.execute(
            """
            SELECT amount, status

            FROM rp_achievement_reward_items

            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?
              AND reward_key=?
            """,
            (
                gid,
                cid,
                aid,
                reward_key,
            ),
        ).fetchone()

        if row is None:
            raise ValueError(
                "找不到這個獎勵項目。"
            )

        if row["status"] == "done":
            return "already_done"

        amount = int(row["amount"])

        if reward_key == "free_points":

            grant_free_points(
                db,
                gid,
                cid,
                amount,
            )

        elif reward_key.startswith(
            "attribute:"
        ):

            attribute_name = (
                reward_key.split(
                    ":",
                    1,
                )[1]
            )

            grant_attribute(
                db,
                gid,
                cid,
                attribute_name,
                amount,
            )

        elif reward_key in EXTERNAL_REWARDS:

            # 保留待發狀態。
            # 不假裝已經完成發放。
            return "pending"

        else:
            # 活動專屬稱號未接入的獎勵，
            # 也先保留而非擅自發獎。
            LOG.warning(
                "稱號獎勵尚無發放適配器：%s",
                reward_key,
            )

            return "pending"

        db.execute(
            """
            UPDATE rp_achievement_reward_items

            SET
                status='done',
                applied_at=CURRENT_TIMESTAMP

            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?
              AND reward_key=?
            """,
            (
                gid,
                cid,
                aid,
                reward_key,
            ),
        )

        refresh_reward_state(
            db,
            gid,
            cid,
            aid,
        )

    return "done"


# ==========================================
# 發放指定稱號的全部獎勵
# ==========================================

def grant_title_rewards(
    guild_id,
    character_id,
    achievement_id,
):
    """只能由可信任的後端，在取得稱號後呼叫。"""

    prepare_rewards(
        guild_id,
        character_id,
        achievement_id,
    )

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                reward_key,
                status

            FROM rp_achievement_reward_items

            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?

            ORDER BY reward_key
            """,
            (
                str(guild_id),
                int(character_id),
                int(achievement_id),
            ),
        ).fetchall()

    results = {
        "done": [],
        "already_done": [],
        "pending": [],
        "errors": [],
    }

    for row in rows:

        try:
            outcome = grant_one_item(
                guild_id,
                character_id,
                achievement_id,
                row["reward_key"],
            )

            results[outcome].append(
                row["reward_key"]
            )

        except Exception as exc:

            LOG.exception(
                "稱號獎勵發放失敗："
                "guild=%s character=%s "
                "achievement=%s reward=%s",
                guild_id,
                character_id,
                achievement_id,
                row["reward_key"],
            )

            results["errors"].append({
                "reward": row["reward_key"],
                "error": str(exc),
            })

    return results


# ==========================================
# 查詢未完成獎勵
# ==========================================

def list_pending_rewards(
    guild_id,
    character_id=None,
):

    init_db()

    query = """
        SELECT
            r.character_id,
            r.achievement_id,
            d.name AS title_name,
            r.reward_key,
            r.amount

        FROM rp_achievement_reward_items r

        JOIN rp_achievement_definitions d
          ON d.id=r.achievement_id
         AND d.guild_id=r.guild_id

        WHERE r.guild_id=?
          AND r.status='pending'
    """

    params = [
        str(guild_id)
    ]

    if character_id is not None:

        query += """
            AND r.character_id=?
        """

        params.append(
            int(character_id)
        )

    query += """
        ORDER BY
            r.character_id,
            r.achievement_id,
            r.reward_key
    """

    with get_connection() as db:

        rows = db.execute(
            query,
            params,
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ==========================================
# 一次性獎勵狀態
# ==========================================

def reward_summary(
    guild_id,
    character_id,
    achievement_id,
):

    init_db()

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                reward_key,
                amount,
                status,
                applied_at

            FROM rp_achievement_reward_items

            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?

            ORDER BY reward_key
            """,
            (
                str(guild_id),
                int(character_id),
                int(achievement_id),
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ==========================================
# 模組載入
# ==========================================

async def setup(
    bot: commands.Bot,
):
    init_db()

    LOG.info(
        "稱號一次性獎勵系統已載入。"
    )
