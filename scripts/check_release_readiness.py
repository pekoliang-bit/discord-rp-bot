
"""Discord RP 部署前總驗收。

執行：
    python -m scripts.check_release_readiness

整合現有檢查工具，提供一個統一入口。

預設只執行唯讀檢查。
不執行評論匯入、不啟動 Discord、
不修改 SQLite 或玩家資料。

執行後產生：
    reports/release_readiness.json
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REPORT_DIR = ROOT / "reports"
REPORT_FILE = REPORT_DIR / "release_readiness.json"


@dataclass(frozen=True)
class CheckDefinition:
    name: str
    module: str
    required: bool
    description: str


CHECKS = (
    CheckDefinition(
        name="project_installation",
        module="scripts.check_project_installation",
        required=True,
        description="專案安裝與 Python 語法",
    ),
    CheckDefinition(
        name="extension_entrypoints",
        module="scripts.check_extension_entrypoints",
        required=True,
        description="Discord 擴充模組 setup 入口",
    ),
    CheckDefinition(
        name="comment_pipeline",
        module="scripts.check_comment_pipeline",
        required=True,
        description="419 則評論與九個 JSON 分檔",
    ),
    CheckDefinition(
        name="rumor_reward",
        module="scripts.check_rumor_reward_pipeline",
        required=True,
        description="傳聞獎懲計算安全",
    ),
    CheckDefinition(
        name="comment_event_rules",
        module="scripts.check_comment_event_rules",
        required=True,
        description="評論事件與授權條件",
    ),
    CheckDefinition(
        name="comment_bridge",
        module="scripts.check_comment_bridge",
        required=True,
        description="評論橋接與預覽安全",
    ),
)


def run_one(check: CheckDefinition) -> dict:
    print()
    print("=" * 64)
    print(f"檢查項目：{check.description}")
    print(f"模組：{check.module}")
    print("=" * 64)

    started = datetime.now(
        timezone.utc
    ).isoformat()

    try:
        module = importlib.import_module(
            check.module
        )

        main: Callable | None = getattr(
            module,
            "main",
            None,
        )

        if main is None or not callable(main):
            raise RuntimeError(
                "找不到可呼叫的 main()"
            )

        result = main()

        if result is None:
            raise RuntimeError(
                "檢查函式沒有回傳狀態碼"
            )

        if type(result) is not int:
            raise TypeError(
                "main() 必須回傳整數狀態碼"
            )

        status = (
            "passed"
            if result == 0
            else "failed"
        )

        detail = (
            "檢查通過"
            if result == 0
            else f"回傳失敗代碼 {result}"
        )

    except Exception as exc:
        status = "failed"
        detail = (
            f"{type(exc).__name__}: {exc}"
        )

        print("[ERROR] 檢查執行失敗")
        traceback.print_exc()

    finished = datetime.now(
        timezone.utc
    ).isoformat()

    print()
    print(
        f"[{status.upper()}] {check.name}"
    )

    return {
        "name": check.name,
        "module": check.module,
        "description": check.description,
        "required": check.required,
        "status": status,
        "detail": detail,
        "started_at": started,
        "finished_at": finished,
    }


def write_report(report: dict) -> None:
    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = REPORT_FILE.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temporary.replace(REPORT_FILE)


def main() -> int:
    print("=" * 64)
    print("Discord RP 正式部署前總驗收")
    print("=" * 64)

    print(
        "本工具只執行檢查，"
        "不匯入模板、不修改角色資料。"
    )

    results: list[dict] = []

    for check in CHECKS:
        result = run_one(check)
        results.append(result)

    required_failures = [
        result
        for result in results
        if result["required"]
        and result["status"] != "passed"
    ]

    optional_failures = [
        result
        for result in results
        if not result["required"]
        and result["status"] != "passed"
    ]

    passed = sum(
        result["status"] == "passed"
        for result in results
    )

    if required_failures:
        overall = "FAILED"
    elif optional_failures:
        overall = "PASS_WITH_WARNINGS"
    else:
        overall = "PASS"

    report = {
        "report_version": 1,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "project": "Discord RP Bot",
        "overall_status": overall,
        "total_checks": len(results),
        "passed_checks": passed,
        "failed_required_checks": len(
            required_failures
        ),
        "failed_optional_checks": len(
            optional_failures
        ),
        "checks": results,
        "notes": [
            "本報告不代表 Discord 正式環境已驗收",
            "未執行正式 SQLite 資料遷移或寫入",
            "未測試 Railway 網路、Token 或 Discord API",
            "未執行任何真實金錢或角色數值結算",
        ],
    }

    print()
    print("=" * 64)
    print("總驗收結果")
    print("=" * 64)

    for result in results:
        symbol = (
            "OK"
            if result["status"] == "passed"
            else "FAIL"
        )

        print(
            f"[{symbol}] "
            f"{result['description']}"
        )

    print("-" * 64)

    print(
        f"通過：{passed}/{len(results)}"
    )

    print(
        f"必要檢查失敗："
        f"{len(required_failures)}"
    )

    print(
        f"選用檢查失敗："
        f"{len(optional_failures)}"
    )

    print(f"總結果：{overall}")

    try:
        write_report(report)
        print(
            f"報告位置：{REPORT_FILE}"
        )
    except OSError as exc:
        print(
            f"[ERROR] 無法寫入檢查報告：{exc}"
        )
        return 1

    if required_failures:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
