
"""Discord RP 陣營供奉系統。

檔案位置：modules/faction_donations.py

功能：
- 驗證角色擁有者與陣營歸屬
- 驗證供奉金額
- 防止同一請求重複扣款
- 透過既有經濟系統扣除角色錢包
- 增加陣營共用經費
- 記錄個人貢獻與積分

不建立獨立斜線指令。
所有操作由 /陣營 介面呼叫。
"""

from __future__ import annotations

from core.database import (
    transaction,
    get_or_create_account,
)

from core.economy import (
    _payment,
    EconomyError,
)

from modules import faction_core


MAX_DONATION = 10**15


def donate(
    guild_id,
    character_id,
    user_id,
    amount,
    request_id,
):
    """執行角色供奉。

    同一 request_id 只能成功處理一次。

    注意：
    此函式依賴既有經濟系統的交易介面。
    部署前必須確認 core.economy._payment
    及 core.database.get_or_create_account
    的實際函式簽名相容。
    """

    gid = faction_core.ensure_guild(guild_id)

    if type(amount) is not int:
        raise ValueError(
            "供奉金額必須為整數。"
        )

    if not 1 <= amount <= MAX_DONATION:
        raise ValueError(
            "供奉金額必須介於 1 至 10^15 金幣。"
        )

    if not str(request_id).strip():
        raise ValueError(
            "交易編號不可為空。"
        )

    key = (
        f"faction:offering:"
        f"{gid}:{request_id}"
    )

    with transaction() as db:

        # 1. 驗證角色

        character = db.execute(
            """
            SELECT
                owner_id,
                status,
                character_type
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                gid,
                int(character_id),
            ),
        ).fetchone()

        if (
            not character
            or str(character["owner_id"]) != str(user_id)
            or character["status"] != "active"
            or character["character_type"] != "player"
        ):
            raise ValueError(
                "只能以自己已啟用的原創角色供奉。"
            )

        # 2. 取得角色所屬陣營

        member = db.execute(
            """
            SELECT faction_id
            FROM faction_members
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                gid,
                int(character_id),
            ),
        ).fetchone()

        if not member:
            raise ValueError(
                "角色尚未加入陣營。"
            )

        faction_id = int(member["faction_id"])

        # 3. 檢查是否已經完成相同交易

        old = db.execute(
            """
            SELECT
                faction_id,
                character_id,
                amount,
                points
            FROM faction_ledger
            WHERE guild_id = ?
              AND memo = ?
              AND source = 'offering'
            """,
            (
                gid,
                key,
            ),
        ).fetchone()

        if old:

            if (
                int(old["faction_id"]) != faction_id
                or int(old["character_id"])
                    != int(character_id)
                or int(old["amount"]) != amount
            ):
                raise ValueError(
                    "交易編號已被其他供奉占用。"
                )

            return {
                "duplicated": True,
                "points": int(old["points"]),
                "amount": amount,
            }

        # 4. 取得角色錢包帳戶

        sender = get_or_create_account(
            db,
            gid,
            "character",
            str(character_id),
            "wallet",
        )

        # 5. 取得陣營金庫帳戶

        receiver = get_or_create_account(
            db,
            gid,
            "organization",
            f"faction:{faction_id}",
            "treasury",
        )

        # 6. 執行錢包轉帳
        # 由既有經濟系統檢查餘額。

        payment = _payment(
            db,
            gid,
            key,
            sender,
            receiver,
            amount,
            "陣營供奉",
        )

        if payment.duplicated:
            raise EconomyError(
                "付款已存在但缺少陣營流水，"
                "請聯絡管理員核對。"
            )

        # 7. 取得當前結算期

        period = faction_core._roll(
            db,
            gid,
        )

        # 8. 計算角色本期既有有效貢獻

        previous = db.execute(
            """
            SELECT
                COALESCE(SUM(amount), 0) AS total
            FROM faction_ledger
            WHERE guild_id = ?
              AND faction_id = ?
              AND character_id = ?
              AND period = ?
              AND source IN (
                  'offering',
                  'commission',
                  'personal_award',
                  'sponsor_personal'
              )
            """,
            (
                gid,
                faction_id,
                int(character_id),
                period,
            ),
        ).fetchone()

        before = int(previous["total"])

        # 每累計滿 100 金幣獲得 1 積分
        points = (
            (before + amount) // 100
            - before // 100
        )

        # 9. 增加陣營共用經費

        db.execute(
            """
            UPDATE faction_groups
            SET pool = pool + ?
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                amount,
                gid,
                faction_id,
            ),
        )

        # 10. 記錄供奉流水

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
                faction_id,
                int(character_id),
                "offering",
                amount,
                points,
                period,
                key,
            ),
        )

        return {
            "duplicated": False,
            "points": points,
            "amount": amount,
        }
