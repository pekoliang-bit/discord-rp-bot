
"""排行榜模組整合檢查。

只檢查匯入，不執行結算或發獎。
"""

import importlib

REQUIRED_MODULES = (
    "modules.leaderboard_catalog",
    "modules.leaderboard_storage",
    "modules.leaderboard_snapshot",
    "modules.leaderboard_settlement",
    "modules.leaderboard_award_ledger",
    "modules.leaderboard_reward_rules",
    "modules.leaderboard_reward_validation",
    "modules.leaderboard_entity_identity",
    "modules.leaderboard_milestone_rules",
    "modules.leaderboard_milestone_storage",
    "modules.leaderboard_milestone_service",
    "modules.leaderboard_points_catalog",
    "modules.leaderboard_points_storage",
    "modules.leaderboard_points_service",
    "modules.leaderboard_points_preview",
    "modules.leaderboard_points_confirmation",
    "modules.leaderboard_title_storage",
    "modules.leaderboard_title_catalog",
    "modules.leaderboard_title_service",
    "modules.leaderboard_title_effects",
    "modules.leaderboard_payout_storage",
    "modules.leaderboard_payout_identity",
    "modules.leaderboard_payout_policy",
    "modules.leaderboard_payout_service",
)


def check_imports():
    results = []

    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
            results.append({
                "module": module_name,
                "status": "ok",
            })
        except Exception as exc:
            results.append({
                "module": module_name,
                "status": "error",
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            })

    failed = [
        item
        for item in results
        if item["status"] != "ok"
    ]

    return {
        "checked": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "ready_for_import": not failed,
        "ready_for_payout": False,
        "results": results,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(
        check_imports(),
        ensure_ascii=False,
        indent=2,
    ))
