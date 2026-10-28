
"""公司、品牌、集團的獨立資料模型與核心規則。"""

from __future__ import annotations

import json

from core.database import (
    get_connection,
    transaction,
    get_or_create_account,
)
from core.economy import _payment


# ===== 一般公司啟動資金 =====

COMPANY_CAPITAL = {
    "個人工作室": 100_000,
    "MCN": 300_000,
    "經紀公司": 500_000,
    "一般企業": 300_000,
    "設計公司": 500_000,
    "餐飲公司": 300_000,
    "媒體公司": 500_000,
    "影視製作公司": 1_000_000,
    "大型娛樂公司": 2_000_000,
}


# ===== 品牌啟動資金 =====

BRAND_CAPITAL = {
    "平價": 100_000,
    "小資": 300_000,
    "輕奢": 500_000,
    "奢侈": 1_000_000,
    "高奢": 5_000_000,
}

GROUP_CAPITAL = 10_000_000


ALLOWED_MANAGER_PERMISSIONS = {
    "recruit",
    "work_review",
    "finance",
    "products",
    "brand",
    "vip",
    "endorsement",
    "public_relations",
}


# ===== 初始化資料庫 =====

def initialize():
    schema = [
        """
        CREATE TABLE IF NOT EXISTS rp_approved_company_authorizations (
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            original_applicant_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('approved', 'revoked')),
            PRIMARY KEY(guild_id, company_id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS rp_business_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            applicant_id TEXT NOT NULL,
            owner_character_id INTEGER NOT NULL,
            kind TEXT NOT NULL
                CHECK(kind IN ('company', 'brand', 'group')),
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            industry TEXT NOT NULL,
            description TEXT NOT NULL,
            region TEXT NOT NULL,
            parent_id TEXT,
            representative_character_id INTEGER,
            funding_source TEXT NOT NULL DEFAULT 'character',
            required_capital INTEGER NOT NULL
                CHECK(required_capital >= 0),
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            reviewed_by TEXT
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS rp_business_entities (
            guild_id TEXT NOT NULL,
            business_id TEXT NOT NULL,
            kind TEXT NOT NULL
                CHECK(kind IN ('company', 'brand', 'group')),
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            industry TEXT NOT NULL,
            description TEXT NOT NULL,
            region TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            owner_character_id INTEGER NOT NULL,
            parent_id TEXT,
            representative_character_id INTEGER,
            brand_tier TEXT,
            founded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'active',
            PRIMARY KEY(guild_id, business_id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS rp_business_managers (
            guild_id TEXT NOT NULL,
            business_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '管理層',
            permissions TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            invited_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, business_id, character_id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS rp_business_rep_consents (
            application_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            representative_character_id INTEGER NOT NULL,
            accepted_by TEXT NOT NULL,
            accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(application_id, guild_id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS rp_business_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            business_id TEXT NOT NULL,
            action TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS ix_business_owner
        ON rp_business_entities(guild_id, owner_id)
        """,

        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_business_name
        ON rp_business_entities(guild_id, kind, name)
        """,
    ]

    with transaction() as db:
        for sql in schema:
            db.execute(sql)


# ===== 角色驗證 =====

def _character(db, guild_id, character_id, user_id=None):
    row = db.execute(
        """
        SELECT owner_id, name
        FROM characters
        WHERE guild_id = ?
          AND id = ?
          AND status = 'active'
        """,
        (str(guild_id), int(character_id)),
    ).fetchone()

    if row is None:
        raise ValueError("角色不存在或尚未啟用。")

    if (
        user_id is not None
        and str(row["owner_id"]) != str(user_id)
    ):
        raise ValueError("這不是你的角色。")

    return row


def _entity(db, guild_id, business_id):
    row = db.execute(
        """
        SELECT *
        FROM rp_business_entities
        WHERE guild_id = ?
          AND business_id = ?
          AND status = 'active'
        """,
        (str(guild_id), business_id),
    ).fetchone()

    if row is None:
        raise ValueError("找不到已成立的公司、品牌或集團。")

    return row


# ===== 申請公司／品牌／集團 =====

def apply_business(
    guild_id,
    user_id,
    character_id,
    kind,
    name,
    category,
    industry,
    description,
    region,
    parent_id=None,
    representative_id=None,
    funding_source="character",
):
    guild_id = str(guild_id)
    user_id = str(user_id)

    name = name.strip()
    industry = industry.strip()
    region = region.strip()
    description = description.strip()

    if kind not in ("company", "brand", "group"):
        raise ValueError("不支援的商業主體類型。")

    if not all((name, industry, region, description)):
        raise ValueError(
            "請填妥名稱、產業、介紹及地區。"
        )

    if max(map(len, (name, industry, region))) > 100:
        raise ValueError("申請欄位超過字數上限。")

    if len(description) > 1000:
        raise ValueError("公司介紹超過字數上限。")

    if kind == "company":
        if category not in COMPANY_CAPITAL:
            raise ValueError("公司類型不合法。")

        capital = COMPANY_CAPITAL[category]

    elif kind == "brand":
        if category not in BRAND_CAPITAL:
            raise ValueError("品牌等級不合法。")

        capital = BRAND_CAPITAL[category]

        if (
            category in ("奢侈", "高奢")
            and representative_id is None
        ):
            raise ValueError(
                "奢侈與高奢品牌必須指定品牌代表。"
            )

    else:
        capital = GROUP_CAPITAL

    with transaction() as db:
        _character(
            db,
            guild_id,
            character_id,
            user_id,
        )

        if representative_id is not None:
            _character(
                db,
                guild_id,
                representative_id,
            )

        if parent_id:
            parent = _entity(
                db,
                guild_id,
                parent_id,
            )

            if (
                parent["owner_id"] != user_id
                or parent["kind"] not in ("company", "group")
            ):
                raise PermissionError(
                    "只能選擇自己持有的母公司或集團。"
                )

        if funding_source != "character":
            funding_company = _entity(
                db,
                guild_id,
                funding_source,
            )

            if (
                kind != "brand"
                or funding_company["kind"] != "company"
                or funding_company["owner_id"] != user_id
            ):
                raise PermissionError(
                    "只有所屬公司的持有人可選擇公司出資。"
                )

            if parent_id != funding_source:
                raise ValueError(
                    "出資公司必須是品牌所屬公司。"
                )

        existing = db.execute(
            """
            SELECT 1
            FROM rp_business_applications
            WHERE guild_id = ?
              AND kind = ?
              AND name = ?
              AND status = 'pending'
            """,
            (guild_id, kind, name),
        ).fetchone()

        if existing:
            raise ValueError(
                "已有同名申請正在審核。"
            )

        cursor = db.execute(
            """
            INSERT INTO rp_business_applications (
                guild_id,
                applicant_id,
                owner_character_id,
                kind,
                name,
                category,
                industry,
                description,
                region,
                parent_id,
                representative_character_id,
                funding_source,
                required_capital
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                character_id,
                kind,
                name,
                category,
                industry,
                description,
                region,
                parent_id,
                representative_id,
                funding_source,
                capital,
            ),
        )

        return cursor.lastrowid, capital


# ===== 查詢持有公司 =====

def list_owned(guild_id, user_id):
    with get_connection() as db:
        return db.execute(
            """
            SELECT business_id, name, kind, category
            FROM rp_business_entities
            WHERE guild_id = ?
              AND owner_id = ?
              AND status = 'active'
            ORDER BY business_id
            """,
            (str(guild_id), str(user_id)),
        ).fetchall()


# ===== 管理層邀請 =====

def invite_manager(
    guild_id,
    business_id,
    user_id,
    target_user_id,
    character_id,
    character_name,
    title="管理層",
):
    guild_id = str(guild_id)
    user_id = str(user_id)
    target_user_id = str(target_user_id)

    with transaction() as db:
        business = _entity(
            db,
            guild_id,
            business_id,
        )

        if business["owner_id"] != user_id:
            raise PermissionError(
                "只有公司持有人可以任命管理層。"
            )

        character = _character(
            db,
            guild_id,
            int(character_id),
            target_user_id,
        )

        if character["name"] != character_name.strip():
            raise ValueError(
                "角色名字與角色編號不相符。"
            )

        db.execute(
            """
            INSERT INTO rp_business_managers (
                guild_id,
                business_id,
                user_id,
                character_id,
                title,
                invited_by
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                guild_id,
                business_id,
                character_id
            )
            DO UPDATE SET
                title = excluded.title,
                status = 'pending',
                permissions = '[]',
                invited_by = excluded.invited_by,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                guild_id,
                business_id,
                target_user_id,
                int(character_id),
                title.strip()[:60],
                user_id,
            ),
        )

    return (
        "✅ 任命邀請已建立，"
        "需由對方確認後才會生效。"
    )


def accept_manager(
    guild_id,
    business_id,
    user_id,
    character_id,
):
    guild_id = str(guild_id)
    user_id = str(user_id)

    with transaction() as db:
        _character(
            db,
            guild_id,
            int(character_id),
            user_id,
        )

        cursor = db.execute(
            """
            UPDATE rp_business_managers
            SET status = 'active'
            WHERE guild_id = ?
              AND business_id = ?
              AND user_id = ?
              AND character_id = ?
              AND status = 'pending'
            """,
            (
                guild_id,
                business_id,
                user_id,
                int(character_id),
            ),
        )

        if not cursor.rowcount:
            raise ValueError(
                "沒有待接受的管理層邀請。"
            )

    return "✅ 已接受管理層任命。"


# ===== 品牌代表確認 =====

def accept_brand_rep(
    guild_id,
    application_id,
    user_id,
    character_id,
):
    guild_id = str(guild_id)
    user_id = str(user_id)

    with transaction() as db:
        row = db.execute(
            """
            SELECT representative_character_id
            FROM rp_business_applications
            WHERE guild_id = ?
              AND id = ?
              AND kind = 'brand'
              AND status = 'pending'
            """,
            (
                guild_id,
                int(application_id),
            ),
        ).fetchone()

        if (
            not row
            or row["representative_character_id"]
            != int(character_id)
        ):
            raise ValueError(
                "找不到指定這個角色的待審品牌申請。"
            )

        _character(
            db,
            guild_id,
            int(character_id),
            user_id,
        )

        db.execute(
            """
            INSERT OR REPLACE INTO rp_business_rep_consents (
                application_id,
                guild_id,
                representative_character_id,
                accepted_by
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                int(application_id),
                guild_id,
                int(character_id),
                user_id,
            ),
        )

    return "✅ 品牌代表邀請已確認，待官方審核。"


# ===== 奢侈、高奢品牌資格 =====

def _verify_brand_qualification(db, guild_id, application):
    if (
        application["kind"] != "brand"
        or application["category"] not in ("奢侈", "高奢")
    ):
        return

    accepted = db.execute(
        """
        SELECT 1
        FROM rp_business_rep_consents
        WHERE guild_id = ?
          AND application_id = ?
          AND representative_character_id = ?
        """,
        (
            guild_id,
            application["id"],
            application["representative_character_id"],
        ),
    ).fetchone()

    if not accepted:
        raise ValueError(
            "品牌代表尚未接受指定。"
        )

    if application["category"] == "奢侈":
        limits = (10_000, 10_000, 5_000)
    else:
        limits = (30_000, 30_000, 15_000)

    attribute_names = (
        "流量熱度",
        "商業價值",
        "影響指數",
    )

    for character_id in (
        application["owner_character_id"],
        application["representative_character_id"],
    ):
        stats = {
            row["name"]: row["value"]
            for row in db.execute(
                """
                SELECT name, value
                FROM attributes
                WHERE guild_id = ?
                  AND character_id = ?
                  AND name IN (
                      '流量熱度',
                      '商業價值',
                      '影響指數'
                  )
                """,
                (
                    guild_id,
                    character_id,
                ),
            )
        }

        for name, required in zip(
            attribute_names,
            limits,
        ):
            if stats.get(name, 0) < required:
                raise ValueError(
                    f"角色 #{character_id} 的{name}不足，"
                    f"未達{application['category']}品牌資格。"
                )


# ===== 管理層授權 =====

def grant_manager_permissions(
    guild_id,
    business_id,
    user_id,
    character_id,
    permissions,
):
    selected = set(permissions)

    if not selected <= ALLOWED_MANAGER_PERMISSIONS:
        raise ValueError(
            "含有未支援的管理權限。"
        )

    with transaction() as db:
        company = _entity(
            db,
            str(guild_id),
            business_id,
        )

        if company["owner_id"] != str(user_id):
            raise PermissionError(
                "只有公司持有人可以修改授權。"
            )

        cursor = db.execute(
            """
            UPDATE rp_business_managers
            SET permissions = ?
            WHERE guild_id = ?
              AND business_id = ?
              AND character_id = ?
              AND status = 'active'
            """,
            (
                json.dumps(
                    sorted(selected),
                    ensure_ascii=False,
                ),
                str(guild_id),
                business_id,
                int(character_id),
            ),
        )

        if not cursor.rowcount:
            raise ValueError(
                "找不到已接受任命的管理層角色。"
            )

        db.execute(
            """
            INSERT INTO rp_business_audit (
                guild_id,
                business_id,
                action,
                actor_id,
                details
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                business_id,
                "manager_permission",
                str(user_id),
                json.dumps(sorted(selected)),
            ),
        )


def has_permission(
    db,
    guild_id,
    business_id,
    user_id,
    permission,
):
    business = _entity(
        db,
        str(guild_id),
        business_id,
    )

    if business["owner_id"] == str(user_id):
        return True

    rows = db.execute(
        """
        SELECT permissions
        FROM rp_business_managers
        WHERE guild_id = ?
          AND business_id = ?
          AND user_id = ?
          AND status = 'active'
        """,
        (
            str(guild_id),
            business_id,
            str(user_id),
        ),
    ).fetchall()

    return any(
        permission in json.loads(row["permissions"])
        for row in rows
    )


# ===== 自動成立集團 =====

def _maybe_auto_group(
    db,
    guild_id,
    user_id,
    character_id,
):
    owned_brands = db.execute(
        """
        SELECT business_id, brand_tier
        FROM rp_business_entities
        WHERE guild_id = ?
          AND owner_id = ?
          AND kind = 'brand'
          AND status = 'active'
        """,
        (
            guild_id,
            user_id,
        ),
    ).fetchall()

    total_brands = len(owned_brands)

    high_luxury_brands = sum(
        brand["brand_tier"] == "高奢"
        for brand in owned_brands
    )

    if (
        total_brands < 3
        and high_luxury_brands < 2
    ):
        return None

    existing = db.execute(
        """
        SELECT business_id
        FROM rp_business_entities
        WHERE guild_id = ?
          AND owner_id = ?
          AND kind = 'group'
          AND status = 'active'
        """,
        (
            guild_id,
            user_id,
        ),
    ).fetchone()

    if existing:
        return existing["business_id"]

    group_id = f"AUTO-GRP-{character_id}"

    db.execute(
        """
        INSERT INTO rp_business_entities (
            guild_id,
            business_id,
            kind,
            name,
            category,
            industry,
            description,
            region,
            owner_id,
            owner_character_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            group_id,
            "group",
            f"集團-{character_id}",
            "自動集團",
            "綜合",
            "符合品牌持有數量自動成立",
            "待設定",
            user_id,
            character_id,
        ),
    )

    db.execute(
        """
        INSERT INTO rp_business_audit (
            guild_id,
            business_id,
            action,
            actor_id,
            details
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            group_id,
            "auto_group",
            user_id,
            "品牌3家或高奢2家，免費成立",
        ),
    )

    return group_id


# ===== 官方核准申請 =====

def approve_application(
    guild_id,
    application_id,
    reviewer_user_id,
    *,
    official_verified=False,
):
    """
    預留給官方審核介面使用。

    注意：
    official_verified 必須由真正完成權限驗證的
    官方審核流程傳入。
    不可直接開放給一般玩家呼叫。
    """

    if not official_verified:
        raise PermissionError(
            "必須由官方審核流程驗證身分。"
        )

    guild_id = str(guild_id)

    with transaction() as db:
        application = db.execute(
            """
            SELECT *
            FROM rp_business_applications
            WHERE guild_id = ?
              AND id = ?
              AND status = 'pending'
            """,
            (
                guild_id,
                int(application_id),
            ),
        ).fetchone()

        if application is None:
            raise ValueError(
                "申請不存在或已處理。"
            )

        _character(
            db,
            guild_id,
            application["owner_character_id"],
            application["applicant_id"],
        )

        if application["representative_character_id"]:
            _character(
                db,
                guild_id,
                application["representative_character_id"],
            )

            consent = db.execute(
                """
                SELECT 1
                FROM rp_business_rep_consents
                WHERE guild_id = ?
                  AND application_id = ?
                  AND representative_character_id = ?
                """,
                (
                    guild_id,
                    application["id"],
                    application["representative_character_id"],
                ),
            ).fetchone()

            if not consent:
                raise ValueError(
                    "指定的品牌代表尚未接受邀請。"
                )

        _verify_brand_qualification(
            db,
            guild_id,
            application,
        )

        duplicate = db.execute(
            """
            SELECT 1
            FROM rp_business_entities
            WHERE guild_id = ?
              AND kind = ?
              AND name = ?
            """,
            (
                guild_id,
                application["kind"],
                application["name"],
            ),
        ).fetchone()

        if duplicate:
            raise ValueError(
                "同名商業主體已存在。"
            )

        if application["kind"] == "company":
            prefix = "COM"
        elif application["kind"] == "brand":
            prefix = "BRD"
        else:
            prefix = "GRP"

        business_id = (
            f"{prefix}-{application['id']:06d}"
        )

        # 確定資金來源。
        if application["funding_source"] == "character":
            sender_type = "character"
            sender_id = str(
                application["owner_character_id"]
            )
        else:
            funding_company = _entity(
                db,
                guild_id,
                application["funding_source"],
            )

            if (
                funding_company["owner_id"]
                != application["applicant_id"]
            ):
                raise PermissionError(
                    "出資公司持有人已改變，請重新申請。"
                )

            sender_type = "organization"
            sender_id = funding_company["business_id"]

        # 公司和品牌使用獨立帳戶。
        if sender_type == "character":
            payer_account = get_or_create_account(
                db,
                guild_id,
                sender_type,
                sender_id,
                "wallet",
            )
        else:
            payer_account = get_or_create_account(
                db,
                guild_id,
                sender_type,
                sender_id,
                "treasury",
            )

        receiver_account = get_or_create_account(
            db,
            guild_id,
            "organization",
            business_id,
            "treasury",
        )

        # 審核成功才扣款，並將資金記入新主體帳戶。
        _payment(
            db,
            guild_id,
            f"business-capital:{application['id']}",
            payer_account,
            receiver_account,
            application["required_capital"],
            f"{business_id} 創立資本",
        )

        db.execute(
            """
            INSERT INTO rp_business_entities (
                guild_id,
                business_id,
                kind,
                name,
                category,
                industry,
                description,
                region,
                owner_id,
                owner_character_id,
                parent_id,
                representative_character_id,
                brand_tier
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                guild_id,
                business_id,
                application["kind"],
                application["name"],
                application["category"],
                application["industry"],
                application["description"],
                application["region"],
                application["applicant_id"],
                application["owner_character_id"],
                application["parent_id"],
                application["representative_character_id"],
                (
                    application["category"]
                    if application["kind"] == "brand"
                    else None
                ),
            ),
        )

        # 兼容舊有公司銀行、人事模組。
        if application["kind"] == "company":
            db.execute(
                """
                INSERT INTO rp_approved_company_authorizations (
                    guild_id,
                    company_id,
                    original_applicant_id,
                    status
                )
                VALUES (?, ?, ?, 'approved')
                """,
                (
                    guild_id,
                    business_id,
                    application["applicant_id"],
                ),
            )

        db.execute(
            """
            UPDATE rp_business_applications
            SET status = 'approved',
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                str(reviewer_user_id),
                application["id"],
            ),
        )

        db.execute(
            """
            INSERT INTO rp_business_audit (
                guild_id,
                business_id,
                action,
                actor_id,
                details
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                business_id,
                "created",
                str(reviewer_user_id),
                str(application["required_capital"]),
            ),
        )

        # 品牌數量達標時，免費成立集團。
        if application["kind"] == "brand":
            _maybe_auto_group(
                db,
                guild_id,
                application["applicant_id"],
                application["owner_character_id"],
            )

    return business_id


async def setup(bot):
    initialize()
