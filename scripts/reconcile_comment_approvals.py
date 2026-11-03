
"""Builds a 960-comment seed only from explicit owner-approved exact-text mapping. No DB writes.

Usage: python -m scripts.reconcile_comment_approvals approved_chat_texts.json [output-dir]
Input JSON: {"C421": "exact approved text", ..., "C961": "..."}
Never auto-approve the older candidate JSON.
"""
from __future__ import annotations
import json, sys, collections
from pathlib import Path
from core.comment_library import SEED_PATH, seed_catalog

def build(approved_file: Path, output: Path) -> None:
    texts=json.loads(approved_file.read_text(encoding="utf-8-sig"))
    wanted={f"C{n:03d}" for n in range(421,962)}
    if not isinstance(texts,dict) or set(texts)!=wanted or any(not isinstance(t,str) or not t.strip() for t in texts.values()):
        raise ValueError("須完整提供 C421～C961 共541則由群主確認的留言原文，不能有缺漏")
    existing=seed_catalog(SEED_PATH)["approved"]
    pending=Path(__file__).resolve().parent.parent / "assets" / "internet_comment_pending_review" / "comments_pending_C421-C961.json"
    if not pending.exists():
        raise FileNotFoundError(f"請先提供核對用途的候選資料：{pending}")
    source=json.loads(pending.read_text(encoding="utf-8-sig"))["comments"]
    by_id={r["id"]:r for r in source}
    if set(by_id)!=wanted:raise ValueError("待審候選資料缺號或重複")
    combined=list(existing)
    for cid in sorted(wanted,key=lambda x:int(x[1:])):
        item=dict(by_id[cid]);item["text"]=texts[cid];item["number"]=int(cid[1:]);item["batch"]=10+(item["number"]-421)//50
        item["status"]="approved";item["source"]="owner_reviewed_chat"
        item["subcategory"]="已核准評論"
        combined.append(item)
    counts=collections.Counter(r["category_id"] for r in combined)
    if len(combined)!=960 or any(counts.get(i)!=80 for i in range(1,13)):
        raise ValueError(f"分類未達成各80則：{counts}")
    output.mkdir(parents=True,exist_ok=True)
    parts=[]
    for i,start in enumerate(range(0,len(combined),50),1):
        group=combined[start:start+50]; filename=f"comments_part_{i:02}.json"
        (output/filename).write_text(json.dumps({"part":i,"approved":group},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        parts.append({"file":filename,"part":i,"count":len(group),"first_id":group[0]["id"],"last_id":group[-1]["id"]})
    manifest={"catalog_version":"reviewed-960-chat-reconciled-v1","status":"approved","chat_text_reconciled":True,"expected_approved_count":960,"initial_target_per_category":80,"category_count":12,"preserved_ids":["C074"],"excluded_ids":["C300"],"parts":parts,"approval_policy":{"new_templates_require_owner_approval":True,"preserve_original_text":True,"automatic_discord_posting":False}}
    (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("輸出960則完整候選資料；請再執行正式校驗後部署：", output)

if __name__=="__main__":
    if len(sys.argv) not in (2,3):raise SystemExit("Usage: python -m scripts.reconcile_comment_approvals approved_chat_texts.json [output-dir]")
    build(Path(sys.argv[1]),Path(sys.argv[2]) if len(sys.argv)==3 else Path("reviewed_960_seed"))
