"""管理員資格回歸：Manage Server 不等於 Administrator。"""
from __future__ import annotations
import subprocess,sys,os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
code=r'''
import sys,types
sys.modules['discord']=types.ModuleType('discord')
from core.permissions import Actor
from types import SimpleNamespace as NS

def fake(user_id,owner,admin=False,manage=False):
 guild=NS(owner_id=owner)
 return NS(guild=guild,guild_id=1,user=NS(id=user_id),
          permissions=NS(administrator=admin,manage_guild=manage))
assert Actor.from_interaction(fake(42,1,False,True)).is_admin is False
assert Actor.from_interaction(fake(42,1,True,False)).is_admin is True
assert Actor.from_interaction(fake(1,1,False,False)).is_admin is True
assert Actor.from_interaction(fake(10,1,False,False)).is_admin is False
print('PASS Owner > Administrator > Manage Server')
'''
result=subprocess.run([sys.executable,'-c',code],cwd=root,env=dict(os.environ,PYTHONPATH=str(root)),capture_output=True,text=True)
print(result.stdout,result.stderr)
assert result.returncode==0
