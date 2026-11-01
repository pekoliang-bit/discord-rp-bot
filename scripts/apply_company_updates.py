
"""將公司帳戶及五公司集團制度接入現有模組。

執行一次：
python scripts/apply_company_updates.py

套用前檢查原始碼版本，並保留 .company_backup 備份。
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(source, old, new, label):
    if new in source and old not in source:
        print(f"已套用：{label}")
        return source

    count = source.count(old)

    if count != 1:
        raise RuntimeError(
            f"{label}：預期找到1處，實際{count}處。"
            "請勿強制套用到不同版本。"
        )

    return source.replace(old, new, 1)


def prepare_application(source):
    old = '''                    raise PermissionError(
                        "公司帳戶開戶後端尚未完成："
                        "不得僅憑核准案件建立假帳戶。"
                    )'''

    new = '''                    from core.company_bank_opening import (
                        approve_company_bank,
                    )

                    approve_company_bank(
                        db,
                        self.gid,
                        r["title"],
                        r["user_id"],
                        r["id"],
                    )'''

    return replace_once(
        source, old, new,
        "公司銀行帳戶正式核准",
    )


def prepare_business(source):
    old = '''        # 品牌數量達標時，免費成立集團。
        if application["kind"] == "brand":
            _maybe_auto_group(
                db,
                guild_id,
                application["applicant_id"],
                application["owner_character_id"],
            )'''

    new = '''        # 集團依正式五公司規則審核，
        # 不再按照品牌數量自動成立。
        if application["kind"] == "group":
            from core.company_group_eligibility import (
                attach_owned_companies,
            )

            attach_owned_companies(
                db,
                guild_id,
                application["applicant_id"],
                business_id,
            )'''

    source = replace_once(
        source, old, new,
        "移除品牌自動成立集團",
    )

    # 集團申請在建立之前確認五公司資格。
    marker = '''        # 公司和品牌使用獨立帳戶。'''

    guard = '''        if application["kind"] == "group":
            from core.company_group_eligibility import (
                validate_group_application,
            )

            validate_group_application(
                db,
                guild_id,
                application["applicant_id"],
            )

'''

    if guard not in source:
        source = replace_once(
            source,
            marker,
            guard + marker,
            "集團審核前資格檢查",
        )

    return source


def main():
    paths = {
        "申請": ROOT / "modules" / "application_extras.py",
        "公司": ROOT / "modules" / "company_business.py",
    }

    changes = {}

    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(path)

        original = path.read_text(encoding="utf-8")

        updated = (
            prepare_application(original)
            if label == "申請"
            else prepare_business(original)
        )

        compile(updated, str(path), "exec")

        changes[path] = (original, updated)

    for path, (original, updated) in changes.items():
        if original == updated:
            continue

        backup = path.with_suffix(
            path.suffix + ".company_backup"
        )

        if not backup.exists():
            backup.write_text(
                original,
                encoding="utf-8",
            )

        path.write_text(
            updated,
            encoding="utf-8",
        )

        print(f"已修改：{path.relative_to(ROOT)}")

    print("完成原始碼更新。")


if __name__ == "__main__":
    main()
