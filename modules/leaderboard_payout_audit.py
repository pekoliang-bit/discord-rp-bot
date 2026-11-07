"""排行榜正式派發前稽核。

唯讀，不修改任何資料庫數值。
可用 audit_guild(guild_id) 取得完整報告。
"""

from __future__ import annotations

import json

from core.database import get_connection
from modules.leaderboard_entity_identity import (
    parse_identity,
)
from modules.leaderboard_payout_identity import (
    CHARACTER_BOARDS,
    validate_identity,
)
from modules.leaderboard_reward_validation import (
    validate_reward,
)


def _parse(raw):
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _extra_eligible(db, gid, cid):
    row = db.execute(
        """
        SELECT status,settings_json
        FROM characters
        WHERE guild_id=? AND id=?
        """,
        (gid, cid),
    ).fetchone()

    if row is None or row["status"] != "active":
        return False, "角色不存在或未啟用"

    settings = _parse(row["settings_json"])

    if settings.get("character_category") != "extra":
        return True, ""

    if settings.get("ranking_eligible", True) is False:
        return False, "路人個別排行榜資格已關閉"

    table = db.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='extra_settings'
        """
    ).fetchone()

    if table:
        cfg = db.execute(
            """
            SELECT allow_ranking
            FROM extra_settings
            WHERE guild_id=?
            """,
            (gid,),
        ).fetchone()

        if cfg and not cfg["allow_ranking"]:
            return False, "路人排行榜總開關已關閉"

    return True, ""


def audit_guild(guild_id, limit=10000):
    gid = str(guild_id)
    limit = max(1, min(int(limit), 100000))

    report = {
        "guild_id": gid,
        "total": 0,
        "pending": 0,
        "unconfigured": 0,
        "ready_for_review": 0,
        "issues": [],
    }

    with get_connection() as db:
        table = db.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='lb_v11_awards'
            """
        ).fetchone()

        if not table:
            report["issues"].append({
                "reason": "排行榜獎勵資料表不存在"
            })
            return report

        rows = db.execute(
            """
            SELECT guild_id,cycle,idx,board,
                   entity_key,rank,reward_json,state
            FROM lb_v11_awards
            WHERE guild_id=?
            ORDER BY idx DESC,board,rank
            LIMIT ?
            """,
            (gid, limit),
        ).fetchall()

        report["total"] = len(rows)

        for row in rows:
            state = str(row["state"])
            if state == "pending_adapter":
                report["pending"] += 1
            if state == "unconfigured":
                report["unconfigured"] += 1

            problems = []

            reward = _parse(row["reward_json"])
            board = str(row["board"])
            entity_key = str(row["entity_key"])

            if not reward:
                problems.append("未配置獎勵")
            else:
                try:
                    validate_reward(reward)
                except (ValueError, TypeError, KeyError) as exc:
                    problems.append(str(exc))

                if reward.get("board") != board:
                    problems.append("排行榜種類不一致")

                if reward.get("rank") != int(row["rank"]):
                    problems.append("獎勵名次不一致")

                if reward.get("reward_version") == (
                    "v11-provisional"
                ):
                    problems.append("獎勵仍為暫定版本")

            # 只有真正封存的 30 天期末快照才能進入派獎審核。
            # 以同一 guild/cycle/index/board/entity_key 唯一定位，
            # 不允許 14 天榜單、遺失快照或名次竄改通過。
            if str(row["cycle"]) != "30d":
                problems.append("14天小結算不得派獎")
            period = db.execute(
                """SELECT state FROM lb_v11_periods
                   WHERE guild_id=? AND cycle=? AND idx=?""",
                (gid, row["cycle"], int(row["idx"])),
            ).fetchone()
            if period is None or period["state"] != "snapshotted":
                problems.append("結算期尚未封存")
            snapshot = db.execute(
                """SELECT rank FROM lb_v11_snapshots
                   WHERE guild_id=? AND cycle=? AND idx=?
                     AND board=? AND entity_key=?""",
                (gid, row["cycle"], int(row["idx"]), board, entity_key),
            ).fetchall()
            if not snapshot:
                problems.append("期末快照不存在")
            elif len(snapshot) != 1 or int(snapshot[0]["rank"]) != int(row["rank"]):
                problems.append("期末快照名次不一致")

            try:
                identity = validate_identity(
                    board, entity_key
                )
            except (ValueError, TypeError) as exc:
                identity = None
                problems.append(str(exc))

            if (
                board in CHARACTER_BOARDS
                and identity is not None
            ):
                value = identity["entity_id"]
                if not value.isdecimal():
                    problems.append("角色ID不是整數")
                else:
                    eligible, reason = _extra_eligible(
                        db, gid, int(value)
                    )
                    if not eligible:
                        problems.append(reason)

            if problems:
                report["issues"].append({
                    "cycle": row["cycle"],
                    "index": int(row["idx"]),
                    "board": board,
                    "rank": int(row["rank"]),
                    "entity_key": entity_key,
                    "state": state,
                    "reasons": problems,
                })
            else:
                report["ready_for_review"] += 1

    return report


def summary(guild_id):
    report = audit_guild(guild_id)

    return (
        f"排行榜獎勵稽核｜{guild_id}\n"
        f"總紀錄：{report['total']}\n"
        f"待適配：{report['pending']}\n"
        f"未配置：{report['unconfigured']}\n"
        f"需處理問題：{len(report['issues'])}\n"
        f"通過資料檢查："
        f"{report['ready_for_review']}"
    )
