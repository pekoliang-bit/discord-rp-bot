"""網路帳號面板及新首頁入口回歸測試，不使用正式 Discord Token。"""
from __future__ import annotations
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class AccountHomeTests(unittest.TestCase):
    def test_home_buttons_valid_and_compact(self):
        src=(ROOT/'modules/network_group_ui.py').read_text(encoding='utf-8-sig')
        tree=ast.parse(src)
        home=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='NetworkHome')
        labels=[]
        for method in home.body:
            if not isinstance(method,ast.AsyncFunctionDef):
                continue
            for dec in method.decorator_list:
                if not isinstance(dec,ast.Call) or getattr(dec.func,'attr',None)!='button':
                    continue
                arguments={kw.arg:ast.literal_eval(kw.value) for kw in dec.keywords
                           if isinstance(kw.value,(ast.Constant,ast.List,ast.Tuple))}
                self.assertNotEqual(arguments.get('emoji'),'@')
                labels.append(arguments['label'])
        self.assertEqual(len(labels),4)
        self.assertTrue(any('發文' in label for label in labels))
        self.assertIn('直播中心',labels)
        self.assertIn('群組中心',labels)
        self.assertIn('帳號資訊',labels)

    def test_picker_ack_first_and_separate_db_from_ui_error(self):
        src=(ROOT/'modules/network_group_ui.py').read_text(encoding='utf-8-sig')
        tree=ast.parse(src)
        picker=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='AccountPicker')
        callback=next(node for node in ast.walk(picker)
                      if isinstance(node,ast.AsyncFunctionDef) and node.name=='chosen')
        self.assertIn('await interaction.response.defer',ast.unparse(callback.body[0]))
        body=ast.unparse(callback)
        for term in ('網路帳號資料庫讀取失敗','網路首頁 Discord 訊息發送失敗','網路首頁建立失敗',"account['character_id']"):
            self.assertIn(term,body)

    def test_existing_character_account_autocreate(self):
        from core import database
        from core import network_groups as ng
        from core.network_account_autocreate import ensure_character_accounts
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(database,'DATABASE_PATH',Path(directory)/'test.sqlite'):
                database.init_db()
                with database.transaction() as db:
                    db.execute("INSERT INTO guilds(guild_id,name) VALUES ('889','testing')")
                    db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(7,'889','44','角色甲','active')")
                    db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(8,'889','55','角色乙','active')")
                ids=ensure_character_accounts('889',7)
                self.assertEqual(len(ids),2)
                for account in ng.accounts('889','44',7):
                    value=ng.account_info('889','44',account['id'])
                    self.assertEqual(value['character_id'],7)
                    self.assertEqual(value['character_name'],'角色甲')
                    self.assertIn(value['account_kind'],('personal','official'))
                    with self.assertRaises(PermissionError):
                        ng.account_info('889','55',account['id'])
                self.assertEqual(ensure_character_accounts('889',7),ids)

if __name__=='__main__': unittest.main()
