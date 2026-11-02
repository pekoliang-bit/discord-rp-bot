
"""路人角色最後整合修正程式。

使用方式：
    python repair_extra_integrations.py

修正：
1. 路人初始咖位與影響指數的衝突。
2. 路人排行榜獎勵資格檢查。
3. 新增舊粉絲資料稽核模組。

不會直接修改正式資料庫。
不會清除任何玩家或路人粉絲。
不會啟用尚未驗收的排行榜發獎器。
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODULES = ROOT / "modules"


def patch_file(filename, original, replacement):
    """精確替換程式片段，修改前自動備份。"""
    path = MODULES / filename

    if not path.is_file():
        raise FileNotFoundError(
            f"找不到檔案：{path}"
        )

    source = path.read_text(
        encoding="utf-8"
    )

    if replacement in source:
        print(f"✅ 已修正過：{filename}")
        return

    occurrences = source.count(original)

    if occurrences != 1:
        raise RuntimeError(
            f"{filename}：預期找到1個修改位置，"
            f"實際找到{occurrences}個。"
            "已停止修改，避免覆蓋不相容版本。"
        )

    updated = source.replace(
        original,
        replacement,
        1,
    )

    # 先驗證語法，成功後才覆蓋。
    ast.parse(
        updated,
        filename=str(path),
    )

    backup = path.with_suffix(
        path.suffix + ".before_extra_fix"
    )

    if not backup.exists():
        shutil.copy2(path, backup)

    path.write_text(
        updated,
        encoding="utf-8",
    )

    print(f"✅ 已修改：{filename}")


def fix_character_status():
    """初始咖位不因影響指數20立即被降至LV1。

    不修改正式LV1～LV20升級門檻。
    其他地位維持與降級判定照常執行。
    """

    original = '''        influence = attrs.get("影響指數", 0)
        if influence < levels[level - 1][4]:'''

    replacement = '''        influence = attrs.get("影響指數", 0)

        # 路人初始咖位代表既有出道履歷。
        # 不因初始影響指數20而立即降級。
        # 後續正式晉升仍須符合全部門檻。
        try:
            extra_settings = json.loads(
                character["settings_json"] or "{}"
            )
        except (TypeError, ValueError):
            extra_settings = {}

        initial_lv = (
            int(
                extra_settings.get(
                    "initial_status_level"
                ) or 1
            )
            if extra_settings.get(
                "character_category"
            ) == "extra"
            else 1
        )

        extra_initial_protected = (
            extra_settings.get(
                "character_category"
            ) == "extra"
            and 1 <= initial_lv <= 9
            and level <= initial_lv
        )

        if (
            not extra_initial_protected
            and influence < levels[level - 1][4]
        ):'''

    patch_file(
        "character_status.py",
        original,
        replacement,
    )


def fix_leaderboard_rewards():
    """路人獎勵入列前檢查角色資格。

    不影響一般玩家與非角色型榜單。
    本修正只處理待發獎紀錄，不直接付款。
    """

    original = '''            entity_key = str(row["entity_key"])

            if rank < 1:
                continue
'''

    replacement = '''            entity_key = str(row["entity_key"])

            if rank < 1:
                continue

            # 以下榜單的entity_key是角色ID。
            character_boards = {
                "impact",
                "fans",
                "antis",
                "main_skill",
                "heat",
                "commercial",
                "appearance",
                "intellect",
                "acting",
                "dance",
                "sing",
                "hosting",
                "variety",
                "talent",
                "awareness",
                "public_favor",
                "pr",
            }

            if (
                board in character_boards
                and entity_key.isdecimal()
            ):
                char = db.execute(
                    """
                    SELECT status, settings_json
                    FROM characters
                    WHERE guild_id=? AND id=?
                    """,
                    (
                        str(guild_id),
                        int(entity_key),
                    ),
                ).fetchone()

                if (
                    char is None
                    or char["status"] != "active"
                ):
                    continue

                try:
                    cfg = json.loads(
                        char["settings_json"] or "{}"
                    )
                except (TypeError, ValueError):
                    cfg = {}

                if cfg.get(
                    "character_category"
                ) == "extra":
                    setting = db.execute(
                        """
                        SELECT allow_ranking
                        FROM extra_settings
                        WHERE guild_id=?
                        """,
                        (str(guild_id),),
                    ).fetchone()

                    disabled = (
                        setting is not None
                        and not bool(
                            setting["allow_ranking"]
                        )
                    )

                    individual_disabled = (
                        cfg.get(
                            "ranking_eligible",
                            True
                        ) is False
                    )

                    if (
                        disabled
                        or individual_disabled
                    ):
                        continue
'''

    patch_file(
        "leaderboard_award_ledger.py",
        original,
        replacement,
    )


AUDIT_CODE = '''"""舊版路人粉絲與咖位資料稽核。

唯讀檢查，不修改資料庫。
避免誤刪正式獲得的忠實粉絲。
"""

from __future__ import annotations

import json

from core.database import get_connection


def audit(guild_id):
    gid = str(guild_id)

    with get_connection() as db:
        tables = {
            row[0]
            for row in db.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            )
        }

        if "characters" not in tables:
            return {
                "characters": 0,
                "issues": [
                    "characters table missing"
                ],
            }

        rows = db.execute(
            """
            SELECT id,settings_json
            FROM characters
            WHERE guild_id=?
            """,
            (gid,),
        ).fetchall()

        issues = []
        count = 0

        for row in rows:
            try:
                settings = json.loads(
                    row["settings_json"] or "{}"
                )
            except (ValueError, TypeError):
                continue

            if settings.get(
                "character_category"
            ) != "extra":
                continue

            count += 1
            cid = int(row["id"])

            initial = settings.get(
                "initial_resources"
            ) or {}

            expected_fans = int(
                initial.get("fans", 0)
            )

            total_fans = None

            if (
                "extra_character_fan_totals"
                in tables
            ):
                found = db.execute(
                    """
                    SELECT total_fans
                    FROM extra_character_fan_totals
                    WHERE guild_id=?
                    AND character_id=?
                    """,
                    (gid, cid),
                ).fetchone()

                if found:
                    total_fans = int(
                        found["total_fans"]
                    )

            loyal_fans = None

            if (
                "character_system_tracking"
                in tables
            ):
                found = db.execute(
                    """
                    SELECT loyal_fans
                    FROM character_system_tracking
                    WHERE guild_id=?
                    AND character_id=?
                    """,
                    (gid, cid),
                ).fetchone()

                if found:
                    loyal_fans = int(
                        found["loyal_fans"]
                    )

            if total_fans is None:
                issues.append({
                    "character_id": cid,
                    "reason": "missing_total_fans",
                    "initial_fans": expected_fans,
                    "loyal_fans": loyal_fans,
                })

            if (
                loyal_fans is not None
                and expected_fans > 0
                and loyal_fans >= expected_fans
            ):
                issues.append({
                    "character_id": cid,
                    "reason": (
                        "possible_legacy_"
                        "initial_fans_as_loyal"
                    ),
                    "initial_fans": expected_fans,
                    "loyal_fans": loyal_fans,
                })

            if not settings.get(
                "initial_status_level"
            ):
                issues.append({
                    "character_id": cid,
                    "reason": (
                        "initial_status_"
                        "level_missing"
                    ),
                })

    return {
        "characters": count,
        "issues": issues,
        "manual_review_required": len(issues),
    }
'''


def create_legacy_audit():
    filename = (
        "extra_character_legacy_audit.py"
    )
    path = MODULES / filename

    ast.parse(
        AUDIT_CODE,
        filename=str(path),
    )

    if path.exists():
        print(
            f"✅ 檔案已存在，未覆蓋：{filename}"
        )
        return

    path.write_text(
        AUDIT_CODE,
        encoding="utf-8",
    )

    print(f"✅ 已新增：{filename}")


def main():
    if not MODULES.is_dir():
        raise RuntimeError(
            "請將本程式放在機器人專案根目錄，"
            "與modules資料夾同一層。"
        )

    fix_character_status()
    fix_leaderboard_rewards()
    create_legacy_audit()

    print()
    print("✅ 三項程式修正已處理。")
    print("📁 原檔備份副檔名：.before_extra_fix")
    print("⚠️ 尚未啟用排行榜實際發獎。")
    print("⚠️ 舊粉絲資料僅稽核，未自動更動。")


if __name__ == "__main__":
    main()
