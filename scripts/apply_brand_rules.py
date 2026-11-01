
"""品牌升級規則整合腳本。

使用：
    python scripts/apply_brand_rules.py

只修改 modules/company_brand_progress.py。
會建立原始檔備份。
不連接資料庫。
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGET = (
    ROOT
    / "modules"
    / "company_brand_progress.py"
)

BACKUP = TARGET.with_suffix(
    ".py.brand_backup"
)


def replace_exact(
    source: str,
    old: str,
    new: str,
    label: str,
) -> str:
    if new in source and old not in source:
        print(f"已完成：{label}")
        return source

    count = source.count(old)

    if count != 1:
        raise RuntimeError(
            f"{label}：找到了{count}處，"
            "預期必須剛好1處。"
            "可能是程式版本不同，已停止修改。"
        )

    return source.replace(old, new, 1)


def main():
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)

    if not (
        ROOT / "core" / "company_brand_rules.py"
    ).is_file():
        raise FileNotFoundError(
            "請先放入 core/company_brand_rules.py"
        )

    original = TARGET.read_text(
        encoding="utf-8"
    )

    updated = original

    # 1. 停止使用舊的集團自動成立功能。
    updated = replace_exact(
        updated,
        "from modules.company_business "
        "import _maybe_auto_group\n",
        "",
        "移除舊集團匯入",
    )

    old_auto_group = '''                if next_level == "高奢":
                    _maybe_auto_group(
                        db,
                        str(guild_id),
                        str(brand["owner_id"]),
                        brand["owner_character_id"],
                    )

'''

    updated = replace_exact(
        updated,
        old_auto_group,
        "",
        "移除高奢自動成立集團",
    )

    # 2. 使用正式產業限制。
    old_industry = '''def eligible_industry(industry, target_level):

    industry = str(industry)

    if target_level == "高奢":
        return any(
            keyword in industry
            for keyword in DESIGN_KEYWORDS
        )

    if target_level == "奢侈":
        return True

    return True
'''

    new_industry = '''def eligible_industry(industry, target_level):
    from core.company_brand_rules import (
        allowed_brand_level,
    )

    return allowed_brand_level(
        industry, target_level
    )
'''

    updated = replace_exact(
        updated,
        old_industry,
        new_industry,
        "統一品牌產業限制",
    )

    # 3. 正確處理 UTC / 時區日期。
    old_date = '''            founded_at = datetime.fromisoformat(
                brand["founded_at"]
            ).replace(tzinfo=timezone.utc)

            age = utc_now() - founded_at
'''

    new_date = '''            from core.company_brand_rules import (
                normalize_datetime,
            )

            founded_at = normalize_datetime(
                brand["founded_at"]
            )

            age = utc_now() - founded_at
'''

    updated = replace_exact(
        updated,
        old_date,
        new_date,
        "修正成立時間及14天等待期",
    )

    # 4. 核對原程式是否已有正式門檻。
    for fragment in (
        '"小資"',
        '"輕奢"',
        '"奢侈"',
        '"高奢"',
        '"consumption"',
        '"promotion"',
        '"cooperation"',
        '"representative"',
        "source_limit = target_heat * 35 // 100",
    ):
        if fragment not in updated:
            raise RuntimeError(
                f"原品牌程式缺少必要規則：{fragment}"
            )

    compile(
        updated,
        str(TARGET),
        "exec",
    )

    if updated == original:
        print("品牌規則已是修正版本。")
        return

    if not BACKUP.exists():
        BACKUP.write_text(
            original,
            encoding="utf-8",
        )

    TARGET.write_text(
        updated,
        encoding="utf-8",
    )

    print(
        "完成品牌程式更新："
        "modules/company_brand_progress.py"
    )
    print(
        "原始碼備份："
        "modules/company_brand_progress.py.brand_backup"
    )


if __name__ == "__main__":
    main()
