"""Discord RP 舉報系統回歸測試（第 12 檔）。

放置：modules/test_report_system.py
執行：python -m unittest discover -s modules -p 'test_report_system.py' -v

不需 Discord Token，也不需要安裝 discord.py。本檔會使用獨立暫存
SQLite 資料庫實測 report_repository，其餘模組進行原始碼結構／
安全規則的靜態檢查。靜態檢查不等於 Discord 端到端測試。

注意：此檔案不能作為 Discord Extension 被 bot.load_extension 載入。
若 main.py 自動載入 modules 下所有 .py，請先修改載入器的排除規則，
或只在本機測試目錄存放本檔，避免啟動時出現 setup() 錯誤。
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
FILES = (
    "report_repository", "report_permission_service", "report_service",
    "report_modals", "report_view", "report_review_service",
    "report_review_view", "report_sanction_service",
    "report_appeal_service", "report_retention_service", "report_commands",
)


def module_tree(name: str) -> ast.Module:
    path = HERE / (name + ".py")
    if not path.is_file():
        raise AssertionError(f"缺少必要檔案：{path.name}")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def definitions(name: str) -> set[str]:
    return {
        node.name for node in module_tree(name).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


class TestAllFilesParse(unittest.TestCase):
    def test_all_modules_parse(self):
        for name in FILES:
            with self.subTest(module=name):
                module_tree(name)

    def test_no_duplicate_slash_commands_in_report_modules(self):
        # 子模組不可重新宣告既有 /申請 或 /審核。
        for name in FILES:
            source = (HERE / f"{name}.py").read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn('@app_commands.command(name="申請"', source)
                self.assertNotIn('@app_commands.command(name="審核"', source)
                self.assertNotIn("@app_commands.command(name='申請'", source)
                self.assertNotIn("@app_commands.command(name='審核'", source)

    def test_permission_service_entrypoints(self):
        self.assertTrue({
            "is_guild_owner", "require_guild_owner", "is_discipline_staff",
            "is_reviewer_staff", "must_route_to_owner", "get_reviewable_report",
            "get_reporter_identity_for_owner", "ensure_owner_channel_access",
        } <= definitions("report_permission_service"))

    def test_review_service_entrypoints(self):
        self.assertTrue({
            "list_review_queue", "get_review_details", "accept_report",
            "request_more_information", "resume_investigation",
            "refer_to_owner", "decide_report", "propose_sanction",
        } <= definitions("report_review_service"))

    def test_sanction_service_entrypoints(self):
        self.assertTrue({
            "approve_major_sanction", "reject_major_sanction",
            "execute_sanction", "reconcile_sanction",
            "process_expired_temporary_bans",
        } <= definitions("report_sanction_service"))

    def test_appeal_service_entrypoints(self):
        self.assertTrue({
            "submit_appeal", "claim_appeal", "decide_appeal",
            "complete_manual_reversal",
        } <= definitions("report_appeal_service"))

    def test_retention_service_entrypoints(self):
        self.assertTrue({
            "set_retention_policy", "extend_case_retention", "set_case_hold",
            "run_retention_cleanup", "cleanup_as_owner",
        } <= definitions("report_retention_service"))

    def test_existing_command_integration_entrypoints(self):
        self.assertTrue({
            "open_report_application", "open_report_review", "open_report_appeals",
            "open_report_appeal_review", "open_report_retention_settings",
            "start_report_maintenance",
        } <= definitions("report_commands"))


class TempDatabaseHarness(unittest.TestCase):
    """只注入臨時 core.database，不碰正式 bot 資料庫。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rp_report_test_")
        self.addCleanup(self.tmp.cleanup)
        db_path = str(Path(self.tmp.name) / "reports.sqlite3")
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.con.close)

        @contextlib.contextmanager
        def get_connection():
            # 對應現有 core.database.get_connection 的 context manager 介面。
            yield self.con

        @contextlib.contextmanager
        def transaction():
            try:
                yield self.con
                self.con.commit()
            except BaseException:
                self.con.rollback()
                raise

        fake_core = types.ModuleType("core")
        fake_core.__path__ = []
        fake_db = types.ModuleType("core.database")
        fake_db.get_connection = get_connection
        fake_db.transaction = transaction
        with patch.dict(sys.modules, {"core": fake_core, "core.database": fake_db}):
            spec = importlib.util.spec_from_file_location(
                f"test_report_repository_{id(self)}", HERE / "report_repository.py"
            )
            self.assertIsNotNone(spec)
            repo = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(repo)
        self.repo = repo
        repo.install_schema()

    def make(self, **overrides):
        data = dict(
            guild_id=123, reporter_user_id=456, target_user_id=789,
            report_type="player", reason_code="harassment",
            description="舉報事件測試", evidence_urls=["https://example.org/evidence/1"],
        )
        data.update(overrides)
        return self.repo.create_report(**data)


class TestRepositorySecurity(TempDatabaseHarness):
    def test_create_report_is_atomic_and_anonymous(self):
        report_id = self.make()
        public = self.repo.get_report_for_reviewer(
            guild_id=123, report_id=report_id, is_owner=False, is_discipline=True,
        )
        self.assertEqual(public["target_user_id"], "789")
        self.assertNotIn("reporter_user_id", public)
        self.assertEqual(self.repo.get_reporter_for_owner(
            guild_id=123, report_id=report_id, is_guild_owner=True
        ), "456")
        with self.assertRaises(PermissionError):
            self.repo.get_reporter_for_owner(
                guild_id=123, report_id=report_id, is_guild_owner=False,
            )
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM rp_report_evidence WHERE report_id=?", (report_id,)
        ).fetchone()[0], 1)

    def test_staff_report_owner_only(self):
        report_id = self.make(report_type="staff")
        self.assertEqual(self.repo.get_report_for_reviewer(
            guild_id=123, report_id=report_id, is_owner=True, is_discipline=False,
        )["owner_only"], 1)
        with self.assertRaises(PermissionError):
            self.repo.get_report_for_reviewer(
                guild_id=123, report_id=report_id, is_owner=False, is_discipline=True,
            )
        self.assertFalse(any(row["id"] == report_id for row in self.repo.list_pending_reports(
            guild_id=123, is_owner=False, is_discipline=True,
        )))

    def test_unprivileged_reviewer_denied(self):
        report_id = self.make()
        with self.assertRaises(PermissionError):
            self.repo.get_report_for_reviewer(
                guild_id=123, report_id=report_id, is_owner=False, is_discipline=False,
            )

    def test_cross_guild_read_denied(self):
        report_id = self.make()
        with self.assertRaises(LookupError):
            self.repo.get_report_for_reviewer(
                guild_id=987, report_id=report_id, is_owner=True, is_discipline=False,
            )

    def test_evidence_required(self):
        for urls in ([], ["http://example.org/insecure"], ["not a url"]):
            with self.subTest(urls=urls), self.assertRaises(ValueError):
                self.make(evidence_urls=urls)

    def test_initial_evidence_limit(self):
        urls = [f"https://example.org/{i}" for i in range(11)]
        with self.assertRaises(ValueError):
            self.make(evidence_urls=urls)

    def test_append_evidence_authorization_and_deduplication(self):
        report_id = self.make()
        with self.assertRaises(PermissionError):
            self.repo.append_evidence(
                guild_id=123, report_id=report_id,
                urls=["https://example.org/evidence/2"],
                actor_user_id=111, authorized=False,
            )
        self.assertEqual(self.repo.append_evidence(
            guild_id=123, report_id=report_id,
            urls=["https://example.org/evidence/2", "https://example.org/evidence/2"],
            actor_user_id=111, authorized=True,
        ), 1)

    def test_character_requires_valid_owner_relation(self):
        self.con.execute("""CREATE TABLE characters (
            id INTEGER PRIMARY KEY, guild_id TEXT, owner_id TEXT
        )""")
        self.con.execute("INSERT INTO characters VALUES (42, '123', '789')")
        self.con.commit()
        self.assertGreater(self.make(report_type="character", target_character_id=42), 0)
        with self.assertRaises(ValueError):
            self.make(report_type="character", target_character_id=43)

    def test_reporter_id_not_in_public_event(self):
        report_id = self.make()
        event = self.con.execute(
            "SELECT actor_user_id, details_json FROM rp_report_events WHERE report_id=?",
            (report_id,),
        ).fetchone()
        self.assertIsNone(event["actor_user_id"])
        self.assertNotIn("456", event["details_json"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
