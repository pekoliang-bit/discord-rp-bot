"""Discord RP｜玩家互動劇情（/劇情）。

依賴：
- modules.story_builder
- modules.character_forums
- modules.story_episodes

功能：
- 官方角色個人攻略劇情
- 分支選項與連續段落
- 玩家進度保存
- 跨回目檢查
- 每回獨立獎勵
- 完成紀錄
- 前置劇情與章節金幣獎勵
"""
from __future__ import annotations

import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
    get_or_create_account,
)
from core.economy import _payment

from modules.story_episodes import (
    init_db as init_episode_db,
    get_episodes,
    episode_for,
    rewards_parse,
    rewards_needing_character,
    verify_character,
    award_episode_in_tx,
)

LOG = logging.getLogger(__name__)


def init_db():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_story_progress (
                guild_id TEXT NOT NULL,
                story_id INTEGER NOT NULL,
                player_id TEXT NOT NULL,
                segment_no INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'playing'
                    CHECK(status IN ('playing','completed')),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                reward_character_id INTEGER,
                PRIMARY KEY (guild_id, story_id, player_id),
                FOREIGN KEY(story_id)
                    REFERENCES rp_stories(id) ON DELETE CASCADE
            )
        """)

        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(rp_story_progress)"
            ).fetchall()
        }
        if "reward_character_id" not in columns:
            db.execute("""
                ALTER TABLE rp_story_progress
                ADD COLUMN reward_character_id INTEGER
            """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_story_progress_player
            ON rp_story_progress(guild_id, player_id, status)
        """)

    init_episode_db()


def _decode(raw, default):
    try:
        result = json.loads(raw or "null")
    except (ValueError, TypeError):
        raise ValueError(
            "劇情設定資料無法解析，請聯絡群主。"
        ) from None

    return default if result is None else result


def _story_config(story):
    requirements = _decode(
        story["prerequisite_json"], {}
    )
    rewards = _decode(
        story["rewards_json"], []
    )

    if (
        not isinstance(requirements, dict)
        or set(requirements) - {"completed_story_ids"}
    ):
        raise ValueError(
            "劇情觸發條件設定無法辨識，目前條件尚未齊全。"
        )

    needed = requirements.get(
        "completed_story_ids", []
    )

    if (
        not isinstance(needed, list)
        or len(needed) > 20
        or any(
            type(x) is not int or x <= 0
            for x in needed
        )
    ):
        raise ValueError(
            "前置劇情設定不合法。"
        )

    if rewards in ({}, []):
        coins = 0
    elif (
        isinstance(rewards, dict)
        and set(rewards) == {"coins"}
        and type(rewards["coins"]) is int
        and 1 <= rewards["coins"] <= 1000000
    ):
        coins = rewards["coins"]
    else:
        raise ValueError(
            "此劇情包含尚未支援的章節獎勵，"
            "目前條件尚未齊全。"
        )

    return needed, coins


def _check_prerequisites(
    db, guild_id, story_id, player_id, story
):
    needed, coins = _story_config(story)

    if story_id in needed:
        raise ValueError(
            "劇情不能以自己為前置條件。"
        )

    if needed:
        placeholders = ",".join(
            "?" for _ in needed
        )

        rows = db.execute(
            f"""
            SELECT story_id
            FROM rp_story_progress
            WHERE guild_id=?
              AND player_id=?
              AND status='completed'
              AND story_id IN ({placeholders})
            """,
            (
                str(guild_id),
                str(player_id),
                *needed,
            ),
        ).fetchall()

        missing = set(needed) - {
            int(r["story_id"]) for r in rows
        }

        if missing:
            raise ValueError(
                "前置劇情尚未完成："
                + "、".join(
                    "#" + str(x)
                    for x in sorted(missing)
                )
            )

    return coins


def _grant_coins_in_tx(
    db, guild_id, story_id, player_id, amount
):
    """通關及金幣分錄在同一交易中提交。"""
    if not amount:
        return

    source = get_or_create_account(
        db,
        guild_id,
        "system",
        "issuance",
        "treasury",
    )

    receiver = get_or_create_account(
        db,
        guild_id,
        "player",
        str(player_id),
        "wallet",
    )

    _payment(
        db,
        str(guild_id),
        f"rp_story_reward:{story_id}:{player_id}",
        source,
        receiver,
        amount,
        f"劇情 #{story_id} 首次通關獎勵",
    )


def available_stories(guild_id, channel_id):
    """尋找角色論壇對應的已開放劇情。"""
    with get_connection() as db:
        rows = db.execute(
            """
            SELECT
                s.id,
                s.title,
                s.kind,
                s.character_id,
                c.name AS character_name,
                s.prerequisite_json,
                s.rewards_json
            FROM character_forum_links f
            JOIN rp_stories s
              ON s.guild_id=f.guild_id
             AND s.character_id=f.character_id
            JOIN characters c
              ON c.guild_id=s.guild_id
             AND c.id=s.character_id
            WHERE f.guild_id=?
              AND (
                  f.forum_id=?
                  OR f.story_id=?
              )
              AND s.status='ready'
              AND c.status IN ('approved','active')
              AND c.character_type='official_npc'
            ORDER BY
                CASE s.kind
                    WHEN 'main' THEN 0
                    ELSE 1
                END,
                s.id
            """,
            (
                str(guild_id),
                str(channel_id),
                str(channel_id),
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def get_story(guild_id, story_id):
    with get_connection() as db:
        row = db.execute(
            """
            SELECT
                s.id,
                s.title,
                s.status,
                s.prerequisite_json,
                s.rewards_json
            FROM rp_stories s
            JOIN characters c
              ON c.guild_id=s.guild_id
             AND c.id=s.character_id
            WHERE s.guild_id=?
              AND s.id=?
              AND c.character_type='official_npc'
              AND c.status IN ('approved','active')
            """,
            (
                str(guild_id),
                int(story_id),
            ),
        ).fetchone()

    if row is None or row["status"] != "ready":
        raise ValueError(
            "劇情已取消公開或不存在。"
        )

    result = dict(row)
    _story_config(result)
    return result


def get_segment(story_id, number):
    with get_connection() as db:
        row = db.execute(
            """
            SELECT
                segment_no,
                speaker,
                dialogue,
                choices_json,
                next_segment
            FROM rp_story_segments
            WHERE story_id=?
              AND segment_no=?
            """,
            (
                int(story_id),
                int(number),
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "劇情段落不存在，請通知群主檢查。"
        )

    part = dict(row)
    choices = _decode(
        part["choices_json"], []
    )

    if (
        not isinstance(choices, list)
        or len(choices) > 3
    ):
        raise ValueError(
            "選項資料格式不正確。"
        )

    for choice in choices:
        if (
            not isinstance(choice, dict)
            or not isinstance(
                choice.get("text"), str
            )
        ):
            raise ValueError(
                "選項資料格式不正確。"
            )

    part["choices"] = choices
    return part


def next_segment_no(
    db, story_id, part, selected=None
):
    if selected is not None:
        target = part["choices"][selected].get(
            "target"
        )

        if target is not None:
            return int(target)

    if part["next_segment"] is not None:
        return int(part["next_segment"])

    row = db.execute(
        """
        SELECT MIN(segment_no)
        FROM rp_story_segments
        WHERE story_id=?
          AND segment_no>?
        """,
        (
            int(story_id),
            int(part["segment_no"]),
        ),
    ).fetchone()

    return (
        int(row[0])
        if row[0] is not None
        else None
    )


def validate_episode_layout(db, story_id):
    """確認回目編號、起始段落和獎勵設定。"""
    episodes = get_episodes(
        db, int(story_id)
    )

    if not episodes:
        raise ValueError(
            "目前條件尚未齊全：這篇個人攻略劇情尚未建立任何回目。"
            "請官方先設定第一回及起始段落，再開放玩家攻略。"
        )

    if (
        episodes[0]["start_segment"] != 1
        or episodes[0]["episode_no"] != 1
    ):
        raise ValueError(
            "第一回必須從第 1 段開始，"
            "且回目編號必須為 1。"
        )

    segments = db.execute(
        """
        SELECT segment_no
        FROM rp_story_segments
        WHERE story_id=?
        ORDER BY segment_no
        """,
        (int(story_id),),
    ).fetchall()

    numbers = {
        int(row["segment_no"])
        for row in segments
    }

    for idx, episode in enumerate(episodes):
        if episode["episode_no"] != idx + 1:
            raise ValueError(
                "回目編號不連續，"
                "請依序設定第 1、2、3 回。"
            )

        if (
            episode["start_segment"]
            not in numbers
        ):
            raise ValueError(
                f'第 {episode["episode_no"]} '
                "回的起始段落不存在。"
            )

        rewards_parse(
            episode["rewards_json"]
        )

    return episodes


def open_progress(
    guild_id,
    story_id,
    player_id,
    character_id=None,
):
    story = get_story(
        guild_id, story_id
    )

    with transaction() as db:
        _check_prerequisites(
            db,
            guild_id,
            story_id,
            player_id,
            story,
        )

        episodes = validate_episode_layout(
            db, story_id
        )

        bound = db.execute(
            """
            SELECT reward_character_id
            FROM rp_story_progress
            WHERE guild_id=?
              AND story_id=?
              AND player_id=?
            """,
            (
                str(guild_id),
                int(story_id),
                str(player_id),
            ),
        ).fetchone()

        existing_character = (
            bound["reward_character_id"]
            if bound
            else None
        )

        effective_character = (
            existing_character
            if existing_character is not None
            else character_id
        )

        if any(
            rewards_needing_character(
                rewards_parse(
                    episode["rewards_json"]
                )
            )
            for episode in episodes
        ):
            verify_character(
                db,
                guild_id,
                player_id,
                effective_character,
            )

        if character_id is not None:
            verify_character(
                db,
                guild_id,
                player_id,
                character_id,
            )

        db.execute(
            """
            INSERT OR IGNORE
            INTO rp_story_progress
            (
                guild_id,
                story_id,
                player_id,
                segment_no
            )
            VALUES (?,?,?,1)
            """,
            (
                str(guild_id),
                int(story_id),
                str(player_id),
            ),
        )

        row = db.execute(
            """
            SELECT
                segment_no,
                status,
                reward_character_id
            FROM rp_story_progress
            WHERE guild_id=?
              AND story_id=?
              AND player_id=?
            """,
            (
                str(guild_id),
                int(story_id),
                str(player_id),
            ),
        ).fetchone()

        if (
            row["reward_character_id"]
            is not None
            and character_id is not None
            and int(
                row["reward_character_id"]
            ) != int(character_id)
        ):
            raise ValueError(
                "本次攻略已綁定另一隻角色，"
                "無法中途更換獎勵歸屬。"
            )

        if (
            row["reward_character_id"]
            is None
            and character_id is not None
        ):
            db.execute(
                """
                UPDATE rp_story_progress
                SET reward_character_id=?
                WHERE guild_id=?
                  AND story_id=?
                  AND player_id=?
                """,
                (
                    int(character_id),
                    str(guild_id),
                    int(story_id),
                    str(player_id),
                ),
            )

        result = db.execute(
            """
            SELECT
                segment_no,
                status,
                reward_character_id
            FROM rp_story_progress
            WHERE guild_id=?
              AND story_id=?
              AND player_id=?
            """,
            (
                str(guild_id),
                int(story_id),
                str(player_id),
            ),
        ).fetchone()

        return dict(result)


def advance(
    guild_id,
    story_id,
    player_id,
    expected_number,
    choice_index=None,
):
    """原子化檢查劇情進度與回目獎勵。"""
    story = get_story(
        guild_id, story_id
    )

    part = get_segment(
        story_id, expected_number
    )

    if part["choices"]:
        if (
            choice_index is None
            or not 0 <= choice_index < len(
                part["choices"]
            )
        ):
            raise ValueError(
                "請先選擇有效的玩家反應。"
            )

    elif choice_index is not None:
        raise ValueError(
            "此段落沒有選項。"
        )

    with transaction() as db:
        row = db.execute(
            """
            SELECT
                segment_no,
                status,
                reward_character_id
            FROM rp_story_progress
            WHERE guild_id=?
              AND story_id=?
              AND player_id=?
            """,
            (
                str(guild_id),
                int(story_id),
                str(player_id),
            ),
        ).fetchone()

        if (
            not row
            or row["status"] != "playing"
        ):
            raise ValueError(
                "此劇情已結束，或尚未開始。"
            )

        if (
            row["segment_no"]
            != int(expected_number)
        ):
            raise ValueError(
                "劇情進度已更新，"
                "請重新使用 /劇情。"
            )

        coins = _check_prerequisites(
            db,
            guild_id,
            story_id,
            player_id,
            story,
        )

        nxt = next_segment_no(
            db,
            story_id,
            part,
            choice_index,
        )

        episodes = validate_episode_layout(
            db, story_id
        )

        episode = (
            episode_for(
                episodes,
                int(expected_number),
            )
            if episodes
            else None
        )

        if episodes and episode is None:
            raise ValueError(
                "目前段落未屬於任何小劇情，"
                "請聯絡群主。"
            )

        destination = (
            episode_for(
                episodes, int(nxt)
            )
            if nxt is not None and episodes
            else None
        )

        if (
            nxt is not None
            and episodes
            and destination is None
        ):
            raise ValueError(
                "下一段未屬於任何回目。"
            )

        if (
            nxt is not None
            and episode
            and destination["episode_no"]
                < episode["episode_no"]
        ):
            raise ValueError(
                "劇情不能由後面的回目"
                "跳回前面的回目。"
            )

        if (
            nxt is not None
            and episode
            and destination["episode_no"]
                > episode["episode_no"] + 1
        ):
            raise ValueError(
                "分支不能跳過整個回目，"
                "請修正劇情跳轉。"
            )

        if (
            nxt is not None
            and episode
            and destination["episode_no"]
                == episode["episode_no"] + 1
        ):
            if (
                nxt != destination[
                    "start_segment"
                ]
            ):
                raise ValueError(
                    "跨回目時必須從下一回的"
                    "起始段落進入。"
                )

        if (
            nxt is None
            and episodes
            and episode["episode_no"]
                != episodes[-1]["episode_no"]
        ):
            raise ValueError(
                "劇情在最後一回之前結束，"
                "請修正段落連結。"
            )

        if episode and (
            nxt is None
            or destination["episode_no"]
                != episode["episode_no"]
        ):
            summaries = award_episode_in_tx(
                db,
                guild_id,
                int(story_id),
                episode,
                player_id,
                row["reward_character_id"],
            )
        else:
            summaries = []

        if nxt is not None:
            target = db.execute(
                """
                SELECT 1
                FROM rp_story_segments
                WHERE story_id=?
                  AND segment_no=?
                """,
                (
                    int(story_id),
                    int(nxt),
                ),
            ).fetchone()

            if target is None:
                raise ValueError(
                    "分支指向不存在的段落。"
                )

            db.execute(
                """
                UPDATE rp_story_progress
                SET
                    segment_no=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE guild_id=?
                  AND story_id=?
                  AND player_id=?
                """,
                (
                    nxt,
                    str(guild_id),
                    int(story_id),
                    str(player_id),
                ),
            )

            return nxt, summaries

        _grant_coins_in_tx(
            db,
            guild_id,
            story_id,
            player_id,
            coins,
        )

        db.execute(
            """
            UPDATE rp_story_progress
            SET
                status='completed',
                completed_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE guild_id=?
              AND story_id=?
              AND player_id=?
            """,
            (
                str(guild_id),
                int(story_id),
                str(player_id),
            ),
        )

        return None, summaries


async def reply_error(interaction, error):
    message = (
        "⚠️ "
        + discord.utils.escape_mentions(
            str(error)
        )[:1750]
    )

    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


class PlayView(discord.ui.View):
    def __init__(
        self,
        guild_id,
        story_id,
        player_id,
        segment_no,
    ):
        super().__init__(timeout=600)

        self.guild_id = guild_id
        self.story_id = story_id
        self.player_id = player_id
        self.segment_no = segment_no

        self.part = get_segment(
            story_id,
            segment_no,
        )

        if self.part["choices"]:
            self.add_item(
                ChoiceSelect(
                    self.part["choices"]
                )
            )
        else:
            self.add_item(
                ContinueButton()
            )

    async def interaction_check(
        self, interaction
    ):
        if (
            interaction.guild_id
                != self.guild_id
            or interaction.user.id
                != self.player_id
        ):
            await reply_error(
                interaction,
                "這是其他玩家的劇情進度。",
            )
            return False

        return True

    async def run_next(
        self, interaction, selected=None
    ):
        try:
            nxt, earned = advance(
                self.guild_id,
                self.story_id,
                self.player_id,
                self.segment_no,
                selected,
            )

            award_text = (
                "\n\n🎁 **完成本回獎勵：**"
                + "、".join(earned)
                if earned
                else ""
            )

            if nxt is None:
                await interaction.response.edit_message(
                    content=(
                        "🏁 **本章劇情攻略完成！**\n"
                        "通關紀錄已保存。"
                        + award_text
                    ),
                    view=None,
                )
                return

            part = get_segment(
                self.story_id, nxt
            )

            prefix = (
                "✅ **上一回攻略完成！**"
                + award_text
                + "\n\n"
                if award_text
                else ""
            )

            await interaction.response.edit_message(
                content=(
                    prefix
                    + render_segment(part)
                )[:2000],
                view=PlayView(
                    self.guild_id,
                    self.story_id,
                    self.player_id,
                    nxt,
                ),
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await reply_error(
                interaction, exc
            )


def render_segment(part):
    speaker = discord.utils.escape_mentions(
        discord.utils.escape_markdown(
            part["speaker"]
        )
    )

    dialogue = discord.utils.escape_mentions(
        part["dialogue"]
    )

    heading = (
        f'📖 **第 {part["segment_no"]} 段**\n'
        f"**{speaker}：**{dialogue}"
    )

    if part["choices"]:
        heading += (
            "\n\n請從下拉選單"
            "選擇你的反應。"
        )
    else:
        heading += (
            "\n\n點選「繼續」"
            "閱讀下一段。"
        )

    return heading[:1900]


class ChoiceSelect(discord.ui.Select):
    def __init__(self, choices):
        super().__init__(
            placeholder="選擇玩家反應",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=x["text"][:100],
                    value=str(idx),
                )
                for idx, x in enumerate(choices)
            ],
        )

    async def callback(self, interaction):
        await self.view.run_next(
            interaction,
            int(self.values[0]),
        )


class ContinueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="▶️ 繼續",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction):
        await self.view.run_next(
            interaction
        )


class StorySelect(discord.ui.Select):
    def __init__(self, stories):
        super().__init__(
            placeholder="選擇要攻略的劇情",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=row["title"][:100],
                    description=(
                        f'{row["character_name"]}｜'
                        f'{"主線" if row["kind"] == "main" else "支線"}'
                    )[:100],
                    value=str(row["id"]),
                )
                for row in stories[:25]
            ],
        )

    async def callback(self, interaction):
        await self.view.begin(
            interaction,
            int(self.values[0]),
        )


class StoryPickerView(discord.ui.View):
    def __init__(
        self,
        interaction,
        stories,
        character_id=None,
    ):
        super().__init__(timeout=300)

        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.channel_id = interaction.channel_id
        self.character_id = character_id

        self.add_item(
            StorySelect(stories)
        )

    async def interaction_check(
        self, interaction
    ):
        if (
            interaction.user.id
                != self.owner_id
            or interaction.guild_id
                != self.guild_id
            or interaction.channel_id
                != self.channel_id
        ):
            await reply_error(
                interaction,
                "此操作不屬於你。",
            )
            return False

        return True

    async def begin(
        self, interaction, story_id
    ):
        try:
            choices = available_stories(
                self.guild_id,
                self.channel_id,
            )

            if story_id not in {
                row["id"] for row in choices
            }:
                raise ValueError(
                    "此劇情不能在目前頻道開始。"
                )

            progress = open_progress(
                self.guild_id,
                story_id,
                self.owner_id,
                self.character_id,
            )

            if (
                progress["status"]
                == "completed"
            ):
                await interaction.response.edit_message(
                    content=(
                        "✅ 你已完成這篇劇情，"
                        "通關紀錄已保存。"
                    ),
                    view=None,
                )
                return

            part = get_segment(
                story_id,
                progress["segment_no"],
            )

            await interaction.response.edit_message(
                content=render_segment(part),
                view=PlayView(
                    self.guild_id,
                    story_id,
                    self.owner_id,
                    progress["segment_no"],
                ),
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await reply_error(
                interaction, exc
            )


class StoryPlay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        from modules.story_builder import (
            init_db as init_story_db,
        )

        init_story_db()
        init_db()

    @app_commands.command(
        name="劇情條件設定",
        description="群主設定前置劇情與一次性金幣獎勵",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        劇情編號="目標劇情編號",
        前置劇情=(
            "前置劇情 ID，以半形逗號分隔，"
            "留空則不限制"
        ),
        金幣獎勵="0 表示不發放金幣",
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        劇情編號: int,
        前置劇情: str = "",
        金幣獎勵: int = 0,
    ):
        if (
            not interaction.guild
            or interaction.guild.owner_id
                != interaction.user.id
        ):
            return await reply_error(
                interaction,
                "僅伺服器群主可以設定劇情條件。",
            )

        try:
            if (
                金幣獎勵 < 0
                or 金幣獎勵 > 1000000
            ):
                raise ValueError(
                    "金幣獎勵需介於 "
                    "0～1,000,000。"
                )

            needed = [
                int(value.strip())
                for value in 前置劇情.split(",")
                if value.strip()
            ]

            if (
                len(needed) > 20
                or len(set(needed)) != len(needed)
                or any(
                    value <= 0
                    for value in needed
                )
                or 劇情編號 in needed
            ):
                raise ValueError(
                    "前置劇情清單不合法。"
                )

            with transaction() as db:
                target = db.execute(
                    """
                    SELECT
                        status,
                        kind,
                        character_id
                    FROM rp_stories
                    WHERE guild_id=?
                      AND id=?
                    """,
                    (
                        str(interaction.guild_id),
                        劇情編號,
                    ),
                ).fetchone()

                if not target:
                    raise ValueError(
                        "找不到這篇劇情。"
                    )

                if target["kind"] != "side":
                    raise ValueError(
                        "主線觸發規則尚未決定；"
                        "目前僅能設定個人支線劇情。"
                    )

                if target["status"] != "draft":
                    raise ValueError(
                        "請先將劇情改回草稿，"
                        "再修改條件與獎勵。"
                    )

                if needed:
                    marks = ",".join(
                        "?" for _ in needed
                    )

                    count = db.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM rp_stories
                        WHERE guild_id=?
                          AND id IN ({marks})
                        """,
                        (
                            str(interaction.guild_id),
                            *needed,
                        ),
                    ).fetchone()[0]

                    if count != len(needed):
                        raise ValueError(
                            "部分前置劇情不存在於"
                            "本伺服器。"
                        )

                db.execute(
                    """
                    UPDATE rp_stories
                    SET
                        prerequisite_json=?,
                        rewards_json=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE guild_id=?
                      AND id=?
                    """,
                    (
                        json.dumps({
                            "completed_story_ids": needed
                        }),
                        json.dumps(
                            {"coins": 金幣獎勵}
                            if 金幣獎勵
                            else []
                        ),
                        str(interaction.guild_id),
                        劇情編號,
                    ),
                )

            await interaction.response.send_message(
                (
                    f"✅ 劇情 #{劇情編號} "
                    "的條件與獎勵已設定。\n"
                    f"前置劇情：{needed or '無'}\n"
                    f"首次通關金幣：{金幣獎勵}"
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await reply_error(
                interaction, exc
            )

    @app_commands.command(
        name="劇情",
        description="在角色專屬論壇開始或繼續攻略劇情",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        角色編號=(
            "領取物品、專屬禮物及"
            "數值獎勵的本人角色編號（可選）"
        )
    )
    async def play(
        self,
        interaction: discord.Interaction,
        角色編號: int | None = None,
    ):
        from modules.task_event_bridge import require_tutorial_channel, mark_tutorial_after_reply
        allowed, tutorial_character = await require_tutorial_channel(interaction, '劇情')
        if not allowed:
            return
        if tutorial_character is not None:
            # Training is a non-mutating preview, never starts a story or rewards.
            await interaction.response.send_message(
                '📖 劇情中心教學：正式劇情須在對應角色的劇情帖子使用 `/劇情`。'
                '劇情進度、獎勵與物品只會在正式劇情流程中處理。', ephemeral=True,
            )
            mark_tutorial_after_reply(interaction, '劇情', tutorial_character)
            return
        try:
            choices = available_stories(
                interaction.guild_id,
                interaction.channel_id,
            )

            if not choices:
                await interaction.response.send_message(
                    (
                        "⚠️ 這裡沒有已就緒的角色劇情。"
                        "請在對應角色論壇或"
                        "劇情討論貼文使用 /劇情。"
                    ),
                    ephemeral=True,
                )
                return

            if len(choices) == 1:
                story_id = choices[0]["id"]

                progress = open_progress(
                    interaction.guild_id,
                    story_id,
                    interaction.user.id,
                    角色編號,
                )

                if (
                    progress["status"]
                    == "completed"
                ):
                    await interaction.response.send_message(
                        (
                            "✅ 你已完成這篇劇情，"
                            "通關紀錄已保存。"
                        ),
                        ephemeral=True,
                    )
                    return

                part = get_segment(
                    story_id,
                    progress["segment_no"],
                )

                await interaction.response.send_message(
                    render_segment(part),
                    view=PlayView(
                        interaction.guild_id,
                        story_id,
                        interaction.user.id,
                        progress["segment_no"],
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                "📖 選擇要攻略的劇情：",
                view=StoryPickerView(
                    interaction,
                    choices,
                    角色編號,
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await reply_error(
                interaction, exc
            )

        except Exception:
            LOG.exception(
                "玩家劇情指令執行失敗"
            )

            await reply_error(
                interaction,
                "劇情系統異常，請查看 Railway 日誌。",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        StoryPlay(bot)
    )
