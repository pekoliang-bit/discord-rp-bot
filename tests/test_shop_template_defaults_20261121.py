"""執行：python -m pytest tests/test_shop_template_defaults_20261121.py -q"""
from pathlib import Path
import ast

BASE = Path(__file__).resolve().parents[1]

def test_template_counts_and_skus():
    for code, prefix in [('b02','BT'), ('b04','DT')]:
        path = BASE / 'assets' / 'shop_defaults' / f'{code}_default.txt'
        lines = [l for l in path.read_text(encoding='utf8').splitlines()
                 if l.strip() and not l.lstrip().startswith('#')]
        assert len(lines) == 50
        assert len(set(l.split('|')[0] for l in lines)) == 50
        assert [l.split('|')[0] for l in lines] == [f'{prefix}-{i:03d}' for i in range(1,51)]
        for line in lines:
            sku, brand, name, price, kind, stat, bonus, uses = line.split('|')
            uses, bonus, price = int(uses), int(bonus), int(price)
            assert name and brand and stat
            assert kind in ('持有','消耗')
            assert (uses == 0) if kind == '持有' else (uses > 0)
            assert price >= 300 * bonus * (uses if uses else 1)

def test_changed_python_syntax():
    for relative in ('modules/shop_b02.py','modules/shop_b04.py','modules/official_shop.py'):
        ast.parse((BASE/relative).read_text(encoding='utf8'))
