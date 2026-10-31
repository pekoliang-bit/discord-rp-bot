
"""娛樂城即時二十一點與賓果，整局原子扣款/開獎/發獎。

二十一點目前是「自動停牌」簡易桌，不是完整互動牌桌。
賓果是即開式 5x5 單人遊戲，不是多人同場賓果。
只有事先核准的 direct 遊戲可以運作；此檔不是 Discord Cog。
"""
from __future__ import annotations

import json
import secrets

from core.database import transaction, get_or_create_account
from core.economy import _payment, MAX_AMOUNT
from modules.casino_service import CasinoError, _eligible_character
from modules.casino_games import (
    new_deck,
    blackjack_score,
    blackjack_result,
    bingo_draw,
    bingo_lines,
)


def play_table(
    *,
    guild_id: int | str,
    player_id: int | str,
    character_id: int,
    game_code: str,
    request_key: str,
    payment_source: str = "wallet",
) -> dict:
    gid = str(guild_id)

    if type(character_id) is not int or character_id <= 0:
        raise CasinoError("角色編號不合法")

    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 100:
        raise CasinoError("操作編號不合法")

    if payment_source not in ("wallet", "bank"):
        raise CasinoError("只能使用角色錢包或銀行活期存款")

    with transaction() as conn:
        _eligible_character(conn, gid, character_id, player_id)

        previous = conn.execute(
            """
            SELECT * FROM casino_rounds
            WHERE guild_id=? AND request_key=?
            """,
            (gid, request_key),
        ).fetchone()

        if previous:
            game_name = conn.execute(
                """
                SELECT code FROM casino_games
                WHERE guild_id=? AND id=?
                """,
                (gid, previous["game_id"]),
            ).fetchone()

            if (
                previous["character_id"] != character_id
                or previous["payment_source"] != payment_source
                or not game_name
                or game_name["code"] != game_code
            ):
                raise CasinoError("操作編號已用於其他交易")

            if previous["status"] != "settled":
                raise CasinoError("先前對局尚未完成")

            return dict(previous)

        game = conn.execute(
            """
            SELECT * FROM casino_games
            WHERE guild_id=? AND code=?
            """,
            (gid, game_code),
        ).fetchone()

        if (
            not game
            or not game["enabled"]
            or game["kind"] not in ("blackjack", "bingo")
            or game["funding_mode"] != "direct"
        ):
            raise CasinoError("此遊戲尚未核准啟用")

        try:
            rules = json.loads(game["settings_json"])
        except (TypeError, ValueError) as exc:
            raise CasinoError("遊戲設定損壞") from exc

        if (
            not isinstance(rules, dict)
            or rules.get("rules_verified") is not True
            or rules.get("wager_enabled") is not True
        ):
            raise CasinoError("遊戲尚未完成規則審核")

        stake = game["ticket_price"]

        if type(stake) is not int or not 100 <= stake <= 100_000:
            raise CasinoError("下注金額超出範圍")

        limit = rules.get("daily_purchase_limit", 20)
        ceiling = rules.get("max_single_reward")

        if (
            type(limit) is not int
            or not 1 <= limit <= 10000
            or type(ceiling) is not int
            or ceiling < 0
        ):
            raise CasinoError("遊戲限額設定錯誤")

        count = conn.execute(
            """
            SELECT count(*) FROM casino_rounds
            WHERE guild_id=?
              AND character_id=?
              AND game_id=?
              AND status!='cancelled'
              AND date(created_at)=date('now')
            """,
            (gid, character_id, game["id"]),
        ).fetchone()[0]

        if count >= limit:
            raise CasinoError("當日遊玩次數已達上限")

        if game["kind"] == "blackjack":
            if rules.get("table_variant") != "auto_stand":
                raise CasinoError("只支援已核准的自動停牌模式")

            deck = new_deck()
            player = [deck.pop(), deck.pop()]
            dealer = [deck.pop(), deck.pop()]

            # 莊家補牌至 17 點；玩家在簡易模式下停牌。
            while blackjack_score(dealer) < 17:
                dealer.append(deck.pop())

            outcome = blackjack_result(
                player,
                dealer,
                stake,
            )

            outcome.update({
                "player_cards": player,
                "dealer_cards": dealer,
                "variant": "auto_stand",
            })

        else:
            if rules.get("table_variant") != "instant_5x5":
                raise CasinoError("只支援已核准的即開賓果")

            draws = rules.get("draw_count", 25)
            paytable = rules.get("line_multiplier")

            if (
                type(draws) is not int
                or not 1 <= draws <= 75
                or not isinstance(paytable, dict)
            ):
                raise CasinoError("賓果規則設定錯誤")

            if any(
                not str(k).isdigit()
                or type(v) is not int
                or not 0 <= v <= 100000
                for k, v in paytable.items()
            ):
                raise CasinoError("賓果賠率表不合法")

            card_numbers = secrets.SystemRandom().sample(
                range(1, 76), 25
            )
            card = [
                card_numbers[i:i + 5]
                for i in range(0, 25, 5)
            ]

            drawn = bingo_draw(
                pool=75,
                count=draws,
            )

            lines = bingo_lines(card, drawn)

            payout = stake * paytable.get(str(lines), 0)

            outcome = {
                "card": card,
                "drawn": drawn,
                "lines": lines,
                "payout": payout,
                "variant": "instant_5x5",
            }

        payout = outcome["payout"]

        if (
            type(payout) is not int
            or payout < 0
            or payout > ceiling
            or payout > MAX_AMOUNT
        ):
            raise CasinoError("本局獎金超過預先核准上限")

        payer = get_or_create_account(
            conn, gid, "character",
            str(character_id), payment_source
        )
        receiver = get_or_create_account(
            conn, gid, "system",
            "casino_receipts", "treasury"
        )

        debit = _payment(
            conn, gid,
            f"casino:table:buy:{request_key}",
            payer, receiver, stake,
            f"娛樂城 {game_code} 下注",
        )

        reward_tx = None

        if payout:
            issuer = get_or_create_account(
                conn, gid, "system",
                "issuance", "treasury"
            )
            wallet = get_or_create_account(
                conn, gid, "character",
                str(character_id), "wallet"
            )

            reward_tx = _payment(
                conn, gid,
                f"casino:table:reward:{request_key}",
                issuer, wallet, payout,
                f"娛樂城 {game_code} 獎金",
            ).transaction_id

        cur = conn.execute(
            """
            INSERT INTO casino_rounds(
                guild_id, game_id, character_id,
                request_key, game_kind, config_version,
                ticket_price, payment_source, funding_mode,
                status, outcome_json, reward_coins,
                pay_tx_id, reward_tx_id, settled_at
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,
                'settled',?,?,?,?,CURRENT_TIMESTAMP
            )
            """,
            (
                gid,
                game["id"],
                character_id,
                request_key,
                game["kind"],
                game["config_version"],
                stake,
                payment_source,
                "direct",
                json.dumps(
                    outcome,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                payout,
                debit.transaction_id,
                reward_tx,
            ),
        )

        if payout:
            conn.execute(
                """
                INSERT INTO casino_rewards(
                    guild_id, round_id, reward_key,
                    reward_type, quantity, state, delivered_at
                )
                VALUES(
                    ?,?, 'coins','coins',?,
                    'delivered',CURRENT_TIMESTAMP
                )
                """,
                (gid, cur.lastrowid, payout),
            )

        return dict(
            conn.execute(
                "SELECT * FROM casino_rounds WHERE id=?",
                (cur.lastrowid,),
            ).fetchone()
        )
