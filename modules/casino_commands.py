"""Discord RP 娛樂城正式指令入口。

包含：
- /娛樂城
- 年滿 20 歲的角色選擇
- 刮刮樂
- 彩票電腦選號購買
- 抽抽樂單抽與十連抽
- 輪盤、骰寶、老虎機
- 簡易二十一點、即開賓果
- 娛樂城排行榜
- 玩家對賭暫停開放

注意：
所有涉及金幣的遊戲，必須先通過遊戲設定審核。
本檔屬 Discord Extension，提供 setup(bot)。
其他 casino_* 服務模組不得被當成 Extension 自動載入。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.database import get_connection
from modules.casino_repository import (
    init_casino_schema,
    list_games,
    list_character_rounds,
    get_game,
)
from modules.casino_lottery import (
    init_lottery_schema,
    buy_lottery,
    _settings as validate_lottery,
)
from modules.casino_effects import (
    init_effects_schema,
    leaderboard,
    queue_settled_round_effects,
)
from modules.casino_scratch import (
    scratch_ticket,
    validate_scratch,
)
from modules.casino_service import CasinoError
from modules.casino_ready_presets import (
    activate_defaults_once, ensure_open_lottery_draws,
)
from modules.casino_maintenance import process_due_lotteries
from modules.casino_admin_ui import CasinoAdminButton, CasinoOwnerOnlyView
from modules.casino_wager_service import play_wager
from modules.casino_gacha import (
    validate_gacha,
    draw_gacha,
    draw_ten,
)


LOG = logging.getLogger(__name__)


def _characters(guild_id: int, user_id: int) -> list[dict]:
    """取得玩家名下年滿 20 歲的已啟用原創角色。"""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, age
            FROM characters
            WHERE guild_id = ?
              AND owner_id = ?
              AND status IN ('active', 'approved')
              AND character_type = 'player'
              AND age >= 20
            ORDER BY id
            LIMIT 25
            """,
            (str(guild_id), str(user_id)),
        ).fetchall()

    return [dict(row) for row in rows]


async def _queue_effect(guild_id: int, round_id: int):
    """交易完成後嘗試建立中獎效果事件。"""

    try:
        await asyncio.to_thread(
            queue_settled_round_effects,
            guild_id,
            round_id,
        )
    except Exception:
        # 中獎效果不能影響已完成的金幣結算。
        # 後續須由排程補處理。
        pass


class ScratchPicker(discord.ui.Select):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        self.character_id = character_id
        self.owner_id = owner_id

        options = [
            discord.SelectOption(
                label=game["name"][:100],
                value=game["code"][:100],
                description=(
                    f"{game['ticket_price']:,} 金幣／張"
                )[:100],
            )
            for game in games[:25]
        ]

        super().__init__(
            placeholder="選擇刮刮樂",
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            code = self.values[0]

            game = await asyncio.to_thread(
                get_game,
                interaction.guild_id,
                code,
            )

            if not game:
                raise CasinoError("找不到票種")

            await asyncio.to_thread(
                validate_scratch,
                game,
            )

            result = await asyncio.to_thread(
                scratch_ticket,
                guild_id=interaction.guild_id,
                player_id=interaction.user.id,
                character_id=self.character_id,
                game_code=code,
                request_key=(
                    f"casino:scratch:{interaction.id}"
                ),
                payment_source="wallet",
            )

            outcome = json.loads(
                result.get("outcome_json") or "{}"
            )

            prize_name = outcome.get(
                "prize_name",
                "銘謝惠顧",
            )

            coins = int(
                result.get("reward_coins") or 0
            )

            await interaction.followup.send(
                f"🎟️ **{game['name']}**\n"
                f"結果：**{prize_name}**\n"
                f"金幣獎勵：{coins:,}\n"
                f"票券紀錄：`{result['id']}`",
                ephemeral=True,
            )

            await _queue_effect(
                interaction.guild_id,
                int(result["id"]),
            )

        except (CasinoError, ValueError) as exc:
            await interaction.followup.send(
                f"購買失敗：{exc}",
                ephemeral=True,
            )

        except Exception:
            await interaction.followup.send(
                "目前無法確認購票結果，"
                "請先檢查交易紀錄，避免重複購買。",
                ephemeral=True,
            )


class ScratchView(discord.ui.View):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.add_item(
            ScratchPicker(
                games,
                character_id,
                owner_id,
            )
        )


class LotteryPicker(discord.ui.Select):
    def __init__(
        self,
        draws: list[dict],
        character_id: int,
        owner_id: int,
    ):
        self.character_id = character_id
        self.owner_id = owner_id

        self.draws = {
            str(draw["id"]): draw
            for draw in draws[:25]
        }

        options = [
            discord.SelectOption(
                label=(
                    f"{draw['name']}｜"
                    f"{draw['draw_code']}"
                )[:100],
                value=str(draw["id"]),
                description=(
                    f"{draw['ticket_price']:,}"
                    " 金幣／注"
                )[:100],
            )
            for draw in draws[:25]
        ]

        super().__init__(
            placeholder="選擇彩票期別",
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        draw = self.draws[self.values[0]]

        await interaction.response.send_message(
            f"🎟️ **{draw['name']}**\n"
            f"期別：{draw['draw_code']}\n"
            f"每注：{draw['ticket_price']:,} 金幣\n"
            "目前使用電腦自動選號。",
            view=LotteryBuyView(
                draw,
                self.character_id,
                self.owner_id,
            ),
            ephemeral=True,
        )


class LotteryBuyView(discord.ui.View):
    def __init__(
        self,
        draw: dict,
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.draw = draw
        self.character_id = character_id
        self.owner_id = owner_id

    @discord.ui.button(
        label="確認購買一注",
        style=discord.ButtonStyle.primary,
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            result = await asyncio.to_thread(
                buy_lottery,
                guild_id=interaction.guild_id,
                player_id=interaction.user.id,
                character_id=self.character_id,
                game_code=self.draw["code"],
                draw_id=int(self.draw["id"]),
                request_key=(
                    f"casino:lottery:{interaction.id}"
                ),
                numbers=None,
                payment_source="wallet",
            )

            numbers = ", ".join(
                map(str, result["numbers"])
            )

            await interaction.followup.send(
                "🎟️ **彩票購買成功**\n"
                f"期別：{self.draw['draw_code']}\n"
                f"號碼：**{numbers}**\n"
                f"票券編號：`{result['round_id']}`",
                ephemeral=True,
            )

        except (CasinoError, ValueError) as exc:
            await interaction.followup.send(
                f"購票失敗：{exc}",
                ephemeral=True,
            )

        except Exception:
            await interaction.followup.send(
                "無法確認購票狀態，"
                "請先查看交易紀錄。",
                ephemeral=True,
            )


class LotteryView(discord.ui.View):
    def __init__(
        self,
        draws: list[dict],
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.add_item(
            LotteryPicker(
                draws,
                character_id,
                owner_id,
            )
        )


def _open_lottery_draws(guild_id: int) -> list[dict]:
    """取得已核准、尚未截止的彩票期別。"""

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                d.id,
                d.draw_code,
                d.cutoff_at,
                g.code,
                g.name,
                g.ticket_price,
                g.kind,
                g.funding_mode,
                g.settings_json
            FROM casino_lottery_draws d
            JOIN casino_games g
              ON g.id = d.game_id
             AND g.guild_id = d.guild_id
            WHERE d.guild_id = ?
              AND g.enabled = 1
              AND d.state = 'selling'
            ORDER BY d.cutoff_at
            LIMIT 100
            """,
            (str(guild_id),),
        ).fetchall()

    found = []

    for row in rows:
        draw = dict(row)

        try:
            validate_lottery(draw)

            cutoff = datetime.fromisoformat(
                draw["cutoff_at"].replace(
                    "Z",
                    "+00:00",
                )
            ).astimezone(timezone.utc)

            if cutoff > datetime.now(timezone.utc):
                found.append(draw)

        except (
            CasinoError,
            ValueError,
            TypeError,
            KeyError,
        ):
            continue

    return found[:25]


class GachaPicker(discord.ui.Select):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        self.character_id = character_id
        self.owner_id = owner_id

        options = [
            discord.SelectOption(
                label=game["name"][:100],
                value=game["code"][:100],
                description=(
                    f"{game['ticket_price']:,}"
                    " 金幣／抽"
                )[:100],
            )
            for game in games[:25]
        ]

        super().__init__(
            placeholder="選擇抽抽樂獎池",
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        game = await asyncio.to_thread(
            get_game,
            interaction.guild_id,
            self.values[0],
        )

        if not game:
            await interaction.response.send_message(
                "獎池已不存在。",
                ephemeral=True,
            )
            return

        try:
            settings = await asyncio.to_thread(
                validate_gacha,
                game,
            )
        except (CasinoError, ValueError) as exc:
            await interaction.response.send_message(
                f"獎池尚未開放：{exc}",
                ephemeral=True,
            )
            return

        allow_ten = not settings["has_items"]

        await interaction.response.send_message(
            f"🎁 **{game['name']}**\n"
            f"每抽 {game['ticket_price']:,} 金幣\n"
            "純金幣獎池可十連抽；"
            "商城商品獎池目前只開放單抽。",
            view=GachaActionView(
                game["code"],
                self.character_id,
                self.owner_id,
                allow_ten,
            ),
            ephemeral=True,
        )


class GachaActionView(discord.ui.View):
    def __init__(
        self,
        code: str,
        character_id: int,
        owner_id: int,
        allow_ten: bool,
    ):
        super().__init__(timeout=180)

        self.code = code
        self.character_id = character_id
        self.owner_id = owner_id

        self.ten.disabled = not allow_ten

    async def _draw(
        self,
        interaction: discord.Interaction,
        ten: bool,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            key = f"casino:gacha:{interaction.id}"

            if ten:
                results = await asyncio.to_thread(
                    draw_ten,
                    guild_id=interaction.guild_id,
                    player_id=interaction.user.id,
                    character_id=self.character_id,
                    game_code=self.code,
                    batch_key=key,
                    payment_source="wallet",
                )

                lines = []

                for result in results:
                    outcome = json.loads(
                        result.get("outcome_json") or "{}"
                    )

                    rarity = outcome.get(
                        "rarity",
                        "?",
                    )

                    coins = int(
                        result.get("reward_coins") or 0
                    )

                    lines.append(
                        f"{rarity}｜{coins:,} 金幣"
                    )

                message = (
                    "🎁 **十連抽結果**\n"
                    + "\n".join(lines)
                )

            else:
                result = await asyncio.to_thread(
                    draw_gacha,
                    guild_id=interaction.guild_id,
                    player_id=interaction.user.id,
                    character_id=self.character_id,
                    game_code=self.code,
                    request_key=key,
                    payment_source="wallet",
                )

                outcome = json.loads(
                    result.get("outcome_json") or "{}"
                )

                rarity = outcome.get(
                    "rarity",
                    "?",
                )

                if outcome.get("type") == "official_shop_item":
                    prize = (
                        "官方商城商品 "
                        f"#{outcome['item_id']} "
                        f"×{outcome.get('quantity', 1)}"
                    )
                else:
                    prize = (
                        f"{int(result.get('reward_coins') or 0):,}"
                        " 金幣"
                    )

                message = (
                    f"🎁 **單抽結果**｜{rarity}\n"
                    f"獎品：{prize}\n"
                    f"紀錄：`{result['id']}`"
                )

                await _queue_effect(
                    interaction.guild_id,
                    int(result["id"]),
                )

            await interaction.followup.send(
                message,
                ephemeral=True,
            )

        except (CasinoError, ValueError) as exc:
            await interaction.followup.send(
                f"抽獎未完成：{exc}",
                ephemeral=True,
            )

        except Exception:
            await interaction.followup.send(
                "無法確認抽獎結果，"
                "請先查看交易紀錄。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="單抽",
        style=discord.ButtonStyle.primary,
    )
    async def single(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._draw(interaction, False)

    @discord.ui.button(
        label="十連抽",
        style=discord.ButtonStyle.secondary,
    )
    async def ten(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._draw(interaction, True)


class GachaView(discord.ui.View):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.add_item(
            GachaPicker(
                games,
                character_id,
                owner_id,
            )
        )


class WagerChoice(discord.ui.Select):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        self.character_id = character_id
        self.owner_id = owner_id

        options = [
            discord.SelectOption(
                label=game["name"][:100],
                value=game["code"][:100],
                description=(
                    f"{game['ticket_price']:,}"
                    " 金幣／局"
                )[:100],
            )
            for game in games[:25]
        ]

        super().__init__(
            placeholder="選擇賭場遊戲",
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        game = await asyncio.to_thread(
            get_game,
            interaction.guild_id,
            self.values[0],
        )

        if (
            not game
            or not game["enabled"]
            or game["kind"] not in (
                "roulette",
                "sicbo",
                "slots",
            )
        ):
            await interaction.response.send_message(
                "遊戲未開放。",
                ephemeral=True,
            )
            return

        settings = json.loads(
            game["settings_json"] or "{}"
        )

        if (
            game["funding_mode"] != "direct"
            or settings.get("rules_verified") is not True
            or settings.get("wager_enabled") is not True
        ):
            await interaction.response.send_message(
                "遊戲尚未核准下注。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"🎰 **{game['name']}**\n"
            f"每局 {game['ticket_price']:,} 金幣\n"
            "目前由角色錢包付款。",
            view=WagerActionView(
                game["code"],
                game["kind"],
                self.character_id,
                self.owner_id,
            ),
            ephemeral=True,
        )


class WagerActionView(discord.ui.View):
    def __init__(
        self,
        code: str,
        kind: str,
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.code = code
        self.kind = kind
        self.character_id = character_id
        self.owner_id = owner_id

        if kind == "roulette":
            options = [
                ("紅色", "red"),
                ("黑色", "black"),
                ("奇數", "odd"),
                ("偶數", "even"),
                ("1～18", "low"),
                ("19～36", "high"),
            ]
        elif kind == "sicbo":
            options = [
                ("小", "small"),
                ("大", "big"),
                ("任意豹子", "any_triple"),
            ]
        else:
            options = [
                ("啟動老虎機", "spin"),
            ]

        self.add_item(
            WagerBetSelect(
                options,
                self,
            )
        )


class WagerBetSelect(discord.ui.Select):
    def __init__(
        self,
        options: list[tuple[str, str]],
        parent_view: WagerActionView,
    ):
        self.parent_view = parent_view

        super().__init__(
            placeholder="選擇下注內容",
            options=[
                discord.SelectOption(
                    label=label,
                    value=value,
                )
                for label, value in options
            ],
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        parent = self.parent_view

        if interaction.user.id != parent.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        choice = (
            ""
            if parent.kind == "slots"
            else self.values[0]
        )

        try:
            result = await asyncio.to_thread(
                play_wager,
                guild_id=interaction.guild_id,
                player_id=interaction.user.id,
                character_id=parent.character_id,
                game_code=parent.code,
                request_key=(
                    f"casino:wager:{interaction.id}"
                ),
                choice=choice,
                payment_source="wallet",
            )

            stake = int(result["ticket_price"])
            payout = int(result["reward_coins"])

            outcome = json.loads(
                result.get("outcome_json") or "{}"
            )

            displayed = str(
                outcome.get(
                    "number",
                    outcome.get(
                        "dice",
                        outcome.get(
                            "symbols",
                            "已結算",
                        ),
                    ),
                )
            )[:120]

            await interaction.followup.send(
                "🎰 **下注結算完成**\n"
                f"遊戲：{parent.code}\n"
                f"下注：{stake:,} 金幣\n"
                f"返還：{payout:,} 金幣\n"
                f"淨損益：{payout - stake:+,} 金幣\n"
                f"結果：`{displayed}`\n"
                f"紀錄：`{result['id']}`",
                ephemeral=True,
            )

            await _queue_effect(
                interaction.guild_id,
                int(result["id"]),
            )

        except (CasinoError, ValueError) as exc:
            await interaction.followup.send(
                f"下注失敗：{exc}",
                ephemeral=True,
            )

        except Exception:
            await interaction.followup.send(
                "無法確認交易狀態，"
                "請檢查紀錄後再試。",
                ephemeral=True,
            )


class WagerView(discord.ui.View):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.add_item(
            WagerChoice(
                games,
                character_id,
                owner_id,
            )
        )


class TableGameSelect(discord.ui.Select):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        self.character_id = character_id
        self.owner_id = owner_id

        options = [
            discord.SelectOption(
                label=game["name"][:100],
                value=game["code"][:100],
                description=(
                    f"{game['ticket_price']:,}"
                    " 金幣／局"
                )[:100],
            )
            for game in games[:25]
        ]

        super().__init__(
            placeholder="選擇簡易牌桌",
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        code = self.values[0]

        await interaction.response.send_message(
            f"🃏 即將使用角色 #{self.character_id}"
            f" 遊玩 {code}。\n"
            "確定後扣除遊戲票價。",
            view=TableConfirmView(
                code,
                self.character_id,
                self.owner_id,
            ),
            ephemeral=True,
        )


class TableConfirmView(discord.ui.View):
    def __init__(
        self,
        code: str,
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=120)

        self.code = code
        self.character_id = character_id
        self.owner_id = owner_id

    @discord.ui.button(
        label="確認遊玩",
        style=discord.ButtonStyle.primary,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            from modules.casino_table_service import play_table

            result = await asyncio.to_thread(
                play_table,
                guild_id=interaction.guild_id,
                player_id=interaction.user.id,
                character_id=self.character_id,
                game_code=self.code,
                request_key=(
                    f"casino:table:{interaction.id}"
                ),
                payment_source="wallet",
            )

            stake = int(result["ticket_price"])
            payout = int(result["reward_coins"])

            outcome = json.loads(
                result.get("outcome_json") or "{}"
            )

            displayed = str(
                outcome.get(
                    "result",
                    outcome.get(
                        "lines",
                        "已開獎",
                    ),
                )
            )[:100]

            await interaction.followup.send(
                f"🃏 **{self.code}**\n"
                f"投注：{stake:,} 金幣\n"
                f"返還：{payout:,} 金幣\n"
                f"淨損益：{payout - stake:+,} 金幣\n"
                f"結果：`{displayed}`\n"
                f"紀錄：`{result['id']}`",
                ephemeral=True,
            )

            await _queue_effect(
                interaction.guild_id,
                int(result["id"]),
            )

        except (CasinoError, ValueError) as exc:
            await interaction.followup.send(
                f"無法開始牌桌：{exc}",
                ephemeral=True,
            )

        except Exception:
            await interaction.followup.send(
                "無法確認牌桌交易狀態，"
                "請先查看紀錄。",
                ephemeral=True,
            )


class TableGameView(discord.ui.View):
    def __init__(
        self,
        games: list[dict],
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.add_item(
            TableGameSelect(
                games,
                character_id,
                owner_id,
            )
        )


class CasinoRankingView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id

    @discord.ui.select(
        placeholder="選擇排行榜",
        options=[
            discord.SelectOption(
                label="淨獲利",
                value="profit",
            ),
            discord.SelectOption(
                label="最高單次獎金",
                value="largest_win",
            ),
            discord.SelectOption(
                label="累積獲獎額",
                value="total_winnings",
            ),
        ],
    )
    async def board(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的排行榜選單。",
                ephemeral=True,
            )
            return

        results = await asyncio.to_thread(
            leaderboard,
            interaction.guild_id,
            select.values[0],
            10,
        )

        lines = [
            (
                f"{index}. "
                f"角色 #{row['character_id']}："
                f"{row['score']:,} 金幣"
            )
            for index, row in enumerate(results, 1)
        ]

        body = "\n".join(lines) or "目前沒有紀錄。"

        await interaction.response.send_message(
            f"🏆 **娛樂城排行榜**\n{body}",
            ephemeral=True,
        )


class CasinoGameMenu(discord.ui.View):
    def __init__(
        self,
        character_id: int,
        owner_id: int,
    ):
        super().__init__(timeout=180)

        self.character_id = character_id
        self.owner_id = owner_id
        self._arrange_buttons()

    def _arrange_buttons(self):
        """同類遊戲排在一起：彩票／抽獎；賭桌；排行榜。"""
        by_label = {
            "刮刮樂": (0, 0), "彩票": (0, 1), "抽抽樂": (0, 2),
            "賭場": (1, 0), "牌桌／賓果": (1, 1),
            "玩家對賭（暫停）": (1, 2), "排行榜": (2, 0),
        }
        for button in self.children:
            if isinstance(button, discord.ui.Button) and button.label in by_label:
                button.row = by_label[button.label][0]
        self._children.sort(key=lambda button: by_label.get(
            getattr(button, "label", None), (3, 0)))

    async def _open(
        self,
        interaction: discord.Interaction,
        kind: str,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        guild_id = interaction.guild_id

        if kind == "lottery":
            draws = await asyncio.to_thread(
                _open_lottery_draws,
                guild_id,
            )

            await interaction.response.send_message(
                (
                    "🎟️ 請選擇開售中的彩票期別。"
                    if draws
                    else "目前沒有開售中的彩票。"
                ),
                view=(
                    LotteryView(
                        draws,
                        self.character_id,
                        self.owner_id,
                    )
                    if draws
                    else None
                ),
                ephemeral=True,
            )
            return

        if kind == "pvp":
            await interaction.response.send_message(
                "🪙 玩家對賭暫停開放。"
                "必須完成對手加入、取消及退款介面"
                "後才會啟用。",
                ephemeral=True,
            )
            return

        if kind in ("wager", "table"):
            categories = (
                ("roulette", "sicbo", "slots")
                if kind == "wager"
                else ("blackjack", "bingo")
            )

            games = []

            for category in categories:
                games.extend(
                    await asyncio.to_thread(
                        list_games,
                        guild_id,
                        kind=category,
                    )
                )

            approved = []

            for game in games:
                try:
                    settings = json.loads(
                        game["settings_json"] or "{}"
                    )

                    if (
                        game["funding_mode"] != "direct"
                        or settings.get("rules_verified") is not True
                        or settings.get("wager_enabled") is not True
                    ):
                        continue

                    if kind == "table":
                        expected = (
                            "auto_stand"
                            if game["kind"] == "blackjack"
                            else "instant_5x5"
                        )

                        if settings.get("table_variant") != expected:
                            continue

                        if not isinstance(
                            settings.get("max_single_reward"),
                            int,
                        ):
                            continue

                    approved.append(game)

                except (
                    ValueError,
                    TypeError,
                    KeyError,
                ):
                    continue

            if kind == "table":
                try:
                    from modules.casino_table_service import play_table
                except ImportError:
                    approved = []

            view = None

            if approved:
                if kind == "wager":
                    view = WagerView(
                        approved[:25],
                        self.character_id,
                        self.owner_id,
                    )
                else:
                    view = TableGameView(
                        approved[:25],
                        self.character_id,
                        self.owner_id,
                    )

            await interaction.response.send_message(
                (
                    f"已核准遊戲：{len(approved)} 種。"
                    if approved
                    else "目前沒有可遊玩的遊戲。"
                ),
                view=view,
                ephemeral=True,
            )
            return

        games = await asyncio.to_thread(
            list_games,
            guild_id,
            kind=kind,
        )

        valid = []

        for game in games:
            try:
                if kind == "scratch":
                    await asyncio.to_thread(
                        validate_scratch,
                        game,
                    )
                else:
                    await asyncio.to_thread(
                        validate_gacha,
                        game,
                    )

                valid.append(game)

            except (
                CasinoError,
                ValueError,
                KeyError,
            ):
                continue

        if kind == "scratch":
            label = "刮刮樂"

            view = (
                ScratchView(
                    valid[:25],
                    self.character_id,
                    self.owner_id,
                )
                if valid
                else None
            )

        else:
            label = "抽抽樂"

            view = (
                GachaView(
                    valid[:25],
                    self.character_id,
                    self.owner_id,
                )
                if valid
                else None
            )

        await interaction.response.send_message(
            (
                f"{label}：{len(valid)} 種已核准獎池。"
                if valid
                else f"{label}目前沒有已開放的獎池。"
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="刮刮樂",
        style=discord.ButtonStyle.primary,
    )
    async def scratch(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._open(interaction, "scratch")

    @discord.ui.button(
        label="彩票",
        style=discord.ButtonStyle.secondary,
    )
    async def lottery(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._open(interaction, "lottery")

    @discord.ui.button(
        label="抽抽樂",
        style=discord.ButtonStyle.success,
    )
    async def gacha(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._open(interaction, "gacha")

    @discord.ui.button(
        label="賭場",
        style=discord.ButtonStyle.secondary,
    )
    async def wager(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._open(interaction, "wager")

    @discord.ui.button(
        label="牌桌／賓果",
        style=discord.ButtonStyle.secondary,
    )
    async def table(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._open(interaction, "table")

    @discord.ui.button(
        label="玩家對賭（暫停）",
        style=discord.ButtonStyle.secondary,
        disabled=True,
    )
    async def pvp(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._open(interaction, "pvp")

    @discord.ui.button(
        label="排行榜",
        style=discord.ButtonStyle.secondary,
    )
    async def ranking(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "🏆 請選擇排行榜。",
            view=CasinoRankingView(self.owner_id),
            ephemeral=True,
        )


class CharacterSelect(discord.ui.Select):
    def __init__(
        self,
        characters: list[dict],
        owner_id: int,
    ):
        self.owner_id = owner_id

        options = [
            discord.SelectOption(
                label=str(character["name"])[:100],
                value=str(character["id"]),
                description=(
                    f"{character['age']} 歲"
                )[:100],
            )
            for character in characters[:25]
        ]

        super().__init__(
            placeholder="選擇年滿20歲的角色",
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這不是你的操作選單。",
                ephemeral=True,
            )
            return

        character_id = int(self.values[0])

        recent = await asyncio.to_thread(
            list_character_rounds,
            interaction.guild_id,
            character_id,
            5,
        )

        await interaction.response.send_message(
            f"🎰 **娛樂城｜角色 #{character_id}**\n"
            f"最近交易：{len(recent)} 筆（最多五筆）\n"
            "請選擇要進入的遊戲。",
            view=CasinoGameMenu(
                character_id,
                self.owner_id,
            ),
            ephemeral=True,
        )


class CharacterView(discord.ui.View):
    def __init__(
        self,
        characters: list[dict],
        owner_id: int,
        *,
        is_guild_owner: bool = False,
    ):
        super().__init__(timeout=180)

        self.add_item(
            CharacterSelect(
                characters,
                owner_id,
            )
        )
        # 群主專屬管理入口；按鈕內部會再次驗證身分。
        if is_guild_owner:
            self.add_item(CasinoAdminButton())


class CasinoCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # 彩票維護只會處理已核准且已到期的期別；不會自動開放遊戲。
        if not self._lottery_maintenance.is_running():
            self._lottery_maintenance.start()

    def cog_unload(self):
        self._lottery_maintenance.cancel()

    @tasks.loop(minutes=5)
    async def _lottery_maintenance(self):
        for guild in list(self.bot.guilds):
            try:
                result = await asyncio.to_thread(process_due_lotteries, guild_id=guild.id)
                await asyncio.to_thread(ensure_open_lottery_draws, guild.id)
                if result.get('errors'):
                    LOG.warning('彩票維護有待檢查的期別：guild=%s result=%s',
                                guild.id, result['errors'])
            except Exception:
                LOG.exception('彩票自動維護失敗：guild=%s', guild.id)

    @_lottery_maintenance.before_loop
    async def _before_lottery_maintenance(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="娛樂城",
        description="開啟角色娛樂城",
    )
    @app_commands.guild_only()
    async def casino(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "請在伺服器內使用娛樂城。",
                ephemeral=True,
            )
            return

        try:
            # 群主已要求內建 17 款遊戲一次性開放；後續人工停用仍然有效。
            launch = await asyncio.to_thread(
                activate_defaults_once, interaction.guild_id, actor_id="system"
            )
            if launch.get("errors"):
                LOG.warning("娛樂城預設開放部分失敗：%s", launch["errors"])
            await asyncio.to_thread(ensure_open_lottery_draws, interaction.guild_id)
            characters = await asyncio.to_thread(
                _characters,
                interaction.guild_id,
                interaction.user.id,
            )

            if not characters:
                if interaction.user.id == interaction.guild.owner_id:
                    await interaction.response.send_message(
                        "🎰 目前沒有符合條件的遊玩角色；群主仍可管理獎池。",
                        view=CasinoOwnerOnlyView(), ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "你目前沒有已啟用、年滿20歲的原創角色，無法使用娛樂城。",
                        ephemeral=True,
                    )
                return

            await interaction.response.send_message(
                "🎰 **娛樂城**\n"
                "請選擇要遊玩的角色。",
                view=CharacterView(
                    characters,
                    interaction.user.id,
                    is_guild_owner=(
                        interaction.guild is not None
                        and interaction.user.id == interaction.guild.owner_id
                    ),
                ),
                ephemeral=True,
            )
            try:
                from modules.task_event_bridge import record_successful_command_interaction
                record_successful_command_interaction(interaction, '娛樂城')
            except Exception:
                pass  # 教學紀錄不得影響原有功能

        except Exception:
            await interaction.response.send_message(
                "娛樂城暫時無法取得角色資料。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await asyncio.to_thread(init_casino_schema)
    await asyncio.to_thread(init_lottery_schema)
    await asyncio.to_thread(init_effects_schema)

    await bot.add_cog(
        CasinoCommands(bot)
    )
