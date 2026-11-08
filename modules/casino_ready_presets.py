
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

    return {
        "created": created,
        "existing_unchanged": skipped,
    }


def _validate_ready(row: dict) -> None:
    staged = dict(row)
    settings = json.loads(row["settings_json"])
    settings["rules_verified"] = True

    staged["settings_json"] = json.dumps(
        settings,
        ensure_ascii=False,
    )

    if row["kind"] == "scratch":
        validate_scratch(staged)
    elif row["kind"] == "gacha":
        validate_gacha(staged)
    else:
        raise ValueError(
            "這個管理面板目前只處理刮刮樂及抽抽樂"
        )


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

        if row["kind"] not in ("scratch", "gacha"):
            raise ValueError("只能開關刮刮樂與抽抽樂")

        if enabled:
            _validate_ready(row)

        settings = json.loads(row["settings_json"])

        if enabled:
            settings["rules_verified"] = True

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
        if row["kind"] in ("scratch", "gacha")
    ]
