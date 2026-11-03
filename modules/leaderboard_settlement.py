
"""排行榜 14/30 天結算。

安全規則：
1. 已封存的結算只能重用原始快照。
2. 不使用現在的排名補造過去的歷史快照。
3. 30 天獎勵只建立待派發紀錄。
4. 不直接變更玩家金錢或屬性。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta

from core.database import transaction, get_connection
from modules.leaderboard_storage import initialize, audit
from modules.leaderboard_snapshot import collect
from modules.leaderboard_cycle import iso
from modules.leaderboard_award_policy import settle_without_prizes


def _as_utc(value):
    if not isinstance(value, datetime):
        raise TypeError("結算時間必須為 datetime")

    if value.tzinfo is None:
        raise ValueError("結算時間必須包含時區")

    return value.astimezone(timezone.utc)


def _saved_rows(guild_id, cycle, index):
    with get_connection() as db:
        rows = db.execute("""
            SELECT board, rank, entity_key
            FROM lb_v11_snapshots
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
            ORDER BY board, rank, entity_key
        """, (
            str(guild_id),
            cycle,
            int(index),
        )).fetchall()

    grouped = defaultdict(list)

    for row in rows:
        grouped[row["board"]].append({
            "rank": int(row["rank"]),
            "entity_key": str(row["entity_key"]),
        })

    return grouped


def _stage_saved_rewards(guild_id, cycle, index):
    if cycle != "30d":
        return 0

    count = 0
    grouped = _saved_rows(guild_id, cycle, index)

    for board, rows in grouped.items():
        if board == "awards":
            continue

        count += settle_without_prizes(
            guild_id,
            cycle,
            index,
            board,
            rows,
        )

    return count


def _validate_period(guild_id, cycle, index, end_utc):
    if cycle not in ("14d", "30d"):
        raise ValueError("未知結算週期")

    index = int(index)

    if index < 1:
        raise ValueError("結算期數必須大於零")

    end_utc = _as_utc(end_utc)
    now = datetime.now(timezone.utc)

    if end_utc > now:
        raise ValueError("尚未到達結算時間")

    with get_connection() as db:
        config = db.execute("""
            SELECT anchor_utc
            FROM lb_v11_config
            WHERE guild_id = ?
              AND enabled = 1
        """, (str(guild_id),)).fetchone()

    if config is None:
        raise ValueError("排行榜尚未正式啟用")

    anchor = datetime.fromisoformat(
        config["anchor_utc"]
    )

    if anchor.tzinfo is None:
        raise ValueError("排行榜啟用時間缺少時區")

    anchor = anchor.astimezone(timezone.utc)

    days = 14 if cycle == "14d" else 30
    expected_end = anchor + timedelta(days=days * index)

    if abs((expected_end - end_utc).total_seconds()) > 1:
        raise ValueError("結算期數與結束時間不一致")

    return index, end_utc, expected_end


def settle(guild_id, cycle, index, end_utc):
    initialize()

    index, end_utc, expected_end = _validate_period(
        guild_id,
        cycle,
        index,
        end_utc,
    )

    with get_connection() as db:
        old = db.execute("""
            SELECT state
            FROM lb_v11_periods
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
        """, (
            str(guild_id),
            cycle,
            index,
        )).fetchone()

    if old is not None and old["state"] == "snapshotted":
        _stage_saved_rewards(guild_id, cycle, index)

        return {
            "status": "already",
            "cycle": cycle,
            "index": index,
            "unavailable": [],
        }

    now = datetime.now(timezone.utc)

    # 無歷史資料時，不能把現在的排名冒充舊排名。
    # 只容許在本期結束後、下一期結束前收集。
    days = 14 if cycle == "14d" else 30
    next_end = expected_end + timedelta(days=days)

    if now >= next_end:
        audit(guild_id, "historical_snapshot_skipped", {
            "cycle": cycle,
            "index": index,
            "reason": "historical_data_unavailable",
        })

        return {
            "status": "historical_unavailable",
            "cycle": cycle,
            "index": index,
            "boards": 0,
            "unavailable": ["historical_data_unavailable"],
        }

    # 即使尚未進入下一期，這仍是延遲收集的
    # 當前資料，不能聲稱是期末當刻的歷史數值。
    if now - expected_end > timedelta(minutes=5):
        audit(guild_id, "late_snapshot_skipped", {
            "cycle": cycle,
            "index": index,
            "reason": "exact_period_end_snapshot_missing",
        })

        return {
            "status": "late_snapshot_unavailable",
            "cycle": cycle,
            "index": index,
            "boards": 0,
            "unavailable": ["exact_period_end_snapshot_missing"],
        }

    snapshots, unavailable = collect(guild_id)

    with transaction() as db:
        existing = db.execute("""
            SELECT state
            FROM lb_v11_periods
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
        """, (
            str(guild_id),
            cycle,
            index,
        )).fetchone()

        if existing is None:
            db.execute("""
                INSERT INTO lb_v11_periods (
                    guild_id, cycle, idx, end_utc, state
                )
                VALUES (?, ?, ?, ?, 'pending')
            """, (
                str(guild_id),
                cycle,
                index,
                iso(end_utc),
            ))

        for board, rows in snapshots.items():
            for rank, entity, name, score in rows:
                db.execute("""
                    INSERT OR IGNORE INTO lb_v11_snapshots (
                        guild_id, cycle, idx,
                        board, rank, entity_key,
                        display_name, score
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(guild_id),
                    cycle,
                    index,
                    board,
                    int(rank),
                    str(entity),
                    str(name),
                    float(score),
                ))

        db.execute("""
            UPDATE lb_v11_periods
            SET state = 'snapshotted'
            WHERE guild_id = ?
              AND cycle = ?
              AND idx = ?
        """, (
            str(guild_id),
            cycle,
            index,
        ))

    _stage_saved_rewards(guild_id, cycle, index)

    audit(guild_id, "snapshot", {
        "cycle": cycle,
        "index": index,
        "unavailable": unavailable,
    })

    return {
        "status": "snapshotted",
        "cycle": cycle,
        "index": index,
        "boards": len(snapshots),
        "unavailable": unavailable,
    }
