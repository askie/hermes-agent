"""Shared helpers for synthetic interactive-card command events."""

from __future__ import annotations

import json
import re
from typing import Any

_CARD_ACTION_TAG_SANITIZER = re.compile(r"[^a-zA-Z0-9_.:-]+")
_CARD_ACTION_METADATA_KEY = "_hermes_card_action"


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


def attach_card_action_metadata(
    raw_message: Any,
    *,
    action_tag: Any,
    action_value: Any = None,
    platform: Any = None,
) -> dict[str, Any]:
    """Attach trusted card-action metadata to a platform payload."""

    payload = dict(raw_message) if isinstance(raw_message, dict) else {"platform_payload": raw_message}
    metadata = {
        "tag": sanitize_card_action_tag(action_tag),
        "value": action_value,
    }
    if platform is not None:
        metadata["platform"] = str(platform)
    payload[_CARD_ACTION_METADATA_KEY] = metadata
    return payload


def extract_card_action_metadata(raw_message: Any) -> dict[str, Any] | None:
    """Extract trusted card-action metadata from a payload wrapper."""

    if not isinstance(raw_message, dict):
        return None

    metadata = raw_message.get(_CARD_ACTION_METADATA_KEY)
    if not isinstance(metadata, dict):
        return None

    normalized = {
        "tag": sanitize_card_action_tag(metadata.get("tag")),
        "value": metadata.get("value"),
    }
    platform = metadata.get("platform")
    if platform not in (None, ""):
        normalized["platform"] = str(platform)
    return normalized


def build_card_action_user_text(action_tag: Any, action_value: Any = None) -> str:
    """Build the normalized agent-facing text for a trusted card action."""

    normalized_tag = sanitize_card_action_tag(action_tag)
    lines = [
        "Interactive card response",
        f"Action: {normalized_tag}",
    ]
    if action_value in (None, "", {}, []):
        return "\n".join(lines)

    try:
        payload = json.dumps(action_value, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = str(action_value)
    lines.append(f"Payload: {payload}")
    return "\n".join(lines)
