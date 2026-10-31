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
    table = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rp_rumor_pr_charges'").fetchone()
    if table is None:
        return None
    return db.execute(
        "SELECT id,rumor_id,character_id,strategy,resource,cost,repeat_count,effectiveness "
        "FROM rp_rumor_pr_charges WHERE guild_id=? AND request_key=?",
        (gid, request_key),
    ).fetchone()


def _verify_effect(db, gid, action_id, rumor_id, character_id, strategy, multiplier):
    row = db.execute(
        "SELECT rumor_id,character_id,strategy,base_score,multiplier,effective_score "
        "FROM rp_rumor_pr_effects WHERE guild_id=? AND action_id=?",
        (gid, action_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("原公關扣費缺少效果紀錄；禁止再次扣費，請管理員調查")
    if (int(row['rumor_id']) != rumor_id or int(row['character_id']) != character_id
            or row['strategy'] != strategy or abs(float(row['multiplier']) - multiplier) > 1e-9):
        raise RuntimeError("已保存公關效果與付費紀錄不符，拒絕執行")
    return int(row['effective_score'])


def perform_paid_pr_action(
    guild_id, actor_id, character_id, rumor_id, strategy, resource, request_key,
    *, trusted=False,
) -> PaidPRResult:
    if not trusted:
        raise PermissionError("此功能僅可從已驗證權限的 Discord 後端呼叫")
    gid, name, key = str(guild_id).strip(), str(strategy).strip(), str(request_key).strip()
    if not gid or not name or not key or len(key) > 200:
        raise ValueError("公關操作參數缺失或過長")
    if type(character_id) is not int or character_id <= 0 or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("角色或傳聞編號不合法")
    if resource not in ('coins', 'paid_actions'):
        raise ValueError("付款資源不合法")

    # 同一次 Discord 互動重送時，直接核對已保存的效果，不讀新分數。
    with get_connection() as db:
        old = _load_original(db, gid, key)
        if old is not None:
            if (int(old['rumor_id']) != rumor_id or int(old['character_id']) != character_id
                    or old['strategy'] != name or old['resource'] != resource):
                raise ValueError("相同請求編號已用在其他公關操作")
            # 仍需驗證使用者是否擁有角色，避免猜中請求編號讀取其他人的操作。
            owner = db.execute(
                "SELECT owner_id FROM characters WHERE guild_id=? AND id=?",
                (gid, character_id),
            ).fetchone()
            if owner is None or str(owner['owner_id']) != str(actor_id):
                raise PermissionError("只能查閱自己角色的公關操作")
            score = _verify_effect(db, gid, int(old['id']), rumor_id,
                                   character_id, name, float(old['effectiveness']))
            return PaidPRResult(int(old['id']), int(old['cost']), resource,
                                int(old['repeat_count']), float(old['effectiveness']), score, True)

        # 不在此處自行猜測或填入官方基礎分數。
        exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='rp_rumor_pr_strategy_config'").fetchone()
        if exists is None:
            raise LookupError("尚未建立正式公關策略設定，禁止扣費")
        cfg = db.execute(
            "SELECT base_score FROM rp_rumor_pr_strategy_config "
            "WHERE guild_id=? AND strategy=? AND enabled=1", (gid, name),
        ).fetchone()
        if cfg is None:
            raise LookupError("此策略尚未由官方啟用，禁止扣費")
        base = int(cfg['base_score'])
        if not 0 <= base <= 10000:
            raise RuntimeError("官方公關基礎分數設定不合法")

    writer = create_effect_writer(gid, rumor_id, character_id, name, base, trusted=True)
    charged = charge_verified_pr_action(
        gid, actor_id, character_id, rumor_id, name, resource, key, effect_writer=writer,
    )
    with get_connection() as db:
        score = _verify_effect(db, gid, charged.action_id, rumor_id,
                               character_id, name, charged.effectiveness)
    return PaidPRResult(charged.action_id, charged.cost, charged.resource,
                        charged.repeat_count, charged.effectiveness, score, charged.duplicated)
