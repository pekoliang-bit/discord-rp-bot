"""驗收新手分頁、抽獎券防重、獎單修改權限、通告檔期唯讀工具。"""
from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UIContracts(unittest.TestCase):
    def check(self, rel):
        source = (ROOT / rel).read_text(encoding='utf-8-sig')
        return source, ast.parse(source)

    def test_newbie_guide_is_public_paginated_and_user_bound(self):
        source, tree = self.check('modules/z_tasks_center.py')
        self.assertIn('class NewbieGuideView', source)
        self.assertIn("label='◀ 上一頁'", source)
        self.assertIn("label='下一頁 ▶'", source)
        self.assertIn('view=guide,ephemeral=False', source)
        self.assertIn('_thread_owned_characters(interaction)', source)

    def test_lottery_admin_has_select_and_safe_editing(self):
        source, _ = self.check('modules/lottery_admin_ui.py')
        for token in ('class LotteryListEditorView', 'class LotteryPrizeEditorView',
                      'class OrdinaryTicketSetupView', 'class EditLotteryPrizeModal',
                      'class EditLotteryListModal', 'LotteryAdminView'):
            self.assertIn(token, source)
        editor, _ = self.check('core/lottery_admin_editor.py')
        self.assertIn('已開獎品允許補充說明', editor)
        self.assertIn('已有中獎紀錄', editor)

    def test_ticket_is_purchase_then_consume_in_one_transaction(self):
        ticket, _ = self.check('core/ordinary_lottery_tickets.py')
        pool, _ = self.check('core/ordinary_lottery_pool.py')
        self.assertIn('WEEKLY_LIMIT = 5', ticket)
        self.assertIn('PRICE = 800', ticket)
        self.assertIn('with transaction() as db:', pool)
        self.assertIn('consume_in_transaction(db,', pool)
        self.assertIn('deliver_official_item(db,', pool)
        self.assertIn('PRIMARY KEY(guild_id,request_key)', pool)

    def test_task_requires_personal_forum_and_persistent_public_response(self):
        source, _ = self.check('modules/z_tasks_center.py')
        self.assertIn('class ReportTaskSelect', source)
        self.assertIn('verified_personal_forum_id=', source)
        self.assertIn('ephemeral=False', source)


class ScheduleAudit(unittest.TestCase):
    def test_readonly_audit_is_explicit_about_missing_schedule(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        from audit_task_schedules import audit
        con = sqlite3.connect(':memory:')
        con.row_factory = sqlite3.Row
        try:
            con.execute('CREATE TABLE rp_task_definitions(id INTEGER, guild_id TEXT, code TEXT, kind TEXT, title TEXT, status TEXT)')
            con.execute('CREATE TABLE rp_work_task_schedule(guild_id TEXT, task_id INTEGER, starts_at TEXT, ends_at TEXT)')
            con.execute("INSERT INTO rp_task_definitions VALUES (1,'g','film_A','screen','影視通告','published')")
            con.execute("INSERT INTO rp_task_definitions VALUES (2,'g','variety_B','variety','綜藝通告','published')")
            con.execute("INSERT INTO rp_work_task_schedule VALUES ('g',1,'2026-12-10T09:00:00+08:00','2026-12-10T18:00:00+08:00')")
            report = audit(con, 'g')
            self.assertEqual(report['totals']['scheduled'], 1)
            self.assertEqual(report['totals']['unscheduled'], 1)
            self.assertEqual(report['totals']['exempt_templates'], 0)
        finally:
            con.close()


if __name__ == '__main__':
    unittest.main()
