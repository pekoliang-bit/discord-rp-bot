"""Pet market V2: selectable stock, required starter kit, atomic checkout and chip\.

Pre\-launch admin configuration is allowed; player operations remain launch gated
by the bot's interaction\-wide gate\. Unapproved catalog prices are provisional\.
"""
from \_\_future\_\_ import annotations
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from core\.database import transaction, get\_connection, get\_or\_create\_account
from core\.economy import \_payment
from core\.world\_interaction\_rules import pet\_rules
from core import pets\_v1 as pets
from core import pet\_catalog\_v21 as v21
from core import pet\_traits\_v22 as behaviour


def setup&#40;db, guild\_id=None&#41;:
    pets\.initialize&#40;db&#41;
    db\.execute&#40;'''CREATE TABLE IF NOT EXISTS rp\_pet\_catalog\_v2 &#40;
      guild\_id TEXT NOT NULL,kind TEXT NOT NULL,code TEXT NOT NULL,
      species TEXT NOT NULL DEFAULT '',label TEXT NOT NULL,price INTEGER NOT NULL,
      stock INTEGER NOT NULL DEFAULT \-1,active INTEGER NOT NULL DEFAULT 1,
      basic INTEGER NOT NULL DEFAULT 0,description TEXT NOT NULL DEFAULT '',
      PRIMARY KEY&#40;guild\_id,kind,code&#41;,CHECK&#40;price\>=0&#41;,CHECK&#40;stock\>=\-1&#41;,
      CHECK&#40;kind IN &#40;'breed','item'&#41;&#41;&#41;'''&#41;
    db\.execute&#40;'''CREATE TABLE IF NOT EXISTS rp\_pet\_cart\_v2 &#40;
      guild\_id TEXT NOT NULL,character\_id INTEGER NOT NULL,
      kind TEXT NOT NULL,code TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY&#40;guild\_id,character\_id,kind,code&#41;,CHECK&#40;quantity\>0&#41; &#41;'''&#41;
    db\.execute&#40;'''CREATE TABLE IF NOT EXISTS rp\_pet\_inventory\_v2 &#40;
      guild\_id TEXT NOT NULL,character\_id INTEGER NOT NULL,code TEXT NOT NULL,
      quantity INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY&#40;guild\_id,character\_id,code&#41;, CHECK&#40;quantity\>=0&#41;&#41;'''&#41;
    db\.execute&#40;'''CREATE TABLE IF NOT EXISTS rp\_pet\_orders\_v2 &#40;
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild\_id TEXT NOT NULL,
      character\_id INTEGER NOT NULL,request\_key TEXT NOT NULL,
      order\_json TEXT NOT NULL,pet\_id INTEGER NOT NULL,
      created\_at TEXT NOT NULL DEFAULT CURRENT\_TIMESTAMP,
      UNIQUE&#40;guild\_id,request\_key&#41;&#41;'''&#41;
    \# 既有 V2 資料表原位升級，保留玩家購物車、訂單與庫存。
    columns=\{r&#91;1&#93; for r in db\.execute&#40;'PRAGMA table\_info&#40;rp\_pet\_catalog\_v2&#41;'&#41;\}
    for field,definition in \{
        'category':"TEXT NOT NULL DEFAULT '日常'",'quality':"TEXT NOT NULL DEFAULT ''",
        'required\_group':"TEXT NOT NULL DEFAULT ''",'hidden\_health':'INTEGER NOT NULL DEFAULT 0',
        'hidden\_bond':'INTEGER NOT NULL DEFAULT 0','permanent':'INTEGER NOT NULL DEFAULT 0',
        'breed\_codes':"TEXT NOT NULL DEFAULT '&#91;&#93;'"\}\.items&#40;&#41;:
        if field not in columns: db\.execute&#40;f'ALTER TABLE rp\_pet\_catalog\_v2 ADD COLUMN \{field\} \{definition\}'&#41;
    if guild\_id is not None:
        guild\_id=str&#40;guild\_id&#41;
        rules=pet\_rules&#40;guild\_id&#41;
        \# 歷史泛用品種保留但停止上架（不動玩家已購買資料）。
        db\.execute&#40;"UPDATE rp\_pet\_catalog\_v2 SET active=0 WHERE guild\_id=? AND kind='breed' AND code LIKE 'default:%'",&#40;guild\_id,&#41;&#41;
        for code,species,label,price,active in v21\.BREEDS:
            db\.execute&#40;"""INSERT OR IGNORE INTO rp\_pet\_catalog\_v2
              &#40;guild\_id,kind,code,species,label,price,active,description&#41;
              VALUES &#40;?,'breed',?,?,?,?,?,'V2\.1 候選價／未逐品種核准'&#41;""",
              &#40;guild\_id,code,species,label,price,active&#41;&#41;
        for item in v21\.item\_templates&#40;&#41;:
            db\.execute&#40;"""INSERT OR IGNORE INTO rp\_pet\_catalog\_v2
              &#40;guild\_id,kind,code,species,label,price,active,basic,description,category,
               quality,required\_group,hidden\_health,hidden\_bond,permanent,breed\_codes&#41;
              VALUES &#40;?,'item',?,?,?,?,1,0,'V2\.1 暫行用品數值',?,?,?,?,?,?,?&#41;""",
              &#40;guild\_id,item&#91;'code'&#93;,item&#91;'species'&#93;,item&#91;'label'&#93;,item&#91;'price'&#93;,item&#91;'category'&#93;,
               item&#91;'quality'&#93;,item&#91;'group'&#93;,item&#91;'hidden\_health'&#93;,item&#91;'hidden\_bond'&#93;,int&#40;item&#91;'permanent'&#93;&#41;,'&#91;&#93;'&#41;&#41;
        \# Legacy items are preserved for existing players; no generic required starter kit for V2\.1\.


def \_row&#40;db,gid,kind,code&#41;:
    row=db\.execute&#40;'SELECT \* FROM rp\_pet\_catalog\_v2 WHERE guild\_id=? AND kind=? AND code=?',
                   &#40;gid,kind,code&#41;&#41;\.fetchone&#40;&#41;
    if not row:raise LookupError&#40;'商品不存在或尚未上架'&#41;
    return row


def catalog&#40;guild\_id,kind=None,species=None,active\_only=True&#41;:
    gid=str&#40;guild\_id&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;
        query='SELECT \* FROM rp\_pet\_catalog\_v2 WHERE guild\_id=?'; args=&#91;gid&#93;
        if kind:query\+=' AND kind=?';args\.append&#40;kind&#41;
        if species is not None:query\+=' AND species=?';args\.append&#40;species&#41;
        if active\_only:query\+=' AND active=1 AND stock\!=0'
        query\+=' ORDER BY kind,species,basic DESC,price,code'
        return &#91;dict&#40;r&#41; for r in db\.execute&#40;query,args&#41;&#93;


def species\_list&#40;guild\_id&#41;:
    return list&#40;dict\.fromkeys&#40;r&#91;'species'&#93; for r in catalog&#40;guild\_id,'breed'&#41; if r&#91;'species'&#93;&#41;&#41;


def manage&#40;guild\_id,kind,code,label,price,\*,species='',stock=\-1,active=True,basic=False,description='',delete=False&#41;:
    gid=str&#40;guild\_id&#41;;kind=str&#40;kind&#41;;code=str&#40;code&#41;\.strip&#40;&#41;;label=str&#40;label&#41;\.strip&#40;&#41;
    if kind not in &#40;'breed','item'&#41;:raise ValueError&#40;'商品類型必須為品種或用品'&#41;
    if not code or len&#40;code&#41;\>80 or not label or len&#40;label&#41;\>100:raise ValueError&#40;'商品代碼或名稱不正確'&#41;
    if not isinstance&#40;price,int&#41; or not 0<=price<=10\*\*10 or not isinstance&#40;stock,int&#41; or stock< \-1:
        raise ValueError&#40;'售價或庫存不正確'&#41;
    if kind=='breed' and not str&#40;species&#41;\.strip&#40;&#41;:raise ValueError&#40;'品種必須指定物種'&#41;
    rules=pet\_rules&#40;gid&#41;
    if kind=='breed' and &#40;str&#40;species&#41; in rules&#91;'protected\_species'&#93; or '保育' in str&#40;species&#41;&#41;:
        raise PermissionError&#40;'保育類物種不可販售'&#41;
    if delete:active=False
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;
        db\.execute&#40;'''INSERT INTO rp\_pet\_catalog\_v2&#40;guild\_id,kind,code,species,label,price,stock,active,basic,description&#41;
          VALUES &#40;?,?,?,?,?,?,?,?,?,?&#41; ON CONFLICT&#40;guild\_id,kind,code&#41; DO UPDATE SET
          species=excluded\.species,label=excluded\.label,price=excluded\.price,stock=excluded\.stock,
          active=excluded\.active,basic=excluded\.basic,description=excluded\.description''',
          &#40;gid,kind,code,str&#40;species&#41;\.strip&#40;&#41; if kind=='breed' else '',label,price,stock,
           int&#40;bool&#40;active&#41;&#41;,int&#40;bool&#40;basic&#41; and kind=='item'&#41;,str&#40;description&#41;&#91;:1000&#93;&#41;&#41;
    return \{'kind':kind,'code':code,'active':bool&#40;active&#41;,'price':price\}


def \_actor&#40;db,gid,cid,uid&#41;:
    pets\.\_actor&#40;db,gid,cid,uid&#41;


def select&#40;guild\_id,character\_id,user\_id,kind,code,quantity=1&#41;:
    gid=str&#40;guild\_id&#41;;cid=int&#40;character\_id&#41;;quantity=int&#40;quantity&#41;
    if kind not in &#40;'breed','item'&#41; or not 1<=quantity<=99:raise ValueError&#40;'選購數量不正確'&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;;\_actor&#40;db,gid,cid,user\_id&#41;
        item=\_row&#40;db,gid,kind,str&#40;code&#41;&#41;
        if not item&#91;'active'&#93; or item&#91;'stock'&#93;==0:raise ValueError&#40;'商品已下架或售罄'&#41;
        if kind=='item' and item&#91;'required\_group'&#93;:
            chosen=db\.execute&#40;"SELECT a\.species FROM rp\_pet\_cart\_v2 c JOIN rp\_pet\_catalog\_v2 a ON a\.guild\_id=c\.guild\_id AND a\.kind=c\.kind AND a\.code=c\.code WHERE c\.guild\_id=? AND c\.character\_id=? AND c\.kind='breed'",&#40;gid,cid&#41;&#41;\.fetchone&#40;&#41;
            if chosen and item&#91;'species'&#93;\!=chosen&#91;'species'&#93;:
                raise ValueError&#40;'商品不適用所選寵物物種'&#41;
            db\.execute&#40;"""DELETE FROM rp\_pet\_cart\_v2 WHERE guild\_id=? AND character\_id=? AND kind='item' AND code IN
              &#40;SELECT code FROM rp\_pet\_catalog\_v2 WHERE guild\_id=? AND kind='item' AND species=? AND required\_group=?&#41;""",
              &#40;gid,cid,gid,item&#91;'species'&#93;,item&#91;'required\_group'&#93;&#41;&#41;
        if kind=='breed':
            quantity=1
            db\.execute&#40;"DELETE FROM rp\_pet\_cart\_v2 WHERE guild\_id=? AND character\_id=? AND kind='breed'",&#40;gid,cid&#41;&#41;
        db\.execute&#40;'''INSERT INTO rp\_pet\_cart\_v2&#40;guild\_id,character\_id,kind,code,quantity&#41;
          VALUES &#40;?,?,?,?,?&#41; ON CONFLICT&#40;guild\_id,character\_id,kind,code&#41;
          DO UPDATE SET quantity=excluded\.quantity''',&#40;gid,cid,kind,str&#40;code&#41;,quantity&#41;&#41;
    return preview&#40;gid,cid&#41;


def remove&#40;guild\_id,character\_id,user\_id,kind,code&#41;:
    gid=str&#40;guild\_id&#41;;cid=int&#40;character\_id&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;;\_actor&#40;db,gid,cid,user\_id&#41;
        db\.execute&#40;'DELETE FROM rp\_pet\_cart\_v2 WHERE guild\_id=? AND character\_id=? AND kind=? AND code=?',&#40;gid,cid,kind,str&#40;code&#41;&#41;&#41;
    return preview&#40;gid,cid&#41;


def \_preview&#40;db,gid,cid&#41;:
    rows=db\.execute&#40;'''SELECT c\.kind,c\.code,c\.quantity,a\.label,a\.price,a\.species,a\.stock,a\.active,a\.basic
       FROM rp\_pet\_cart\_v2 c JOIN rp\_pet\_catalog\_v2 a ON
       c\.guild\_id=a\.guild\_id AND c\.kind=a\.kind AND c\.code=a\.code
       WHERE c\.guild\_id=? AND c\.character\_id=? ORDER BY c\.kind,c\.code''',&#40;gid,cid&#41;&#41;\.fetchall&#40;&#41;
    content=&#91;dict&#40;x&#41; for x in rows&#93;
    if any&#40;x&#91;'active'&#93;==0 or x&#91;'stock'&#93;==0 or &#40;x&#91;'stock'&#93;\>=0 and x&#91;'quantity'&#93;\>x&#91;'stock'&#93;&#41; for x in content&#41;:
        valid=False
    else:valid=True
    breed=&#91;x for x in content if x&#91;'kind'&#93;=='breed'&#93;
    chosen=breed&#91;0&#93; if len&#40;breed&#41;==1 else None
    if chosen and chosen&#91;'code'&#93;\.startswith&#40;'v21:'&#41;:
        selected\_breed=db\.execute&#40;"SELECT label FROM rp\_pet\_catalog\_v2 WHERE guild\_id=? AND kind='breed' AND code=?",&#40;gid,chosen&#91;'code'&#93;&#41;&#41;\.fetchone&#40;&#41;
        breed\_label=selected\_breed&#91;'label'&#93; if selected\_breed else ''
        required=&#91;g for g,\_,req in v21\.wizard\_pages&#40;chosen&#91;'species'&#93;,breed\_label&#41; if req&#93;
        selected\_groups=&#91;&#93;
        for x in content:
            if x&#91;'kind'&#93;\!='item': continue
            meta=db\.execute&#40;"SELECT species,required\_group,breed\_codes FROM rp\_pet\_catalog\_v2 WHERE guild\_id=? AND kind='item' AND code=?",&#40;gid,x&#91;'code'&#93;&#41;&#41;\.fetchone&#40;&#41;
            if meta and meta&#91;'species'&#93;==chosen&#91;'species'&#93; and meta&#91;'required\_group'&#93;:
                selected\_groups\.append&#40;meta&#91;'required\_group'&#93;&#41;
        missing=&#91;g for g in required if g not in selected\_groups&#93;
        for x in content:
            if x&#91;'kind'&#93;=='item':
                row=db\.execute&#40;"SELECT species FROM rp\_pet\_catalog\_v2 WHERE guild\_id=? AND kind='item' AND code=?",&#40;gid,x&#91;'code'&#93;&#41;&#41;\.fetchone&#40;&#41;
                if row and row&#91;'species'&#93; and row&#91;'species'&#93;\!=chosen&#91;'species'&#93;: valid=False
    else:
        basics=&#91;dict&#40;x&#41; for x in db\.execute&#40;"SELECT \* FROM rp\_pet\_catalog\_v2 WHERE guild\_id=? AND kind='item' AND basic=1 AND active=1",&#40;gid,&#41;&#41;&#93;
        missing=&#91;x&#91;'code'&#93; for x in basics if not any&#40;y&#91;'code'&#93;==x&#91;'code'&#93; and y&#91;'quantity'&#93;\>=1 for y in content&#41;&#93;
        for required\_code in &#40;'starter\_feed','starter\_kit'&#41;:
            if required\_code not in &#91;r&#91;'code'&#93; for r in basics&#93; or required\_code not in &#91;r&#91;'code'&#93; for r in content if r&#91;'kind'&#93;=='item'&#93;:
                if required\_code not in missing:missing\.append&#40;required\_code&#41;
        if not basics:missing\.append&#40;'尚未上架任何基礎用品'&#41;
    return \{'items':content,'total':sum&#40;x&#91;'price'&#93;\*x&#91;'quantity'&#93; for x in content&#41;,
            'pet\_selected':len&#40;breed&#41;==1,'missing\_basics':missing,
            'ready':valid and len&#40;breed&#41;==1 and not missing\}


def preview&#40;guild\_id,character\_id&#41;:
    gid=str&#40;guild\_id&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;
        return \_preview&#40;db,gid,int&#40;character\_id&#41;&#41;


def checkout&#40;guild\_id,character\_id,user\_id,pet\_name,request\_key&#41;:
    gid=str&#40;guild\_id&#41;;cid=int&#40;character\_id&#41;;pet\_name=str&#40;pet\_name&#41;\.strip&#40;&#41;&#91;:50&#93;
    if not pet\_name:raise ValueError&#40;'請先填寫寵物名字'&#41;
    if not str&#40;request\_key&#41;\.strip&#40;&#41;:raise ValueError&#40;'缺少交易識別碼'&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;;\_actor&#40;db,gid,cid,user\_id&#41;
        old=db\.execute&#40;'SELECT order\_json FROM rp\_pet\_orders\_v2 WHERE guild\_id=? AND request\_key=?',&#40;gid,str&#40;request\_key&#41;&#41;&#41;\.fetchone&#40;&#41;
        if old:return json\.loads&#40;old&#91;'order\_json'&#93;&#41;
        from core\.pet\_agreement\_v23 import has\_signed
        if not has\_signed&#40;db,gid,cid&#41;:
            raise PermissionError&#40;'請先依序閱讀十頁須知並以正式姓名及身分證字號簽署'&#41;
        state=\_preview&#40;db,gid,cid&#41;
        if not state&#91;'ready'&#93;:raise ValueError&#40;'請先選擇寵物，並加入全部必備用品；確認商品未下架且庫存足夠'&#41;
        breed=next&#40;x for x in state&#91;'items'&#93; if x&#91;'kind'&#93;=='breed'&#41;
        rules=pet\_rules&#40;gid&#41;
        if breed&#91;'species'&#93; in rules&#91;'protected\_species'&#93; or '保育' in breed&#91;'species'&#93;:
            raise PermissionError&#40;'保育類動物不可交易'&#41;
        for x in state&#91;'items'&#93;:
            if x&#91;'stock'&#93;\>=0:
                result=db\.execute&#40;'''UPDATE rp\_pet\_catalog\_v2 SET stock=stock\-? WHERE guild\_id=? AND kind=? AND code=? AND active=1 AND stock\>=?''',
                  &#40;x&#91;'quantity'&#93;,gid,x&#91;'kind'&#93;,x&#91;'code'&#93;,x&#91;'quantity'&#93;&#41;&#41;
                if result\.rowcount\!=1:raise ValueError&#40;'商品庫存不足，請重新確認訂單'&#41;
        timestamp=datetime\.now&#40;timezone\.utc&#41;\.isoformat&#40;&#41;
        chip='P\-'\+secrets\.token\_hex&#40;12&#41;\.upper&#40;&#41;
        inserted=db\.execute&#40;'''INSERT INTO rp\_pets\_v1&#40;guild\_id,main\_character\_id,name,species,rarity,chip,health,bond,last\_play,last\_tick&#41;
          VALUES &#40;?,?,?,?,?,?,?,?,?,?&#41;''',&#40;gid,cid,pet\_name,breed&#91;'species'&#93;,breed&#91;'code'&#93;,chip,
                 rules&#91;'health\_max'&#93;,rules&#91;'bond\_max'&#93;,timestamp,timestamp&#41;&#41;
        pid=int&#40;inserted\.lastrowid&#41;
        if state&#91;'total'&#93;:
            sender=get\_or\_create\_account&#40;db,gid,'character',cid,'wallet'&#41;
            receiver=get\_or\_create\_account&#40;db,gid,'system','pet\_center','wallet'&#41;
            \_payment&#40;db,gid,'pet:checkout:'\+gid\+':'\+str&#40;request\_key&#41;,sender,receiver,state&#91;'total'&#93;,'寵物＋必備飼養用品一次結帳與晶片登記'&#41;
        for item in state&#91;'items'&#93;:
            if item&#91;'kind'&#93;=='item':
                db\.execute&#40;'''INSERT INTO rp\_pet\_inventory\_v2&#40;guild\_id,character\_id,code,quantity&#41;
                    VALUES &#40;?,?,?,?&#41; ON CONFLICT&#40;guild\_id,character\_id,code&#41;
                    DO UPDATE SET quantity=quantity\+excluded\.quantity''',
                    &#40;gid,cid,item&#91;'code'&#93;,item&#91;'quantity'&#93;&#41;&#41;
        db\.execute&#40;'INSERT INTO rp\_pet\_owners\_v1 VALUES &#40;?,?,?,1&#41;',&#40;gid,pid,cid&#41;&#41;
        behaviour\.attach\_purchased&#40;db,gid,pid,cid,state&#91;'items'&#93;&#41;
        from core\.pet\_medical\_v23 import register\_first\_check
        register\_first\_check&#40;db,gid,pid&#41;
        db\.execute&#40;'INSERT INTO rp\_pet\_events\_v1&#40;guild\_id,pet\_id,actor\_character\_id,event\_type,detail\_json&#41; VALUES &#40;?,?,?,?,?&#41;',
          &#40;gid,pid,cid,'checkout',json\.dumps&#40;state,ensure\_ascii=False&#41;&#41;&#41;
        result=\{'pet\_id':pid,'name':pet\_name,'species':breed&#91;'species'&#93;,'breed':breed&#91;'label'&#93;,
                'chip':chip,'total':state&#91;'total'&#93;,'items':state&#91;'items'&#93;\}
        db\.execute&#40;'INSERT INTO rp\_pet\_orders\_v2&#40;guild\_id,character\_id,request\_key,order\_json,pet\_id&#41; VALUES &#40;?,?,?,?,?&#41;',
                   &#40;gid,cid,str&#40;request\_key&#41;,json\.dumps&#40;result,ensure\_ascii=False&#41;,pid&#41;&#41;
        db\.execute&#40;'DELETE FROM rp\_pet\_cart\_v2 WHERE guild\_id=? AND character\_id=?',&#40;gid,cid&#41;&#41;
        return result


def inventory&#40;guild\_id,character\_id&#41;:
    with transaction&#40;&#41; as db:
        setup&#40;db,guild\_id&#41;
        return &#91;dict&#40;r&#41; for r in db\.execute&#40;'SELECT code,quantity FROM rp\_pet\_inventory\_v2 WHERE guild\_id=? AND character\_id=? AND quantity\>0',&#40;str&#40;guild\_id&#41;,int&#40;character\_id&#41;&#41;&#41;&#93;


def checkout\_supplies&#40;guild\_id,character\_id,user\_id,request\_key&#41;:
    \# Restock supplies for existing pets without buying another animal/chip\.
    gid=str&#40;guild\_id&#41;;cid=int&#40;character\_id&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;;\_actor&#40;db,gid,cid,user\_id&#41;
        old=db\.execute&#40;'SELECT order\_json FROM rp\_pet\_orders\_v2 WHERE guild\_id=? AND request\_key=?',&#40;gid,str&#40;request\_key&#41;&#41;&#41;\.fetchone&#40;&#41;
        if old:return json\.loads&#40;old&#91;'order\_json'&#93;&#41;
        state=\_preview&#40;db,gid,cid&#41;
        if not state&#91;'items'&#93; or any&#40;x&#91;'kind'&#93;=='breed' for x in state&#91;'items'&#93;&#41;:
            raise ValueError&#40;'用品補貨只能選擇飼料／用品；選了寵物請走晶片結帳流程'&#41;
        if any&#40;not x&#91;'active'&#93; or x&#91;'stock'&#93;==0 or &#40;x&#91;'stock'&#93;\>=0 and x&#91;'quantity'&#93;\>x&#91;'stock'&#93;&#41; for x in state&#91;'items'&#93;&#41;:
            raise ValueError&#40;'商品已下架或庫存不足'&#41;
        for x in state&#91;'items'&#93;:
            if x&#91;'stock'&#93;\>=0:
                changed=db\.execute&#40;'''UPDATE rp\_pet\_catalog\_v2 SET stock=stock\-? WHERE guild\_id=? AND kind=? AND code=? AND active=1 AND stock\>=?''',
                    &#40;x&#91;'quantity'&#93;,gid,x&#91;'kind'&#93;,x&#91;'code'&#93;,x&#91;'quantity'&#93;&#41;&#41;
                if changed\.rowcount\!=1:raise ValueError&#40;'庫存不足'&#41;
        if state&#91;'total'&#93;:
            sender=get\_or\_create\_account&#40;db,gid,'character',cid,'wallet'&#41;
            receiver=get\_or\_create\_account&#40;db,gid,'system','pet\_center','wallet'&#41;
            \_payment&#40;db,gid,'pet:supplies:'\+gid\+':'\+str&#40;request\_key&#41;,sender,receiver,state&#91;'total'&#93;,'寵物飼料／用品統一結帳'&#41;
        for x in state&#91;'items'&#93;:
            db\.execute&#40;'''INSERT INTO rp\_pet\_inventory\_v2&#40;guild\_id,character\_id,code,quantity&#41;
                    VALUES &#40;?,?,?,?&#41; ON CONFLICT&#40;guild\_id,character\_id,code&#41;
                    DO UPDATE SET quantity=quantity\+excluded\.quantity''',
                    &#40;gid,cid,x&#91;'code'&#93;,x&#91;'quantity'&#93;&#41;&#41;
        result=\{'pet\_id':None,'chip':None,'total':state&#91;'total'&#93;,'items':state&#91;'items'&#93;\}
        db\.execute&#40;'''INSERT INTO rp\_pet\_orders\_v2&#40;guild\_id,character\_id,request\_key,order\_json,pet\_id&#41;
                    VALUES &#40;?,?,?,?,0&#41;''',&#40;gid,cid,str&#40;request\_key&#41;,json\.dumps&#40;result,ensure\_ascii=False&#41;&#41;&#41;
        db\.execute&#40;'DELETE FROM rp\_pet\_cart\_v2 WHERE guild\_id=? AND character\_id=?',&#40;gid,cid&#41;&#41;
        return result


def feed&#40;guild\_id,pet\_id,character\_id,user\_id&#41;:
    \# Consume one previously purchased food unit, then restore pet health\.
    gid=str&#40;guild\_id&#41;;cid=int&#40;character\_id&#41;
    rules=pet\_rules&#40;gid&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;;\_actor&#40;db,gid,cid,user\_id&#41;;pets\.\_access&#40;db,gid,pet\_id,cid&#41;
        target=pets\.\_pet&#40;db,gid,pet\_id&#41;
        if target&#91;'state'&#93; in &#40;'missing','deceased'&#41;:
            raise ValueError&#40;'目前無法餵食這隻寵物'&#41;
        used=db\.execute&#40;'''UPDATE rp\_pet\_inventory\_v2 SET quantity=quantity\-1
            WHERE guild\_id=? AND character\_id=? AND code='starter\_feed' AND quantity\>=1''',&#40;gid,cid&#41;&#41;
        if used\.rowcount\!=1:raise ValueError&#40;'飼料不足，請先到 /寵物中心 選購並結帳'&#41;
        new\_health=pets\.clamp&#40;int&#40;target&#91;'health'&#93;&#41;\+4,rules&#91;'health\_max'&#93;&#41;
        db\.execute&#40;'UPDATE rp\_pets\_v1 SET health=? WHERE guild\_id=? AND id=?',&#40;new\_health,gid,int&#40;pet\_id&#41;&#41;&#41;
        db\.execute&#40;'INSERT INTO rp\_pet\_events\_v1&#40;guild\_id,pet\_id,actor\_character\_id,event\_type&#41; VALUES &#40;?,?,?,?&#41;',
                   &#40;gid,int&#40;pet\_id&#41;,cid,'feed'&#41;&#41;
        return \{'health':new\_health,'bond':int&#40;target&#91;'bond'&#93;&#41;,'state':target&#91;'state'&#93;\}


def rename&#40;guild\_id,pet\_id,character\_id,user\_id,new\_name&#41;:
    gid=str&#40;guild\_id&#41;;name=str&#40;new\_name&#41;\.strip&#40;&#41;&#91;:50&#93;
    if not name:raise ValueError&#40;'名字不能空白'&#41;
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;;\_actor&#40;db,gid,character\_id,user\_id&#41;
        pets\.\_access&#40;db,gid,pet\_id,character\_id&#41;
        p=pets\.\_pet&#40;db,gid,pet\_id&#41;
        if p&#91;'state'&#93;=='deceased':raise ValueError&#40;'無法更改已故寵物的名字'&#41;
        db\.execute&#40;'UPDATE rp\_pets\_v1 SET name=? WHERE guild\_id=? AND id=?',&#40;name,gid,int&#40;pet\_id&#41;&#41;&#41;
        db\.execute&#40;'INSERT INTO rp\_pet\_events\_v1&#40;guild\_id,pet\_id,actor\_character\_id,event\_type,detail\_json&#41; VALUES &#40;?,?,?,?,?&#41;',
                   &#40;gid,int&#40;pet\_id&#41;,int&#40;character\_id&#41;,'rename',json\.dumps&#40;\{'old':p&#91;'name'&#93;,'new':name\},ensure\_ascii=False&#41;&#41;&#41;
        return name


def action\_encounter&#40;guild\_id,character\_id,user\_id,action\_key,rng=None,location\_key=None&#41;:
    """Roll \*after successful /行動\*\. Idempotent per action transaction\.

    Return None when no encounter\. Never callable from a selectable /寵物中心 menu\.
    """
    import random
    from core\.pet\_location\_events\_v21 import get\_rules as get\_location\_rules
    gid=str&#40;guild\_id&#41;;cid=int&#40;character\_id&#41;
    rules=pet\_rules&#40;gid&#41;;rng=rng or random\.SystemRandom&#40;&#41;
    local=get\_location\_rules&#40;gid,location\_key&#41; if location\_key is not None else None
    with transaction&#40;&#41; as db:
        setup&#40;db,gid&#41;;\_actor&#40;db,gid,cid,user\_id&#41;
        db\.execute&#40;'''CREATE TABLE IF NOT EXISTS rp\_pet\_action\_rolls\_v2 &#40;
          guild\_id TEXT NOT NULL,request\_key TEXT NOT NULL,character\_id INTEGER NOT NULL,
          encounter\_id INTEGER,PRIMARY KEY&#40;guild\_id,request\_key&#41;&#41;'''&#41;
        prev=db\.execute&#40;'SELECT encounter\_id FROM rp\_pet\_action\_rolls\_v2 WHERE guild\_id=? AND request\_key=?',&#40;gid,str&#40;action\_key&#41;&#41;&#41;\.fetchone&#40;&#41;
        if prev:return int&#40;prev&#91;'encounter\_id'&#93;&#41; if prev&#91;'encounter\_id'&#93; else None
        chance=max&#40;0,min&#40;100,int&#40;local&#91;'chance'&#93; if local else rules\.get&#40;'encounter\_after\_action\_chance',5&#41;&#41;&#41;&#41;
        encounter\_id=None
        if rng\.randint&#40;1,100&#41;<=chance:
            posters=db\.execute&#40;"SELECT pet\_id,reward FROM rp\_pet\_posters\_v1 WHERE guild\_id=? AND state='open'",&#40;gid,&#41;&#41;\.fetchall&#40;&#41;
            chance\_missing=min&#40;45,15\+sum&#40;min&#40;5,int&#40;x&#91;'reward'&#93;&#41;//3000&#41; for x in posters&#41;&#41; if posters else 0
            if posters and rng\.randint&#40;1,100&#41;<=chance\_missing:
                selected=rng\.choice&#40;posters&#41;;kind='missing';pid=int&#40;selected&#91;'pet\_id'&#93;&#41;
            else:
                pool=local&#91;'pool'&#93; if local else &#91;'stray\_cat','stray\_dog','missing\_pet','nothing'&#93;
                available=&#91;x for x in pool if x not in &#40;'nothing','missing\_pet'&#41;&#93;
                if not available: available=&#91;'stray\_cat'&#93;
                chosen=rng\.choice&#40;available&#41;
                kind='stray' if chosen in &#40;'stray\_cat','stray\_dog'&#41; else 'injured'
                pid=None
            encounter\_id=db\.execute&#40;'INSERT INTO rp\_pet\_encounters\_v1&#40;guild\_id,actor\_character\_id,kind,pet\_id&#41; VALUES &#40;?,?,?,?&#41;',
                &#40;gid,cid,kind,pid&#41;&#41;\.lastrowid
        db\.execute&#40;'INSERT INTO rp\_pet\_action\_rolls\_v2 VALUES &#40;?,?,?,?&#41;',&#40;gid,str&#40;action\_key&#41;,cid,encounter\_id&#41;&#41;
        return encounter\_id


def encounter\_detail&#40;guild\_id,encounter\_id&#41;:
    with get\_connection&#40;&#41; as db:
        row=db\.execute&#40;'SELECT \* FROM rp\_pet\_encounters\_v1 WHERE guild\_id=? AND id=?',&#40;str&#40;guild\_id&#41;,int&#40;encounter\_id&#41;&#41;&#41;\.fetchone&#40;&#41;
        return dict&#40;row&#41; if row else None


def wizard\_items&#40;guild\_id, species, group, breed\_code=None&#41;:
    """依頁面、物種列出商品，精確隱藏加成不回傳給 UI。"""
    if species not in v21\.PAGES: return &#91;&#93;
    rows=catalog&#40;guild\_id,'item',species&#41;
    return &#91;r for r in rows if r\.get&#40;'required\_group'&#41;==group&#93;


def wizard\_pages&#40;guild\_id,breed\_code&#41;:
    row=next&#40;&#40;r for r in catalog&#40;guild\_id,'breed'&#41; if r&#91;'code'&#93;==breed\_code&#41;,None&#41;
    if not row:raise LookupError&#40;'請先選擇已上架品種'&#41;
    return v21\.wizard\_pages&#40;row&#91;'species'&#93;,row&#91;'label'&#93;&#41;
