
"""Discord RP｜稱號一次性獎勵 V2。

檔案：modules/achievement_rewards.py

整合：
- achievement_core.py
- core/stat_rewards.py
- core/database.py

功能：
1. 稱號取得後建立一次性獎勵項目。
2. 正式屬性透過統一數值獎勵核心發放。
3. 粉絲及大眾認知度使用統一數值紀錄。
4. 影響指數只允許固定的稱號獎勵。
5. 每一項獎勵獨立防止重複發放。
6. 發獎中斷可以重新執行。
7. 無法確認的獎勵保留 pending。
8. 不擅自建立假的背包商品或房產。

注意：
這個模組不負責判斷角色是否達成稱號條件。
條件驗證仍由 achievement_triggers.py 處理。
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

from core.stat_rewards import (
    init_db as init_stat_rewards,
    grant_stats,
)

from modules.achievement_core import (
    init_db as init_achievements,
)


LOG = logging.getLogger(__name__)


# ==========================================
# 獎勵名稱對應
# ==========================================

STAT_MAP = {
    "heat": "流量熱度",
    "business_value": "商業價值",
    "favor": "路人緣",
    "pr": "公關",
    "influence": "影響指數",
    "appearance": "容貌",
    "intelligence": "才智",
    "fans": "粉絲",
    "recognition": "大眾認知度",
}

PERFORMANCE_ATTRIBUTES = (
    "歌唱",
    "舞蹈",
    "演繹",
    "主持",
    "綜藝",
    "個人才藝",
)

# 這些需要另外連接既有的專用系統。
EXTERNAL_REWARDS = frozenset({
    "action_item",
    "ordinary_apartment_room",
})

SPECIAL_REWARDS = frozenset({
    "free_points",
    "money",
})

SUPPORTED_KEYS = (
    set(STAT_MAP)
    | set(PERFORMANCE_ATTRIBUTES)
    | SPECIAL_REWARDS
    | EXTERNAL_REWARDS
    | {"all_performance_stats"}
)


# ==========================================
# 資料庫
# ==========================================

def init_db():
    init_achievements()
    init_stat_rewards()

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
                    CHECK(status IN (
                        'pending',
                        'done'
                    )),

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
# 確認稱號已正式取得
# ==========================================

def get_unlocked_title(
    guild_id,
    character_id,
    achievement_id,
):
    init_db()

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
              ON d.id=a.achievement_id
             AND d.guild_id=a.guild_id

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
            "角色尚未正式取得此稱號。"
        )

    return dict(row)


# ==========================================
# 展開獎勵
# ==========================================

def expand_rewards(reward_data):
    """將所有一次性獎勵拆成獨立項目。

    例如：
        heat: 50
        fans: 100

    會轉成：
        stat:流量熱度
        stat:粉絲

    方便逐項檢查發放紀錄。
    """

    if not isinstance(reward_data, dict):
        raise ValueError(
            "稱號獎勵資料必須是字典。"
        )

    result = {}

    for key, amount in reward_data.items():
        if key not in SUPPORTED_KEYS:
            raise ValueError(
                f"尚未支援的稱號獎勵：{key}"
            )

        if (
            type(amount) is not int
            or amount < 0
        ):
            raise ValueError(
                f"{key} 必須是非負整數。"
            )

        if amount == 0:
            continue

        if key == "all_performance_stats":
            for name in PERFORMANCE_ATTRIBUTES:
                result[f"stat:{name}"] = amount

        elif key in STAT_MAP:
            result[
                f"stat:{STAT_MAP[key]}"
            ] = amount

        elif key in PERFORMANCE_ATTRIBUTES:
            result[f"stat:{key}"] = amount

        else:
            result[key] = amount

    return result


# ==========================================
# 建立待發獎項目
# ==========================================

def prepare_rewards(
    guild_id,
    character_id,
    achievement_id,
):
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
            existing = db.execute(
                """
                SELECT amount
                FROM rp_achievement_reward_items
                WHERE guild_id=?
                  AND character_id=?
                  AND achievement_id=?
                  AND reward_key=?
                """,
                (
                    str(guild_id),
                    int(character_id),
                    int(achievement_id),
                    key,
                ),
            ).fetchone()

            if existing is not None:
                if int(existing["amount"]) != amount:
                    raise ValueError(
                        "稱號獎勵已建立紀錄，"
                        "不可直接修改原獎勵數值。"
                    )

                continue

            db.execute(
                """
                INSERT INTO rp_achievement_reward_items (
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

        refresh_reward_state(
            db,
            guild_id,
            character_id,
            achievement_id,
        )

    return expanded


# ==========================================
# 更新整體發獎狀態
# ==========================================

def refresh_reward_state(
    db,
    guild_id,
    character_id,
    achievement_id,
):
    row = db.execute(
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
    ).fetchone()

    pending_count = int(
        row["total"]
    )

    state = (
        "done"
        if pending_count == 0
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
            state,
            str(guild_id),
            int(character_id),
            int(achievement_id),
        ),
    )

    return state


# ==========================================
# 標記項目已發放
# ==========================================

def mark_done(
    guild_id,
    character_id,
    achievement_id,
    reward_key,
):
    with transaction() as db:
        db.execute(
            """
            UPDATE rp_achievement_reward_items

            SET status='done',
                applied_at=CURRENT_TIMESTAMP

            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?
              AND reward_key=?
            """,
            (
                str(guild_id),
                int(character_id),
                int(achievement_id),
                reward_key,
            ),
        )

        refresh_reward_state(
            db,
            guild_id,
            character_id,
            achievement_id,
        )


# ==========================================
# 統一數值發放
# ==========================================

def grant_stat_item(
    guild_id,
    character_id,
    achievement_id,
    reward_key,
    amount,
    title_name,
):
    """所有正式屬性及追蹤數據統一發放。

    每個稱號、每項數值各有獨立 source_id。

    即使數值已入帳，但機器人在標記
    done 前重新啟動，也不會重複增加。
    """

    stat_name = reward_key.split(
        ":",
        1,
    )[1]

    unique_id = (
        f"achievement:"
        f"{guild_id}:"
        f"{character_id}:"
        f"{achievement_id}:"
        f"{reward_key}"
    )

    result = grant_stats(
        guild_id,
        character_id,
        {
            stat_name: int(amount),
        },
        source="title_reward",
        source_id=unique_id,
        reason=(
            f"取得稱號「{title_name}」"
            "的一次性獎勵"
        ),
        random=False,
        purchased=False,
    )

    # 重試時只要統一核心已記錄同一筆獎勵，
    # 就可安全將外部獎勵項目標為完成。
    if result["applied"] or result["duplicate"]:
        mark_done(
            guild_id,
            character_id,
            achievement_id,
            reward_key,
        )

        return (
            "done"
            if result["applied"]
            else "already_done"
        )

    return "pending"


# ==========================================
# 自由點數
# ==========================================

def grant_free_points(
    guild_id,
    character_id,
    achievement_id,
    amount,
):
    """使用既有自由點數資料表。

    點數入帳與獎勵項目完成記錄
    必須處於同一筆交易。
    """

    with transaction() as db:
        row = db.execute(
            """
            SELECT status
            FROM rp_achievement_reward_items
            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?
              AND reward_key='free_points'
            """,
            (
                str(guild_id),
                int(character_id),
                int(achievement_id),
            ),
        ).fetchone()

        if row is None:
            raise ValueError(
                "找不到自由點數獎勵。"
            )

        if row["status"] == "done":
            return "already_done"

        # 不自行創建第二套 VIP 餘額結構。
        # 必須先存在原本的正式資料表。
        table = db.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
              AND name='vip_balances'
            """
        ).fetchone()

        if table is None:
            return "pending"

        columns = {
            r["name"]
            for r in db.execute(
                "PRAGMA table_info(vip_balances)"
            ).fetchall()
        }

        if not {
            "guild_id",
            "character_id",
            "free_points",
        }.issubset(columns):
            return "pending"

        existing = db.execute(
            """
            SELECT 1
            FROM vip_balances
            WHERE guild_id=?
              AND character_id=?
            """,
            (
                str(guild_id),
                int(character_id),
            ),
        ).fetchone()

        if existing is None:
            # 避免既有表有其他必填欄位，
            # 暫不自行建立不完整的 VIP 帳戶。
            return "pending"

        db.execute(
            """
            UPDATE vip_balances
            SET free_points=free_points+?
            WHERE guild_id=?
              AND character_id=?
            """,
            (
                int(amount),
                str(guild_id),
                int(character_id),
            ),
        )

        db.execute(
            """
            UPDATE rp_achievement_reward_items
            SET status='done',
                applied_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND character_id=?
              AND achievement_id=?
              AND reward_key='free_points'
            """,
            (
                str(guild_id),
                int(character_id),
                int(achievement_id),
            ),
        )

        refresh_reward_state(
            db,
            guild_id,
            character_id,
            achievement_id,
        )

    return "done"


# ==========================================
# 單項獎勵執行
# ==========================================

def grant_one_item(
    guild_id,
    character_id,
    achievement_id,
    reward_key,
):
    """回傳 done、already_done 或 pending。"""

    title = get_unlocked_title(
        guild_id,
        character_id,
        achievement_id,
    )

    with get_connection() as db:
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
                str(guild_id),
                int(character_id),
                int(achievement_id),
                reward_key,
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "找不到指定的獎勵項目。"
        )

    if row["status"] == "done":
        return "already_done"

    amount = int(
        row["amount"]
    )

    # 正式屬性、粉絲、大眾認知度
    if reward_key.startswith("stat:"):
        return grant_stat_item(
            guild_id,
            character_id,
            achievement_id,
            reward_key,
            amount,
            title["name"],
        )

    # 自由點數
    if reward_key == "free_points":
        return grant_free_points(
            guild_id,
            character_id,
            achievement_id,
            amount,
        )

    # 金幣、物品、房產：
    # 不在尚未核實對應接口時擅自發放。
    if (
        reward_key == "money"
        or reward_key in EXTERNAL_REWARDS
    ):
        return "pending"

    LOG.warning(
        "未支援的稱號獎勵：%s",
        reward_key,
    )

    return "pending"


# ==========================================
# 執行完整一次性獎勵
# ==========================================

def grant_title_rewards(
    guild_id,
    character_id,
    achievement_id,
):
    """由稱號解鎖後的可信任流程呼叫。

    可以安全重試，已完成項目不會重發。
    """

    prepare_rewards(
        guild_id,
        character_id,
        achievement_id,
    )

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT reward_key
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
        key = row["reward_key"]

        try:
            outcome = grant_one_item(
                guild_id,
                character_id,
                achievement_id,
                key,
            )

            results[outcome].append(key)

        except Exception as exc:
            LOG.exception(
                "稱號一次性獎勵失敗："
                "guild=%s character=%s "
                "title=%s reward=%s",
                guild_id,
                character_id,
                achievement_id,
                key,
            )

            results["errors"].append({
                "reward": key,
                "error": str(exc),
            })

    return results


# ==========================================
# 查詢待發獎項目
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

    params = [str(guild_id)]

    if character_id is not None:
        query += " AND r.character_id=?"
        params.append(int(character_id))

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

    return [dict(row) for row in rows]


# ==========================================
# 查詢指定稱號獎勵狀態
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

    return [dict(row) for row in rows]


# ==========================================
# Discord 模組入口
# ==========================================

async def setup(bot: commands.Bot):
    init_db()

    LOG.info(
        "稱號一次性獎勵 V2 已載入。"
    )
