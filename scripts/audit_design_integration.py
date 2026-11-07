"""靜態整合核對（不執行派獎／扣款／Discord API）。

只確認可觀測程式特徵；「已存在」不等於功能已通過實機驗收。
"""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def exists(path):return (ROOT/path).is_file()
def contains(path,needle):
    p=ROOT/path
    return p.is_file() and needle in p.read_text(encoding='utf-8')
def collect():
    from modules.medical_service_rules import load_services
    catalog=load_services()
    return [
       {'system':'/行動','feature':'醫院新版服務資料','status':'整合新版醫院設定，仍維持待審核與私密預覽','evidence':'assets/action_locations/medical_default_pending.json','ok':len(catalog['services'])==10},
       {'system':'/行動','feature':'普通行動實際扣款／發薪／給點','status':'已接已核准行動之原子交易；付費行動值可消耗，免費行動值及健康連動待完成','evidence':'core/action_execution.py','ok':exists('core/action_execution.py') and contains('modules/location_action_commands.py','ActionExecuteView')},
       {'system':'/任務','feature':'共用檔期核心','status':'存在；仍需實際Discord操作驗證','evidence':'core/work_schedule.py','ok':exists('core/work_schedule.py')},
       {'system':'/任務','feature':'正式代言快速建檔完整串接','status':'未經端到端驗證','evidence':'modules/task_admin.py','ok':False},
       {'system':'/任務','feature':'破格選角、帶資、口碑完整流程','status':'未完成最終規格與完整驗收','evidence':'任務設定包：02、07、09','ok':False},
       {'system':'/排行','feature':'14/30天結算倒數','status':'已有程式；待實機驗證','evidence':'modules/leaderboard_cycle.py','ok':exists('modules/leaderboard_cycle.py')},
       {'system':'/排行','feature':'正式派獎','status':'刻意關閉，待獎勵值與全部適配器核准','evidence':'modules/leaderboard_payout_policy.py','ok':not contains('modules/leaderboard_payout_policy.py','PAYOUT_ENABLED = False')},
       {'system':'/傳聞','feature':'雙人各自建案','status':'已有相關核心程式；待資料庫／Discord驗證','evidence':'core/rumor_auto_case_bridge.py','ok':contains('core/rumor_auto_case_bridge.py','create_linked_case')},
       {'system':'/娛樂城','feature':'完整規格一致性','status':'尚缺娛樂城專用最終設定包','evidence':'娛樂城規格未單獨提供','ok':False},
    ]

def main():
    data=collect()
    for d in data:
        mark='✓' if d['ok'] else '·'
        print(f"{mark} {d['system']}｜{d['feature']}｜{d['status']}")
    output=ROOT/'docs'/'設計整合靜態狀態.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('寫入',output)
if __name__=='__main__':main()
