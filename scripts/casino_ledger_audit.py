
"""娛樂城資料庫唯讀帳務檢查。

放置：scripts/casino_ledger_audit.py

使用：
python scripts/casino_ledger_audit.py /path/to/bot.sqlite3

不建立資料表、不寫入交易，
也不會顯示玩家個人資料。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def audit(path: Path) -> int:
    if not path.is_file():
        print("❌ 找不到資料庫：", path)
        return 2

    uri = path.resolve().as_uri() + "?mode=ro"
    problems = []

    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row

        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table'"
            )
        }

        required = {
            "casino_games",
            "casino_rounds",
            "casino_rewards",
        }

        missing = required - names

        if missing:
            print(
                "⚠️ 娛樂城尚未初始化：",
                ", ".join(sorted(missing)),
            )
            return 2

        checks = [
            (
                "重複購買請求",
                """
                SELECT guild_id, request_key, COUNT(*) AS n
                FROM casino_rounds
                GROUP BY guild_id, request_key
                HAVING COUNT(*) > 1
                """,
            ),
            (
                "完成但未有結算時間",
                """
                SELECT id
                FROM casino_rounds
                WHERE status='settled'
                  AND settled_at IS NULL
                """,
            ),
            (
                "未完成卻有獎金",
                """
                SELECT id
                FROM casino_rounds
                WHERE status!='settled'
                  AND reward_coins > 0
                """,
            ),
            (
                "負數或無效獎金",
                """
                SELECT id
                FROM casino_rounds
                WHERE ticket_price <= 0
                   OR reward_coins < 0
                """,
            ),
            (
                "已發金幣獎勵與回合金額不一致",
                """
                SELECT r.id
                FROM casino_rounds r
                LEFT JOIN casino_rewards a
                  ON a.guild_id=r.guild_id
                 AND a.round_id=r.id
                 AND a.reward_type='coins'
                 AND a.state='delivered'
                GROUP BY r.guild_id, r.id
                HAVING r.status='settled'
                   AND r.reward_coins > 0
                   AND COALESCE(SUM(a.quantity), 0)
                       <> r.reward_coins
                """,
            ),
            (
                "重複獎品編號",
                """
                SELECT
                    guild_id,
                    round_id,
                    reward_key,
                    COUNT(*) AS n
                FROM casino_rewards
                GROUP BY guild_id, round_id, reward_key
                HAVING COUNT(*) > 1
                """,
            ),
        ]

        for title, query in checks:
            try:
                rows = conn.execute(query).fetchmany(5)

                if rows:
                    problems.append(title)
                    print(
                        f"❌ {title}：至少 "
                        f"{len(rows)} 筆異常"
                    )
                else:
                    print("✅", title)

            except sqlite3.Error as exc:
                problems.append(title)
                print(
                    f"❌ 無法檢查 {title}：{exc}"
                )

        pending = conn.execute(
            """
            SELECT COUNT(*)
            FROM casino_rounds
            WHERE status='pending'
            """
        ).fetchone()[0]

        print(
            "ℹ️ 尚待完成的回合：",
            pending,
        )

    print(
        "唯讀檢查結束；"
        "不代表已完成 Discord 實機測試。"
    )

    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "用法：python "
            "scripts/casino_ledger_audit.py "
            "/path/to/bot.sqlite3"
        )
        raise SystemExit(2)

    raise SystemExit(
        audit(Path(sys.argv[1]))
    )
