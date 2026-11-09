
"""公司、集團／合約對外編號與下拉選單隔離測試；不接觸 Discord。"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import database
from core.company_public_ids import (
    prefix, public_company, resolve_company, contract_code,
    resolve_contract, list_companies,
)
from core.company_contract_store import initialize_contracts
from modules.company_business import initialize as init_business

ROOT = Path(__file__).resolve().parents[1]
GUILD = '1544051203493601380'


class CompanyNumberTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(database, 'DATABASE_PATH', str(Path(self.tmp.name) / 'company.sqlite3'))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.init_db()
        init_business()
        with database.transaction() as db:
            db.execute('INSERT INTO guilds(guild_id) VALUES (?)', (GUILD,))
            db.execute('INSERT INTO guilds(guild_id) VALUES (?)', ('99888',))
            db.execute('''INSERT INTO rp_approved_company_authorizations VALUES(?,?,?,?)''',
                       (GUILD, 'COM-000001', 'u1', 'approved'))
            for gid, bid, kind, name in [
                (GUILD, 'COM-000001', 'company', '舊公司'),
                (GUILD, 'GRP-000002', 'group', '旗下集團'),
                (GUILD, 'BRD-000003', 'brand', '附屬品牌'),
                ('99888', 'COM-999999', 'company', '其他伺服器'),
            ]:
                db.execute('''INSERT INTO rp_business_entities
                    (guild_id,business_id,kind,name,category,industry,description,region,owner_id,owner_character_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (gid, bid, kind, name, '一般企業', '商業', '-', '東京', 'u1', 10))
            db.execute('''INSERT INTO rp_business_entities
                (guild_id,business_id,kind,name,category,industry,description,region,owner_id,owner_character_id,status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (GUILD, 'COM-000004','company','已停用','一般企業','商業','-','東京','u1',10,'closed'))
            initialize_contracts(db)
            for _ in range(3):
                db.execute('''INSERT INTO rp_company_employee_contracts
                    (guild_id,company_id,character_id,employee_user_id,employer_user_id,salary,cycle_days,penalty)
                    VALUES(?,?,?,?,?,?,?,?)''', (GUILD, 'COM-000001', 12+_, 'u2','u1',2000,7,0))

    def test_prefix_and_stable_existing_company(self):
        self.assertEqual(prefix(GUILD), '15380')
        with database.transaction() as db:
            self.assertEqual(public_company(db, GUILD, 'COM-000001'), '15380-1')
            self.assertEqual(public_company(db, GUILD, 'GRP-000002'), '15380-2')
            self.assertEqual(public_company(db, GUILD, 'COM-000001'), '15380-1')
            self.assertEqual(resolve_company(db, GUILD, '15380-1'), 'COM-000001')
            self.assertEqual(resolve_company(db, GUILD, 'COM-000001'), 'COM-000001')

    def test_company_dropdown_is_server_scoped_and_active(self):
        companies = list_companies(GUILD)
        self.assertEqual({item[0] for item in companies}, {'COM-000001', 'GRP-000002'})
        self.assertEqual({item[3] for item in companies}, {'15380-1', '15380-2'})
        self.assertEqual(len(list_companies(GUILD, ('company',))), 1)
        self.assertEqual({item[0] for item in list_companies('99888')}, {'COM-999999'})

    def test_closed_company_never_reappears_from_legacy_approval(self):
        with database.transaction() as db:
            db.execute("""INSERT INTO rp_approved_company_authorizations
                (guild_id, company_id, original_applicant_id, status)
                VALUES (?, ?, ?, 'approved')""", (GUILD, 'COM-000004', 'u1'))
        self.assertNotIn('COM-000004', {item[0] for item in list_companies(GUILD)})

    def test_contract_number_is_per_company_and_reversible(self):
        with database.transaction() as db:
            for n in range(1, 4):
                value = contract_code(db, GUILD, n)
                self.assertEqual(value, f'15380-1-{n}')
                self.assertEqual(resolve_contract(db, GUILD, value), n)
                self.assertEqual(resolve_contract(db, GUILD, str(n)), n)
            with self.assertRaises(LookupError):
                resolve_contract(db, GUILD, '15380-1-15')
            with self.assertRaises(LookupError):
                resolve_contract(db, '99888', '15380-1-1')

    def test_automatic_group_uses_next_server_code(self):
        from core.company_group_auto import maybe_create_group
        with patch('core.company_group_auto.group_eligibility', return_value={
            'existing_group_id': None, 'automatic_eligible': True,
        }), patch('core.company_group_auto.attach_all_owned', return_value=5):
            with database.transaction() as db:
                group_id = maybe_create_group(db, GUILD, 'u1', 10)
                self.assertEqual(group_id, '15380-4')
                row = db.execute(
                    "SELECT kind FROM rp_business_entities WHERE guild_id=? AND business_id=?",
                    (GUILD, group_id),
                ).fetchone()
                self.assertEqual(row['kind'], 'group')
                self.assertEqual(public_company(db, GUILD, group_id), group_id)

    def test_picker_and_page_layout_are_present(self):
        picker = (ROOT / 'modules/company_select_ui.py').read_text(encoding='utf-8')
        portal = (ROOT / 'modules/company_portal.py').read_text(encoding='utf-8')
        group = (ROOT / 'modules/company_group_management_ui.py').read_text(encoding='utf-8')
        for source in (picker, portal, group):
            ast.parse(source)
        self.assertIn('list_companies(self.gid, self.kinds)', picker)
        self.assertIn('CompanyEmployeePanel', portal)
        self.assertIn('CompanyManagementCategoryView', portal)
        self.assertIn('open_company_chooser(interaction, self.uid, GroupAttachModal', group)


if __name__ == '__main__':
    unittest.main()
