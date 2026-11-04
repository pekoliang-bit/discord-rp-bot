
"""任務核心流程的標準函式庫測試；不需要 Discord 連線。"""
import sys,unittest,tempfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules.task_evidence import validate_evidence
from modules.task_conditions import evaluate

class TaskFinishTests(unittest.TestCase):
    def test_valid_message(self):
        when=datetime.now(timezone.utc)
        result=validate_evidence({'message_id':'123','channel_id':'77','author_id':'10',
          'created_at':when,'content':'這是一段真實的通告作品完成文字內容'},'10','77',(when-timedelta(minutes=1)).isoformat())
        self.assertEqual(result['message_id'],'123')
    def test_wrong_author(self):
        when=datetime.now(timezone.utc)
        with self.assertRaises(ValueError):validate_evidence({'message_id':'1','channel_id':'77','author_id':'11',
          'created_at':when,'content':'這是一段真實的通告作品完成文字內容'},'10','77',when.isoformat())
    def test_old_message(self):
        now=datetime.now(timezone.utc)
        with self.assertRaises(ValueError):validate_evidence({'message_id':'1','channel_id':'77','author_id':'10',
          'created_at':now-timedelta(hours=1),'content':'這是一段真實的通告作品完成文字內容'},'10','77',now.isoformat())
    def test_cafe(self):
        self.assertTrue(evaluate({'min_cafe_level':3},{'cafe_level':4})['eligible'])
        self.assertFalse(evaluate({'min_cafe_level':5},{'cafe_level':4})['eligible'])
    def test_named_invite(self):
        self.assertTrue(evaluate({'allowed_character_ids':[17]},{'character_id':17})['eligible'])
        self.assertFalse(evaluate({'allowed_character_ids':[17]},{'character_id':18})['eligible'])
if __name__=='__main__':unittest.main()
