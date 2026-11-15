"""Phase 3/4 regression checks; no writes to production data."""
from __future__ import annotations
import ast
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
files={
    "modules/character_forums.py": "ensure_activated_forum",
    "modules/profile.py": "ensure_activated_forum",
    "modules/npc_history_v23.py": "ensure_activated_forum",
    "modules/notifications.py": "ensure_activated_forum",
    "modules/leaderboard_payout_service.py": "_require_archived_award",
}
for path, token in files.items():
    content=(ROOT/path).read_text(encoding="utf-8")
    ast.parse(content,filename=path)
    assert token in content, f"{path} missing {token}"

payout=(ROOT/"modules/leaderboard_payout_service.py").read_text(encoding="utf-8")
assert "_require_archived_award(db, job, reward)" in payout
assert "WHERE guild_id=? AND cycle=? AND idx=? AND board=? AND entity_key=?" in payout
policy=(ROOT/"modules/leaderboard_payout_policy.py").read_text(encoding="utf-8")
assert "PAYOUT_ENABLED = False" in policy, "Unverified automatic payout must remain disabled"
forum=(ROOT/"modules/character_forums.py").read_text(encoding="utf-8")
assert "_private_forum_is_secure(forum," in forum
assert all(name in forum for name in ('"notice_id"','"question_id"','"story_id"','"bot_id"'))
notifications=(ROOT/"modules/notifications.py").read_text(encoding="utf-8")
assert "if not channel_id and row['kind'] == 'private'" in notifications
print('PASS: immediate forum hook, private delivery recovery, strict archived leaderboard checks, payout still locked')
