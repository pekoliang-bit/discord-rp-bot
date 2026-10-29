"""角色地位 LV1-20：獨立資料層、評分、維持進度與通知排程。

放入 modules/character_status.py；不註冊額外斜線指令。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)

# LV / 名稱 / 熱度 / 商業價值 / 影響指數
LEVELS = (
    (1, "籍籍無名", 0, 0, 0),
    (2, "十八線藝人", 100, 100, 50),
    (3, "十五線藝人", 500, 300, 100),
    (4, "十二線藝人", 1000, 500, 250),
    (5, "人氣新秀", 2000, 1000, 500),
    (6, "八線藝人", 3500, 2000, 750),
    (7, "小有名氣", 5000, 3000, 1000),
    (8, "人氣新星", 8000, 5000, 2000),
    (9, "五線藝人", 12000, 8000, 3000),
    (10, "四線藝人", 20000, 15000, 5000),
    (11, "三線藝人", 35000, 25000, 8000),
    (12, "二線藝人", 50000, 40000, 12000),
    (13, "準一線藝人", 80000, 60000, 20000),
    (14, "一線藝人", 120000, 100000, 30000),
    (15, "人氣頂流", 200000, 150000, 50000),
    (16, "國民巨星", 350000, 250000, 80000),
    (17, "國際巨星", 500000, 400000, 120000),
    (18, "世界巨星", 800000, 650000, 200000),
    (19, "傳奇人物", 1200000, 1000000, 300000),
    (20, "業界傳說", 2000000, 1500000, 500000),
)

WORK_MULTIPLIERS = (
    1, 1.05, 1.1, 1.15, 1.25, 1.35, 1.5, 1.65, 1.85, 2,
    2.25, 2.5, 2.8, 3.2, 3.75, 4.5, 5.5, 7, 9, 12,
)
ENDORSEMENT_MAX_TIER = (1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 9, 9, 9)
ENDORSEMENT_MIN_FEE = tuple(15000 * 2 ** i for i in range(10))



def world_status_rules(guild_id, db=None):
    """讀取此伺服器已保存的世界觀設定；無設定時使用既有規則。"""
    try:
        if db is None:
            with get_connection() as connection:
                row = connection.execute(
                    "SELECT config_json FROM world_configs WHERE guild_id=?",
                    (str(guild_id),)
                ).fetchone()
        else:
            row = db.execute(
                "SELECT config_json FROM world_configs WHERE guild_id=?",
                (str(guild_id),)
            ).fetchone()
        data = json.loads(row[0]) if row else {}
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        LOG.warning("Invalid world config for guild %s", guild_id)
        return {}
    except Exception as exc:
        if "no such table" not in str(exc).lower():
            LOG.exception("Cannot load world status settings")
        return {}


def effective_levels(guild_id, config=None):
    """只覆寫名稱、熱度及商業價值門檻；影響指數維持特殊管制。"""
    config = config if config is not None else world_status_rules(guild_id)
    overrides = config.get("status_levels", {})
    if not isinstance(overrides, dict):
        return LEVELS
    result = []
    for lv, name, heat, commercial, influence in LEVELS:
        rule = overrides.get(str(lv), {})
        if isinstance(rule, dict) and rule.get("enabled", True):
            name = str(rule.get("name") or name)[:80]
            for field, old in (("heat_min", heat), ("commercial_min", commercial)):
                raw = rule.get(field, old)
                if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                    if field == "heat_min":
                        heat = raw
                    else:
                        commercial = raw
        result.append((lv, name, heat, commercial, influence))
    return tuple(result)


def enabled_stat(guild_id, key, config=None):
    if key == "影響指數":
        return True  # 特殊管制數值不可透過一般數值開關停用
    rules = (config if config is not None else world_status_rules(guild_id)).get("stats", {})
    rule = rules.get(key, {}) if isinstance(rules, dict) else {}
    return rule.get("enabled", True) is not False if isinstance(rule, dict) else True

def utcnow():
    return datetime.now(timezone.utc)


def parse_time(raw):
    if not raw:
        return None
    try:
        result = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def normalized(actual, target):
    return 100.0 if target <= 0 else clamp(actual * 100.0 / target)


def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_character_status (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            maintenance REAL NOT NULL DEFAULT 100,
            last_review TEXT, last_promotion TEXT, warning_active INTEGER NOT NULL DEFAULT 0,
            main_skill_value INTEGER NOT NULL DEFAULT 0,
            main_skill_changed_at TEXT,
            stagnation_notified_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, character_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_character_status_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL, created_at TEXT NOT NULL,
            event TEXT NOT NULL, old_level INTEGER, new_level INTEGER,
            detail TEXT NOT NULL DEFAULT ''
        )""")


def _attributes(db, guild_id, character_id):
    rows = db.execute("""SELECT name, value FROM attributes
        WHERE guild_id=? AND character_id=?""", (str(guild_id), character_id)).fetchall()
    return {str(row["name"]): max(0, int(row["value"] or 0)) for row in rows}


def _tracking(db, guild_id, character_id):
    exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='character_system_tracking'").fetchone()
    if not exists:
        return {"loyal_fans": 0, "active_antis": 0, "public_awareness": 0}
    row = db.execute("""SELECT loyal_fans, active_antis, public_awareness
        FROM character_system_tracking WHERE guild_id=? AND character_id=?""", (str(guild_id), character_id)).fetchone()
    result = dict(row) if row else {"loyal_fans": 0, "active_antis": 0, "public_awareness": 0}
    award_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_award_grants'").fetchone()
    if award_table:
        result["award_count"] = db.execute(
            "SELECT COUNT(*) AS n FROM rp_award_grants WHERE guild_id=? AND character_id=? AND revoked=0",
            (str(guild_id), character_id),
        ).fetchone()["n"]
    else:
        result["award_count"] = 0
    return result


def _main_skill(character, attrs):
    try:
        settings = json.loads(character["settings_json"] or "{}")
    except (ValueError, TypeError):
        settings = {}
    track = str(settings.get("main_track") or "")
    aliases = {"演藝": "演繹", "演技": "演繹", "演員": "演繹", "唱歌": "歌唱", "舞蹈": "舞蹈", "主持": "主持", "綜藝": "綜藝"}
    key = aliases.get(track, track)
    return key, attrs.get(key, 0)


def _auxiliary(attrs, tracking, level, levels=LEVELS):
    # 參考值是可調的評分基準，不會改動玩家屬性或創角上限。
    fans = max(0, int(tracking.get("loyal_fans") or 0))
    antis = max(0, int(tracking.get("active_antis") or 0))
    anti_score = 100.0 * fans / max(1, fans + antis)
    score = (
        0.15 * normalized(fans, max(100, levels[level - 1][2] * 10))
        + 0.10 * anti_score
        + 0.15 * normalized(attrs.get("路人緣", 0), max(100, level * 50))
        + 0.15 * normalized(attrs.get("公關", 0), max(100, level * 50))
        + 0.15 * normalized(tracking.get("public_awareness", 0), max(100, level * 100))
        + 0.10 * normalized(max((attrs.get(k, 0) for k in ("歌唱", "舞蹈", "演繹", "主持", "綜藝")), default=0), max(100, level * 30))
        + 0.05 * normalized(attrs.get("個人才藝", 0), max(100, level * 20))
        + 0.10 * normalized(tracking.get("award_count", 0), max(1, level // 3))
        + 0.025 * normalized(attrs.get("容貌", 0), 100)
        + 0.025 * normalized(attrs.get("才智", 0), 100)
    )
    return clamp(score)


def score_for(level, attrs, tracking, skill_value, days_stale=0, *, levels=LEVELS, disabled=frozenset()):
    _, _, heat_min, commercial_min, influence_min = levels[level - 1]
    heat, commercial, influence = (attrs.get("流量熱度", 0), attrs.get("商業價值", 0), attrs.get("影響指數", 0))
    hard_pass = (("流量熱度" in disabled or heat >= heat_min) and ("商業價值" in disabled or commercial >= commercial_min) and influence >= influence_min)
    professional_target = max(35, level * 25)
    ability = normalized(skill_value, professional_target)
    growth = 100 if days_stale < 14 or ability >= 100 else 0
    # 無技能養成歷史時，平衡分先以中性值處理；後續由技能模組提供精確成長資料。
    professional = 0.5 * ability + 0.3 * growth + 0.2 * 50
    aux = _auxiliary(attrs, tracking, level, levels)
    if level >= 19:
        weights = (0.50, 0.15, 0.10, 0.15, 0.10)
    else:
        weights = (0.35, 0.20, 0.15, 0.15, 0.15)
    values = (normalized(influence, influence_min), 100 if "流量熱度" in disabled else normalized(heat, heat_min), 100 if "商業價值" in disabled else normalized(commercial, commercial_min), professional, aux)
    score = sum(w * v for w, v in zip(weights, values))
    return round(score, 2), hard_pass


def get_status(guild_id, character_id):
    initialize()
    with get_connection() as db:
        row = db.execute("SELECT * FROM rp_character_status WHERE guild_id=? AND character_id=?", (str(guild_id), int(character_id))).fetchone()
    return dict(row) if row else {"level": 1, "maintenance": 100, "warning_active": 0}


def minimum_work_pay(base_pay, level):
    return round(max(0, int(base_pay)) * WORK_MULTIPLIERS[max(1, min(20, int(level))) - 1])


def minimum_endorsement_fee(tier):
    if not 1 <= int(tier) <= 10:
        raise ValueError("代言級別需介於 T1～T10")
    return ENDORSEMENT_MIN_FEE[int(tier) - 1]


def review_character(guild_id, character_id, *, now=None):
    """每次只結算已經經過的完整 24 小時；影響不足即刻降級。

    回傳要發送的通知文字；本函式本身不發 Discord API。
    """
    now = now or utcnow()
    guild_id, character_id = str(guild_id), int(character_id)
    messages = []
    with transaction() as db:
        character = db.execute("SELECT * FROM characters WHERE guild_id=? AND id=? AND status='active'", (guild_id, character_id)).fetchone()
        if not character:
            return []
        db.execute("INSERT OR IGNORE INTO rp_character_status(guild_id, character_id) VALUES (?, ?)", (guild_id, character_id))
        state = dict(db.execute("SELECT * FROM rp_character_status WHERE guild_id=? AND character_id=?", (guild_id, character_id)).fetchone())
        attrs, tracking = _attributes(db, guild_id, character_id), _tracking(db, guild_id, character_id)
        skill_name, skill_value = _main_skill(character, attrs)
        changed_at = parse_time(state["main_skill_changed_at"]) or now
        if skill_value != state["main_skill_value"]:
            changed_at = now
            state["stagnation_notified_at"] = None
        stale_days = max(0, (now - changed_at).days)
        level = max(1, min(20, int(state["level"])))
        original_level = level
        config = world_status_rules(guild_id, db=db)
        levels = effective_levels(guild_id, config=config)
        disabled = frozenset(
            key for key in ("流量熱度", "商業價值")
            if not enabled_stat(guild_id, key, config=config)
        )
        maintenance = float(state["maintenance"])
        last_review = parse_time(state["last_review"]) or now
        elapsed_days = max(0, int((now - last_review).total_seconds() // 86400))
        # 影響指數優先：直接降至影響門檻所允許的最高地位。
        influence = attrs.get("影響指數", 0)
        if influence < levels[level - 1][4]:
            level = max((i for i in range(1, level + 1) if influence >= levels[i - 1][4]), default=1)
            maintenance = 100
            state["warning_active"] = 0
            messages.append(f"🚨 影響指數不足：地位立即下降至 LV{level} {levels[level-1][1]}。")
        score, hard_pass = score_for(level, attrs, tracking, skill_value, stale_days, levels=levels, disabled=disabled)
        other_ok = ("流量熱度" in disabled or attrs.get("流量熱度", 0) >= levels[level-1][2]) and ("商業價值" in disabled or attrs.get("商業價值", 0) >= levels[level-1][3]) and score >= 80
        if not other_ok and level > 1:
            if not state["warning_active"]:
                messages.append(f"⚠️ 地位維持提醒：目前 LV{level} 未達維持條件，每滿24小時扣除30%維持進度。")
            state["warning_active"] = 1
            for _ in range(min(elapsed_days, 365)):
                maintenance -= 30
                if maintenance <= 0 and level > 1:
                    level -= 1
                    maintenance = 100
                    messages.append(f"📉 地位下降至 LV{level} {levels[level-1][1]}。")
                    score, hard_pass = score_for(level, attrs, tracking, skill_value, stale_days, levels=levels, disabled=disabled)
                    if hard_pass and score >= 80:
                        break
        else:
            if state["warning_active"]:
                messages.append("✅ 地位維持條件已恢復，警告解除。")
            state["warning_active"] = 0
            maintenance = 100
        promotion_at = parse_time(state["last_promotion"])
        # 預設不可跳級：僅評估下一級，且每次晉升至少相隔24小時。
        # 下一級若停用則停止晉升，不越過該級。
        if level < 20 and not state["warning_active"] and (not promotion_at or now - promotion_at >= timedelta(hours=24)):
            next_score, next_ok = score_for(level + 1, attrs, tracking, skill_value, stale_days, levels=levels, disabled=disabled)
            next_rule = config.get("status_levels", {}).get(str(level + 1), {})
            next_enabled = not (isinstance(next_rule, dict) and next_rule.get("enabled") is False)
            if next_enabled and next_ok and next_score >= 80:
                level += 1
                state["last_promotion"] = now.isoformat()
                messages.append(f"🌟 地位晉升至 LV{level} {levels[level-1][1]}（綜合評分 {next_score}）。")
        if skill_name and stale_days >= 14 and not state["stagnation_notified_at"]:
            messages.append(f"📋 專業成長提醒：主攻「{skill_name}」已連續{stale_days}天沒有提升。")
            state["stagnation_notified_at"] = now.isoformat()
        # 每個通知寫入唯一事件紀錄，保留稽核資料。
        for message in messages:
            db.execute("INSERT INTO rp_character_status_events(guild_id, character_id, created_at, event, old_level, new_level, detail) VALUES(?,?,?,?,?,?,?)", (guild_id, character_id, now.isoformat(), "status_review", original_level, level, message))
        db.execute("""UPDATE rp_character_status SET level=?, maintenance=?, last_review=?, last_promotion=?,
            warning_active=?, main_skill_value=?, main_skill_changed_at=?, stagnation_notified_at=?, updated_at=?
            WHERE guild_id=? AND character_id=?""", (
            level, clamp(maintenance), (last_review + timedelta(days=elapsed_days)).isoformat(),
            state["last_promotion"], int(state["warning_active"]), skill_value, changed_at.isoformat(),
            state["stagnation_notified_at"], now.isoformat(), guild_id, character_id,
        ))
    return messages


class CharacterStatus(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.status_scheduler.start()

    def cog_unload(self):
        self.status_scheduler.cancel()

    @tasks.loop(minutes=30)
    async def status_scheduler(self):
        with get_connection() as db:
            rows = db.execute("SELECT guild_id, id FROM characters WHERE status='active'").fetchall()
        for row in rows:
            try:
                guild_id, cid = str(row["guild_id"]), int(row["id"])
                messages = await asyncio.to_thread(review_character, guild_id, cid)
                if not messages:
                    continue
                with get_connection() as db:
                    link_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='character_forum_links'").fetchone()
                    link = (db.execute("SELECT notice_id FROM character_forum_links WHERE guild_id=? AND character_id=?", (guild_id, cid)).fetchone() if link_table else None)
                if not link or not link["notice_id"]:
                    LOG.warning("角色 %s 通知貼文尚未建立，通知已保留在 rp_character_status_events", cid)
                    continue
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    continue
                channel = guild.get_thread(int(link["notice_id"]))
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(int(link["notice_id"]))
                    except (discord.NotFound, discord.Forbidden):
                        continue
                for message in messages:
                    await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                LOG.exception("地位結算失敗：角色 %s", row["id"])

    @status_scheduler.before_loop
    async def before_status_scheduler(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await asyncio.to_thread(initialize)
    await bot.add_cog(CharacterStatus(bot))
