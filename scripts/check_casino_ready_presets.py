
"""娛樂城九款獎表與群主開關隔離測試。

執行：
python scripts/check_casino_ready_presets.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    with tempfile.TemporaryDirectory(
        prefix="casino_ready_"
    ) as td:
        os.environ["DATABASE_PATH"] = str(
            Path(td) / "test.sqlite3"
        )

        from core import database
        from modules.casino_repository import (
            get_game,
            list_games,
        )
        from modules.casino_ready_presets import (
            install_all,
            toggle_game,
        )
        from modules.casino_scratch import validate_scratch
        from modules.casino_gacha import validate_gacha
        from modules.casino_service import CasinoError

        database.init_db()
        database.register_guild(
            "9950",
            "隔離測試伺服器",
        )

        first = install_all(
            "9950",
            actor_id="owner",
        )
        assert len(first["created"]) == 9, first
        assert not list_games("9950"), (
            "預設不應自動開放"
        )

        second = install_all(
            "9950",
            actor_id="owner",
        )
        assert len(second["created"]) == 0

        records = list_games(
            "9950",
            enabled_only=False,
        )
        assert len(records) == 9

        for row in records:
            assert row["enabled"] == 0

            if row["kind"] == "scratch":
                try:
                    validate_scratch(row)
                    raise AssertionError(
                        "未核准獎表應拒絕"
                    )
                except CasinoError:
                    pass

            if row["kind"] == "gacha":
                try:
                    validate_gacha(row)
                    raise AssertionError(
                        "未核准獎池應拒絕"
                    )
                except CasinoError:
                    pass

            verified = toggle_game(
                "9950",
                row["code"],
                actor_id="owner",
                enabled=True,
            )

            if row["kind"] == "scratch":
                spec = validate_scratch(verified)
                assert (
                    0 < spec["rtp_ppm"] < 1_000_000
                )
            else:
                spec = validate_gacha(verified)
                assert len(spec["prizes"]) == 5
                assert not spec["has_items"]

        assert len(list_games("9950")) == 9

        disabled = toggle_game(
            "9950",
            "scratch_star",
            actor_id="owner",
            enabled=False,
        )
        assert disabled["enabled"] == 0
        assert len(list_games("9950")) == 8

        print(
            "PASS: 9 款預設、未核准關閉、"
            "重複安裝保護、獎表驗證、開放及停用"
        )


if __name__ == "__main__":
    run()
