
"""娛樂城預設可核准獎表：五款刮刮樂、四個純金幣抽抽樂池。

僅供管理者明確按鈕操作；安裝不開放販售、不改動現有設定。
只在管理者再次核准後切換 rules_verified / enabled。
"""
from __future__ import annotations

import json
from decimal import Decimal

from core.database import transaction
from modules.casino_repository import (
    init_casino_schema,
    get_game,
    upsert_game,
    list_games,
)
from modules.casino_scratch import DEFAULT_SCRATCH, validate_scratch
from modules.casino_lottery import (
    DEFAULT_LOTTERY, _settings as validate_lottery, prize_probabilities,
)
from modules.casino_gacha import (
    DEFAULT_POOLS,
    DEFAULT_WEIGHTS,
    validate_gacha,
)


def _scratch_settings(price: int, jackpot: int, target: str) -> dict:
    # 有獎權重：250000 / 100000 / 19000 / 特等獎。
    # 其餘為銘謝惠顧。
    factor = jackpot // price
    base = Decimal("0.74")
    jackpot_weight = int(
        (
            (Decimal(target) - base)
            * 1_000_000
            / factor
        ).to_integral_value()
    )
    no_prize = (
        1_000_000
        - 250_000
        - 100_000
        - 19_000
        - jackpot_weight
    )
    return {
        "model": "independent",
        "target_rtp": target,
        "rtp_min": str(Decimal(target) - Decimal("0.001")),
        "rtp_max": str(Decimal(target) + Decimal("0.001")),
        "max_single_reward": jackpot,
        "rules_verified": False,
        "daily_purchase_limit": 20,
        "announcements": {"jackpot": True},
        "prizes": [
            {
                "code": "no_prize",
                "name": "銘謝惠顧",
                "coins": 0,
                "weight": no_prize,
            },
            {
                "code": "small",
                "name": "幸運獎",
                "coins": price,
                "weight": 250_000,
            },
            {
                "code": "medium",
                "name": "閃耀獎",
                "coins": price * 3,
                "weight": 100_000,
            },
            {
                "code": "large",
                "name": "大獎",
                "coins": price * 10,
                "weight": 19_000,
            },
            {
                "code": "jackpot",
                "name": "特等獎",
                "coins": jackpot,
                "weight": jackpot_weight,
            },
        ],
    }


def _gacha_settings(price: int, code: str) -> dict:
    # 所有獎勵使用遊戲金幣。
    # 不依賴尚未核准的商城物品 ID。
    multipliers = {
        "N": 0.4,
        "R": 0.8,
        "SR": 1.2,
        "SSR": 3,
        "UR": 6,
    }
    prizes = [
        {
            "code": f"{code}_{rarity.lower()}",
            "name": f"{rarity} 金幣獎勵",
            "rarity": rarity,
            "type": "coins",
            "coins": round(price * multiplier),
            "weight": 1,
        }
        for rarity, multiplier in multipliers.items()
    ]
    return {
        "rules_verified": False,
        "pool_family": code,
        "rarity_weights": dict(DEFAULT_WEIGHTS),
        "prizes": prizes,
        "daily_purchase_limit": 30,
        "max_single_reward": price * 6,
        "guarantee_sr_ten": True,
        "ssr_pity": 50,
        "ur_pity": 100,
    }


# 其他賭場遊戲：在官方核准前只建立停用的獎表範本。
# 支付金額、上限與賠率必須與 casino_wager_service / casino_table_service 一致。
DEFAULT_CASINO_TABLES = (
    ("roulette_standard", "歐式輪盤", "roulette", 1000,
     {"wager_enabled": False, "rules_verified": False, "max_single_reward": 36000,
      "daily_purchase_limit": 20}),
    ("sicbo_standard", "骰寶", "sicbo", 1000,
     {"wager_enabled": False, "rules_verified": False, "max_single_reward": 151000,
      "daily_purchase_limit": 20}),
    ("slots_classic", "經典三軸老虎機", "slots", 1000,
     {"wager_enabled": False, "rules_verified": False, "max_single_reward": 20000,
      "daily_purchase_limit": 20, "symbols": ["🍒", "🍋", "🔔", "⭐", "💎"],
      "weights": [35, 30, 20, 10, 5], "reels": 3,
      "paytable": {"🍒": 3, "🍋": 5, "🔔": 8, "⭐": 12, "💎": 20}}),
    ("blackjack_standard", "簡易二十一點", "blackjack", 1000,
     {"wager_enabled": False, "rules_verified": False, "max_single_reward": 2500,
      "daily_purchase_limit": 20, "table_variant": "auto_stand"}),
    ("bingo_instant", "即開五乘五賓果", "bingo", 1000,
     {"wager_enabled": False, "rules_verified": False, "max_single_reward": 12000,
      "daily_purchase_limit": 20, "table_variant": "instant_5x5", "draw_count": 25,
      "line_multiplier": {"0": 0, "1": 2, "2": 4, "3": 6, "4": 8,
                          "5": 10, "6": 12, "7": 12, "8": 12, "9": 12,
                          "10": 12, "11": 12, "12": 12}}),
)


def _lottery_settings(price: int, pool: int, picks: int, jackpot: int) -> dict:
    # 非累積彩池；不同命中數對應固定獎金。開賣前仍須創建期別。
    return {
        "number_pool": pool, "pick_count": picks,
        "max_single_reward": jackpot, "rules_verified": False,
        "daily_purchase_limit": 100, "rollover": False,
        "announcements": {"jackpot": True},
        "prizes": {str(picks): jackpot, str(picks - 1): max(price * 10, jackpot // 200),
                   str(picks - 2): price * 2},
    }


def _validate_other_game(row: dict) -> None:
    cfg = json.loads(row["settings_json"] or "{}")
    kind = row["kind"]
    if kind == "lottery":
        validate_lottery(row)
        prob = prize_probabilities(number_pool=cfg["number_pool"], pick_count=cfg["pick_count"])
        rtp = sum(prob.get(int(k), 0) * v for k, v in cfg["prizes"].items()) / row["ticket_price"]
        if rtp > 1:
            raise ValueError("彩票獎表預期返還率超過100%，需要重新核准")
    elif kind in {"roulette", "sicbo", "slots", "blackjack", "bingo"}:
        if row["funding_mode"] != "direct" or cfg.get("wager_enabled") is not True:
            raise ValueError("賭場規則或資金來源未核准")
        if type(cfg.get("max_single_reward")) is not int or cfg["max_single_reward"] < 0:
            raise ValueError("遊戲最高返還額不合法")
        if not (100 <= row["ticket_price"] <= 100000):
            raise ValueError("下注金額不合法")
        if kind == "blackjack" and cfg.get("table_variant") != "auto_stand":
            raise ValueError("二十一點模式尚未支援")
        if kind == "bingo" and (cfg.get("table_variant") != "instant_5x5" or
            cfg.get("draw_count", 0) not in range(1, 76) or
            not isinstance(cfg.get("line_multiplier"), dict)):
            raise ValueError("賓果規則尚未完整設定")
        if kind == "slots":
            from modules.casino_games import slot_spin, slot_result
            symbols, weights = cfg["symbols"], cfg["weights"]
            if set(cfg["paytable"]) - set(symbols):
                raise ValueError("老虎機賠率對應到未定義圖案")
            spin = [symbols[0]] * cfg["reels"]
            slot_result(spun=spin, bet=row["ticket_price"], paytable=cfg["paytable"])
            if max(cfg["paytable"].values()) * row["ticket_price"] > cfg["max_single_reward"]:
                raise ValueError("老虎機獎金超過單次上限")
    else:
        raise ValueError("不支援的遊戲類型")


def install_all(
    guild_id: int | str,
    *,
    actor_id: int | str,
) -> dict:
    """只新增不存在的九款獎池，並保持關閉。"""
    init_casino_schema()
    created = []
    skipped = []

    for code, name, price, jackpot, target in DEFAULT_SCRATCH:
        if get_game(guild_id, code):
            skipped.append(code)
            continue

        upsert_game(
            guild_id=guild_id,
            code=code,
            name=name,
            kind="scratch",
            ticket_price=price,
            settings=_scratch_settings(price, jackpot, target),
            enabled=False,
            actor_id=actor_id,
        )
        created.append(code)

    for code, name, price in DEFAULT_POOLS:
        if get_game(guild_id, code):
            skipped.append(code)
            continue

        upsert_game(
            guild_id=guild_id,
            code=code,
            name=name,
            kind="gacha",
            ticket_price=price,
            settings=_gacha_settings(price, code),
            enabled=False,
            actor_id=actor_id,
        )
        created.append(code)

    for code, name, price, pool, picks, jackpot in DEFAULT_LOTTERY:
        if get_game(guild_id, code):
            skipped.append(code)
            continue
        upsert_game(guild_id=guild_id, code=code, name=name, kind="lottery",
                    ticket_price=price, settings=_lottery_settings(price, pool, picks, jackpot),
                    enabled=False, actor_id=actor_id)
        created.append(code)

    for code, name, kind, price, settings in DEFAULT_CASINO_TABLES:
        if get_game(guild_id, code):
            skipped.append(code)
            continue
        upsert_game(guild_id=guild_id, code=code, name=name, kind=kind,
                    ticket_price=price, settings=settings, enabled=False, actor_id=actor_id)
        created.append(code)

    return {"created": created, "existing_unchanged": skipped}


def _validate_ready(row: dict) -> None:
    staged = dict(row)
    settings = json.loads(row["settings_json"])
    settings["rules_verified"] = True
    if row["kind"] in {"roulette", "sicbo", "slots", "blackjack", "bingo"}:
        settings["wager_enabled"] = True

    staged["settings_json"] = json.dumps(
        settings,
        ensure_ascii=False,
    )

    if row["kind"] == "scratch":
        validate_scratch(staged)
    elif row["kind"] == "gacha":
        validate_gacha(staged)
    else:
        _validate_other_game(staged)


def toggle_game(
    guild_id: int | str,
    code: str,
    *,
    actor_id: int | str,
    enabled: bool,
) -> dict:
    """驗證獎池並在單一資料庫交易中更新開關。"""
    init_casino_schema()
    gid = str(guild_id)

    with transaction() as db:
        row = db.execute(
            """
            SELECT *
            FROM casino_games
            WHERE guild_id=? AND code=?
            """,
            (gid, code),
        ).fetchone()

        if not row:
            raise ValueError("找不到遊戲，請先安裝預設獎表")

        row = dict(row)

        if row["kind"] == "pvp":
            raise ValueError("玩家對賭尚未完成對手加入及退款流程，禁止開放")

        if enabled:
            _validate_ready(row)

        settings = json.loads(row["settings_json"])

        if enabled:
            settings["rules_verified"] = True
            if row["kind"] in {"roulette", "sicbo", "slots", "blackjack", "bingo"}:
                settings["wager_enabled"] = True

        db.execute(
            """
            UPDATE casino_games
            SET enabled=?,
                settings_json=?,
                config_version=config_version+1
            WHERE guild_id=? AND code=?
            """,
            (
                int(enabled),
                json.dumps(
                    settings,
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                gid,
                code,
            ),
        )

        db.execute(
            """
            INSERT INTO casino_audit(
                guild_id,
                actor_id,
                action,
                ref_key,
                details_json
            ) VALUES(?,?,?,?,?)
            """,
            (
                gid,
                str(actor_id),
                (
                    "admin_game_enable"
                    if enabled
                    else "admin_game_disable"
                ),
                code,
                json.dumps(
                    {"enabled": bool(enabled)},
                    ensure_ascii=False,
                ),
            ),
        )

        return dict(
            db.execute(
                """
                SELECT *
                FROM casino_games
                WHERE guild_id=? AND code=?
                """,
                (gid, code),
            ).fetchone()
        )


def manageable_games(guild_id: int | str) -> list[dict]:
    init_casino_schema()

    return [
        row
        for row in list_games(
            guild_id,
            enabled_only=False,
        )
        if row["kind"] != "pvp"
    ]
