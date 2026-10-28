
"""Discord RP｜統一數值獎勵核心。

正式 13 項屬性沿用 attributes。
粉絲、大眾認知度、CP好感度暫存於獨立數值表。

影響指數只能由正式系統規則或指定獎勵發放。
禁止購買、付費配點、隨機加成及道具效果取得。

注意：只有可信任的後端模組可以呼叫 grant_stats。
呼叫端必須先驗證獎勵資格及真實事件來源。
"""

from __future__ import annotations

import json
from typing import Mapping

from core.database import get_connection, transaction


ATTRIBUTES = frozenset({
    "流量熱度",
    "商業價值",
    "路人緣",
    "公關",
    "影響指數",
    "容貌",
    "才智",
    "歌唱",
    "舞蹈",
    "演繹",
    "主持",
    "綜藝",
    "個人才藝",
})

METRICS = frozenset({
    "粉絲",
    "大眾認知度",
    "CP好感度",
})

INFLUENCE = "影響指數"

SOURCE_TYPES = frozenset({
    "system_rule",
    "title_reward",
    "event_reward",
    "mission_reward",
    "story_reward",
    "competition_reward",
    "shop_purchase",
    "paid_allocation",
    "random_reward",
    "item_use",
    "rumor",
    "admin_adjustment",
})

INFLUENCE_ALLOWED = frozenset({
    "system_rule",
    "title_reward",
    "event_reward",
    "mission_reward",
    "story_reward",
    "competition_reward",
    "admin_adjustment",
})


# ==========================================
# 資料庫
# ==========================================

def init_db():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_reward_metrics (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                metric TEXT NOT NULL,
                subject_character_id INTEGER
                    NOT NULL DEFAULT 0,
                value INTEGER NOT NULL DEFAULT 0
                    CHECK(value >= 0),

                PRIMARY KEY(
                    guild_id,
                    character_id,
                    metric,
                    subject_character_id
                ),

                FOREIGN KEY(guild_id, character_id)
                    REFERENCES characters(guild_id, id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_stat_reward_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                actor_id TEXT,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                UNIQUE(
                    guild_id,
                    source,
                    source_id,
                    character_id
                ),

                FOREIGN KEY(guild_id, character_id)
                    REFERENCES characters(guild_id, id)
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_rp_stat_reward_log_character
            ON rp_stat_reward_log(
                guild_id,
                character_id,
                id
            )
        """)


# ==========================================
# 數值驗證
# ==========================================

def normalize_changes(changes: Mapping):
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError(
            "獎勵必須是非空的數值字典。"
        )

    result = {}

    for raw_name, amount in changes.items():
        name = str(raw_name).strip().replace(
            "CP 好感度",
            "CP好感度",
        )

        if name not in ATTRIBUTES | METRICS:
            raise ValueError(
                f"未知或不允許發放的數值：{name}"
            )

        if type(amount) is not int or amount == 0:
            raise ValueError(
                f"{name} 的增減量必須是非零整數。"
            )

        if name in result:
            raise ValueError(
                f"重複的數值欄位：{name}"
            )

        result[name] = amount

    return result


# ==========================================
# 來源與影響指數管制
# ==========================================

def check_source(
    source,
    changes,
    *,
    random,
    purchased,
    approved,
    actor_id,
    reason,
):
    if source not in SOURCE_TYPES:
        raise ValueError(
            "未註冊的獎勵來源。"
        )

    # 隨機效果不能冒用固定獎勵來源。
    if random and source in INFLUENCE_ALLOWED:
        raise PermissionError(
            "隨機獎勵不可使用指定獎勵來源。"
        )

    # 購物效果不能冒用系統獎勵來源。
    if purchased and source in INFLUENCE_ALLOWED:
        raise PermissionError(
            "購物獎勵不可冒用系統或指定獎勵來源。"
        )

    if source == "admin_adjustment":
        if (
            not approved
            or not actor_id
            or not reason.strip()
        ):
            raise PermissionError(
                "官方調整必須先完成權限審核，"
                "並填寫操作者與原因。"
            )

    if INFLUENCE in changes:
        if (
            source not in INFLUENCE_ALLOWED
            or random
            or purchased
        ):
            raise PermissionError(
                "影響指數只能透過正式系統規則"
                "或指定獎勵取得；禁止隨機、"
                "購買、付費配點及道具效果。"
            )


# ==========================================
# 角色驗證
# ==========================================

def check_character(db, guild_id, character_id):
    row = db.execute(
        """
        SELECT status
        FROM characters
        WHERE guild_id=?
          AND id=?
        """,
        (
            str(guild_id),
            int(character_id),
        ),
    ).fetchone()

    if row is None:
        raise ValueError(
            "角色不存在。"
        )

    if row["status"] not in (
        "approved",
        "active",
    ):
        raise ValueError(
            "角色尚未核准或啟用。"
        )


# ==========================================
# 統一數值發放
# ==========================================

def grant_stats(
    guild_id,
    character_id,
    changes,
    *,
    source,
    source_id,
    actor_id=None,
    reason="",
    random=False,
    purchased=False,
    approved=False,
    cp_target_character_id=None,
):
    """發放或扣除角色數值。

    參數：
        guild_id：伺服器 ID
        character_id：角色 ID
        changes：例如 {"流量熱度": 50}
        source：獎勵來源類型
        source_id：唯一獎勵或事件編號

    同一角色的相同 source + source_id
    只能成功執行一次。

    所有數值更新及發放紀錄在同一交易完成。
    """

    gid = str(guild_id)
    cid = int(character_id)

    source_id = str(source_id).strip()

    if not source_id or len(source_id) > 200:
        raise ValueError(
            "source_id 必須是有效的唯一事件編號。"
        )

    if len(reason) > 500:
        raise ValueError(
            "獎勵原因不可超過 500 字。"
        )

    normalized = normalize_changes(
        changes
    )

    check_source(
        source,
        normalized,
        random=random,
        purchased=purchased,
        approved=approved,
        actor_id=actor_id,
        reason=reason,
    )

    has_cp = (
        "CP好感度" in normalized
    )

    if has_cp != (
        cp_target_character_id is not None
    ):
        raise ValueError(
            "CP 好感度必須指定對象，"
            "其他獎勵不可指定 CP 對象。"
        )

    cp_id = (
        int(cp_target_character_id)
        if has_cp
        else 0
    )

    if has_cp and (
        cp_id <= 0
        or cp_id == cid
    ):
        raise ValueError(
            "CP 對象必須是另一個有效角色。"
        )

    payload = {
        "changes": normalized,
        "cp_target_character_id": cp_id,
        "random": bool(random),
        "purchased": bool(purchased),
    }

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    init_db()

    with transaction() as db:
        check_character(
            db,
            gid,
            cid,
        )

        if has_cp:
            check_character(
                db,
                gid,
                cp_id,
            )

        # 防止同一筆獎勵重複發放。
        old = db.execute(
            """
            SELECT payload_json
            FROM rp_stat_reward_log
            WHERE guild_id=?
              AND character_id=?
              AND source=?
              AND source_id=?
            """,
            (
                gid,
                cid,
                source,
                source_id,
            ),
        ).fetchone()

        if old is not None:
            if old["payload_json"] != canonical:
                raise ValueError(
                    "同一獎勵事件 ID 已使用，"
                    "但獎勵內容不同。"
                )

            return {
                "applied": False,
                "duplicate": True,
                "changes": normalized,
            }

        for name, amount in normalized.items():

            # 十三項正式屬性
            if name in ATTRIBUTES:
                current = db.execute(
                    """
                    SELECT value
                    FROM attributes
                    WHERE guild_id=?
                      AND character_id=?
                      AND name=?
                    """,
                    (
                        gid,
                        cid,
                        name,
                    ),
                ).fetchone()

                old_value = (
                    int(current["value"])
                    if current
                    else 0
                )

                if old_value + amount < 0:
                    raise ValueError(
                        f"{name} 扣除後不可低於 0。"
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

                    ON CONFLICT(
                        guild_id,
                        character_id,
                        name
                    )

                    DO UPDATE SET
                        value=value+excluded.value,
                        version=version+1
                    """,
                    (
                        gid,
                        cid,
                        name,
                        amount,
                    ),
                )

            # 粉絲、大眾認知度、CP 好感度
            else:
                target = (
                    cp_id
                    if name == "CP好感度"
                    else 0
                )

                previous = db.execute(
                    """
                    SELECT value
                    FROM rp_reward_metrics
                    WHERE guild_id=?
                      AND character_id=?
                      AND metric=?
                      AND subject_character_id=?
                    """,
                    (
                        gid,
                        cid,
                        name,
                        target,
                    ),
                ).fetchone()

                old_value = (
                    int(previous["value"])
                    if previous
                    else 0
                )

                if old_value + amount < 0:
                    raise ValueError(
                        f"{name} 扣除後不可低於 0。"
                    )

                db.execute(
                    """
                    INSERT INTO rp_reward_metrics (
                        guild_id,
                        character_id,
                        metric,
                        subject_character_id,
                        value
                    )
                    VALUES (?, ?, ?, ?, ?)

                    ON CONFLICT(
                        guild_id,
                        character_id,
                        metric,
                        subject_character_id
                    )

                    DO UPDATE SET
                        value=value+excluded.value
                    """,
                    (
                        gid,
                        cid,
                        name,
                        target,
                        amount,
                    ),
                )

        # 整筆交易成功後記錄來源。
        db.execute(
            """
            INSERT INTO rp_stat_reward_log (
                guild_id,
                character_id,
                source,
                source_id,
                payload_json,
                actor_id,
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                cid,
                source,
                source_id,
                canonical,
                (
                    str(actor_id)
                    if actor_id is not None
                    else None
                ),
                reason.strip(),
            ),
        )

    return {
        "applied": True,
        "duplicate": False,
        "changes": normalized,
    }


# ==========================================
# 取得目前數值
# ==========================================

def get_metric(
    guild_id,
    character_id,
    name,
    *,
    cp_target_character_id=None,
):
    name = str(name).strip().replace(
        "CP 好感度",
        "CP好感度",
    )

    if name not in ATTRIBUTES | METRICS:
        raise ValueError(
            "未知數值名稱。"
        )

    if (
        name == "CP好感度"
        and cp_target_character_id is None
    ):
        raise ValueError(
            "查詢 CP 好感度時必須指定對象。"
        )

    gid = str(guild_id)
    cid = int(character_id)

    with get_connection() as db:
        if name in ATTRIBUTES:
            row = db.execute(
                """
                SELECT value
                FROM attributes
                WHERE guild_id=?
                  AND character_id=?
                  AND name=?
                """,
                (
                    gid,
                    cid,
                    name,
                ),
            ).fetchone()

        else:
            target = (
                int(cp_target_character_id)
                if name == "CP好感度"
                else 0
            )

            row = db.execute(
                """
                SELECT value
                FROM rp_reward_metrics
                WHERE guild_id=?
                  AND character_id=?
                  AND metric=?
                  AND subject_character_id=?
                """,
                (
                    gid,
                    cid,
                    name,
                    target,
                ),
            ).fetchone()

    return (
        int(row["value"])
        if row
        else 0
    )


# ==========================================
# 獎勵歷史紀錄
# ==========================================

def reward_history(
    guild_id,
    character_id,
    *,
    limit=30,
):
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT
                source,
                source_id,
                payload_json,
                actor_id,
                reason,
                created_at
            FROM rp_stat_reward_log
            WHERE guild_id=?
              AND character_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                str(guild_id),
                int(character_id),
                max(
                    1,
                    min(int(limit), 100),
                ),
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]
