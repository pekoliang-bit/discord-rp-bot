
"""正式付費公關入口；設定缺失時拒絕扣費，重試以已存效果為準。"""
from __future__ import annotations

from dataclasses import dataclass
from core.database import get_connection
from core.rumor_pr_action_service import charge_verified_pr_action
from core.rumor_pr_effects import create_effect_writer


@dataclass(frozen=True)
class PaidPRResult:
    action_id: int
    cost: int
    resource: str
    repeat_count: int
    multiplier: float
    effective_score: int
    duplicate: bool


def initialize_pr_strategy_config(db):
    """僅建表，不植入未經使用者核准的策略分數。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_rumor_pr_strategy_config (
            guild_id TEXT NOT NULL,
            strategy TEXT NOT NULL,
            base_score INTEGER NOT NULL CHECK(base_score BETWEEN 0 AND 10000),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
            PRIMARY KEY(guild_id, strategy)
        )
    """)


def _load_original(db, gid, request_key):
    table = db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='rp_rumor_pr_charges'"
    ).fetchone()
    if table is None:
        return None
    return db.execute(
        "SELECT id,rumor_id,character_id,strategy,resource,cost,"
        "repeat_count,effectiveness FROM rp_rumor_pr_charges "
        "WHERE guild_id=? AND request_key=?",
        (gid, request_key),
    ).fetchone()


def _verify_effect(db, gid, action_id, rumor_id, character_id, strategy, multiplier):
    row = db.execute(
        "SELECT rumor_id,character_id,strategy,base_score,multiplier,"
        "effective_score FROM rp_rumor_pr_effects "
        "WHERE guild_id=? AND action_id=?",
        (gid, action_id),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "原公關扣費缺少效果紀錄；禁止再次扣費，請管理員調查"
        )
    if (
        int(row["rumor_id"]) != rumor_id
        or int(row["character_id"]) != character_id
        or row["strategy"] != strategy
        or abs(float(row["multiplier"]) - multiplier) > 1e-9
    ):
        raise RuntimeError(
            "已保存公關效果與付費紀錄不符，拒絕執行"
        )
    return int(row["effective_score"])


def perform_paid_pr_action(
    guild_id,
    actor_id,
    character_id,
    rumor_id,
    strategy,
    resource,
    request_key,
    *,
    trusted=False,
    funding_company=None,
) -> PaidPRResult:
    if not trusted:
        raise PermissionError(
            "此功能僅可從已驗證權限的 Discord 後端呼叫"
        )

    gid = str(guild_id).strip()
    name = str(strategy).strip()
    key = str(request_key).strip()

    if not gid or not name or not key or len(key) > 200:
        raise ValueError("公關操作參數缺失或過長")

    if (
        type(character_id) is not int
        or character_id <= 0
        or type(rumor_id) is not int
        or rumor_id <= 0
    ):
        raise ValueError("角色或傳聞編號不合法")

    if resource not in ("coins", "paid_actions", "company_coins"):
        raise ValueError("付款資源不合法")

    if resource == "company_coins":
        if not funding_company:
            raise ValueError("公司付款必須指定公司編號")
        funding_company = str(funding_company)
    else:
        funding_company = None

    stored_resource = (
        "coins" if resource == "company_coins" else resource
    )

    with get_connection() as db:
        old = _load_original(db, gid, key)

        if old is not None:
            if (
                int(old["rumor_id"]) != rumor_id
                or int(old["character_id"]) != character_id
                or old["strategy"] != name
                or old["resource"] != stored_resource
            ):
                raise ValueError(
                    "相同請求編號已用在其他公關操作"
                )

            owner = db.execute(
                "SELECT owner_id FROM characters "
                "WHERE guild_id=? AND id=?",
                (gid, character_id),
            ).fetchone()

            if (
                owner is None
                or str(owner["owner_id"]) != str(actor_id)
            ):
                raise PermissionError(
                    "只能查閱自己角色的公關操作"
                )

            if resource == "company_coins":
                from core.company_pr_funding import (
                    initialize as init_funding,
                    verify_company_payer,
                )
                init_funding(db)
                funding = db.execute(
                    "SELECT business_id FROM rp_company_pr_funding "
                    "WHERE guild_id=? AND request_key=?",
                    (gid, key),
                ).fetchone()
                if (
                    not funding
                    or str(funding["business_id"]) != funding_company
                ):
                    raise ValueError(
                        "這筆款項不是指定公司的付款"
                    )
                verify_company_payer(
                    db,
                    gid,
                    funding_company,
                    actor_id,
                    rumor_id,
                    int(old["cost"]),
                )
            else:
                funding_table = db.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' "
                    "AND name='rp_company_pr_funding'"
                ).fetchone()
                if funding_table and db.execute(
                    "SELECT 1 FROM rp_company_pr_funding "
                    "WHERE guild_id=? AND request_key=?",
                    (gid, key),
                ).fetchone():
                    raise ValueError(
                        "這筆款項由公司付款，不能當成個人付款"
                    )

            score = _verify_effect(
                db,
                gid,
                int(old["id"]),
                rumor_id,
                character_id,
                name,
                float(old["effectiveness"]),
            )
            return PaidPRResult(
                int(old["id"]),
                int(old["cost"]),
                resource,
                int(old["repeat_count"]),
                float(old["effectiveness"]),
                score,
                True,
            )

        exists = db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' "
            "AND name='rp_rumor_pr_strategy_config'"
        ).fetchone()
        if exists is None:
            raise LookupError(
                "尚未建立正式公關策略設定，禁止扣費"
            )

        cfg = db.execute(
            "SELECT base_score FROM rp_rumor_pr_strategy_config "
            "WHERE guild_id=? AND strategy=? AND enabled=1",
            (gid, name),
        ).fetchone()

        if cfg is None:
            raise LookupError(
                "此策略尚未由官方啟用，禁止扣費"
            )

        base = int(cfg["base_score"])
        if not 0 <= base <= 10000:
            raise RuntimeError(
                "官方公關基礎分數設定不合法"
            )

    writer = create_effect_writer(
        gid,
        rumor_id,
        character_id,
        name,
        base,
        trusted=True,
    )

    charged = charge_verified_pr_action(
        gid,
        actor_id,
        character_id,
        rumor_id,
        name,
        stored_resource,
        key,
        effect_writer=writer,
        funding_company=funding_company,
    )

    with get_connection() as db:
        score = _verify_effect(
            db,
            gid,
            charged.action_id,
            rumor_id,
            character_id,
            name,
            charged.effectiveness,
        )

    return PaidPRResult(
        charged.action_id,
        charged.cost,
        resource,
        charged.repeat_count,
        charged.effectiveness,
        score,
        charged.duplicated,
    )
