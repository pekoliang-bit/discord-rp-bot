
"""臨床 V2.3：獨立資料庫、診斷、住院、探病、續住、出院測試。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fd, db_path = tempfile.mkstemp(suffix='.sqlite')
os.close(fd)

try:
    import core.config as config
    config.DATABASE_PATH = db_path

    from core.database import (
        init_db,
        transaction,
        get_or_create_account,
    )
    from core.economy import _payment
    from core import pets_v1 as pets
    from core import pet_medical_v23 as medical
    from core import pet_clinical_v23 as clinical

    init_db()
    gid, uid = 'clinical-test', '98765'

    with transaction() as db:
        db.execute(
            'INSERT INTO guilds(guild_id,name) VALUES (?,?)',
            (gid, 'clinic')
        )

        cid = db.execute(
            '''INSERT INTO characters
            (guild_id,owner_id,name,status,public_code)
            VALUES (?,?,?, 'active',?)''',
            (gid, uid, '測試飼主', 'TEST-ID')
        ).lastrowid

        pets.initialize(db)
        t = medical.timestamp(medical.utcnow())

        pid = db.execute(
            '''INSERT INTO rp_pets_v1
            (
                guild_id,main_character_id,name,species,
                chip,health,bond,last_play,last_tick,illness
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (
                gid, cid, '布丁', '狗', 'TEST-CHIP',
                60, 100, t, t, 'injured'
            )
        ).lastrowid

        db.execute(
            'INSERT INTO rp_pet_owners_v1 VALUES (?,?,?,1)',
            (gid, pid, cid)
        )

        system = get_or_create_account(
            db, gid, 'system', 'seed', 'wallet'
        )
        wallet = get_or_create_account(
            db, gid, 'character', cid, 'wallet'
        )

        _payment(
            db, gid, 'clinical-fixture',
            system, wallet, 2_000_000,
            '測試資金'
        )

        medical.register_first_check(
            db, gid, pid,
            medical.utcnow() - timedelta(days=8)
        )

    class FixedRNG:
        def randint(self, start, end):
            return 90

    result = clinical.diagnose(
        gid, pid, cid, uid, rng=FixedRNG()
    )

    assert result['kind'] == 'injury'
    assert result['case_id'] == clinical.diagnose(
        gid, pid, cid, uid
    )['case_id']

    code = f"injury_{result['severity']}"
    q = clinical.quote_treatment({
        'severity': result['severity'],
        'illness_type': 'injury'
    })

    assert q['total'] == medical.PRICES[code][1]

    invoice = clinical.treat(
        gid, pid, cid, uid,
        result['case_id'],
        'clinical-treatment'
    )

    assert invoice['total'] == q['total']
    assert clinical.treat(
        gid, pid, cid, uid,
        result['case_id'],
        'clinical-treatment'
    ) == invoice

    stay = clinical.admit(
        gid, pid, cid, uid,
        'ward_basic', 2, 'clinical-stay',
        case_id=result['case_id']
    )

    assert stay['total'] == 20_000
    assert clinical.stay_info(
        gid, pid, cid, uid
    )['ward_code'] == 'ward_basic'

    try:
        pets.care(gid, pid, cid, uid, 'walk')
    except ValueError:
        pass
    else:
        raise AssertionError('住院期間仍可散步')

    visit = clinical.visit(gid, pid, cid, uid)
    assert visit['bond_plus'] == 5

    try:
        clinical.visit(gid, pid, cid, uid)
    except ValueError:
        pass
    else:
        raise AssertionError('12小時冷卻未生效')

    extension = clinical.extend(
        gid, pid, cid, uid,
        1, 'clinical-extend'
    )

    assert extension['total'] == 10_000
    assert clinical.extend(
        gid, pid, cid, uid,
        1, 'clinical-extend'
    )['repeated']

    transfer = clinical.transfer_ward(
        gid, pid, cid, uid,
        'ward_comfort', 'clinical-ward'
    )

    assert transfer['total'] > 0
    assert clinical.stay_info(
        gid, pid, cid, uid
    )['ward_code'] == 'ward_comfort'

    discharged = clinical.discharge(
        gid, pid, cid, uid, 'clinical-discharge'
    )

    assert discharged['pet_id'] == pid
    assert clinical.stay_info(
        gid, pid, cid, uid
    ) is None

    assert clinical.discharge(
        gid, pid, cid, uid, 'clinical-discharge'
    ) == discharged

    assert clinical.history(
        gid, pid, cid, uid
    )['invoices']

    print(
        'PASS: clinical diagnosis, treatment, admission, '
        'visits, extension, transfer, discharge'
    )

finally:
    try:
        os.unlink(db_path)
    except OSError:
        pass
