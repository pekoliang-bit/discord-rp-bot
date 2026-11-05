"""Regression guards for privacy-sensitive integration paths."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def test_sources_parse():
    for name in ("modules/character_forums.py", "modules/notifications.py"):
        ast.parse((ROOT / name).read_text(encoding="utf-8"))


def test_forum_creation_has_explicit_overwrites():
    source = (ROOT / "modules/character_forums.py").read_text(encoding="utf-8")
    assert "overwrites=overwrites" in source
    assert "guild.default_role: discord.PermissionOverwrite(view_channel=False)" in source
    assert "_private_forum_is_secure(forum, character['owner_id'])" in source or '_private_forum_is_secure(forum, character["owner_id"])' in source


def test_private_dispatcher_checks_forum_security():
    source = (ROOT / "modules/notifications.py").read_text(encoding="utf-8")
    assert "if row['kind'] == 'private':" in source
    assert "_private_forum_is_secure(forum, owner_row['owner_id'])" in source
