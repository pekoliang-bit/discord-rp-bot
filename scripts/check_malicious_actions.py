"""惡意行動核心驗收：只操作暫存 SQLite，不使用正式角色或金幣。\n執行：python -m scripts.check_malicious_actions\n"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from core import database
from core import malicious_actions as mal
from core import malicious_forum_permissions as forum


def assert_raises(error, fn, label):
    try:
        fn()
    except error:
        print(f"[PASS] {label}")
    else:
        raise AssertionError(f"沒有拒絕：{label}")


class FixedRandom:
    def __init__(self, *values):
        self.values = iter(values)
    def random(self):
        return next(self.values)


def run_in_temp_database(path: Path):
    # 使用正式資料庫的 DDL，但完全隔離於臨時目錄。
    with patch.object(database, 'get_connection', side_effect=lambda: open_temp(path)):
        with database.get_connection() as db:
            for sql in database.SCHEMA:
                db.execute(sql)
            db.execute("INSERT INTO guilds(guild_id) VALUES ('123')")
            db.executemany(
                "INSERT INTO characters(guild_id,owner_id,name,status) VALUES (?,?,?,'active')",
                [('123', '111', '甲'), ('123', '222', '乙')],
            )
            db.execute('''CREATE TABLE IF NOT EXISTS character_forum_links (
                guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
                forum_id TEXT, notice_id TEXT, question_id TEXT, story_id TEXT,
                PRIMARY KEY(guild_id,character_id))''')
            db.execute("INSERT INTO character_forum_links(guild_id,character_id,forum_id) VALUES ('123',1,'567')")
            db.commit()

        # 模組對 transaction/get_connection 的函式是從 core.database 直接 import，
        # 所以要一併 patch，確保不會接觸正式資料庫。
        with patch.object(mal, 'transaction', database.transaction), \
             patch.object(forum, 'transaction', database.transaction), \
             patch.object(forum, 'get_connection', database.get_connection):
            assert_raises(PermissionError,
                lambda: forum.register_bot_thread(123, 1, 567, 890, setter_id=999),
                '無群主授權不能綁定帖子')
            forum.register_bot_thread(123, 1, 567, 890, setter_id=999, owner_authorized=True)
            auth = forum.authorize_malicious(123, 111, 890, 567)
            assert auth.character_id == 1
            print('[PASS] 正確持有人及指定帖子可通過')
            assert_raises(PermissionError,
                lambda: forum.authorize_malicious(123, 222, 890, 567),
                '別人不能使用該角色帖子')
            assert_raises(PermissionError,
                lambda: forum.authorize_malicious(123, 111, 891, 567),
                '未登記帖子不能使用')
            assert_raises(PermissionError,
                lambda: forum.authorize_malicious(123, 111, 890, 568),
                '不能跨論壇使用')

            request = mal.MaliciousRequest(
                guild_id='123', actor_user_id='111', actor_character_id=1,
                target_character_id=2, action_type='rumor', level='normal',
                title='測試傳聞', description='虛構遊戲事件',
            )
            def debit(db, guild_id, actor_id, amount, unique_key):
                db.execute('''CREATE TABLE IF NOT EXISTS test_charges (
                    unique_key TEXT PRIMARY KEY, amount INTEGER NOT NULL)''')
                db.execute('INSERT INTO test_charges VALUES (?,?)', (unique_key, amount))

            result = mal.resolve_action(
                request, debit=debit, forum_authorized=True, trusted_stats=True,
                now=1800000000, rng=FixedRandom(0.1,0.99),
            )
            assert result.succeeded and not result.exposed and result.cost == 5000
            print('[PASS] 行動完成且成功／暴露獨立判定')
            assert_raises(ValueError,
                lambda: mal.resolve_action(
                    request, debit=debit, forum_authorized=True,
                    trusted_stats=True, now=1800000001,
                ), '同目標 24 小時冷卻')
            assert_raises(PermissionError,
                lambda: mal.resolve_action(
                    request, debit=debit, forum_authorized=False,
                    trusted_stats=True, now=1800200000,
                ), '未通過論壇驗證不能行動')
            assert_raises(PermissionError,
                lambda: mal.resolve_action(
                    request, debit=debit, forum_authorized=True,
                    trusted_stats=False, now=1800200000,
                ), '未驗證能力資料不能行動')

            def failing_debit(*args):
                raise RuntimeError('模擬帳本失敗')
            assert_raises(RuntimeError,
                lambda: mal.resolve_action(
                    request, debit=failing_debit, forum_authorized=True,
                    trusted_stats=True, now=1800200000,
                ), '扣款失敗會取消整筆交易')
            with database.get_connection() as db:
                count = db.execute('SELECT COUNT(*) FROM rp_malicious_actions').fetchone()[0]
                charges = db.execute('SELECT COUNT(*) FROM test_charges').fetchone()[0]
            assert count == charges == 1, (count, charges)
            print('[PASS] 惡意紀錄與模擬帳本數量一致')


def open_temp(path):
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def main() -> int:
    print('=== /惡意 核心／論壇授權驗收 ===')
    try:
        with tempfile.TemporaryDirectory(prefix='rp_malicious_test_') as folder:
            run_in_temp_database(Path(folder) / 'test.sqlite3')
    except Exception as exc:
        print(f'[FAIL] {type(exc).__name__}: {exc}')
        return 1
    print('[PASS] 所有臨時資料庫測試通過（未執行真實 Discord 或傳聞建案）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
