"""Pet market V2: selectable stock, required starter kit, atomic checkout and chip.

Pre-launch admin configuration is allowed; player operations remain launch gated
by the bot's interaction-wide gate. Unapproved catalog prices are provisional.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone

from core.database import transaction, get_connection, get_or_create_account
from core.economy import _payment
from core.world_interaction_rules import pet_rules
from core import pets_v1 as pets
from core import pet_catalog_v21 as v21
from core import pet_traits_v22 as behaviour


def setup(db, guild_id=None):
    pets.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_catalog_v2 (
      guild_id TEXT NOT NULL,kind TEXT NOT NULL,code TEXT NOT NULL,
      species TEXT NOT NULL DEFAULT '',label TEXT NOT NULL,price INTEGER NOT NULL,
      stock INTEGER NOT NULL DEFAULT -1,active INTEGER NOT NULL DEFAULT 1,
      basic INTEGER NOT NULL DEFAULT 0,description TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(guild_id,kind,code),CHECK(price>=0),CHECK(stock>=-1),
      CHECK(kind IN ('breed','item')))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_cart_v2 (
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
      kind TEXT NOT NULL,code TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(guild_id,character_id,kind,code),CHECK(quantity>0) )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_inventory_v2 (
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,code TEXT NOT NULL,
      quantity INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,character_id,code), CHECK(quantity>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_orders_v2 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
      character_id INTEGER NOT NULL,request_key TEXT NOT NULL,
      order_json TEXT NOT NULL,pet_id INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(guild_id,request_key))''')

    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_food_daily_v3 (
      guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,day TEXT NOT NULL,
      meals INTEGER NOT NULL DEFAULT 0,last_at TEXT,
      PRIMARY KEY(guild_id,pet_id,day))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_open_consumables_v3 (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
      character_id INTEGER NOT NULL, code TEXT NOT NULL,
      remaining INTEGER NOT NULL, opened_at TEXT NOT NULL,
      CHECK(remaining>=0))''')

    # 既有 V2 資料表原位升級，保留玩家購物車、訂單與庫存。
    columns = {
        r[1] for r in db.execute(
            'PRAGMA table_info(rp_pet_catalog_v2)'
        )
    }

    definitions = {
        'category': "TEXT NOT NULL DEFAULT '日常'",
        'subcategory': "TEXT NOT NULL DEFAULT ''",
        'servings': 'INTEGER NOT NULL DEFAULT 0',
        'quality': "TEXT NOT NULL DEFAULT ''",
        'required_group': "TEXT NOT NULL DEFAULT ''",
        'hidden_health': 'INTEGER NOT NULL DEFAULT 0',
        'hidden_bond': 'INTEGER NOT NULL DEFAULT 0',
        'permanent': 'INTEGER NOT NULL DEFAULT 0',
        'breed_codes': "TEXT NOT NULL DEFAULT '[]'",
    }

    for field, definition in definitions.items():
        if field not in columns:
            db.execute(
                f'ALTER TABLE rp_pet_catalog_v2 ADD COLUMN {field} {definition}'
            )

    if guild_id is not None:
        guild_id = str(guild_id)
        rules = pet_rules(guild_id)

        # 歷史泛用品種保留但停止上架（不動玩家已購買資料）。
        db.execute(
            """UPDATE rp_pet_catalog_v2 SET active=0
            WHERE guild_id=? AND kind='breed'
              AND code LIKE 'default:%'""",
            (guild_id,)
        )

        for code, species, label, price, active in v21.BREEDS:
            db.execute(
                """INSERT OR IGNORE INTO rp_pet_catalog_v2
                (guild_id,kind,code,species,label,price,active,description)
                VALUES (?,'breed',?,?,?,?,?,'正式核准品種基礎售價')""",
                (
                    guild_id, code, species,
                    label, price, active
                )
            )

        for item in v21.item_templates():
            db.execute(
                """INSERT OR IGNORE INTO rp_pet_catalog_v2
                (
                    guild_id,kind,code,species,label,price,
                    active,basic,description,category,quality,
                    required_group,hidden_health,hidden_bond,
                    permanent,breed_codes,subcategory,servings
                )
                VALUES (
                    ?,'item',?,?,?,?,1,0,'五級用品正式預設數值',
                    ?,?,?,?,?,?,?,?,?
                )""",
                (
                    guild_id,
                    item['code'],
                    item['species'],
                    item['label'],
                    item['price'],
                    item['category'],
                    item['quality'],
                    item['group'],
                    item['hidden_health'],
                    item['hidden_bond'],
                    int(item['permanent']),
                    '[]',item.get('subcategory',item['label']),item.get('servings',0)
                )
            )


def _row(db, gid, kind, code):
    row = db.execute(
        """SELECT * FROM rp_pet_catalog_v2
        WHERE guild_id=? AND kind=? AND code=?""",
        (gid, kind, code)
    ).fetchone()

    if not row:
        raise LookupError('商品不存在或尚未上架')

    return row


def catalog(guild_id, kind=None, species=None, active_only=True):
    gid = str(guild_id)

    with transaction() as db:
        setup(db, gid)

        query = 'SELECT * FROM rp_pet_catalog_v2 WHERE guild_id=?'
        args = [gid]

        if kind:
            query += ' AND kind=?'
            args.append(kind)

        if species is not None:
            query += ' AND species=?'
            args.append(species)

        if active_only:
            query += ' AND active=1 AND stock!=0'

        query += ' ORDER BY kind,species,basic DESC,price,code'

        return [
            dict(r) for r in db.execute(query, args)
        ]


def species_list(guild_id):
    return list(
        dict.fromkeys(
            r['species']
            for r in catalog(guild_id, 'breed')
            if r['species']
        )
    )


def manage(
    guild_id,
    kind,
    code,
    label,
    price,
    *,
    species='',
    stock=-1,
    active=True,
    basic=False,
    description='',
    delete=False
):
    gid = str(guild_id)
    kind = str(kind)
    code = str(code).strip()
    label = str(label).strip()

    if kind not in ('breed', 'item'):
        raise ValueError('商品類型必須為品種或用品')

    if not code or len(code) > 80 or not label or len(label) > 100:
        raise ValueError('商品代碼或名稱不正確')

    if (
        not isinstance(price, int)
        or not 0 <= price <= 10**10
        or not isinstance(stock, int)
        or stock < -1
    ):
        raise ValueError('售價或庫存不正確')

    if kind == 'breed' and not str(species).strip():
        raise ValueError('品種必須指定物種')

    rules = pet_rules(gid)

    if kind == 'breed' and (
        str(species) in rules['protected_species']
        or '保育' in str(species)
    ):
        raise PermissionError('保育類物種不可販售')

    if delete:
        active = False

    with transaction() as db:
        setup(db, gid)

        db.execute(
            '''INSERT INTO rp_pet_catalog_v2
            (
                guild_id,kind,code,species,label,price,
                stock,active,basic,description
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(guild_id,kind,code)
            DO UPDATE SET
                species=excluded.species,
                label=excluded.label,
                price=excluded.price,
                stock=excluded.stock,
                active=excluded.active,
                basic=excluded.basic,
                description=excluded.description''',
            (
                gid,
                kind,
                code,
                str(species).strip() if kind == 'breed' else '',
                label,
                price,
                stock,
                int(bool(active)),
                int(bool(basic) and kind == 'item'),
                str(description)[:1000]
            )
        )

    return {
        'kind': kind,
        'code': code,
        'active': bool(active),
        'price': price
    }


def _actor(db, gid, cid, uid):
    pets._actor(db, gid, cid, uid)


def select(guild_id, character_id, user_id, kind, code, quantity=1):
    gid = str(guild_id)
    cid = int(character_id)
    quantity = int(quantity)

    if kind not in ('breed', 'item') or not 1 <= quantity <= 99:
        raise ValueError('選購數量不正確')

    with transaction() as db:
        setup(db, gid)
        _actor(db, gid, cid, user_id)

        item = _row(db, gid, kind, str(code))

        if not item['active'] or item['stock'] == 0:
            raise ValueError('商品已下架或售罄')

        if kind == 'item' and item['required_group']:
            chosen = db.execute(
                """SELECT a.species
                FROM rp_pet_cart_v2 c
                JOIN rp_pet_catalog_v2 a
                  ON a.guild_id=c.guild_id
                 AND a.kind=c.kind
                 AND a.code=c.code
                WHERE c.guild_id=?
                  AND c.character_id=?
                  AND c.kind='breed'""",
                (gid, cid)
            ).fetchone()

            if chosen and item['species'] != chosen['species']:
                raise ValueError('商品不適用所選寵物物種')

            db.execute(
                """DELETE FROM rp_pet_cart_v2
                WHERE guild_id=? AND character_id=?
                  AND kind='item'
                  AND code IN (
                    SELECT code FROM rp_pet_catalog_v2
                    WHERE guild_id=? AND kind='item'
                      AND species=? AND required_group=?
                  )""",
                (
                    gid, cid, gid,
                    item['species'],
                    item['required_group']
                )
            )

        if kind == 'breed':
            quantity = 1
            db.execute(
                """DELETE FROM rp_pet_cart_v2
                WHERE guild_id=? AND character_id=? AND kind='breed'""",
                (gid, cid)
            )

        db.execute(
            '''INSERT INTO rp_pet_cart_v2
            (guild_id,character_id,kind,code,quantity)
            VALUES (?,?,?,?,?)
            ON CONFLICT(guild_id,character_id,kind,code)
            DO UPDATE SET quantity=excluded.quantity''',
            (gid, cid, kind, str(code), quantity)
        )

    return preview(gid, cid)


def select_many(guild_id, character_id, user_id, selections):
    """多選商品分別指定數量；整批原子加入購物車。"""
    gid,cid=str(guild_id),int(character_id)
    if not selections or len(selections)>5:raise ValueError('一次只能勾選1～5款商品')
    pairs=[(str(code),int(qty)) for code,qty in selections]
    if len({x[0] for x in pairs})!=len(pairs):raise ValueError('不能重複選擇相同商品')
    if any(qty<1 or qty>99 for _,qty in pairs):raise ValueError('每項商品數量必須是1～99')
    with transaction() as db:
        setup(db,gid);_actor(db,gid,cid,user_id)
        breed=db.execute("""SELECT b.species FROM rp_pet_cart_v2 c
            JOIN rp_pet_catalog_v2 b ON b.guild_id=c.guild_id AND b.kind=c.kind AND b.code=c.code
            WHERE c.guild_id=? AND c.character_id=? AND c.kind='breed' LIMIT 1""",(gid,cid)).fetchone()
        for code,qty in pairs:
            item=_row(db,gid,'item',code)
            if not item['active'] or item['stock']==0 or (item['stock']>=0 and qty>item['stock']):
                raise ValueError('商品下架或庫存不足')
            if breed and item['species'] and item['species']!=breed['species']:
                raise ValueError('商品不符合已選寵物物種')
            db.execute("""INSERT INTO rp_pet_cart_v2(guild_id,character_id,kind,code,quantity)
                VALUES (?,?,'item',?,?) ON CONFLICT(guild_id,character_id,kind,code)
                DO UPDATE SET quantity=excluded.quantity""",(gid,cid,code,qty))
        return _preview(db,gid,cid)


def remove(guild_id, character_id, user_id, kind, code):
    gid = str(guild_id)
    cid = int(character_id)

    with transaction() as db:
        setup(db, gid)
        _actor(db, gid, cid, user_id)

        db.execute(
            '''DELETE FROM rp_pet_cart_v2
            WHERE guild_id=? AND character_id=? AND kind=? AND code=?''',
            (gid, cid, kind, str(code))
        )

    return preview(gid, cid)


def _wizard_item_compatible(species,group,subcategory):
    """只判定首購引導是否能滿足必需品，不禁止玩家另購其他商品。"""
    sub=str(subcategory or '')
    if species=='鸚鵡':
        if group=='bed':return '棲木' in sub or '鳥架' in sub
        if group=='toilet':return '籠底紙' in sub
        if group=='carrier':return '航空箱' in sub or '外出籠' in sub
    elif group=='bed':
        return '睡床' in sub or '寵物窩' in sub
    if species=='貓' and group=='toilet':return '廁所' in sub or '貓砂盆' in sub
    if species=='狗' and group=='leash':return '牽繩' in sub or '背帶' in sub
    return True


def _preview(db, gid, cid):
    rows = db.execute(
        '''SELECT
            c.kind,c.code,c.quantity,
            a.label,a.price,a.species,
            a.stock,a.active,a.basic
        FROM rp_pet_cart_v2 c
        JOIN rp_pet_catalog_v2 a
          ON c.guild_id=a.guild_id
         AND c.kind=a.kind
         AND c.code=a.code
        WHERE c.guild_id=? AND c.character_id=?
        ORDER BY c.kind,c.code''',
        (gid, cid)
    ).fetchall()

    content = [dict(x) for x in rows]

    valid = not any(
        x['active'] == 0
        or x['stock'] == 0
        or (
            x['stock'] >= 0
            and x['quantity'] > x['stock']
        )
        for x in content
    )

    breed = [
        x for x in content
        if x['kind'] == 'breed'
    ]

    chosen = breed[0] if len(breed) == 1 else None

    if chosen and chosen['code'].startswith('v21:'):
        selected_breed = db.execute(
            """SELECT label FROM rp_pet_catalog_v2
            WHERE guild_id=? AND kind='breed' AND code=?""",
            (gid, chosen['code'])
        ).fetchone()

        breed_label = (
            selected_breed['label']
            if selected_breed
            else ''
        )

        required = [
            group
            for group, _, is_required in v21.wizard_pages(
                chosen['species'],
                breed_label
            )
            if is_required
        ]

        selected_groups = []

        for item in content:
            if item['kind'] != 'item':
                continue

            meta = db.execute(
                """SELECT species,required_group,breed_codes,subcategory
                FROM rp_pet_catalog_v2
                WHERE guild_id=? AND kind='item' AND code=?""",
                (gid, item['code'])
            ).fetchone()

            if (
                meta
                and meta['species'] == chosen['species']
                and meta['required_group']
                and _wizard_item_compatible(chosen['species'],meta['required_group'],meta['subcategory'])
            ):
                selected_groups.append(meta['required_group'])

        missing = [group for group in required if group not in selected_groups]
        # 食盆及水碗必須各選一款；同一組商品不能以單個食盆充作兩件。
        bowl_types=set()
        for item in content:
            if item['kind']=='item':
                row=db.execute("SELECT subcategory,required_group FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind='item' AND code=?",(gid,item['code'])).fetchone()
                if row and row['required_group']=='bowl':
                    bowl_types.add(str(row['subcategory']))
        if 'bowl' in required and not ('食盆' in bowl_types and '水盆' in bowl_types):
            if 'bowl' not in missing:missing.append('bowl')

        for item in content:
            if item['kind'] != 'item':
                continue

            row = db.execute(
                """SELECT species FROM rp_pet_catalog_v2
                WHERE guild_id=? AND kind='item' AND code=?""",
                (gid, item['code'])
            ).fetchone()

            if (
                row
                and row['species']
                and row['species'] != chosen['species']
            ):
                valid = False

    else:
        basics = [
            dict(x)
            for x in db.execute(
                """SELECT * FROM rp_pet_catalog_v2
                WHERE guild_id=? AND kind='item'
                  AND basic=1 AND active=1""",
                (gid,)
            )
        ]

        missing = [
            item['code']
            for item in basics
            if not any(
                chosen_item['code'] == item['code']
                and chosen_item['quantity'] >= 1
                for chosen_item in content
            )
        ]

        if chosen:
            for required_code in ('starter_feed', 'starter_kit'):
                if (
                    required_code not in [
                        r['code'] for r in basics
                    ]
                    or required_code not in [
                        r['code']
                        for r in content
                        if r['kind'] == 'item'
                    ]
                ):
                    if required_code not in missing:
                        missing.append(required_code)
        if chosen and not basics:
            missing.append('尚未上架任何基礎用品')

    return {
        'items': content,
        'total': sum(
            x['price'] * x['quantity']
            for x in content
        ),
        'pet_selected': len(breed) == 1,
        'missing_basics': missing,
        'ready': valid and len(breed) == 1 and not missing
    }


def preview(guild_id, character_id):
    gid = str(guild_id)

    with transaction() as db:
        setup(db, gid)
        return _preview(db, gid, int(character_id))


def _birthday_discount(db, gid, cid, rows):
    """Apply exactly one externally configured 72h birthday offer.

    The birthday module is optional during staged deployment; when installed,
    it must expose eligible_discount(db,guild_id,character_id) returning 0..1.
    Pet breed prices are never eligible; only item lines are discounted.
    """
    try:
        from core.birthday_core_v3 import eligible_discount
    except ImportError:
        return 0
    rate=float(eligible_discount(db,gid,cid))
    if not 0<=rate<=1:
        raise ValueError('生日商城折扣設定無效')
    discounted=sum(int(x['price'])*int(x['quantity']) for x in rows if x['kind']=='item')
    return int(discounted*rate)


def checkout(guild_id, character_id, user_id, pet_name, request_key, *, age_stage='young'):
    gid = str(guild_id)
    cid = int(character_id)
    pet_name = str(pet_name).strip()[:50]

    if not pet_name:
        raise ValueError('請先填寫寵物名字')
    if age_stage not in ('young', 'adult'):
        raise ValueError('寵物年齡請選擇幼年或成年')

    if not str(request_key).strip():
        raise ValueError('缺少交易識別碼')

    with transaction() as db:
        setup(db, gid)
        _actor(db, gid, cid, user_id)

        old = db.execute(
            '''SELECT order_json FROM rp_pet_orders_v2
            WHERE guild_id=? AND request_key=?''',
            (gid, str(request_key))
        ).fetchone()

        if old:
            recorded=json.loads(old['order_json'])
            owner=db.execute('SELECT character_id FROM rp_pet_orders_v2 WHERE guild_id=? AND request_key=?',
                (gid,str(request_key))).fetchone()
            if not owner or int(owner['character_id'])!=cid:
                raise PermissionError('交易識別碼屬於其他角色')
            return recorded

        from core.pet_agreement_v23 import has_signed

        if not has_signed(db, gid, cid):
            raise PermissionError(
                '請先依序閱讀十頁須知並以正式姓名及身分證字號簽署'
            )

        state = _preview(db, gid, cid)

        if not state['ready']:
            raise ValueError(
                '請先選擇寵物，並加入全部必備用品；'
                '確認商品未下架且庫存足夠'
            )

        discount=_birthday_discount(db,gid,cid,state['items'])
        # 寵物用品商城與一般商城一樣：9折及生日折價券擇優、不可疊加。
        # 寵物本體不是用品，不能拿寵物售價抵扣優惠券。
        eligible_items=sum(int(row['price'])*int(row['quantity'])
                           for row in state['items'] if row['kind']=='item')
        from core.birthday_core_v3 import apply_armed_coupon
        coupon_saved=apply_armed_coupon(db,gid,cid,
            f'pet-center:{gid}:{request_key}',eligible_items,discount)
        discount=max(discount,coupon_saved)
        payable=state['total']-discount

        breed = next(
            x for x in state['items']
            if x['kind'] == 'breed'
        )

        rules = pet_rules(gid)

        if (
            breed['species'] in rules['protected_species']
            or '保育' in breed['species']
        ):
            raise PermissionError('保育類動物不可交易')

        for item in state['items']:
            if item['stock'] >= 0:
                result = db.execute(
                    '''UPDATE rp_pet_catalog_v2
                    SET stock=stock-?
                    WHERE guild_id=? AND kind=? AND code=?
                      AND active=1 AND stock>=?''',
                    (
                        item['quantity'],
                        gid,
                        item['kind'],
                        item['code'],
                        item['quantity']
                    )
                )

                if result.rowcount != 1:
                    raise ValueError(
                        '商品庫存不足，請重新確認訂單'
                    )

        timestamp = datetime.now(timezone.utc).isoformat()
        chip = 'P-' + secrets.token_hex(12).upper()

        inserted = db.execute(
            '''INSERT INTO rp_pets_v1
            (
                guild_id,main_character_id,name,species,
                rarity,chip,health,bond,last_play,last_tick
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (
                gid,
                cid,
                pet_name,
                breed['species'],
                breed['code'],
                chip,
                rules['health_max'],
                rules['bond_max'],
                timestamp,
                timestamp
            )
        )

        pid = int(inserted.lastrowid)
        pets._new_life(db, gid, pid, breed['code'], age_stage)

        if payable:
            sender = get_or_create_account(
                db, gid, 'character', cid, 'wallet'
            )
            receiver = get_or_create_account(
                db, gid, 'system', 'pet_center', 'wallet'
            )

            _payment(
                db,
                gid,
                'pet:checkout:' + gid + ':' + str(request_key),
                sender,
                receiver,
                payable,
                '寵物＋必備飼養用品一次結帳與晶片登記'
            )

        for item in state['items']:
            if item['kind'] != 'item':
                continue

            db.execute(
                '''INSERT INTO rp_pet_inventory_v2
                (guild_id,character_id,code,quantity)
                VALUES (?,?,?,?)
                ON CONFLICT(guild_id,character_id,code)
                DO UPDATE SET quantity=quantity+excluded.quantity''',
                (
                    gid,
                    cid,
                    item['code'],
                    item['quantity']
                )
            )

        db.execute(
            'INSERT INTO rp_pet_owners_v1 VALUES (?,?,?,1)',
            (gid, pid, cid)
        )

        behaviour.attach_purchased(
            db, gid, pid, cid, state['items']
        )

        from core.pet_medical_v23 import register_first_check

        register_first_check(db, gid, pid)

        db.execute(
            '''INSERT INTO rp_pet_events_v1
            (
                guild_id,pet_id,actor_character_id,
                event_type,detail_json
            )
            VALUES (?,?,?,?,?)''',
            (
                gid,
                pid,
                cid,
                'checkout',
                json.dumps(
                    state,
                    ensure_ascii=False
                )
            )
        )

        result = {
            'pet_id': pid,
            'name': pet_name,
            'species': breed['species'],
            'breed': breed['label'],
            'chip': chip,
            'age_stage': age_stage,
            'total': payable,
            'original_total': state['total'],
            'birthday_discount': discount,
            'items': state['items']
        }

        db.execute(
            '''INSERT INTO rp_pet_orders_v2
            (
                guild_id,character_id,request_key,
                order_json,pet_id
            )
            VALUES (?,?,?,?,?)''',
            (
                gid,
                cid,
                str(request_key),
                json.dumps(
                    result,
                    ensure_ascii=False
                ),
                pid
            )
        )

        db.execute(
            '''DELETE FROM rp_pet_cart_v2
            WHERE guild_id=? AND character_id=?''',
            (gid, cid)
        )

        return result


def inventory(guild_id, character_id):
    with transaction() as db:
        setup(db, guild_id)

        return [
            dict(r)
            for r in db.execute(
                '''SELECT code,quantity
                FROM rp_pet_inventory_v2
                WHERE guild_id=? AND character_id=?
                  AND quantity>0''',
                (str(guild_id), int(character_id))
            )
        ]


def checkout_supplies(
    guild_id,
    character_id,
    user_id,
    request_key
):
    # Restock supplies for existing pets without buying another animal/chip.
    gid = str(guild_id)
    cid = int(character_id)

    with transaction() as db:
        setup(db, gid)
        _actor(db, gid, cid, user_id)

        old = db.execute(
            '''SELECT order_json
            FROM rp_pet_orders_v2
            WHERE guild_id=? AND request_key=?''',
            (gid, str(request_key))
        ).fetchone()

        if old:
            recorded=json.loads(old['order_json'])
            owner=db.execute('SELECT character_id FROM rp_pet_orders_v2 WHERE guild_id=? AND request_key=?',
                (gid,str(request_key))).fetchone()
            if not owner or int(owner['character_id'])!=cid:
                raise PermissionError('交易識別碼屬於其他角色')
            return recorded

        state = _preview(db, gid, cid)

        if (
            not state['items']
            or any(
                x['kind'] == 'breed'
                for x in state['items']
            )
        ):
            raise ValueError(
                '用品補貨只能選擇飼料／用品；'
                '選了寵物請走晶片結帳流程'
            )

        if any(
            not x['active']
            or x['stock'] == 0
            or (
                x['stock'] >= 0
                and x['quantity'] > x['stock']
            )
            for x in state['items']
        ):
            raise ValueError('商品已下架或庫存不足')

        for item in state['items']:
            if item['stock'] >= 0:
                changed = db.execute(
                    '''UPDATE rp_pet_catalog_v2
                    SET stock=stock-?
                    WHERE guild_id=? AND kind=? AND code=?
                      AND active=1 AND stock>=?''',
                    (
                        item['quantity'],
                        gid,
                        item['kind'],
                        item['code'],
                        item['quantity']
                    )
                )

                if changed.rowcount != 1:
                    raise ValueError('庫存不足')

        discount=_birthday_discount(db,gid,cid,state['items'])
        # 寵物用品商城與一般商城一樣：9折及生日折價券擇優、不可疊加。
        # 寵物本體不是用品，不能拿寵物售價抵扣優惠券。
        eligible_items=sum(int(row['price'])*int(row['quantity'])
                           for row in state['items'] if row['kind']=='item')
        from core.birthday_core_v3 import apply_armed_coupon
        coupon_saved=apply_armed_coupon(db,gid,cid,
            f'pet-center:{gid}:{request_key}',eligible_items,discount)
        discount=max(discount,coupon_saved)
        payable=state['total']-discount
        if payable:
            sender = get_or_create_account(
                db, gid, 'character', cid, 'wallet'
            )
            receiver = get_or_create_account(
                db, gid, 'system', 'pet_center', 'wallet'
            )

            _payment(
                db,
                gid,
                'pet:supplies:' + gid + ':' + str(request_key),
                sender,
                receiver,
                payable,
                '寵物飼料／用品統一結帳'
            )

        for item in state['items']:
            db.execute(
                '''INSERT INTO rp_pet_inventory_v2
                (
                    guild_id,character_id,code,quantity
                )
                VALUES (?,?,?,?)
                ON CONFLICT(guild_id,character_id,code)
                DO UPDATE SET quantity=quantity+excluded.quantity''',
                (
                    gid,
                    cid,
                    item['code'],
                    item['quantity']
                )
            )

        result = {
            'pet_id': None,
            'chip': None,
            'total': payable,
            'original_total': state['total'],
            'birthday_discount': discount,
            'items': state['items']
        }

        db.execute(
            '''INSERT INTO rp_pet_orders_v2
            (
                guild_id,character_id,request_key,
                order_json,pet_id
            )
            VALUES (?,?,?,?,0)''',
            (
                gid,
                cid,
                str(request_key),
                json.dumps(
                    result,
                    ensure_ascii=False
                )
            )
        )

        db.execute(
            '''DELETE FROM rp_pet_cart_v2
            WHERE guild_id=? AND character_id=?''',
            (gid, cid)
        )

        return result


def feed(guild_id, pet_id, character_id, user_id):
    """Use one serving from an opened eligible feed package, not the entire item.

    All packaging and serving updates occur in the same economy transaction.
    Existing legacy starter_feed is still recognised for player inventory safety.
    """
    gid, cid, pid = str(guild_id), int(character_id), int(pet_id)
    rules = pet_rules(gid)
    with transaction() as db:
        setup(db, gid)
        _actor(db, gid, cid, user_id)
        pets._access(db, gid, pid, cid)
        pet = pets._pet(db, gid, pid)
        if pets._hospitalized(db, gid, pid):
            raise ValueError('寵物住院中，請從醫療中心進行照護與探病')
        if pet['state'] in ('missing', 'deceased', 'critical'):
            raise ValueError('目前無法一般餵食；危急狀態必須接受醫療')
        if pet['illness']:
            # Normal food is still permitted with illness; it does not cure it.
            pass
        current=datetime.now(timezone.utc)
        day=current.date().isoformat()
        used=db.execute('SELECT meals,last_at FROM rp_pet_food_daily_v3 WHERE guild_id=? AND pet_id=? AND day=?',
                        (gid,pid,day)).fetchone()
        if used and int(used['meals'])>=2:
            raise ValueError('這隻寵物今天已正常餵食兩餐')
        if used and used['last_at'] and (current-datetime.fromisoformat(used['last_at'])).total_seconds()<21600:
            raise ValueError('兩餐之間至少需間隔6小時')
        # Only a valid species-specific feed or legacy feed can be selected.
        possible = db.execute('''SELECT i.code, i.quantity, c.quality, c.hidden_health
          FROM rp_pet_inventory_v2 i LEFT JOIN rp_pet_catalog_v2 c
          ON c.guild_id=i.guild_id AND c.kind='item' AND c.code=i.code
          WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0
            AND (i.code='starter_feed' OR
                 (c.required_group='feed' AND c.species=?))
          ORDER BY i.code''', (gid,cid,pet['species'])).fetchall()
        open_row = db.execute('''SELECT o.id,o.code,o.remaining,c.hidden_health
          FROM rp_pet_open_consumables_v3 o LEFT JOIN rp_pet_catalog_v2 c
            ON c.guild_id=o.guild_id AND c.kind='item' AND c.code=o.code
          WHERE o.guild_id=? AND o.character_id=? AND o.remaining>0
            AND (o.code='starter_feed' OR
                 (c.required_group='feed' AND c.species=?))
          ORDER BY o.opened_at,o.id LIMIT 1''', (gid,cid,pet['species'])).fetchone()
        if open_row:
            code = open_row['code']
            db.execute('UPDATE rp_pet_open_consumables_v3 SET remaining=remaining-1 WHERE id=? AND remaining>0',
                       (int(open_row['id']),))
            extra = int(open_row['hidden_health'] or 0)
        elif possible:
            pkg=possible[0]
            code=pkg['code']
            reduced=db.execute('''UPDATE rp_pet_inventory_v2 SET quantity=quantity-1
              WHERE guild_id=? AND character_id=? AND code=? AND quantity>=1''', (gid,cid,code))
            if reduced.rowcount != 1:
                raise ValueError('飼料庫存不足')
            db.execute('''INSERT INTO rp_pet_open_consumables_v3
              (guild_id,character_id,code,remaining,opened_at) VALUES (?,?,?,?,?)''',
                       (gid,cid,code,9,datetime.now(timezone.utc).isoformat()))
            extra=int(pkg['hidden_health'] or 0)
        else:
            raise ValueError('沒有適用的飼料，請先到寵物用品商城購買')
        db.execute('''INSERT INTO rp_pet_food_daily_v3(guild_id,pet_id,day,meals,last_at)
          VALUES (?,?,?,?,?) ON CONFLICT(guild_id,pet_id,day)
          DO UPDATE SET meals=meals+1,last_at=excluded.last_at''',
          (gid,pid,day,1,current.isoformat()))
        # T1..T5 is determined by catalogue quality; never multiply per owned package.
        health=pets.clamp(int(pet['health'])+4+max(0,min(5,extra)),rules['health_max'])
        db.execute('UPDATE rp_pets_v1 SET health=? WHERE guild_id=? AND id=?', (health,gid,pid))
        db.execute('''INSERT INTO rp_pet_events_v1
          (guild_id,pet_id,actor_character_id,event_type,detail_json)
          VALUES (?,?,?,?,?)''',
          (gid,pid,cid,'feed',json.dumps({'code':code,'servings':1},ensure_ascii=False)))
        return {'health':health,'bond':int(pet['bond']),'state':pet['state'],'code':code}


def rename(
    guild_id,
    pet_id,
    character_id,
    user_id,
    new_name
):
    gid = str(guild_id)
    name = str(new_name).strip()[:50]

    if not name:
        raise ValueError('名字不能空白')

    with transaction() as db:
        setup(db, gid)
        _actor(db, gid, character_id, user_id)
        pets._access(
            db, gid, pet_id, character_id
        )

        pet = pets._pet(db, gid, pet_id)

        if pet['state'] == 'deceased':
            raise ValueError(
                '無法更改已故寵物的名字'
            )

        db.execute(
            '''UPDATE rp_pets_v1
            SET name=?
            WHERE guild_id=? AND id=?''',
            (name, gid, int(pet_id))
        )

        db.execute(
            '''INSERT INTO rp_pet_events_v1
            (
                guild_id,pet_id,actor_character_id,
                event_type,detail_json
            )
            VALUES (?,?,?,?,?)''',
            (
                gid,
                int(pet_id),
                int(character_id),
                'rename',
                json.dumps(
                    {
                        'old': pet['name'],
                        'new': name
                    },
                    ensure_ascii=False
                )
            )
        )

        return name


def action_encounter(
    guild_id,
    character_id,
    user_id,
    action_key,
    rng=None,
    location_key=None
):
    """Roll after successful /行動.

    Idempotent per action transaction.
    Return None when no encounter.
    Never callable from a selectable /寵物中心 menu.
    """
    import random

    from core.pet_location_events_v21 import (
        get_rules as get_location_rules
    )

    gid = str(guild_id)
    cid = int(character_id)
    rules = pet_rules(gid)
    rng = rng or random.SystemRandom()

    local = (
        get_location_rules(gid, location_key)
        if location_key is not None
        else None
    )

    with transaction() as db:
        setup(db, gid)
        _actor(db, gid, cid, user_id)

        db.execute(
            '''CREATE TABLE IF NOT EXISTS rp_pet_action_rolls_v2 (
              guild_id TEXT NOT NULL,
              request_key TEXT NOT NULL,
              character_id INTEGER NOT NULL,
              encounter_id INTEGER,
              PRIMARY KEY(guild_id,request_key)
            )'''
        )

        prev = db.execute(
            '''SELECT encounter_id
            FROM rp_pet_action_rolls_v2
            WHERE guild_id=? AND request_key=?''',
            (gid, str(action_key))
        ).fetchone()

        if prev:
            return (
                int(prev['encounter_id'])
                if prev['encounter_id']
                else None
            )

        chance = max(
            0,
            min(
                100,
                int(
                    local['chance']
                    if local
                    else rules.get(
                        'encounter_after_action_chance',
                        5
                    )
                )
            )
        )

        encounter_id = None

        if rng.randint(1, 100) <= chance:
            posters = db.execute(
                """SELECT pet_id,reward
                FROM rp_pet_posters_v1
                WHERE guild_id=? AND state='open'""",
                (gid,)
            ).fetchall()

            chance_missing = (
                min(
                    45,
                    15 + sum(
                        min(
                            5,
                            int(x['reward']) // 3000
                        )
                        for x in posters
                    )
                )
                if posters
                else 0
            )

            if (
                posters
                and rng.randint(1, 100)
                <= chance_missing
            ):
                selected = rng.choice(posters)
                kind = 'missing'
                pid = int(selected['pet_id'])

            else:
                pool = (
                    local['pool']
                    if local
                    else [
                        'stray_cat',
                        'stray_dog',
                        'missing_pet',
                        'nothing'
                    ]
                )

                available = [
                    x for x in pool
                    if x not in (
                        'nothing',
                        'missing_pet'
                    )
                ]

                if not available:
                    available = ['stray_cat']

                chosen = rng.choice(available)

                kind = (
                    'stray'
                    if chosen in (
                        'stray_cat',
                        'stray_dog'
                    )
                    else 'injured'
                )
                pid = None

            encounter_id = db.execute(
                '''INSERT INTO rp_pet_encounters_v1
                (
                    guild_id,actor_character_id,
                    kind,pet_id
                )
                VALUES (?,?,?,?)''',
                (gid, cid, kind, pid)
            ).lastrowid
            if kind in ('stray','injured'):
                breed_code = 'stray:cat' if chosen == 'stray_cat' else 'stray:dog:medium'
                pets._generate_stray_life(db,gid,encounter_id,breed_code)

        db.execute(
            '''INSERT INTO rp_pet_action_rolls_v2
            VALUES (?,?,?,?)''',
            (
                gid,
                str(action_key),
                cid,
                encounter_id
            )
        )

        return encounter_id


def encounter_detail(
    guild_id,
    encounter_id
):
    with get_connection() as db:
        row = db.execute(
            '''SELECT *
            FROM rp_pet_encounters_v1
            WHERE guild_id=? AND id=?''',
            (
                str(guild_id),
                int(encounter_id)
            )
        ).fetchone()

        if not row:
            return None
        # Miracle identity and locked lifespan remain server-only secrets.
        return {k: v for k, v in dict(row).items()
                if k not in ('miracle', 'life_years')}


def shop_categories(guild_id, species):
    rows=catalog(guild_id,'item',species)
    return sorted({str(r['category']) for r in rows})


def shop_subcategories(guild_id, species, category):
    rows=catalog(guild_id,'item',species)
    return sorted({str(r.get('subcategory') or r['label'].split('・')[0])
                   for r in rows if r['category']==category})


def shop_products(guild_id,species,category,subcategory):
    return [r for r in catalog(guild_id,'item',species)
            if r['category']==category and
            (r.get('subcategory') or r['label'].split('・')[0])==subcategory]


def wizard_items(
    guild_id,
    species,
    group,
    breed_code=None
):
    """依頁面、物種列出商品；隱藏加成不回傳給介面。"""
    if species not in v21.PAGES:
        return []

    rows = catalog(
        guild_id,
        'item',
        species
    )

    return [row for row in rows if row.get('required_group')==group
            and _wizard_item_compatible(species,group,row.get('subcategory'))]


def wizard_pages(
    guild_id,
    breed_code
):
    row = next(
        (
            r for r in catalog(
                guild_id,
                'breed'
            )
            if r['code'] == breed_code
        ),
        None
    )

    if not row:
        raise LookupError('請先選擇已上架品種')

    return v21.wizard_pages(
        row['species'],
        row['label']
    )
