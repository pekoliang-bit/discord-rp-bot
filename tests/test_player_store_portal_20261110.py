"""商城整合入口靜態回歸（不需 discord.py 或正式 DB）。"""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class StorePortalTests(unittest.TestCase):
    def read(self,relative):
        raw=(ROOT/relative).read_text(encoding='utf-8')
        return ast.parse(raw),raw
    def test_shop_command_has_no_manual_fields(self):
        tree,_=self.read('modules/shop.py')
        cog=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Shop')
        func=next(n for n in cog.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='shop')
        self.assertEqual([a.arg for a in func.args.args],['self','interaction'])
    def test_store_hub_and_return(self):
        tree,src=self.read('modules/player_store_portal.py')
        cls={n.name for n in tree.body if isinstance(n,ast.ClassDef)}
        self.assertTrue({'ShopHubView','StoreList','StoreDetail','OwnerPanel','BuyModal','AddProduct','AddJob','PendingPanel'}<=cls)
        self.assertIn('🔄 切換角色',src)
        self.assertIn('🏪 玩家商鋪',src)
        self.assertIn('⬅ 返回商城',src)
        self.assertIn('⬅ 返回商鋪',src)
        self.assertIn('⬅ 返回店家管理',src)
    def test_existing_storefront_reused(self):
        _,src=self.read('modules/player_store_portal.py')
        for method in ('storefront.list_stores','storefront.list_products','storefront.purchase','storefront.create_product','storefront.update_product'):
            self.assertIn(method,src)
    def test_store_back_uses_hub(self):
        _,src=self.read('modules/zzz_shop_home.py')
        self.assertIn("modules.player_store_portal",src)

if __name__=='__main__':unittest.main()
