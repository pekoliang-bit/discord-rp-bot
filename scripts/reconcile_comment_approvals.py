"""Rebuild approved 960-comment split seeds from an exact owner-reviewed text map.

Usage: python -m scripts.reconcile_comment_approvals assets/internet_comment_pending_review/approved_chat_texts.json [output-dir]
Does not write SQLite or post to Discord. Old 419 seed files are copied unchanged.
"""
from __future__ import annotations
import collections
import json
import shutil
import sys
from pathlib import Path

from core.comment_library import SEED_PATH


def build(approved_file: Path, output: Path) -> None:
    texts = json.loads(approved_file.read_text(encoding="utf-8-sig"))
    wanted = {f"C{n:03d}" for n in range(421, 962)}
    if not isinstance(texts, dict) or set(texts) != wanted or any(
        not isinstance(t, str) or not t.strip() for t in texts.values()
    ):
        raise ValueError("必須提供 C421～C961 共 541 則核准原文")

    old = []
    for number in range(1, 10):
        filename = SEED_PATH / f"comments_part_{number:02d}.json"
        old.extend(json.loads(filename.read_text(encoding="utf-8-sig"))["approved"])
    if len(old) != 419 or "C300" in {r["id"] for r in old}:
        raise ValueError("原始 419 則分檔異常，拒絕重建")

    pending = SEED_PATH.parent / "internet_comment_pending_review" / "comments_pending_C421-C961.json"
    by_id = {r["id"]: r for r in json.loads(pending.read_text(encoding="utf-8-sig"))["comments"]}
    if set(by_id) != wanted:
        raise ValueError("候選資料缺少新 ID 或包含額外 ID")

    new = []
    for number in range(421, 962):
        cid = f"C{number:03d}"
        row = dict(by_id[cid])
        row.update(text=texts[cid], status="approved", source="owner_reviewed_chat",
                   subcategory="已核准評論", batch=10 + (number - 421) // 50)
        new.append(row)
    counts = collections.Counter(row["category_id"] for row in old + new)
    if counts != {i: 80 for i in range(1, 13)}:
        raise ValueError(f"12 類未各達 80 則：{counts}")

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    parts = []
    for number in range(1, 10):
        name = f"comments_part_{number:02d}.json"
        src = (SEED_PATH / name).resolve()
        dest = output / name
        if src != dest:
            shutil.copy2(src, dest)
        rows = json.loads(dest.read_text(encoding="utf-8-sig"))["approved"]
        parts.append({"file": name, "part": number, "count": len(rows),
                      "first_id": rows[0]["id"], "last_id": rows[-1]["id"]})
    for number, start in enumerate(range(0, len(new), 50), 10):
        group = new[start:start + 50]
        name = f"comments_part_{number:02d}.json"
        (output / name).write_text(json.dumps({"part": number, "approved": group}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        parts.append({"file": name, "part": number, "count": len(group),
                      "first_id": group[0]["id"], "last_id": group[-1]["id"]})
    manifest = {
        "catalog_version": "reviewed-960-chat-reconciled-v1",
        "status": "approved", "chat_text_reconciled": True,
        "expected_approved_count": 960, "initial_target_per_category": 80,
        "category_count": 12, "first_comment_id": "C001", "last_comment_id": "C961",
        "next_comment_id": "C962", "preserved_ids": ["C074"], "excluded_ids": ["C300"],
        "parts": parts,
        "approval_policy": {"new_templates_require_owner_approval": True,
                            "preserve_original_text": True, "automatic_discord_posting": False},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已產生 {len(parts)} 個分檔，共 960 則：{output}")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("用法：python -m scripts.reconcile_comment_approvals approved_chat_texts.json [output-dir]")
    build(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) == 3 else Path("reviewed_960_seed"))
