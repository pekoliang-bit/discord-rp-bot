"""代言合約固定期限：短期15天、中期30天、長期60天。"""

from __future__ import annotations

import logging
import time

from discord.ext import commands, tasks

from core.database import get_connection, transaction
from modules.endorse import initialize as initialize_endorse

LOG = logging.getLogger(__name__)
DURATION_DAYS = {"短期": 15, "中期": 30, "長期": 60}
REMINDER_SECONDS = 3 * 24 * 3600


def initialize():
    initialize_endorse()
    with get_connection() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(rp_endorsement_contracts)")}
        if "starts_at" not in columns:
            db.execute("ALTER TABLE rp_endorsement_contracts ADD COLUMN starts_at INTEGER")
        if "expires_at" not in columns:
            db.execute("ALTER TABLE rp_endorsement_contracts ADD COLUMN expires_at INTEGER")
        if "reminded_at" not in columns:
            db.execute("ALTER TABLE rp_endorsement_contracts ADD COLUMN reminded_at INTEGER")


def apply_term(guild_id, contract_id):
    """在雙方完成簽約後計算期限；重複執行不重設日期。"""
    initialize()
    with transaction() as db:
        row = db.execute("""SELECT status,contract_type,decided_at,starts_at
            FROM rp_endorsement_contracts WHERE guild_id=? AND id=?""", (str(guild_id), int(contract_id))).fetchone()
        if not row or row["status"] != "active" or row["starts_at"] is not None:
            return
        start = int(row["decided_at"] or time.time())
        days = DURATION_DAYS.get(row["contract_type"])
        end = start + days * 86400 if days else None
        db.execute("""UPDATE rp_endorsement_contracts
            SET starts_at=?,expires_at=? WHERE guild_id=? AND id=? AND starts_at IS NULL""",
            (start, end, str(guild_id), int(contract_id)))


def expiring_contracts(now=None):
    """只讀取已生效、有到期日的合約；不包括單次合約。"""
    initialize()
    now = int(now or time.time())
    with get_connection() as db:
        rows = db.execute("""SELECT c.id,c.guild_id,c.business_id,c.character_id,
            c.expires_at,ch.owner_id AS artist_user_id,b.owner_id AS company_user_id
            FROM rp_endorsement_contracts c
            JOIN characters ch ON ch.guild_id=c.guild_id AND ch.id=c.character_id
            JOIN rp_business_entities b ON b.guild_id=c.guild_id AND b.business_id=c.business_id
            WHERE c.status='active' AND c.expires_at IS NOT NULL
              AND c.reminded_at IS NULL AND c.expires_at>? AND c.expires_at<=?""",
            (now, now + REMINDER_SECONDS)).fetchall()
    return [dict(row) for row in rows]


def mark_reminded(guild_id, contract_id):
    with transaction() as db:
        db.execute("""UPDATE rp_endorsement_contracts SET reminded_at=?
            WHERE guild_id=? AND id=? AND reminded_at IS NULL AND status='active'""",
            (int(time.time()), str(guild_id), int(contract_id)))


def expire_due(now=None):
    """合約到期改為 expired，保留簽約、金流和歷史資料。"""
    initialize()
    now = int(now or time.time())
    with transaction() as db:
        rows = db.execute("""SELECT guild_id,id FROM rp_endorsement_contracts
            WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?""", (now,)).fetchall()
        for row in rows:
            db.execute("""UPDATE rp_endorsement_contracts SET status='expired'
                WHERE guild_id=? AND id=? AND status='active'""", (row["guild_id"], row["id"]))
    return len(rows)


class ContractTermScheduler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.watch.start()

    def cog_unload(self):
        self.watch.cancel()

    @tasks.loop(hours=1)
    async def watch(self):
        # 未能送達時下輪重試；不提前標記提醒完成。
        for row in expiring_contracts():
            delivered = True
            message = (f"📆 代言合約 #{row['id']} 將於 <t:{row['expires_at']}:F> 到期。"
                       "到期後不會自動扣費或續約，需重新取得雙方同意。")
            for user_id in {row["artist_user_id"], row["company_user_id"]}:
                try:
                    user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                    await user.send(message)
                except Exception:
                    delivered = False
                    LOG.warning("代言合約 #%s 到期提醒未完全送達", row["id"])
            if delivered:
                mark_reminded(row["guild_id"], row["id"])
        expire_due()

    @watch.before_loop
    async def before_watch(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    initialize()
    from modules import endorse
    if not getattr(endorse, "_term_hooked", False):
        original_decide = endorse.decide

        def decide_with_term(guild_id, actor_id, contract_id, accept):
            result = original_decide(guild_id, actor_id, contract_id, accept)
            if accept:
                apply_term(guild_id, contract_id)
            return result

        endorse.decide = decide_with_term
        endorse._term_hooked = True
    # 對舊有的有效合約依原簽約時間補上期限，不重置已存在日期。
    with get_connection() as db:
        rows = db.execute("""SELECT guild_id,id FROM rp_endorsement_contracts
            WHERE status='active' AND starts_at IS NULL""").fetchall()
    for row in rows:
        apply_term(row["guild_id"], row["id"])
    await bot.add_cog(ContractTermScheduler(bot))
