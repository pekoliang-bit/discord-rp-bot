
"""娛樂城雙人擲幣對賭（安全簡化版）。

只支援兩名不同玩家角色、等額押注、系統自動隨機決勝。
第一人建立房間並鎖定押金，第二人加入時在同一交易內
扣押金、決勝及把整個獎池發給勝者。

未完成房間可由發起者取消退款。
不支援多方下注或管理員指定勝者。
本檔為一般服務模組，不是 Discord Extension。
"""
from __future__ import annotations

import json
import secrets

from core.database import transaction, get_or_create_account
from core.economy import _payment, MAX_AMOUNT
from modules.casino_service import CasinoError, _eligible_character


def init_pvp_schema() -> None:
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS casino_pvp_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                game_id INTEGER NOT NULL,
                creator_id INTEGER NOT NULL,
                opponent_id INTEGER,
                request_key TEXT NOT NULL,
                join_key TEXT,
                stake INTEGER NOT NULL
                    CHECK(stake BETWEEN 100 AND 100000),
                payment_source TEXT NOT NULL,
                opponent_payment_source TEXT,
                state TEXT NOT NULL DEFAULT 'open'
                    CHECK(state IN ('open','settled','cancelled')),
                winner_id INTEGER,
                create_tx INTEGER,
                join_tx INTEGER,
                payout_tx INTEGER,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                UNIQUE(guild_id, request_key),
                UNIQUE(guild_id, join_key)
            )
            """
        )


def _escrow(conn, gid: str, match_id: int) -> int:
    return get_or_create_account(
        conn,
        gid,
        "system",
        f"casino_pvp:{match_id}",
        "treasury",
    )


def _game(conn, gid: str, code: str):
    row = conn.execute(
        """
        SELECT * FROM casino_games
        WHERE guild_id=? AND code=?
        """,
        (gid, code),
    ).fetchone()

    if (
        not row
        or not row["enabled"]
        or row["kind"] != "pvp"
        or row["funding_mode"] != "direct"
    ):
        raise CasinoError("對賭遊戲未啟用")

    try:
        config = json.loads(row["settings_json"])
    except (ValueError, TypeError) as exc:
        raise CasinoError("對賭設定損壞") from exc

    if (
        not isinstance(config, dict)
        or config.get("rules_verified") is not True
        or config.get("pvp_enabled") is not True
        or config.get("variant") != "coinflip_2p"
    ):
        raise CasinoError("雙人擲幣對賭未完成核准")

    return row


def create_match(
    *,
    guild_id: int | str,
    player_id: int | str,
    character_id: int,
    game_code: str,
    request_key: str,
    payment_source: str = "wallet",
) -> dict:
    init_pvp_schema()
    gid = str(guild_id)

    if (
        type(character_id) is not int
        or character_id <= 0
        or not isinstance(request_key, str)
        or not 1 <= len(request_key) <= 100
    ):
        raise CasinoError("對賭參數不合法")

    if payment_source not in ("wallet", "bank"):
        raise CasinoError("支付來源不合法")

    with transaction() as conn:
        _eligible_character(
            conn, gid, character_id, player_id
        )

        old = conn.execute(
            """
            SELECT * FROM casino_pvp_matches
            WHERE guild_id=? AND request_key=?
            """,
            (gid, request_key),
        ).fetchone()

        if old:
            if (
                old["creator_id"] != character_id
                or old["payment_source"] != payment_source
            ):
                raise CasinoError("操作編號已使用")

            code = conn.execute(
                """
                SELECT code FROM casino_games
                WHERE guild_id=? AND id=?
                """,
                (gid, old["game_id"]),
            ).fetchone()

            if not code or code["code"] != game_code:
                raise CasinoError("操作編號屬於其他遊戲")

            return dict(old)

        game = _game(conn, gid, game_code)
        stake = game["ticket_price"]

        if (
            type(stake) is not int
            or not 100 <= stake <= 100000
            or stake * 2 > MAX_AMOUNT
        ):
            raise CasinoError("對賭押金超過限制")

        cur = conn.execute(
            """
            INSERT INTO casino_pvp_matches(
                guild_id, game_id, creator_id,
                request_key, stake, payment_source
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                gid,
                game["id"],
                character_id,
                request_key,
                stake,
                payment_source,
            ),
        )

        match_id = cur.lastrowid

        source = get_or_create_account(
            conn,
            gid,
            "character",
            str(character_id),
            payment_source,
        )

        debit = _payment(
            conn,
            gid,
            f"casino:pvp:hold:{match_id}:creator",
            source,
            _escrow(conn, gid, match_id),
            stake,
            "對賭發起押金",
        )

        conn.execute(
            """
            UPDATE casino_pvp_matches
            SET create_tx=?
            WHERE id=?
            """,
            (debit.transaction_id, match_id),
        )

        return dict(
            conn.execute(
                "SELECT * FROM casino_pvp_matches WHERE id=?",
                (match_id,),
            ).fetchone()
        )


def join_match(
    *,
    guild_id: int | str,
    match_id: int,
    player_id: int | str,
    character_id: int,
    request_key: str,
    payment_source: str = "wallet",
) -> dict:
    init_pvp_schema()
    gid = str(guild_id)

    if (
        type(match_id) is not int
        or match_id <= 0
        or type(character_id) is not int
        or character_id <= 0
    ):
        raise CasinoError("對賭編號不合法")

    if (
        not isinstance(request_key, str)
        or not 1 <= len(request_key) <= 100
        or payment_source not in ("wallet", "bank")
    ):
        raise CasinoError("加入參數不合法")

    with transaction() as conn:
        _eligible_character(
            conn, gid, character_id, player_id
        )

        row = conn.execute(
            """
            SELECT * FROM casino_pvp_matches
            WHERE guild_id=? AND id=?
            """,
            (gid, match_id),
        ).fetchone()

        if not row:
            raise CasinoError("找不到對賭房間")

        if (
            row["join_key"] == request_key
            and row["opponent_id"] == character_id
            and row["opponent_payment_source"]
            == payment_source
            and row["state"] == "settled"
        ):
            return dict(row)

        if (
            row["state"] != "open"
            or row["join_key"] is not None
        ):
            raise CasinoError("房間已關閉")

        if row["creator_id"] == character_id:
            raise CasinoError("不能與自己的角色對賭")

        creator = conn.execute(
            """
            SELECT owner_id FROM characters
            WHERE guild_id=? AND id=?
            """,
            (gid, row["creator_id"]),
        ).fetchone()

        if (
            not creator
            or str(creator["owner_id"]) == str(player_id)
        ):
            raise CasinoError(
                "不能使用同一玩家的不同角色對賭"
            )

        code = conn.execute(
            """
            SELECT code FROM casino_games
            WHERE guild_id=? AND id=?
            """,
            (gid, row["game_id"]),
        ).fetchone()

        if not code:
            raise CasinoError("原遊戲不存在")

        _game(conn, gid, code["code"])

        # 以房間成立時鎖定的押金為準。
        source = get_or_create_account(
            conn,
            gid,
            "character",
            str(character_id),
            payment_source,
        )

        hold = _escrow(conn, gid, match_id)

        join_tx = _payment(
            conn,
            gid,
            f"casino:pvp:hold:{match_id}:opponent",
            source,
            hold,
            row["stake"],
            "對賭加入押金",
        )

        winner = secrets.choice((
            int(row["creator_id"]),
            character_id,
        ))

        wallet = get_or_create_account(
            conn,
            gid,
            "character",
            str(winner),
            "wallet",
        )

        payout = _payment(
            conn,
            gid,
            f"casino:pvp:payout:{match_id}",
            hold,
            wallet,
            row["stake"] * 2,
            "雙人對賭獎池結算",
        )

        conn.execute(
            """
            UPDATE casino_pvp_matches
            SET opponent_id=?,
                join_key=?,
                opponent_payment_source=?,
                state='settled',
                winner_id=?,
                join_tx=?,
                payout_tx=?,
                closed_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND id=?
              AND state='open'
            """,
            (
                character_id,
                request_key,
                payment_source,
                winner,
                join_tx.transaction_id,
                payout.transaction_id,
                gid,
                match_id,
            ),
        )

        return dict(
            conn.execute(
                "SELECT * FROM casino_pvp_matches WHERE id=?",
                (match_id,),
            ).fetchone()
        )


def cancel_match(
    *,
    guild_id: int | str,
    match_id: int,
    player_id: int | str,
    character_id: int,
) -> dict:
    init_pvp_schema()
    gid = str(guild_id)

    with transaction() as conn:
        _eligible_character(
            conn, gid, character_id, player_id
        )

        row = conn.execute(
            """
            SELECT * FROM casino_pvp_matches
            WHERE guild_id=? AND id=?
            """,
            (gid, match_id),
        ).fetchone()

        if (
            not row
            or row["creator_id"] != character_id
        ):
            raise CasinoError(
                "只有房間建立者可以取消"
            )

        if row["state"] == "cancelled":
            return dict(row)

        if row["state"] != "open":
            raise CasinoError(
                "已有對手加入或房間已結算"
            )

        destination = get_or_create_account(
            conn,
            gid,
            "character",
            str(character_id),
            row["payment_source"],
        )

        _payment(
            conn,
            gid,
            f"casino:pvp:refund:{match_id}",
            _escrow(conn, gid, match_id),
            destination,
            row["stake"],
            "對賭取消退款",
        )

        conn.execute(
            """
            UPDATE casino_pvp_matches
            SET state='cancelled',
                closed_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND id=?
            """,
            (gid, match_id),
        )

        return dict(
            conn.execute(
                "SELECT * FROM casino_pvp_matches WHERE id=?",
                (match_id,),
            ).fetchone()
        )
