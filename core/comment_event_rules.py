
"""評論事件白名單規則（唯讀、保守預覽版）。

不自行認定事件屬實、不操作資料庫、不自動發布留言。
每一個 event fact 必須由上層已驗證的 RP 事件系統提供。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Mapping


# 事實標記必須由可信的事件資料來源產生，
# 不接受使用者輸入原文直接轉換。
KNOWN_FACTS = frozenset({
    "birthday", "birthday_post", "birthday_photo",
    "birthday_campaign",
    "weather_cold", "care_post", "care_history", "winter",
    "performance", "live_vocals", "high_note",
    "acoustic", "performance_issue",
    "audio_issue", "audio_good", "host_interview",
    "host_good", "host_bad",
    "red_carpet", "styling_photo",
    "styling_good", "styling_bad",
    "concert", "tickets_sold_out", "ticket_paid",
    "encore", "audience_singalong",
    "event_organization_good",
    "event_organization_bad", "event_completed",
    "film_trailer", "film_released", "script_known",
    "adaptation",
    "rehearsed_collaboration", "first_collaboration",
    "variety_show",
    "public_controversy", "verified_evidence",
    "public_response", "response_delay",
    "rumor_authorized", "prior_public_statement",
    "fan_conflict",
    "trending", "trending_first", "criticism_history",
    "brand_campaign", "product_released", "endorsement",
    "social_action", "social_disclosure", "social_debate",
    "staff_contribution", "fan_interaction",
    "work_shooting",
})


# 每個主分類至少需要符合一組情境。
CATEGORY_REQUIREMENTS: dict[
    int, tuple[FrozenSet[str], ...]
] = {
    1: (
        frozenset({"care_post"}),
        frozenset({"fan_interaction"}),
        frozenset({"weather_cold"}),
    ),
    2: (
        frozenset({"film_trailer"}),
        frozenset({"film_released"}),
        frozenset({"variety_show"}),
    ),
    3: (
        frozenset({"performance"}),
        frozenset({"red_carpet"}),
        frozenset({"concert"}),
        frozenset({"host_interview"}),
    ),
    4: (
        frozenset({"brand_campaign"}),
        frozenset({"product_released"}),
        frozenset({"endorsement"}),
    ),
    5: (
        frozenset({"fan_interaction"}),
        frozenset({"performance"}),
    ),
    6: (
        frozenset({"fan_interaction"}),
        frozenset({"rehearsed_collaboration"}),
    ),
    7: (
        frozenset({"public_controversy"}),
    ),
    8: (
        frozenset({"public_response"}),
        frozenset({"public_controversy"}),
    ),
    9: (
        frozenset({"trending"}),
        frozenset({"trending_first"}),
    ),
    10: (
        frozenset({"trending"}),
        frozenset({"brand_campaign"}),
        frozenset({"public_controversy"}),
    ),
    11: (
        frozenset({"social_action"}),
        frozenset({"social_debate"}),
    ),
    12: (
        frozenset({"birthday"}),
        frozenset({"concert"}),
        frozenset({"event_completed"}),
    ),
}


# 已逐條確認的額外限制。
# 沒有列在此處的模板不能自動推測為已驗證。
SPECIFIC_REQUIRED: dict[str, FrozenSet[str]] = {
    "C309": frozenset({"birthday"}),
    "C310": frozenset({"birthday_photo"}),
    "C313": frozenset({"birthday_campaign"}),
    "C318": frozenset({"birthday_campaign"}),
    "C321": frozenset({"birthday", "fan_conflict"}),
    "C324": frozenset({"birthday", "film_released"}),
    "C325": frozenset({"birthday", "birthday_post"}),
    "C326": frozenset({
        "birthday",
        "public_controversy",
        "rumor_authorized",
    }),
    "C330": frozenset({"weather_cold", "care_post"}),
    "C333": frozenset({"care_post", "trending"}),
    "C334": frozenset({"weather_cold", "care_post"}),
    "C338": frozenset({"care_post", "care_history"}),
    "C340": frozenset({"care_post", "work_shooting"}),
    "C342": frozenset({"care_post"}),
    "C344": frozenset({"care_post", "winter"}),
    "C346": frozenset({
        "public_controversy",
        "verified_evidence",
    }),
    "C347": frozenset({
        "public_controversy",
        "verified_evidence",
    }),
    "C348": frozenset({
        "public_controversy",
        "prior_public_statement",
    }),
    "C351": frozenset({
        "public_controversy",
        "verified_evidence",
    }),
    "C360": frozenset({
        "public_controversy",
        "response_delay",
    }),
    "C364": frozenset({
        "public_controversy",
        "trending_first",
    }),
    "C368": frozenset({"tickets_sold_out"}),
    "C369": frozenset({
        "styling_photo",
        "red_carpet",
    }),
    "C372": frozenset({
        "performance",
        "audio_issue",
    }),
    "C373": frozenset({
        "event_completed",
        "performance_issue",
    }),
    "C375": frozenset({"film_trailer"}),
    "C378": frozenset({
        "performance",
        "live_vocals",
    }),
    "C380": frozenset({"variety_show"}),
    "C382": frozenset({"event_organization_bad"}),
    "C383": frozenset({"performance"}),
    "C385": frozenset({
        "styling_photo",
        "styling_good",
    }),
    "C387": frozenset({
        "ticket_paid",
        "event_completed",
    }),
    "C391": frozenset({
        "performance",
        "ticket_paid",
    }),
    "C392": frozenset({
        "performance",
        "audio_issue",
    }),
    "C393": frozenset({"acoustic"}),
    "C394": frozenset({"high_note"}),
    "C398": frozenset({"styling_photo"}),
    "C404": frozenset({
        "performance",
        "audio_good",
    }),
    "C406": frozenset({"event_organization_good"}),
    "C409": frozenset({"encore"}),
    "C410": frozenset({
        "first_collaboration",
        "performance",
    }),
    "C411": frozenset({"film_trailer"}),
    "C412": frozenset({
        "film_released",
        "script_known",
    }),
    "C413": frozenset({"film_released"}),
    "C414": frozenset({
        "adaptation",
        "film_released",
    }),
    "C415": frozenset({
        "film_released",
        "script_known",
    }),
    "C418": frozenset({"audience_singalong"}),
    "C419": frozenset({
        "event_completed",
        "staff_contribution",
    }),
    "C420": frozenset({
        "performance_issue",
        "event_completed",
    }),
}


# 僅角色本人或授權帳號能使用。
AUTHOR_ONLY = frozenset({
    "C325",
    "C342",
})


# 含爭議、指控或明顯負面爆料的評論。
# 必須另行取得爭議使用授權。
ALLEGATION_IDS = frozenset({
    "C326",
    "C346",
    "C347",
    "C348",
    "C349",
    "C350",
    "C351",
    "C352",
    "C353",
    "C354",
    "C355",
    "C357",
    "C358",
    "C359",
    "C360",
    "C361",
    "C362",
    "C363",
    "C364",
    "C365",
    "C366",
    "C367",
})


@dataclass(frozen=True, slots=True)
class VerifiedCommentContext:
    """已核實的評論事件上下文。

    facts 必須由可信的 RP 事件服務提供，
    不可以直接根據玩家自由輸入的文字建立。
    """

    event_id: str

    category_id: int

    event_verified: bool

    facts: FrozenSet[str] = field(
        default_factory=frozenset
    )

    reviewed_comment_ids: FrozenSet[str] = field(
        default_factory=frozenset
    )

    authorized_speaker: bool = False

    allegation_use_approved: bool = False


def allowed_comment_ids(
    ctx: VerifiedCommentContext,
    rows: Iterable[Mapping],
) -> frozenset[str]:
    """取得可安全使用的評論 ID。

    須同時符合：

    1. 事件已核實
    2. 評論經過審核
    3. 主分類相符
    4. 個別事件條件相符
    5. 發言者具有必要權限
    6. 爭議評論已獲使用授權

    不寫入資料庫、不發送 Discord 訊息。
    """

    if (
        not ctx.event_verified
        or not ctx.event_id.strip()
    ):
        return frozenset()

    if ctx.category_id not in CATEGORY_REQUIREMENTS:
        return frozenset()

    if not ctx.facts <= KNOWN_FACTS:
        raise ValueError(
            "事件包含未定義的 fact，"
            "請先更新規則及驗證來源"
        )

    category_rules = CATEGORY_REQUIREMENTS[
        ctx.category_id
    ]

    if not any(
        required <= ctx.facts
        for required in category_rules
    ):
        return frozenset()

    allowed: set[str] = set()

    for row in rows:
        comment_id = str(
            row.get("id", "")
        )

        if not comment_id:
            continue

        if comment_id not in ctx.reviewed_comment_ids:
            continue

        if row.get("category_id") != ctx.category_id:
            continue

        if not bool(row.get("approved", 0)):
            continue

        if (
            comment_id in AUTHOR_ONLY
            and not ctx.authorized_speaker
        ):
            continue

        if (
            bool(row.get("requires_authorization"))
            and not ctx.authorized_speaker
        ):
            continue

        if comment_id in ALLEGATION_IDS:
            if not (
                ctx.allegation_use_approved
                and "public_controversy" in ctx.facts
            ):
                continue

        specific = SPECIFIC_REQUIRED.get(
            comment_id
        )

        # 尚未完成逐條規則的模板，
        # 暫時禁止自動抽取。
        if specific is None:
            continue

        if specific <= ctx.facts:
            allowed.add(comment_id)

    return frozenset(allowed)
