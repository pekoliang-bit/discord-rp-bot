
"""Discord RP 陣營退出與轉換核心。

路徑：modules/faction_membership.py

功能：
- 每個角色獨立退出或轉換陣營
- 依原陣營設定收取違約金
- 違約金轉入原陣營金庫
- 違約金不產生個人積分
- 交易與角色歸屬更新使用同一資料庫交易
- 防止同一請求重複扣款

本檔案不註冊 Discord 指令。

注意：
- 合約到期免違約金及被開除流程尚未接入
- 違約金由授權管理介面設定
- 未設定違約金的陣營不可執行主動退出
"""

from __future__ import annotations

from contextlib import nullcontext

from core.database import (
    transaction,
    get_or_create_account,
)
from core.economy import _payment
from modules import faction_core


MAX_PENALTY = 10**15


def initialize():
    """建立陣營退出與轉換所需資料表。"""

    faction_core.initialize()

    with transaction() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            faction_exit_rules (
                guild_id TEXT NOT NULL,
                faction_id INTEGER NOT NULL,
                penalty INTEGER
                    CHECK (
                        penalty IS NULL
                        OR penalty >= 0
                    ),
                PRIMARY KEY (
                    guild_id,
                    faction_id
                )
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS
            faction_membership_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                request_key TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                old_faction_id INTEGER NOT NULL,
                new_faction_id INTEGER,
                event_type TEXT NOT NULL,
                penalty INTEGER NOT NULL DEFAULT 0,
                economy_transaction_id INTEGER,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (
                    guild_id,
                    request_key
                )
            )
        """)


def configure_penalty(
    guild_id,
    faction_id,
    penalty,
):
    """設定某陣營主動退出時的違約金。

    只能由已完成權限驗證的管理模組呼叫。
    不可直接提供給一般玩家。
    """

    if (
        type(penalty) is not int
        or not 0 <= penalty <= MAX_PENALTY
    ):
        raise ValueError("違約金金額不合法。")

    gid = faction_core.ensure_guild(guild_id)
    fid = int(faction_id)

    initialize()

    with transaction() as db:

        faction = db.execute(
            """
            SELECT 1
            FROM faction_groups
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, fid),
        ).fetchone()

        if faction is None:
            raise ValueError("陣營不存在。")

        db.execute(
            """
            INSERT INTO faction_exit_rules (
                guild_id,
                faction_id,
                penalty
            )
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, faction_id)
            DO UPDATE SET penalty = excluded.penalty
            """,
            (gid, fid, penalty),
        )


def get_penalty(guild_id, faction_id):
    """查詢目前設定的違約金。"""

    gid = faction_core.ensure_guild(guild_id)

    initialize()

    with transaction() as db:

        row = db.execute(
            """
            SELECT penalty
            FROM faction_exit_rules
            WHERE guild_id = ?
              AND faction_id = ?
            """,
            (gid, int(faction_id)),
        ).fetchone()

        if row is None or row["penalty"] is None:
            return None

        return int(row["penalty"])


def change_membership(
    guild_id,
    character_id,
    user_id,
    request_id,
    new_faction_id=None,
    expected_penalty=None,
    db=None,
):
    """角色主動退出或轉換陣營。

    new_faction_id=None：退出陣營
    new_faction_id=整數：轉換至指定陣營

    expected_penalty 必須是玩家在確認畫面
    已經看過並同意的違約金額。

    如果確認後管理員修改了金額，
    系統會拒絕交易，要求重新確認。
    """

    gid = faction_core.ensure_guild(guild_id) if db is None else str(guild_id)
    if db is None:
        initialize()

    cid = int(character_id)

    request_id = str(request_id).strip()

    if not request_id or len(request_id) > 100:
        raise ValueError("請求編號不合法。")

    key = (
        f"faction:membership:{gid}:{request_id}"
    )

    if len(key) > 200:
        raise ValueError("請求編號過長。")

    target_id = (
        int(new_faction_id)
        if new_faction_id is not None
        else None
    )

    with (transaction() if db is None else nullcontext(db)) as db:

        # 重複請求直接回傳既有結果。
        old_event = db.execute(
            """
            SELECT *
            FROM faction_membership_events
            WHERE guild_id = ?
              AND request_key = ?
            """,
            (gid, key),
        ).fetchone()

        if old_event is not None:

            if (
                int(old_event["character_id"]) != cid
                or old_event["new_faction_id"] != target_id
            ):
                raise ValueError(
                    "請求編號已被其他操作使用。"
                )

            return {
                "duplicated": True,
                "old_faction_id":
                    old_event["old_faction_id"],
                "new_faction_id":
                    old_event["new_faction_id"],
                "penalty": old_event["penalty"],
                "points": 0,
            }

        # 驗證角色擁有者。
        character = db.execute(
            """
            SELECT owner_id, status, character_type
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, cid),
        ).fetchone()

        if (
            character is None
            or str(character["owner_id"]) != str(user_id)
            or character["status"] != "active"
            or character["character_type"] != "player"
        ):
            raise ValueError(
                "只能操作自己已啟用的原創角色。"
            )

        # 查詢原陣營。
        member = db.execute(
            """
            SELECT faction_id
            FROM faction_members
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (gid, cid),
        ).fetchone()

        if member is None:
            raise ValueError("角色尚未加入陣營。")

        old_faction_id = int(
            member["faction_id"]
        )

        if target_id == old_faction_id:
            raise ValueError(
                "新陣營不能與原陣營相同。"
            )

        # 驗證新陣營。
        if target_id is not None:

            target = db.execute(
                """
                SELECT 1
                FROM faction_groups
                WHERE guild_id = ?
                  AND id = ?
                """,
                (gid, target_id),
            ).fetchone()

            if target is None:
                raise ValueError("目標陣營不存在。")

        # 讀取原陣營違約金設定。
        rule = db.execute(
            """
            SELECT penalty
            FROM faction_exit_rules
            WHERE guild_id = ?
              AND faction_id = ?
            """,
            (gid, old_faction_id),
        ).fetchone()

        if rule is None or rule["penalty"] is None:
            raise ValueError(
                "原陣營尚未設定退出規則，"
                "目前不能直接退出或轉換。"
            )

        penalty = int(rule["penalty"])

        # 玩家必須事先確認確切金額。
        if (
            type(expected_penalty) is not int
            or expected_penalty != penalty
        ):
            raise ValueError(
                "違約金尚未確認或金額已變更，"
                "請重新查看並確認。"
            )

        payment_id = None

        # 有違約金才需要轉帳。
        if penalty > 0:

            sender = get_or_create_account(
                db,
                gid,
                "character",
                str(cid),
                "wallet",
            )

            receiver = get_or_create_account(
                db,
                gid,
                "organization",
                f"faction:{old_faction_id}",
                "treasury",
            )

            payment = _payment(
                db,
                gid,
                key,
                sender,
                receiver,
                penalty,
                "陣營退出／轉換違約金",
            )

            if payment.duplicated:
                raise ValueError(
                    "發現已存在的付款但缺少"
                    "陣營操作紀錄，已停止處理。"
                )

            payment_id = payment.transaction_id

            # 原陣營經費池增加。
            updated = db.execute(
                """
                UPDATE faction_groups
                SET pool = pool + ?
                WHERE guild_id = ?
                  AND id = ?
                """,
                (
                    penalty,
                    gid,
                    old_faction_id,
                ),
            )

            if updated.rowcount != 1:
                raise ValueError(
                    "原陣營不存在，操作已取消。"
                )

            period = faction_core._roll(
                db,
                gid,
            )

            # 違約金不計算任何角色積分。
            db.execute(
                """
                INSERT INTO faction_ledger (
                    guild_id,
                    faction_id,
                    character_id,
                    source,
                    amount,
                    points,
                    period,
                    memo
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gid,
                    old_faction_id,
                    cid,
                    "departure_penalty",
                    penalty,
                    0,
                    period,
                    key,
                ),
            )

        # 更新角色陣營歸屬。
        if target_id is None:

            db.execute(
                """
                DELETE FROM faction_members
                WHERE guild_id = ?
                  AND character_id = ?
                  AND faction_id = ?
                """,
                (
                    gid,
                    cid,
                    old_faction_id,
                ),
            )

            event_type = "leave"

        else:

            db.execute(
                """
                UPDATE faction_members
                SET faction_id = ?,
                    joined_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                  AND faction_id = ?
                """,
                (
                    target_id,
                    gid,
                    cid,
                    old_faction_id,
                ),
            )

            event_type = "transfer"

        # 保存操作歷史。
        db.execute(
            """
            INSERT INTO faction_membership_events (
                guild_id,
                request_key,
                character_id,
                old_faction_id,
                new_faction_id,
                event_type,
                penalty,
                economy_transaction_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                key,
                cid,
                old_faction_id,
                target_id,
                event_type,
                penalty,
                payment_id,
            ),
        )

        return {
            "duplicated": False,
            "old_faction_id": old_faction_id,
            "new_faction_id": target_id,
            "penalty": penalty,
            "points": 0,
        }
