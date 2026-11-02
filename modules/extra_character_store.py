
"""路人資料庫與批次建立：照片唯一使用、正式屬性與地位整合。"""
from __future__ import annotations

import json
import random

from core.database import get_connection, transaction

DEFAULT_JOBS = (
    "演員", "舞者", "歌手", "偶像", "導演",
    "模特兒", "編劇", "主持人", "配音員",
    "編舞師", "製作人", "網紅", "綜藝藝人",
)


def initialize(gid=None):
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS extra_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                url TEXT NOT NULL,
                used_character_id INTEGER,
                UNIQUE(guild_id,url)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS extra_photo_meta (
                photo_id INTEGER PRIMARY KEY,
                gender TEXT NOT NULL,
                age_group TEXT NOT NULL,
                source_channel_id TEXT,
                source_message_id TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS extra_photo_channels (
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                gender TEXT NOT NULL,
                age_group TEXT NOT NULL,
                PRIMARY KEY(guild_id,channel_id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS extra_name_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                nationality TEXT NOT NULL,
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                UNIQUE(guild_id,nationality,kind,value)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS extra_jobs (
                guild_id TEXT NOT NULL,
                job TEXT NOT NULL,
                PRIMARY KEY(guild_id,job)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS extra_settings (
                guild_id TEXT PRIMARY KEY,
                allow_ranking INTEGER NOT NULL DEFAULT 1,
                min_age INTEGER NOT NULL DEFAULT 18,
                max_age INTEGER NOT NULL DEFAULT 65,
                min_level INTEGER NOT NULL DEFAULT 1,
                max_level INTEGER NOT NULL DEFAULT 9
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS extra_seeded_guilds (
                guild_id TEXT PRIMARY KEY
            )
        """)

        if gid is None:
            return

        gid = str(gid)

        db.execute("""
            INSERT OR IGNORE INTO extra_settings(guild_id)
            VALUES(?)
        """, (gid,))

        db.execute("""
            UPDATE extra_settings
            SET min_level=MAX(1,MIN(9,min_level)),
                max_level=MAX(1,MIN(9,max_level))
            WHERE guild_id=?
        """, (gid,))

        seeded = db.execute("""
            SELECT 1 FROM extra_seeded_guilds
            WHERE guild_id=?
        """, (gid,)).fetchone()

        if not seeded:
            from modules.extra_character_seed import (
                seed_into_connection,
            )
            seed_into_connection(db, gid)
            db.execute("""
                INSERT OR IGNORE INTO extra_seeded_guilds
                VALUES(?)
            """, (gid,))


def add_photos(
    gid, urls, *, gender="male",
    age_group="adult", channel_id=None,
    message_id=None
):
    if gender not in ("male", "female"):
        raise ValueError("照片性別分類不正確")
    if age_group not in ("adult", "minor"):
        raise ValueError("照片年齡分類不正確")

    initialize(gid)
    added = 0

    with transaction() as db:
        for url in dict.fromkeys(urls):
            url = str(url).strip()
            if not url.startswith(("https://", "http://")):
                continue
            if len(url) > 1800:
                continue

            cursor = db.execute("""
                INSERT OR IGNORE INTO extra_photos
                (guild_id,url)
                VALUES(?,?)
            """, (str(gid), url))

            if cursor.rowcount != 1:
                continue

            photo_id = cursor.lastrowid

            db.execute("""
                INSERT INTO extra_photo_meta (
                    photo_id,gender,age_group,
                    source_channel_id,source_message_id
                ) VALUES(?,?,?,?,?)
            """, (
                photo_id, gender, age_group,
                str(channel_id) if channel_id else None,
                str(message_id) if message_id else None,
            ))
            added += 1

    return added


def add_names(gid, nationality, kind, values):
    if kind not in ("surname", "given"):
        raise ValueError("姓名類型只能是 surname 或 given")

    initialize(gid)
    added = 0

    with transaction() as db:
        for value in dict.fromkeys(values):
            value = str(value).strip()
            if not value or len(value) > 80:
                continue
            added += db.execute("""
                INSERT OR IGNORE INTO extra_name_pool
                (guild_id,nationality,kind,value)
                VALUES(?,?,?,?)
            """, (
                str(gid), nationality, kind, value,
            )).rowcount

    return added


def add_jobs(gid, jobs):
    initialize(gid)
    added = 0

    with transaction() as db:
        for job in dict.fromkeys(jobs):
            job = str(job).strip()
            if not job or len(job) > 80:
                continue
            added += db.execute("""
                INSERT OR IGNORE INTO extra_jobs
                VALUES(?,?)
            """, (str(gid), job)).rowcount

    return added


def set_photo_channel(gid, channel_id, gender, age_group):
    if gender not in ("male", "female"):
        raise ValueError("性別分類不正確")
    if age_group not in ("adult", "minor"):
        raise ValueError("年齡分類不正確")

    initialize(gid)
    with transaction() as db:
        db.execute("""
            INSERT OR REPLACE INTO extra_photo_channels
            VALUES(?,?,?,?)
        """, (
            str(gid), str(channel_id), gender, age_group,
        ))


def get_photo_channel(gid, channel_id):
    initialize(gid)
    with get_connection() as db:
        row = db.execute("""
            SELECT gender,age_group
            FROM extra_photo_channels
            WHERE guild_id=? AND channel_id=?
        """, (
            str(gid), str(channel_id),
        )).fetchone()
    return dict(row) if row else None


def photo_inventory(gid):
    initialize(gid)
    with get_connection() as db:
        rows = db.execute("""
            SELECT m.gender,m.age_group,
                   COUNT(*) AS remaining
            FROM extra_photos p
            JOIN extra_photo_meta m
              ON m.photo_id=p.id
            WHERE p.guild_id=?
              AND p.used_character_id IS NULL
            GROUP BY m.gender,m.age_group
        """, (str(gid),)).fetchall()

    return {
        (r["gender"], r["age_group"]): r["remaining"]
        for r in rows
    }


def inventory(gid):
    initialize(gid)
    with get_connection() as db:
        photos = db.execute("""
            SELECT COUNT(*) FROM extra_photos
            WHERE guild_id=?
              AND used_character_id IS NULL
        """, (str(gid),)).fetchone()[0]

        nations = db.execute("""
            SELECT COUNT(DISTINCT nationality)
            FROM extra_name_pool WHERE guild_id=?
        """, (str(gid),)).fetchone()[0]

        names = db.execute("""
            SELECT COUNT(*) FROM extra_name_pool
            WHERE guild_id=?
        """, (str(gid),)).fetchone()[0]

        jobs = db.execute("""
            SELECT COUNT(*) FROM extra_jobs
            WHERE guild_id=?
        """, (str(gid),)).fetchone()[0]

        existing = db.execute("""
            SELECT COUNT(*) FROM characters
            WHERE guild_id=?
              AND json_extract(
                  settings_json,'$.character_category'
              )='extra'
        """, (str(gid),)).fetchone()[0]

    return dict(
        photos=photos,
        nations=nations,
        names=names,
        jobs=jobs,
        existing=existing,
    )


def get_config(gid):
    initialize(gid)
    with get_connection() as db:
        row = db.execute("""
            SELECT * FROM extra_settings
            WHERE guild_id=?
        """, (str(gid),)).fetchone()

    return dict(row)


def set_ranking(gid, enabled):
    initialize(gid)
    with transaction() as db:
        db.execute("""
            UPDATE extra_settings
            SET allow_ranking=?
            WHERE guild_id=?
        """, (
            int(bool(enabled)), str(gid),
        ))


def _title(gid, level):
    from modules.character_status import effective_levels
    return effective_levels(str(gid))[int(level) - 1][1]


def _photo_stock(db, gid, gender, age_group, count):
    sql = """
        SELECT p.id,p.url,m.gender,m.age_group
        FROM extra_photos p
        JOIN extra_photo_meta m ON m.photo_id=p.id
        WHERE p.guild_id=?
          AND p.used_character_id IS NULL
    """
    params = [str(gid)]

    if gender:
        sql += " AND m.gender=?"
        params.append(gender)
    if age_group:
        sql += " AND m.age_group=?"
        params.append(age_group)

    sql += " ORDER BY RANDOM() LIMIT ?"
    params.append(count)

    return db.execute(sql, params).fetchall()


def generate(
    gid, owner_id, count, *,
    nationality=None, level=None,
    gender=None, age_group=None
):
    from modules.extra_character_profile import (
        generate_profile, write_profile,
    )
    from modules.extra_character_resources import (
        initialize_resources,
    )
    from modules.extra_character_growth import bootstrap
    from modules.extra_character_status_bridge import (
        initialize_extra_status,
    )

    gid = str(gid)
    count = int(count)

    if not 1 <= count <= 100:
        raise ValueError("一次只能建立1～100位路人")
    if gender not in (None, "male", "female"):
        raise ValueError("性別分類不正確")
    if age_group not in (None, "adult", "minor"):
        raise ValueError("年齡分類不正確")

    initialize(gid)
    cfg = get_config(gid)

    low = max(1, int(cfg["min_level"]))
    high = min(9, int(cfg["max_level"]))

    if low > high:
        raise ValueError("咖位生成範圍設定錯誤")

    if level is not None:
        level = int(level)
        if not low <= level <= high:
            raise ValueError("初始咖位僅允許LV1～LV9")

    created = []

    with transaction() as db:
        photos = _photo_stock(
            db, gid, gender, age_group, count,
        )

        if len(photos) < count:
            raise ValueError(
                f"照片不足，只有{len(photos)}張可用"
            )

        rows = db.execute("""
            SELECT nationality,kind,value
            FROM extra_name_pool
            WHERE guild_id=?
        """, (gid,)).fetchall()

        pools = {}

        for row in rows:
            pools.setdefault(
                row["nationality"], {},
            ).setdefault(
                row["kind"], [],
            ).append(row["value"])

        nations = [
            nation for nation, parts in pools.items()
            if parts.get("surname") and parts.get("given")
        ]

        if nationality:
            if nationality not in nations:
                raise ValueError("指定國籍姓名庫不足")
            nations = [nationality]

        if not nations:
            raise ValueError("沒有可用的完整國籍姓名庫")

        jobs = [
            row["job"]
            for row in db.execute("""
                SELECT job FROM extra_jobs
                WHERE guild_id=?
            """, (gid,)).fetchall()
        ]

        if not jobs:
            raise ValueError("職業庫沒有可用職業")

        used_names = {
            row["name"]
            for row in db.execute("""
                SELECT name FROM characters
                WHERE guild_id=?
            """, (gid,)).fetchall()
        }

        for photo in photos:
            nation = random.choice(nations)
            parts = pools[nation]
            name = None

            for _ in range(300):
                candidate = (
                    random.choice(parts["surname"])
                    + " "
                    + random.choice(parts["given"])
                )

                if candidate not in used_names:
                    name = candidate
                    break

            if name is None:
                raise ValueError(
                    f"{nation}姓名組合不足，批次已取消"
                )

            used_names.add(name)

            lv = (
                level if level is not None
                else random.randint(low, high)
            )

            if photo["age_group"] == "minor":
                age = random.randint(12, 17)
            else:
                minimum = max(18, int(cfg["min_age"]))
                maximum = max(
                    minimum,
                    int(cfg["max_age"]),
                )
                age = random.randint(minimum, maximum)

            job = random.choice(jobs)

            profile = generate_profile(
                age, job, lv, photo["gender"],
            )

            title = _title(gid, lv)

            settings = {
                "character_category": "extra",
                "public_category": "extra",
                "created_as_extra": True,
                "portrait_url": photo["url"],
                "nationality": nation,
                "status_level": lv,
                "level": lv,
                "status_title": title,
                "rank_display": f"LV{lv} {title}",
                "ranking_eligible": True,
                "biography": "",
                "public_biography": "",
                **profile,
            }

            cursor = db.execute("""
                INSERT INTO characters (
                    guild_id,owner_id,name,age,
                    occupation,status,
                    character_type,settings_json
                )
                VALUES(?,?,?,?,?,'active','official_npc',?)
            """, (
                gid, str(owner_id), name, age, job,
                json.dumps(settings, ensure_ascii=False),
            ))

            cid = int(cursor.lastrowid)

            write_profile(db, gid, cid, profile)
            initialize_resources(db, gid, cid, profile)
            initialize_extra_status(db, gid, cid, lv)
            bootstrap(db, gid, cid)

            updated = db.execute("""
                UPDATE extra_photos
                SET used_character_id=?
                WHERE id=?
                  AND guild_id=?
                  AND used_character_id IS NULL
            """, (
                cid, photo["id"], gid,
            ))

            if updated.rowcount != 1:
                raise RuntimeError(
                    "照片被其他角色占用，批次已回滾"
                )

            created.append(
                (cid, name, job, lv, title)
            )

    return created
