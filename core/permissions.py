"""Discord RP permission & character-review core.

Pure service layer: does not register slash commands. Discord Cogs should call
these helpers. Authorization is rechecked at the time of every mutation.
Works with the schema from core/database.py (2026-10-25).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.database import get_connection, transaction

