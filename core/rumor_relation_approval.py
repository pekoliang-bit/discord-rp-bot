
"""Discord RP 傳聞網友關係人工核准寫入服務。

嚴格要求：
上層必須完成管理員身分及伺服器權限核驗，
才能呼叫本服務。

本服務沒有 Discord 身分驗證能力，
不得直接暴露為玩家指令。

只修改虛構網友熟悉度與立場，
不修改角色粉絲、金錢或影響指數。

包含：
- 資料庫交易
- 原數值比對
- 同案件防重複寫入
- 審核操作紀錄
"""

from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from core.internet_user_profiles import (
    initialize as initialize_profiles,
)


RELATIONS = frozenset({
    "neutral",
    "fan",
    "superfan",
    "fansite",
    "solo_fan",
    "anti",
    "cp_fan",
})


DDL = """
CREATE TABLE IF NOT EXISTS
internet_rumor_relation_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    guild_id TEXT NOT NULL,
    rumor_id INTEGER NOT NULL,
    virtual_user_id INTEGER NOT NULL,
    character_id INTEGER NOT NULL,

    reviewer_id TEXT NOT NULL,
    approval_reference TEXT NOT NULL,

    old_relation TEXT NOT NULL,
    new_relation TEXT NOT NULL,

    old_familiarity INTEGER NOT NULL,
    new_familiarity INTEGER NOT NULL,

    reason TEXT NOT NULL,

    created_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(
        guild_id,
        rumor_id,
        virtual_user_id,
        character_id
    )
)
"""


@dataclass(frozen=True)
class ApprovedRelationChange:
    guild_id: str
    rumor_id: int
    character_id: int
    virtual_user_id: int

    reviewer_id: str
    approval_reference: str

    old_relation: str
    new_relation: str

    old_familiarity: int
    new_familiarity: int

    reason: str


def _positive(
    value: int,
    name: str,
) -> None:
    if (
        type(value) is not int
        or value <= 0
    ):
        raise ValueError(
            f"{name} 必須是正整數"
        )


def _validate(
    change: ApprovedRelationChange,
) -> None:
    if not str(
        change.guild_id
    ).strip():
        raise ValueError(
            "guild_id 不可空白"
        )

    for key in (
        "rumor_id",
        "character_id",
        "virtual_user_id",
    ):
        _positive(
            getattr(change, key),
            key,
        )

    if (
        not str(change.reviewer_id).strip()
        or not str(
            change.approval_reference
        ).strip()
    ):
        raise ValueError(
            "缺少審核員或核准紀錄編號"
        )

    if not (
        3
        <= len(
            str(change.reason).strip()
        )
        <= 500
    ):
        raise ValueError(
            "須提供 3～500 字核准理由"
        )

    if (
        change.old_relation not in RELATIONS
        or change.new_relation not in RELATIONS
    ):
        raise ValueError(
            "不支援的網友關係"
        )

    for label in (
        "old_familiarity",
        "new_familiarity",
    ):
        value = getattr(
            change,
            label,
        )

        if (
            type(value) is not int
            or not 0 <= value <= 100
        ):
            raise ValueError(
                f"{label} 必須為 0～100"
            )


def apply_approved_relation_change(
    change: ApprovedRelationChange,
    *,
    reviewer_authorized: bool = False,
    case_reviewed: bool = False,
    disclosure_authorized: bool = False,
) -> bool:
    """執行經核准的固定網友關係變更。

    回傳：
    True  = 首次成功套用變更
    False = 同案件已套用完全相同的變更

    三個授權參數都必須由可信的上層服務提供。

    本函式不是管理員身分驗證端點，
    不可直接讓玩家控制這三個參數。
    """

    _validate(change)

    if not (
        reviewer_authorized
        and case_reviewed
        and disclosure_authorized
    ):
        raise PermissionError(
            "未通過身分、案件或公開授權檢查"
        )

    gid = str(
        change.guild_id
    ).strip()

    db = get_connection()

    try:
        db.execute(
            "BEGIN IMMEDIATE"
        )

        initialize_profiles(db)

        db.execute(DDL)

        current = db.execute(
            """
            SELECT
                r.relation_type,
                r.familiarity

            FROM internet_virtual_user_relations r

            JOIN internet_virtual_users u
              ON u.id = r.virtual_user_id
             AND u.guild_id = r.guild_id

            WHERE r.guild_id = ?
              AND r.character_id = ?
              AND r.virtual_user_id = ?
              AND u.active = 1
            """,
            (
                gid,
                change.character_id,
                change.virtual_user_id,
            ),
        ).fetchone()

        if current is None:
            raise LookupError(
                "找不到同伺服器的有效網友關係"
            )

        previous = db.execute(
            """
            SELECT *
            FROM internet_rumor_relation_audit

            WHERE guild_id = ?
              AND rumor_id = ?
              AND virtual_user_id = ?
              AND character_id = ?
            """,
            (
                gid,
                change.rumor_id,
                change.virtual_user_id,
                change.character_id,
            ),
        ).fetchone()

        if previous is not None:
            # 重試只承認原本核准的內容。
            # 不可在相同案件下偷偷更換版本。

            same = (
                previous["reviewer_id"]
                == str(change.reviewer_id)

                and previous["approval_reference"]
                == str(change.approval_reference)

                and previous["old_relation"]
                == change.old_relation

                and previous["new_relation"]
                == change.new_relation

                and previous["old_familiarity"]
                == change.old_familiarity

                and previous["new_familiarity"]
                == change.new_familiarity

                and previous["reason"]
                == change.reason
            )

            if not same:
                raise ValueError(
                    "同一案件已存在不同核准結果，"
                    "不可覆蓋"
                )

            db.commit()
            return False

        if (
            current["relation_type"]
            != change.old_relation

            or current["familiarity"]
            != change.old_familiarity
        ):
            raise ValueError(
                "網友關係已變動，"
                "請重新產生並審核提案"
            )

        if (
            change.new_relation
            == change.old_relation

            and change.new_familiarity
            == change.old_familiarity
        ):
            raise ValueError(
                "沒有實際變化，"
                "不需要建立核准紀錄"
            )

        update = db.execute(
            """
            UPDATE internet_virtual_user_relations

            SET
                relation_type = ?,
                familiarity = ?

            WHERE guild_id = ?
              AND character_id = ?
              AND virtual_user_id = ?

              AND relation_type = ?
              AND familiarity = ?
            """,
            (
                change.new_relation,
                change.new_familiarity,

                gid,
                change.character_id,
                change.virtual_user_id,

                change.old_relation,
                change.old_familiarity,
            ),
        )

        if update.rowcount != 1:
            raise RuntimeError(
                "網友關係更新未成功"
            )

        db.execute(
            """
            INSERT INTO internet_rumor_relation_audit (
                guild_id,
                rumor_id,
                virtual_user_id,
                character_id,

                reviewer_id,
                approval_reference,

                old_relation,
                new_relation,

                old_familiarity,
                new_familiarity,

                reason
            )

            VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                gid,
                change.rumor_id,
                change.virtual_user_id,
                change.character_id,

                str(change.reviewer_id),
                str(change.approval_reference),

                change.old_relation,
                change.new_relation,

                change.old_familiarity,
                change.new_familiarity,

                change.reason,
            ),
        )

        db.commit()
        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
