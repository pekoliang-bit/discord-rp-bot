
"""公司解約服務基本安全測試。

只驗證權限和完成案件的去重入口，
不執行真實資產交易。
"""

import sqlite3
import unittest

from unittest.mock import patch

from core import company_contract_exit_service as service


class Connection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.db.rollback()
        return False


class ExitServiceTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        self.db.execute("""
            CREATE TABLE rp_company_contract_exits (
                id INTEGER PRIMARY KEY,
                guild_id TEXT,
                contract_id INTEGER,
                company_id TEXT,
                character_id INTEGER,
                requested_by TEXT,
                reason TEXT,
                company_approved INTEGER,
                penalty_amount INTEGER,
                payment_key TEXT,
                status TEXT
            )
        """)

        self.db.execute("""
            INSERT INTO rp_company_contract_exits
            VALUES (
                1,'g',10,'c',5,'employee',
                'ordinary',0,10000,
                'exit:10','completed'
            )
        """)

    def run_finalize(self, actor):
        with (
            patch.object(service, "initialize"),
            patch.object(
                service,
                "transaction",
                return_value=Connection(self.db),
            ),
            patch.object(
                service,
                "_payment",
            ) as payment,
        ):
            if actor != "employee":
                with self.assertRaises(PermissionError):
                    service.finalize_exit(
                        "g", 1, actor
                    )
            else:
                text = service.finalize_exit(
                    "g", 1, actor
                )
                self.assertIn("未再次扣費", text)

            payment.assert_not_called()

    def test_completed_not_charged_again(self):
        self.run_finalize("employee")

    def test_wrong_actor_denied(self):
        self.run_finalize("intruder")

    def test_unknown_case(self):
        with (
            patch.object(service, "initialize"),
            patch.object(
                service,
                "transaction",
                return_value=Connection(self.db),
            ),
        ):
            with self.assertRaises(LookupError):
                service.finalize_exit(
                    "g", 999, "employee"
                )


if __name__ == "__main__":
    unittest.main()
