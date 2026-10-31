"""陣營專屬商店加成帳本：增益不直接改寫基礎屬性。

其他系統必須使用 get_faction_bonus() 讀取加成，方能計入實際玩法。
"""
from __future__ import annotations

from core.database import transaction

# 正面效果白名單，禁止商店間接提供受管制的影響指數。
ALLOWED_KEYS = frozenset({
    "流量熱度", "商業價值", "路人緣", "公關", "大眾認知度",
    "容貌", "歌唱", "舞蹈", "演技", "創作", "才藝",
    "體能", "口才", "智力", "魅力",
    "才智", "演繹", "主持", "綜藝", "個人才藝",
})
MAX_BONUS_PER_ORDER = 10000

def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS faction_shop_buff_grants (
            guild_id TEXT NOT NULL,
            order_id INTEGER NOT NULL,
            faction_id INTEGER NOT NULL,
            effect_key TEXT NOT NULL,
            effect_value INTEGER NOT NULL CHECK(effect_value > 0),
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            revoked_at TEXT,
            expires_at TEXT,
            PRIMARY KEY(guild_id,order_id)
        )""")
        # 向下相容遷移：舊有加成永久有效，不自行設定到期日。
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(faction_shop_buff_grants)")}
        if "expires_at" not in columns:
            db.execute("ALTER TABLE faction_shop_buff_grants ADD COLUMN expires_at TEXT")
        db.execute("""CREATE INDEX IF NOT EXISTS faction_shop_buff_lookup
            ON faction_shop_buff_grants(guild_id,faction_id,effect_key,revoked_at)""")

def grant_in_transaction(db, guild_id, order_id, faction_id, key, value, quantity):
    key = str(key).strip()
    value,quantity = int(value),int(quantity)
    if key not in ALLOWED_KEYS or value <= 0 or quantity <= 0 or value*quantity > MAX_BONUS_PER_ORDER:
        raise ValueError('專屬增益效果未在允許清單內，或數值不合法。')
    db.execute("""INSERT INTO faction_shop_buff_grants
        (guild_id,order_id,faction_id,effect_key,effect_value,quantity)
        VALUES (?,?,?,?,?,?)""",(str(guild_id),int(order_id),int(faction_id),key,value,quantity))
    return value*quantity

def get_faction_bonus(guild_id, character_id, effect_key):
    """讀取角色當前所屬陣營的已購買增益；退陣營即不再享有。

    加成獨立於角色基礎屬性。只有接入這個讀取接口的遊戲計算才會生效。
    """
    if effect_key not in ALLOWED_KEYS:
        return 0
    initialize()
    with transaction() as db:
        row = db.execute("""SELECT COALESCE(SUM(g.effect_value*g.quantity),0) AS bonus
            FROM faction_members m
            JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id
            JOIN faction_shop_buff_grants g
              ON g.guild_id=m.guild_id AND g.faction_id=m.faction_id
            WHERE m.guild_id=? AND m.character_id=?
              AND c.status='active' AND c.character_type='player'
              AND g.effect_key=? AND g.revoked_at IS NULL
              AND (g.expires_at IS NULL OR g.expires_at > CURRENT_TIMESTAMP)""",
              (str(guild_id),int(character_id),str(effect_key))).fetchone()
        return int(row['bonus']) if row else 0

def revoke_order(guild_id, order_id):
    """僅供已驗證權限的管理／退款程序呼叫，不做退款。"""
    initialize()
    with transaction() as db:
        return db.execute("""UPDATE faction_shop_buff_grants SET revoked_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND order_id=? AND revoked_at IS NULL""",
            (str(guild_id),int(order_id))).rowcount == 1


def list_active_bonuses(guild_id, character_id):
    """一次讀取角色目前陣營所有已登記加成；只讀，不修改基礎數值。"""
    initialize()
    with transaction() as db:
        rows = db.execute("""SELECT g.effect_key,
                   SUM(g.effect_value * g.quantity) AS bonus
            FROM faction_members m
            JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id
            JOIN faction_shop_buff_grants g ON g.guild_id=m.guild_id
                AND g.faction_id=m.faction_id
            WHERE m.guild_id=? AND m.character_id=?
              AND c.status='active' AND c.character_type='player'
              AND g.revoked_at IS NULL
              AND (g.expires_at IS NULL OR g.expires_at > CURRENT_TIMESTAMP)
            GROUP BY g.effect_key ORDER BY g.effect_key""",
            (str(guild_id), int(character_id))).fetchall()
        return {str(r['effect_key']): int(r['bonus']) for r in rows
                if str(r['effect_key']) in ALLOWED_KEYS and int(r['bonus']) > 0}


def expire_order(guild_id, order_id, expires_at):
    """僅供權限已驗證的管理服務設定到期時間；不會退款。

    expires_at 使用 UTC 的 SQLite 時間格式 YYYY-MM-DD HH:MM:SS。
    禁止延後已過期的加成，以免無意重新開啟既有增益。
    """
    from datetime import datetime, timezone
    try:
        expiry = datetime.strptime(str(expires_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as error:
        raise ValueError("到期時間格式應為 UTC YYYY-MM-DD HH:MM:SS") from error
    if expiry <= datetime.now(timezone.utc):
        raise ValueError("到期時間必須晚於現在；立即解除請使用撤銷操作。")
    initialize()
    with transaction() as db:
        return db.execute("""UPDATE faction_shop_buff_grants SET expires_at=?
            WHERE guild_id=? AND order_id=? AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP)""",
            (expiry.strftime("%Y-%m-%d %H:%M:%S"), str(guild_id), int(order_id))).rowcount == 1
