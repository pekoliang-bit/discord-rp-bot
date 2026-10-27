
"""Discord RP｜B09 特殊道具兌換系統。

指令：
/兌換 申請
/兌換 紀錄
/兌換 待審
/兌換 審核

只新增兌換紀錄表。
不清空角色、銀行或背包資料。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction
from modules.shop import adjust_inventory


LOG = logging.getLogger(__name__)

AUTOMATIC = {"rename", "stage_name"}


def now():
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")


# ==========================================
# 兌換資料表
# ==========================================

def init_db():
    with transaction() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS b09_redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                owner_id TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                action_code TEXT NOT NULL,
                requested_value TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (
                        status IN (
                            'pending',
                            'completed',
                            'rejected'
                        )
                    ),
                operator_id TEXT,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_b09_requests
            ON b09_redemptions (
                guild_id,
                status,
                id
            )
            """
        )


# ==========================================
# 玩家提出兌換
# ==========================================

def submit(gid, uid, cid, product_id, desired):
    desired = desired.strip()

    if not desired or len(desired) > 180:
        raise ValueError(
            "申請內容必須為 1～180 字。"
        )

    with transaction() as db:
        character = db.execute(
            """
            SELECT owner_id, status
            FROM characters
            WHERE guild_id=? AND id=?
            """,
            (gid, cid),
        ).fetchone()

        if not character or character["status"] != "active":
            raise ValueError(
                "角色不存在或未啟用。"
            )

        if str(character["owner_id"]) != str(uid):
            raise ValueError(
                "只有角色擁有者能提出兌換。"
            )

        item = db.execute(
            """
            SELECT c.action_code, p.name
            FROM b09_catalog c
            JOIN shop_products p
              ON p.guild_id=c.guild_id
             AND p.id=c.product_id
            WHERE c.guild_id=?
              AND c.product_id=?
            """,
            (gid, product_id),
        ).fetchone()

        if not item:
            raise ValueError(
                "此商品不是 B09 特殊憑證。"
            )

        bag = db.execute(
            """
            SELECT quantity
            FROM shop_inventory
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
            """,
            (gid, cid, product_id),
        ).fetchone()

        if not bag or int(bag["quantity"]) < 1:
            raise ValueError(
                "背包中沒有這張憑證。"
            )

        pending = db.execute(
            """
            SELECT 1
            FROM b09_redemptions
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
              AND status='pending'
            """,
            (gid, cid, product_id),
        ).fetchone()

        if pending:
            raise ValueError(
                "已有同款憑證正在申請。"
            )

        if (
            item["action_code"] in AUTOMATIC
            and len(desired) > 60
        ):
            raise ValueError(
                "姓名或藝名不能超過 60 字。"
            )

        result = db.execute(
            """
            INSERT INTO b09_redemptions (
                guild_id,
                character_id,
                owner_id,
                product_id,
                action_code,
                requested_value,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                cid,
                str(uid),
                product_id,
                item["action_code"],
                desired,
                now(),
                now(),
            ),
        )

        return (
            f"✅ 已提交兌換申請 #{result.lastrowid}\n"
            f"憑證：{item['name']}\n"
            "目前尚未扣除憑證，等待群主審核。"
        )


# ==========================================
# 查看申請
# ==========================================

def list_requests(
    gid,
    uid,
    cid=None,
    all_pending=False,
):
    with transaction() as db:
        if all_pending:
            rows = db.execute(
                """
                SELECT *
                FROM b09_redemptions
                WHERE guild_id=?
                  AND status='pending'
                ORDER BY id
                LIMIT 15
                """,
                (gid,),
            ).fetchall()

        else:
            if cid is None:
                raise ValueError(
                    "請提供角色編號。"
                )

            character = db.execute(
                """
                SELECT owner_id
                FROM characters
                WHERE guild_id=? AND id=?
                """,
                (gid, cid),
            ).fetchone()

            if (
                not character
                or str(character["owner_id"]) != str(uid)
            ):
                raise ValueError(
                    "只能查看自己角色的紀錄。"
                )

            rows = db.execute(
                """
                SELECT *
                FROM b09_redemptions
                WHERE guild_id=?
                  AND character_id=?
                ORDER BY id DESC
                LIMIT 15
                """,
                (gid, cid),
            ).fetchall()

    if not rows:
        return "目前沒有兌換申請。"

    return "\n".join(
        f"#{r['id']}｜角色 #{r['character_id']}"
        f"｜商品 #{r['product_id']}"
        f"｜{r['action_code']}"
        f"｜{r['status']}"
        f"｜{r['requested_value'][:45]}"
        for r in rows
    )[:1850]


# ==========================================
# 群主審核與實際兌換
# ==========================================

def review(
    gid,
    uid,
    request_id,
    operation,
    note,
):
    if operation not in ("完成", "退回"):
        raise ValueError(
            "只能選擇完成或退回。"
        )

    with transaction() as db:
        request = db.execute(
            """
            SELECT *
            FROM b09_redemptions
            WHERE guild_id=? AND id=?
            """,
            (gid, request_id),
        ).fetchone()

        if request is None:
            raise ValueError(
                "找不到這筆兌換申請。"
            )

        if request["status"] != "pending":
            raise ValueError(
                "這筆申請已經處理，不能重複操作。"
            )

        # 退回時不扣除憑證。
        if operation == "退回":
            db.execute(
                """
                UPDATE b09_redemptions
                SET status='rejected',
                    operator_id=?,
                    note=?,
                    updated_at=?
                WHERE guild_id=?
                  AND id=?
                  AND status='pending'
                """,
                (
                    uid,
                    note,
                    now(),
                    gid,
                    request_id,
                ),
            )

            return (
                f"✅ 已退回申請 #{request_id}\n"
                "憑證沒有扣除。"
            )

        character = db.execute(
            """
            SELECT name, status, settings_json
            FROM characters
            WHERE guild_id=? AND id=?
            """,
            (
                gid,
                request["character_id"],
            ),
        ).fetchone()

        if not character or character["status"] != "active":
            raise ValueError(
                "角色已停用，不能完成兌換。"
            )

        bag = db.execute(
            """
            SELECT quantity
            FROM shop_inventory
            WHERE guild_id=?
              AND character_id=?
              AND product_id=?
            """,
            (
                gid,
                request["character_id"],
                request["product_id"],
            ),
        ).fetchone()

        if not bag or int(bag["quantity"]) < 1:
            raise ValueError(
                "憑證已不在背包，不能完成兌換。"
            )

        action = request["action_code"]
        value = request["requested_value"].strip()

        # ==================================
        # 角色改名卡
        # ==================================

        if action == "rename":
            if not 1 <= len(value) <= 60:
                raise ValueError(
                    "角色名稱長度不合法。"
                )

            db.execute(
                """
                UPDATE characters
                SET name=?,
                    version=version+1
                WHERE guild_id=? AND id=?
                """,
                (
                    value,
                    gid,
                    request["character_id"],
                ),
            )

        # ==================================
        # 角色藝名變更卡
        # ==================================

        elif action == "stage_name":
            settings = json.loads(
                character["settings_json"] or "{}"
            )

            if not isinstance(settings, dict):
                raise ValueError(
                    "角色設定資料格式錯誤。"
                )

            if not 1 <= len(value) <= 60:
                raise ValueError(
                    "藝名長度不合法。"
                )

            settings["stage_name"] = value

            db.execute(
                """
                UPDATE characters
                SET settings_json=?,
                    version=version+1
                WHERE guild_id=? AND id=?
                """,
                (
                    json.dumps(
                        settings,
                        ensure_ascii=False,
                    ),
                    gid,
                    request["character_id"],
                ),
            )

        # ==================================
        # 其他特殊憑證
        # ==================================

        else:
            if not note.strip():
                raise ValueError(
                    "這張憑證需要先人工完成"
                    "對應服務，再於備註填寫"
                    "實際處理結果。"
                )

        # 只有完成操作，才消耗憑證。
        adjust_inventory(
            db,
            gid,
            request["character_id"],
            request["product_id"],
            -1,
        )

        db.execute(
            """
            UPDATE b09_redemptions
            SET status='completed',
                operator_id=?,
                note=?,
                updated_at=?
            WHERE guild_id=?
              AND id=?
              AND status='pending'
            """,
            (
                uid,
                note,
                now(),
                gid,
                request_id,
            ),
        )

    return (
        f"✅ 申請 #{request_id} 已完成！\n"
        "憑證已從背包扣除 1 張。"
    )


# ==========================================
# 群主權限
# ==========================================

def owner(interaction):
    if (
        not interaction.guild
        or interaction.user.id
        != interaction.guild.owner_id
    ):
        raise ValueError(
            "僅限伺服器群主審核兌換。"
        )


# ==========================================
# Discord 斜線指令
# ==========================================

class Redeem(commands.Cog):

    group = app_commands.Group(
        name="兌換",
        description="B09 特殊道具兌換",
        guild_only=True,
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(init_db)

    @group.command(
        name="申請",
        description="使用 B09 憑證提出申請",
    )
    @app_commands.describe(
        角色編號="你的角色編號",
        商品編號="商城中的數字商品編號",
        申請內容="想修改的名稱或兌換事項",
    )
    async def apply(
        self,
        interaction: discord.Interaction,
        角色編號: int,
        商品編號: int,
        申請內容: str,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            result = await asyncio.to_thread(
                submit,
                str(interaction.guild_id),
                str(interaction.user.id),
                角色編號,
                商品編號,
                申請內容,
            )

        except (ValueError, sqlite3.Error) as exc:
            result = f"⚠️ {exc}"

        except Exception:
            LOG.exception("B09 申請失敗")
            result = "⚠️ 系統錯誤，請查看 Railway 日誌。"

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )

    @group.command(
        name="紀錄",
        description="查看自己的特殊憑證申請",
    )
    async def history(
        self,
        interaction: discord.Interaction,
        角色編號: int,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            result = await asyncio.to_thread(
                list_requests,
                str(interaction.guild_id),
                str(interaction.user.id),
                角色編號,
            )

        except (ValueError, sqlite3.Error) as exc:
            result = f"⚠️ {exc}"

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )

    @group.command(
        name="待審",
        description="群主查看待處理憑證申請",
    )
    async def pending(
        self,
        interaction: discord.Interaction,
    ):
        try:
            owner(interaction)
        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        result = await asyncio.to_thread(
            list_requests,
            str(interaction.guild_id),
            str(interaction.user.id),
            None,
            True,
        )

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )

    @group.command(
        name="審核",
        description="群主完成或退回特殊憑證申請",
    )
    @app_commands.describe(
        申請編號="待審清單中的申請編號",
        處理="完成或退回",
        備註="其他憑證必須記錄實際完成的服務",
    )
    @app_commands.choices(
        處理=[
            app_commands.Choice(
                name="完成",
                value="完成",
            ),
            app_commands.Choice(
                name="退回",
                value="退回",
            ),
        ]
    )
    async def decide(
        self,
        interaction: discord.Interaction,
        申請編號: int,
        處理: app_commands.Choice[str],
        備註: str = "",
    ):
        try:
            owner(interaction)
        except ValueError as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            result = await asyncio.to_thread(
                review,
                str(interaction.guild_id),
                str(interaction.user.id),
                申請編號,
                處理.value,
                備註,
            )

        except (ValueError, sqlite3.Error) as exc:
            result = f"⚠️ {exc}"

        except Exception:
            LOG.exception("B09 審核失敗")
            result = "⚠️ 系統錯誤，請查看 Railway 日誌。"

        await interaction.followup.send(
            result[:1900],
            ephemeral=True,
        )


# ==========================================
# 模組載入
# ==========================================

async def setup(bot: commands.Bot):
    await bot.add_cog(Redeem(bot))
