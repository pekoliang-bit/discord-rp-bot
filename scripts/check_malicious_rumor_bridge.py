"""惡意行動 → 傳聞橋接驗收（使用暫存 SQLite 與受控替身，不寫正式資料）。
執行：python -m scripts.check_malicious_rumor_bridge
注意：測試橋接的交易與冪等邏輯，不代表真實傳聞獎懲流程已上線。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from core import database
from core import malicious_actions as malicious
from core import malicious_rumor_bridge as bridge
from core.rumor_reward_planner import RuleConfig


def open_temp(path: Path):
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def expect_error(error_type, fn, label):
    try:
        fn()
    except error_type:
        print(f'[PASS] {label}')
        return
    raise AssertionError(f'未拒絕：{label}')


def fake_insert(db, **kwargs):
    if not db.in_transaction:
        raise AssertionError('建案不在交易內')
    row = db.execute('INSERT INTO test_rumors(title) VALUES (?)',
                     (kwargs['title'],))
    rid = int(row.lastrowid)
    db.execute('INSERT INTO rp_rumor_case_meta(guild_id,rumor_id,visibility) VALUES (?,?,?)',
               (kwargs['guild_id'], rid, 'public'))
    return rid


def fake_allowed(db, guild_id, character_id, category):
    return category != '商業'  # 用於驗證權限拒絕後整筆回滾


def make_row(db, *, succeeded, action_type='rumor'):
    cur = db.execute('''INSERT INTO rp_malicious_actions
        (guild_id, actor_user_id, actor_character_id, target_character_id,
         action_type, level, anonymous, title, description, cost,
         success_chance, succeeded, exposed, created_at)
        VALUES ('123','111',1,2,?,'normal',1,'測試事件','虛構描述',5000,40,?,0,1800000000)''',
        (action_type, int(succeeded)))
    return int(cur.lastrowid)


def main() -> int:
    print('=== /惡意 → /傳聞 橋接驗收 ===')
    try:
        with tempfile.TemporaryDirectory(prefix='rp_malicious_bridge_') as dirname:
            path = Path(dirname) / 'test.sqlite3'
            with patch.object(database, 'get_connection', side_effect=lambda: open_temp(path)):
                with database.get_connection() as db:
                    malicious.initialize(db)
                    db.execute('CREATE TABLE test_rumors (id INTEGER PRIMARY KEY, title TEXT NOT NULL)')
                    db.execute('''CREATE TABLE rp_rumor_case_meta (
                        guild_id TEXT NOT NULL, rumor_id INTEGER NOT NULL,
                        visibility TEXT NOT NULL DEFAULT 'public')''')
                    success = make_row(db, succeeded=True)
                    failed = make_row(db, succeeded=False)
                    blocked = make_row(db, succeeded=True, action_type='business')
                    db.commit()

                import core.rumor_case_creation as creation
                pr_rumor = types.ModuleType("modules.pr_rumor")
                pr_rumor.rumor_allowed = fake_allowed
                # 僅替換外部傳聞建案/授權函式，測試真實橋接實作的
                # 交易、關聯與防重複機制。
                with patch.object(bridge, 'transaction', database.transaction), \
                     patch.object(creation, 'insert_case_and_snapshot', fake_insert), \
                     patch.dict(sys.modules, {'modules.pr_rumor': pr_rumor}):
                    settings = bridge.RumorCreationSettings(
                        business_id='simulation', tier=1, complexity=1,
                        complexity_version='test',
                        rules=RuleConfig(version="test", grade_changes={}, tier_percent={},
                                         caps={}, heat_reference=100, max_heat_bonus_permille=0),
                    )
                    expect_error(PermissionError,
                        lambda: bridge.create_rumor_for_action('123', success, settings=settings),
                        '缺少正式案件授權時禁止建案')
                    assert bridge.create_rumor_for_action(
                        '123', failed, settings=settings, case_authorized=True) is None
                    print('[PASS] 行動失敗不建立針對目標的傳聞')
                    expect_error(PermissionError,
                        lambda: bridge.create_rumor_for_action(
                            '123', blocked, settings=settings, case_authorized=True),
                        '目標關閉該分類傳聞時拒絕建案')
                    rumor_id = bridge.create_rumor_for_action(
                        '123', success, settings=settings, case_authorized=True,
                        publicly_visible=False)
                    assert rumor_id > 0
                    again = bridge.create_rumor_for_action(
                        '123', success, settings=settings, case_authorized=True,
                        publicly_visible=False)
                    assert again == rumor_id
                    with database.get_connection() as db:
                        count = db.execute('SELECT COUNT(*) FROM test_rumors').fetchone()[0]
                        linked = db.execute('SELECT rumor_id FROM rp_malicious_actions WHERE id=?',
                                            (success,)).fetchone()[0]
                        visible = db.execute('SELECT visibility FROM rp_rumor_case_meta WHERE rumor_id=?',
                                             (rumor_id,)).fetchone()[0]
                    assert count == 1 and linked == rumor_id and visible == 'private'
                    print('[PASS] 相同行動重試只建立一則傳聞')
                    print('[PASS] 非公開案件不會立即公開')
                    print('[PASS] 惡意與傳聞共用來源關聯 ID')
    except Exception as exc:
        print(f'[FAIL] {type(exc).__name__}: {exc}')
        return 1
    print('[PASS] 橋接隔離測試完成')
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
