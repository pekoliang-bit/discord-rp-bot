
"""娛樂城：角色私人論壇通知派送。

放置位置：modules/casino_private_notice.py

安全規則：
1. 只能派送至群主已配置的角色通知帖子。
2. 所屬論壇不得對 @everyone 公開。
3. 不會自動使用 Discord 私訊。
4. 發送成功後才標記事件完成。
5. 沒有配置、安全驗證失敗時保留 pending。
6. 服務模組，不註冊斜線指令。
"""

from __future__ import annotations

import json
from typing import Any

import discord

from core.database import get_connection, transaction
from modules.casino_effects import (
    init_effects_schema,
    mark_event_completed,
)


def init_private_notice_schema() -> None:
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            casino_private_notice_routes (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                thread_id TEXT NOT NULL,
                forum_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, character_id)
            )
            """
        )


def configure_notice_route(
    guild_id: int,
    character_id: int,
    forum_id: int,
    thread_id: int,
) -> None:
    """只供已授權的官方管理程式呼叫。

    此函式不負責驗證管理員身份；
    玩家互動不得直接呼叫本設定函式。
    """
    for value in (
        guild_id,
        character_id,
        forum_id,
        thread_id,
    ):
        if type(value) is not int or value <= 0:
            raise ValueError("無效的通知設定")

    init_private_notice_schema()

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO casino_private_notice_routes
                (guild_id, character_id,
                 thread_id, forum_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, character_id)
            DO UPDATE SET
                thread_id = excluded.thread_id,
                forum_id = excluded.forum_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                character_id,
                str(thread_id),
                str(forum_id),
            ),
        )


def get_notice_route(
    guild_id: int,
    character_id: int,
) -> dict | None:
    init_private_notice_schema()

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM casino_private_notice_routes
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                str(guild_id),
                character_id,
            ),
        ).fetchone()

    return dict(row) if row else None


def _notification_content(event: dict) -> str | None:
    kind = event["event_kind"]

    try:
        payload = json.loads(
            event.get("payload_json") or "{}"
        )
    except (ValueError, TypeError):
        return None

    if kind == "private_result":
        name = str(
            payload.get("game_name") or "娛樂城"
        )[:80].replace("@", "＠")

        coins = int(
            payload.get("reward_coins") or 0
        )

        return (
            "🎰 **娛樂城結算通知**\n"
            f"遊戲：{name}\n"
            f"金幣獎勵：{coins:,}\n"
            f"交易編號：{payload.get('round_id')}"
        )

    if kind == "risk_review":
        level = str(
            payload.get("risk_level") or "normal"
        )

        names = {
            "normal": "正常",
            "moderate": "中度",
            "high": "高度",
            "severe": "嚴重",
        }

        return (
            "⚠️ **娛樂城過度遊玩提醒**\n"
            f"風險程度：{names.get(level, level)}\n"
            "系統已記錄最近七天的娛樂城活動。\n"
            "角色數值與傳聞效果須依正式規則處理。"
        )

    return None


async def _resolve_safe_thread(
    bot: Any,
    guild_id: int,
    route: dict,
) -> discord.Thread:
    guild = bot.get_guild(guild_id)

    if guild is None:
        raise ValueError("找不到指定伺服器")

    forum_id = int(route["forum_id"])
    thread_id = int(route["thread_id"])

    forum = guild.get_channel(forum_id)

    if forum is None:
        forum = await guild.fetch_channel(forum_id)

    if not isinstance(forum, discord.ForumChannel):
        raise ValueError("通知來源不是論壇頻道")

    # 私人通知不得發至公開論壇。
    everyone = guild.default_role

    if forum.permissions_for(everyone).view_channel:
        raise ValueError(
            "通知論壇對一般成員公開，禁止發送"
        )

    thread = guild.get_thread(thread_id)

    if thread is None:
        thread = await guild.fetch_channel(thread_id)

    if not isinstance(thread, discord.Thread):
        raise ValueError("指定通知位置不是帖子")

    if (
        thread.guild.id != guild_id
        or thread.parent_id != forum_id
    ):
        raise ValueError("通知帖子不屬於指定論壇")

    bot_member = guild.me

    if bot_member is None:
        raise ValueError("無法驗證機器人權限")

    if not thread.permissions_for(bot_member).send_messages_in_threads:
        raise ValueError("機器人無法在帖子發言")

    return thread


async def dispatch_private_notices(
    bot: Any,
    guild_id: int,
    *,
    limit: int = 20,
) -> dict[str, int]:
    """派送私人中獎結果。

    風險事件需要完成獨立效果處理，
    因此此函式不將 risk_review 標為完成。
    """
    if type(guild_id) is not int or guild_id <= 0:
        raise ValueError("伺服器編號不合法")

    init_effects_schema()
    init_private_notice_schema()

    limit = min(max(int(limit), 1), 100)

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM casino_effect_events
            WHERE guild_id = ?
              AND event_kind = 'private_result'
              AND status = 'pending'
            ORDER BY id
            LIMIT ?
            """,
            (str(guild_id), limit),
        ).fetchall()

    sent = 0
    skipped = 0

    for source in rows:
        event = dict(source)
        character_id = int(event["character_id"])

        route = get_notice_route(
            guild_id,
            character_id,
        )

        if route is None:
            skipped += 1
            continue

        content = _notification_content(event)

        if not content:
            skipped += 1
            continue

        try:
            thread = await _resolve_safe_thread(
                bot,
                guild_id,
                route,
            )

            # 檢查交易仍然有效。
            payload = json.loads(event["payload_json"])
            round_id = int(payload["round_id"])

            with get_connection() as conn:
                source_round = conn.execute(
                    """
                    SELECT status
                    FROM casino_rounds
                    WHERE id = ?
                      AND guild_id = ?
                      AND character_id = ?
                    """,
                    (
                        round_id,
                        str(guild_id),
                        character_id,
                    ),
                ).fetchone()

            if (
                source_round is None
                or source_round["status"] != "settled"
            ):
                skipped += 1
                continue

            await thread.send(
                content,
                allowed_mentions=discord.AllowedMentions.none(),
            )

            if mark_event_completed(
                guild_id,
                int(event["id"]),
            ):
                sent += 1

        except (
            discord.DiscordException,
            ValueError,
            TypeError,
            KeyError,
        ):
            # 失敗時保留 pending，不誤標完成。
            skipped += 1

    return {
        "sent": sent,
        "skipped": skipped,
    }
