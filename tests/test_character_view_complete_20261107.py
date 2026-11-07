"""/查看介面合約與獨立隱私設定測試：不需 Discord token。"""
from __future__ import annotations
import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ViewStaticContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / 'modules' / 'character_view.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.src)

    def test_public_panel_has_two_pages_and_last_biography(self):
        self.assertIn('elif detail_page == 1:', self.src)
        self.assertIn('else:\n        personality, biography = visible_biography', self.src)
        self.assertIn('name="📖 生平"', self.src)
        self.assertNotIn('"📄 面板頁數"', self.src)

    def test_my_characters_fast_path(self):
        self.assertIn('"mine": "我的角色"', self.src)
        self.assertIn('label="👤 查看自己的角色"', self.src)
        self.assertIn('if not is_owner(actor, character):', self.src)

    def test_four_row_layout(self):
        detail = next(n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == 'CharacterDetailView')
        labels = {}
        for node in detail.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                kwargs = {x.arg: x.value for x in decorator.keywords}
                if 'label' in kwargs and 'row' in kwargs:
                    labels[ast.literal_eval(kwargs['label'])] = ast.literal_eval(kwargs['row'])
        expected = {
            '🌐 公開': 0, '🔒 私人': 0, '💰 資產': 0, '🎒 背包': 0,
            '📖 攻略': 1, '💕 關係': 1, '🏆 成就榮譽': 1,
            '📝 修改角色資料': 2, '🎯 點數分配': 2,
            '◀️': 3, '▶️': 3, '⬅️ 返回': 3,
        }
        self.assertEqual(labels, expected)
        self.assertEqual([sum(v == row for v in labels.values()) for row in range(4)], [4, 3, 2, 3])

    def test_privacy_controls_both_independent(self):
        self.assertIn("await self._toggle(interaction, 'personality')", self.src)
        self.assertIn("await self._toggle(interaction, 'biography')", self.src)


class MyCharacterListingTests(unittest.TestCase):
    def setUp(self):
        import contextlib
        import types
        from modules.character_view_privacy import visibility_state
        self.tmp = tempfile.TemporaryDirectory()
        dbfile = str(Path(self.tmp.name)/'list.sqlite3')
        self.connection = sqlite3.connect(dbfile)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('''CREATE TABLE characters(guild_id TEXT,id INTEGER,owner_id TEXT,name TEXT,occupation TEXT,
                              settings_json TEXT,public_code TEXT,status TEXT,character_type TEXT)''')
        for i in range(1,31):
            self.connection.execute('INSERT INTO characters VALUES(?,?,?,?,?,?,?,?,?)',
                ('one',i,'A' if i<=27 else 'B',f'角色{i}', '演員','{}',f'CODE{i}', 'active','player'))
        self.connection.execute('INSERT INTO characters VALUES(?,?,?,?,?,?,?,?,?)',
            ('two',99,'A','另一伺服器角色','演員','{}','CODE99','active','player'))
        self.connection.execute('INSERT INTO characters VALUES(?,?,?,?,?,?,?,?,?)',
            ('one',50,'A','已刪角色','演員','{}','CODE50','deleted','player'))
        self.connection.commit()
        source = (ROOT/'modules'/'character_view.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        func = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='find_characters')
        @contextlib.contextmanager
        def db():
            yield self.connection
        ns = dict(get_connection=db, CATEGORIES={'mine':'我的角色','original':'原創角色'},
            PAGE_SIZE=25,safe_json=json.loads,
            get_category=lambda c:'original',is_owner=lambda a,c:a.user_id==c['owner_id'],
            can_see_character=lambda a,c:c['status']!='deleted' and (c['status']=='active' or a.user_id==c['owner_id']))
        exec(compile(ast.Module(body=[func],type_ignores=[]),'<list-test>','exec'),ns)
        self.find = ns['find_characters']
        self.actor = types.SimpleNamespace(guild_id='one',user_id='A',is_admin=False)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    def test_owned_characters_cross_categories_and_pagination(self):
        r=self.find(self.actor,'mine')
        self.assertEqual(r['total'],27)
        self.assertEqual(len(r['items']),25)
        r2=self.find(self.actor,'mine',page=1)
        self.assertEqual(len(r2['items']),2)
        self.assertTrue(all(x['id']<=27 for x in r2['items']))
        self.assertEqual(r2['max_page'],1)

    def test_no_other_owner_even_admin(self):
        self.actor.is_admin=True
        self.assertEqual(self.find(self.actor,'mine')['total'],27)
        self.actor.is_admin=False
        self.actor.user_id='B'
        self.assertEqual(self.find(self.actor,'mine')['total'],3)

    def test_keyword_filter_scoped_to_owner(self):
        self.assertEqual(self.find(self.actor,'mine','CODE2')['total'],9)
        self.assertEqual(self.find(self.actor,'mine','角色29')['total'],0)


class PublicPrivateRenderTests(unittest.TestCase):
    """用替身渲染實際 make_character_embed 函式，測試公開資訊不洩露。"""

    def setUp(self):
        import types
        from modules.character_view_privacy import visible_biography
        source = (ROOT / 'modules' / 'character_view.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'make_character_embed')
        fn.decorator_list = []
        class MockEmbed:
            def __init__(self, *, title, description, color):
                self.title, self.description, self.fields = title, description, []
                self.color = color
            def add_field(self, *, name, value, inline):
                self.fields.append((name, value))
            def set_image(self, *, url):
                self.image_url = url
            def set_footer(self, *, text):
                self.footer = text
        class Color:
            @staticmethod
            def blurple(): return 0x123456
            def __new__(cls, value): return value
        self.settings = {'gender':'女性', 'private_personality':'絕對不可洩露的性格',
                         'biography':'絕對不可洩露的身世', 'portrait_url':'https://example.test/photo.png'}
        self.actor = types.SimpleNamespace(guild_id='1', user_id='99', is_admin=False)
        def get_character(actor, cid):
            return {'name':'測試員','id':cid,'public_code':'ABC','age':21, 'occupation':'演員',
                    'settings_json':json.dumps(self.settings,ensure_ascii=False),
                    'owner_id':'42','status':'active','character_type':'player'}
        def add_lines(embed, name, lines):
            embed.add_field(name=name, value='\n'.join(lines), inline=False)
        def pages(text, page_chars=1500):
            return [text] if text else []
        ns = dict(
            discord=types.SimpleNamespace(Embed=MockEmbed, Color=Color),
            get_character=get_character, can_see_private=lambda a,c: a.user_id == c['owner_id'],
            get_category=lambda c: 'original', safe_json=json.loads,
            _character_detail_max_page=lambda *a,**kw: 2,
            shorten=lambda x,n=1000: str(x)[:n], CATEGORIES={'original':'原創角色'},
            display_value=lambda x,d='未公開': str(x) if x else d,
            _panel_faction=lambda *a: '影子陣營', _panel_cp=lambda *a:'測試 CP',
            _character_status=lambda *a: 'LV1 籍籍無名',
            read_money_assets=lambda *a: {'total':150000,'wallet':100000,'bank':50000},
            read_attributes=lambda *a: {'容貌':50,'才智':88},
            read_tracking=lambda *a: {'loyal_fans':1,'active_antis':0,'public_awareness':2},
            _add_asset_summary=lambda embed,*a: embed.add_field(name='🏠 房產',value='海邊別墅',inline=False),
            add_field_chunks=add_lines, visible_biography=visible_biography,
            _biography_pages=pages, ATTRIBUTE_GENERAL=('流量熱度',),
            ATTRIBUTE_TALENT=('容貌','才智'), PRIVATE_FIELDS={'private_personality':'性格','biography':'生平','notes':'私密備註'},
            PermissionDenied=PermissionError,
        )
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<render-test>', 'exec'), ns)
        self.render = ns['make_character_embed']

    def test_first_second_last_page_contents(self):
        first = self.render(self.actor, 1, detail_page=0)
        fields = dict(first.fields)
        self.assertIn('📋 基本資料', fields)
        self.assertIn('🏳️ 所屬陣營', fields)
        self.assertIn('💕 正式 CP', fields)
        self.assertIn('🏅 角色地位', fields)
        self.assertIn('💰 公開貨幣資產', fields)
        self.assertNotIn('📈 商業與名氣（5 項）', fields)
        self.assertEqual(first.image_url,'https://example.test/photo.png')
        second = dict(self.render(self.actor, 1, detail_page=1).fields)
        self.assertIn('📈 商業與名氣（5 項）', second)
        self.assertIn('🎤 藝能與實力（8 項）', second)
        self.assertIn('🏠 房產', second)
        last = dict(self.render(self.actor,1,detail_page=2).fields)
        self.assertIn('生平', ''.join(last))
        self.assertNotIn('絕對不可洩露', ''.join(last.values()))

    def test_public_preview_even_owner_hides_unpublished(self):
        self.actor.user_id = '42'
        shown = dict(self.render(self.actor,1,private=False,detail_page=2).fields)
        self.assertNotIn('絕對不可洩露', ''.join(shown.values()))
        private = dict(self.render(self.actor,1,private=True,detail_page=2).fields)
        self.assertIn('絕對不可洩露的性格', ''.join(private.values()))
        self.assertIn('絕對不可洩露的身世', ''.join(private.values()))

    def test_one_visibility_on_other_visibility_off(self):
        self.settings['show_personality'] = True
        shown = dict(self.render(self.actor,1,detail_page=2).fields)
        self.assertIn('絕對不可洩露的性格', ''.join(shown.values()))
        self.assertNotIn('絕對不可洩露的身世', ''.join(shown.values()))
        self.settings['show_personality'] = False
        self.settings['show_biography'] = True
        shown = dict(self.render(self.actor,1,detail_page=2).fields)
        self.assertNotIn('絕對不可洩露的性格', ''.join(shown.values()))
        self.assertIn('絕對不可洩露的身世', ''.join(shown.values()))

    def test_private_tab_for_non_owner_forbidden(self):
        with self.assertRaises(PermissionError):
            self.render(self.actor,1,private=True,detail_page=1)


class PrivacyRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbfile = str(Path(self.tmp.name) / 'characters.sqlite3')
        self.config_patch = patch('core.config.DATABASE_PATH', self.dbfile)
        self.database_patch = patch('core.database.DATABASE_PATH', self.dbfile)
        self.config_patch.start()
        self.database_patch.start()
        from core.database import get_connection
        with get_connection() as db:
            db.execute('CREATE TABLE characters (guild_id TEXT, id INTEGER,owner_id TEXT,status TEXT,settings_json TEXT,version INTEGER DEFAULT 0)')
            db.execute('INSERT INTO characters VALUES(?,?,?,?,?,?)', ('123', 1, '42', 'active', json.dumps({
                'private_personality':'內向但堅定', 'biography':'秘密出身', 'stage_name':'測試藝名', 'other':'不變'
            }, ensure_ascii=False), 0))
            db.execute('INSERT INTO characters VALUES(?,?,?,?,?,?)', ('other', 1, '42', 'active', '{}', 0))
            db.commit()

    def tearDown(self):
        self.database_patch.stop()
        self.config_patch.stop()
        self.tmp.cleanup()

    def _settings(self, guild='123'):
        from core.database import get_connection
        with get_connection() as db:
            return json.loads(db.execute('SELECT settings_json FROM characters WHERE guild_id=? AND id=1', (guild,)).fetchone()[0])

    def test_default_closed_and_all_four_combinations(self):
        from modules.character_view_privacy import visible_biography, update_visibility, visibility_state
        self.assertEqual(visible_biography(self._settings()), ('',''))
        self.assertEqual(visible_biography(self._settings(), True), ('內向但堅定','秘密出身'))
        update_visibility('123',1,'42','personality',True)
        self.assertEqual(visible_biography(self._settings()), ('內向但堅定',''))
        update_visibility('123',1,'42','biography',True)
        self.assertEqual(visible_biography(self._settings()), ('內向但堅定','秘密出身'))
        update_visibility('123',1,'42','personality',False)
        self.assertEqual(visible_biography(self._settings()), ('','秘密出身'))
        update_visibility('123',1,'42','biography',False)
        self.assertEqual(visible_biography(self._settings()), ('',''))
        self.assertEqual(self._settings()['other'], '不變')
        self.assertEqual(visibility_state(self._settings()), {'personality':False,'biography':False})

    def test_cross_user_and_cross_guild_isolation(self):
        from modules.character_view_privacy import update_visibility
        with self.assertRaises(PermissionError):
            update_visibility('123',1,'wrong','personality',True)
        with self.assertRaises(PermissionError):
            update_visibility('missing',1,'42','personality',True)
        self.assertEqual(self._settings('other'), {})
        self.assertFalse(self._settings().get('show_personality',False))

    def test_unknown_fields_and_non_bool_rejected(self):
        from modules.character_view_privacy import update_visibility
        with self.assertRaises(ValueError):
            update_visibility('123',1,'42','gender',True)
        with self.assertRaises(ValueError):
            update_visibility('123',1,'42','personality','true')

    def test_inactive_role_is_not_editable(self):
        from core.database import get_connection
        from modules.character_view_privacy import update_visibility
        with get_connection() as db:
            db.execute("UPDATE characters SET status='retired' WHERE guild_id='123'")
            db.commit()
        with self.assertRaises(ValueError):
            update_visibility('123',1,'42','biography',True)


if __name__ == '__main__':
    unittest.main()
