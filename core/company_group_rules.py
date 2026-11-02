
"""集團成立與歸屬的正式規則。"""

from __future__ import annotations

DIRECT_GROUP_CAPITAL = 10_000_000
AUTO_GROUP_COMPANIES = 5

COUNTABLE_KINDS = frozenset({"company"})
NON_COUNTABLE_KINDS = frozenset({
    "brand",
    "shop",
    "brand_shop",
    "mall_shop",
})


def group_capital(method: str) -> int:
    if method == "direct":
        return DIRECT_GROUP_CAPITAL
    if method == "automatic":
        return 0
    raise ValueError("未知的集團成立方式")


def company_counts(kind, status) -> bool:
    return (
        str(kind) in COUNTABLE_KINDS
        and str(status) == "active"
    )


def check_group_creation(
    *,
    method: str,
    company_count: int,
    owns_group: bool,
) -> None:
    if owns_group:
        raise ValueError(
            "目前已經持有集團，不得重複成立。"
        )

    if method == "direct":
        return

    if method == "automatic":
        if company_count < AUTO_GROUP_COMPANIES:
            raise ValueError(
                "自動成立集團需要至少五間正式公司。"
            )
        return

    raise ValueError("未知的集團成立方式")


def check_group_membership(
    *,
    is_chairperson: bool,
    owns_company: bool,
    existing_group_id=None,
    target_group_id=None,
) -> None:
    if is_chairperson:
        raise PermissionError(
            "集團董事長不得加入他人的集團。"
        )

    if not owns_company:
        raise PermissionError(
            "只能處理自己擁有的公司。"
        )

    if (
        existing_group_id
        and str(existing_group_id)
        != str(target_group_id)
    ):
        raise ValueError(
            "公司已隸屬其他集團，"
            "不可直接重複加入。"
        )
