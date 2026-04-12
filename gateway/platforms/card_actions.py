"""Shared helpers for synthetic interactive-card command events."""

from __future__ import annotations

import json
import re
from typing import Any

_CARD_ACTION_TAG_SANITIZER = re.compile(r"[^a-zA-Z0-9_.:-]+")


def sanitize_card_action_tag(value: Any, fallback: str = "button") -> str:
    """Return a stable, command-safe action tag."""

    normalized = _CARD_ACTION_TAG_SANITIZER.sub("_", str(value or "").strip()).strip("_")
    return normalized or fallback


def build_card_action_command(action_tag: Any, action_value: Any = None) -> str:
    """Build the shared synthetic ``/card`` command text."""

    command = f"/card {sanitize_card_action_tag(action_tag)}"
    if action_value in (None, "", {}, []):
        return command

    try:
        return f"{command} {json.dumps(action_value, ensure_ascii=False)}"
    except (TypeError, ValueError):
        return command
