"""任務獎勵的可重試結算。僅可信任的任務審核流程可呼叫。

金幣交給雙分錄金流，數值交給正式 stat_rewards。兩者各自使用
穩定的唯一鍵；斷線重試不重複發放。物品尚無驗證完成的通用
入庫介面，因此留待發放，不會錯誤標為 claimed。
"""
from __future__ import annotations

import json
from core.database import get_connection, transaction
from modules.task_repository import initialize
from modules.task_validation import validate_rewards


def initialize_rewards():
    initialize()
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_reward_parts (
          guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
          task_id INTEGER NOT NULL, cycle_key TEXT NOT NULL,
          part TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
          details TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(guild_id,character_id,task_id,cycle_key,part),
          CHECK(state IN ('pending','done'))
        )''')


def _row(guild_id, character_id, task_id, cycle_key):
    with get_connection() as db:
        row = db.execute('''SELECT * FROM rp_task_progress WHERE guild_id=?
            AND character_id=? AND task_id=? AND cycle_key=?''',
            (str(guild_id), int(character_id), int(task_id), str(cycle_key))).fetchone()
    if row is None or row['status'] not in ('completed', 'claimed'):
        raise ValueError('僅能結算已核准完成的任務')
    return dict(row)


def _done(gid, cid, tid, cycle, part):
    with transaction() as db:
        db.execute('''INSERT INTO rp_task_reward_parts
           (guild_id,character_id,task_id,cycle_key,part,state) VALUES (?,?,?,?,?,'done')
           ON CONFLICT(guild_id,character_id,task_id,cycle_key,part)
           DO UPDATE SET state='done',updated_at=CURRENT_TIMESTAMP''',
           (gid,cid,tid,cycle,part))


def _is_done(gid, cid, tid, cycle, part):
    with get_connection() as db:
        row=db.execute('''SELECT state FROM rp_task_reward_parts WHERE guild_id=?
           AND character_id=? AND task_id=? AND cycle_key=? AND part=?''',
           (gid,cid,tid,cycle,part)).fetchone()
    return bool(row and row['state']=='done')


def settle_reward(guild_id, character_id, task_id, cycle_key='once'):
    """返回各部分已結算情況。異常不宣稱完成，重試可安全續領。"""
    initialize_rewards()
    gid,cid,tid,cycle=str(guild_id),int(character_id),int(task_id),str(cycle_key)
    row=_row(gid,cid,tid,cycle)
    snapshot=json.loads(row['snapshot_json'])
    rewards=validate_rewards(json.loads(snapshot.get('rewards_json') or '{}'))
    prefix=f'task:{gid}:{cid}:{tid}:{cycle}'
    outcome={'coins':'none','attributes':'none','items':'none','claimed':False}

    amount=rewards.get('coins',0)
    if amount:
        if not _is_done(gid,cid,tid,cycle,'coins'):
            from core.economy import issue_system_reward
            issue_system_reward(gid,prefix+':coins','character',cid,amount,
                                f'任務 #{tid} 獎勵')
            _done(gid,cid,tid,cycle,'coins')
        outcome['coins']='done'

    changes=rewards.get('attributes') or {}
    if changes:
        if not _is_done(gid,cid,tid,cycle,'attributes'):
            from core.stat_rewards import grant_stats
            grant_stats(gid,cid,changes,source='mission_reward',
                        source_id=prefix+':attributes',reason=f'任務 #{tid} 核准獎勵')
            _done(gid,cid,tid,cycle,'attributes')
        outcome['attributes']='done'

    items=rewards.get('items') or []
    if items:
        # 只有官方商城已存在的商品 ID 才能結算；每件商品和
        # part 完成記錄在同一個 SQLite 交易中，杜絕重複領取。
        # 獎勵為系統派發，不消耗商城公開銷售庫存。
        from modules.task_inventory_bridge import deliver_official_item
        for item in items:
            raw_id=str(item['item_id'])
            if not raw_id.isdigit() or int(raw_id)<=0:
                outcome['items']='pending_invalid_product_id'
                return outcome
            product_id=int(raw_id)
            part=f'item:{product_id}'
            # 相同商品在獎勵清單出現多次時合併數量。
            if _is_done(gid,cid,tid,cycle,part):
                continue
            quantity=sum(int(x['quantity']) for x in items if str(x['item_id'])==raw_id)
            with transaction() as db:
                existing=db.execute('''SELECT state FROM rp_task_reward_parts
                   WHERE guild_id=? AND character_id=? AND task_id=?
                   AND cycle_key=? AND part=?''',(gid,cid,tid,cycle,part)).fetchone()
                if existing and existing['state']=='done':
                    continue
                product=db.execute('SELECT 1 FROM shop_products WHERE guild_id=? AND id=?',
                                   (gid,product_id)).fetchone()
                if not product:
                    outcome['items']='pending_missing_product'
                    return outcome
                deliver_official_item(db,gid,cid,product_id,quantity)
                db.execute('''INSERT INTO rp_task_reward_parts
                  (guild_id,character_id,task_id,cycle_key,part,state)
                  VALUES (?,?,?,?,?,'done')
                  ON CONFLICT(guild_id,character_id,task_id,cycle_key,part)
                  DO UPDATE SET state='done',updated_at=CURRENT_TIMESTAMP''',
                  (gid,cid,tid,cycle,part))
        outcome['items']='done'

    # claimed 只代表每個已配置的部分都已結算。
    if ((not amount or _is_done(gid,cid,tid,cycle,'coins')) and
        (not changes or _is_done(gid,cid,tid,cycle,'attributes')) and
        all(_is_done(gid,cid,tid,cycle,f'item:{int(x["item_id"])}') for x in items)):
        with transaction() as db:
            db.execute('''UPDATE rp_task_progress SET status='claimed',
              claimed_at=COALESCE(claimed_at,CURRENT_TIMESTAMP)
              WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=?
              AND status IN ('completed','claimed')''',(gid,cid,tid,cycle))
        outcome['claimed']=True
    return outcome


def pending_reward_rows(guild_id, limit=100):
    initialize_rewards()
    with get_connection() as db:
        return [dict(r) for r in db.execute('''SELECT guild_id,character_id,task_id,cycle_key
          FROM rp_task_progress WHERE guild_id=? AND status='completed'
          ORDER BY completed_at LIMIT ?''',(str(guild_id),min(500,max(1,int(limit)))))]
