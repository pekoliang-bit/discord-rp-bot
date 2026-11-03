
"""修正路人初始地位、排行榜資格，建立舊資料稽核。

執行：python repair_extra_20261103.py
不修改正式資料庫，不自動派發排行榜獎勵。
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULES = ROOT / "modules"

STATUS_FROM = '''        # 影響指數優先：直接降至影響門檻所允許的最高地位。
        influence = attrs.get("影響指數", 0)
        if influence < levels[level - 1][4]:'''

STATUS_TO = '''        # 路人既有初始咖位不需憑空提高影響指數。
        # 僅豁免初始咖位的影響門檻，不豁免其他維持規則。
        influence = attrs.get("影響指數", 0)
        try:
            extra_settings = json.loads(
                character["settings_json"] or "{}"
            )
            if not isinstance(extra_settings, dict):
                extra_settings = {}
        except (TypeError, ValueError):
            extra_settings = {}

        initial_extra_level = 1
        if extra_settings.get("character_category") == "extra":
            try:
                initial_extra_level = int(
                    extra_settings.get(
                        "initial_status_level",
                        extra_settings.get("status_level", 1),
                    )
                )
            except (ValueError, TypeError):
                initial_extra_level = 1

        initial_extra_level = max(
            1, min(9, initial_extra_level)
        )
        protected_initial_rank = (
            extra_settings.get("character_category") == "extra"
            and level <= initial_extra_level
        )

        if (
            not protected_initial_rank
            and influence < levels[level - 1][4]
        ):'''

AWARD_FROM = '''            if rank < 1:
                continue

            reward = calculate(board, rank)'''

AWARD_TO = '''            if rank < 1:
                continue

            # 只有角色榜能將entity_key解讀為角色ID。
            character_boards = {
                "impact", "heat", "commercial",
                "public_favor", "pr", "appearance",
                "intellect", "main_skill", "acting",
                "dance", "sing", "hosting", "variety",
                "talent", "fans", "antis", "awareness",
                "wealth", "newcomer",
            }

            if board in character_boards:
                if not entity_key.isdecimal():
                    continue

                ch = db.execute(
                    "SELECT status,settings_json "
                    "FROM characters "
                    "WHERE guild_id=? AND id=?",
                    (str(guild_id), int(entity_key)),
                ).fetchone()

                if ch is None or ch["status"] != "active":
                    continue

                try:
                    character_settings = json.loads(
                        ch["settings_json"] or "{}"
                    )
                    if not isinstance(character_settings, dict):
                        character_settings = {}
                except (TypeError, ValueError):
                    character_settings = {}

                if character_settings.get(
                    "character_category"
                ) == "extra":
                    if character_settings.get(
                        "ranking_eligible", True
                    ) is False:
                        continue

                    cfg = db.execute(
                        "SELECT allow_ranking "
                        "FROM extra_settings "
                        "WHERE guild_id=?",
                        (str(guild_id),),
                    ).fetchone()

                    if (
                        cfg is not None
                        and not bool(cfg["allow_ranking"])
                    ):
                        continue

            reward = calculate(board, rank)'''

AUDIT_CODE = '''"""唯讀稽核舊版路人粉絲、咖位，不修改資料庫。"""
from __future__ import annotations

import json
from core.database import get_connection


def audit(guild_id):
    gid = str(guild_id)
    issues = []

    with get_connection() as db:
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table'"
            )
        }

        if "characters" not in tables:
            return {
                "characters": 0,
                "issues": ["missing characters table"],
            }

        rows = db.execute(
            "SELECT id,settings_json FROM characters "
            "WHERE guild_id=?",
            (gid,),
        ).fetchall()

        count = 0

        for row in rows:
            try:
                settings = json.loads(
                    row["settings_json"] or "{}"
                )
            except (TypeError, ValueError):
                continue

            if not isinstance(settings, dict):
                continue

            if settings.get(
                "character_category"
            ) != "extra":
                continue

            count += 1
            cid = int(row["id"])
            resources = (
                settings.get("initial_resources") or {}
            )
            expected_fans = int(
                resources.get("fans", 0)
            )

            total_fans = None
            loyal_fans = None

            if "extra_character_fan_totals" in tables:
                found = db.execute(
                    "SELECT total_fans "
                    "FROM extra_character_fan_totals "
                    "WHERE guild_id=? AND character_id=?",
                    (gid, cid),
                ).fetchone()
                if found:
                    total_fans = int(found[0])

            if "character_system_tracking" in tables:
                found = db.execute(
                    "SELECT loyal_fans "
                    "FROM character_system_tracking "
                    "WHERE guild_id=? AND character_id=?",
                    (gid, cid),
                ).fetchone()
                if found:
                    loyal_fans = int(found[0])

            if total_fans is None:
                issues.append({
                    "id": cid,
                    "issue": "missing_total_fans",
                    "initial": expected_fans,
                })

            if (
                loyal_fans is not None
                and expected_fans > 0
                and loyal_fans >= expected_fans
            ):
                issues.append({
                    "id": cid,
                    "issue": "possible_initial_fans_as_loyal",
                    "loyal": loyal_fans,
                    "initial": expected_fans,
                })

            if settings.get(
                "initial_status_level"
            ) is None:
                issues.append({
                    "id": cid,
                    "issue": "missing_initial_status_level",
                })

            if "rp_character_status" in tables:
                status = db.execute(
                    "SELECT level FROM rp_character_status "
                    "WHERE guild_id=? AND character_id=?",
                    (gid, cid),
                ).fetchone()

                if status is None:
                    issues.append({
                        "id": cid,
                        "issue": "missing_formal_status",
                    })

    return {
        "characters": count,
        "issues": issues,
        "manual_review_required": len(issues),
    }
'''


def replace_checked(filename, before, after):
    path = MODULES / filename
    original = path.read_text(encoding="utf-8")

    if after in original:
        return f"已套用：{filename}"

    if original.count(before) != 1:
        raise RuntimeError(
            f"{filename} 找不到唯一修改位置，"
            "停止修改，避免覆蓋錯誤版本。"
        )

    updated = original.replace(
        before, after, 1
    )

    ast.parse(
        updated,
        filename=filename,
    )

    backup = path.with_suffix(
        path.suffix + ".before_extra_20261103"
    )

    if not backup.exists():
        shutil.copy2(path, backup)

    path.write_text(
        updated,
        encoding="utf-8",
    )

    return f"完成：{filename}"


def main():
    if not MODULES.is_dir():
        raise RuntimeError(
            "請放在有modules資料夾的專案根目錄"
        )

    ast.parse(AUDIT_CODE)

    checks = (
        (
            "character_status.py",
            STATUS_FROM,
            STATUS_TO,
        ),
        (
            "leaderboard_award_ledger.py",
            AWARD_FROM,
            AWARD_TO,
        ),
    )

    # 先檢查兩份原檔，確認相容才寫入。
    for filename, before, after in checks:
        source = (
            MODULES / filename
        ).read_text(encoding="utf-8")

        if (
            after not in source
            and source.count(before) != 1
        ):
            raise RuntimeError(
                f"{filename}版本不相容，"
                "尚未修改任何檔案。"
            )

    for args in checks:
        print(replace_checked(*args))

    audit_path = (
        MODULES /
        "extra_character_legacy_audit.py"
    )

    if not audit_path.exists():
        audit_path.write_text(
            AUDIT_CODE,
            encoding="utf-8",
        )
        print(
            "新增：extra_character_legacy_audit.py"
        )
    else:
        print(
            "保留現有：extra_character_legacy_audit.py"
        )

    print("✅ 路人整合修正完成")
    print("✅ 舊資料未自動修改")
    print("✅ 排行榜待發獎紀錄不會自動付款")


if __name__ == "__main__":
    main()
