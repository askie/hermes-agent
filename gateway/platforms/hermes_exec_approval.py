"""Hermes-side exec approval payload builders.

This module owns the business mapping for Hermes dangerous-command approvals.
Platform transports should forward the returned payloads unchanged.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

DEFAULT_EXEC_APPROVAL_HOST = "hermes"
DEFAULT_EXEC_APPROVAL_TIMEOUT_SEC = 300
_ALLOWED_DECISIONS = ("allow-once", "allow-always", "deny")


@dataclass(frozen=True)
class HermesStructuredMessage:
    content: str
    biz_card: Optional[Dict[str, Any]] = None
    channel_data: Optional[Dict[str, Any]] = None


def _normalize_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _clone_json_object(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return copy.deepcopy(dict(value))


def _compact_text(value: str, limit: int) -> str:
    normalized = " ".join(_normalize_text(value).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)] + "..."


def _decision_commands(approval_id: str) -> Dict[str, str]:
    return {
        "allow-once": f"/approve {approval_id} allow-once",
        "allow-always": f"/approve {approval_id} allow-always",
        "deny": f"/approve {approval_id} deny",
    }


def build_exec_approval_message(
    *,
    approval_id: str,
    command: str,
    description: str = "dangerous command",
    raw_approval_data: Optional[Mapping[str, Any]] = None,
    host: str = DEFAULT_EXEC_APPROVAL_HOST,
    timeout_sec: int = DEFAULT_EXEC_APPROVAL_TIMEOUT_SEC,
) -> HermesStructuredMessage:
    """Build the structured approval payload Hermes sends over AIBOT/Grix."""

    normalized_approval_id = _normalize_text(approval_id)
    normalized_command = _normalize_text(command)
    normalized_description = _normalize_text(description)
    normalized_host = _normalize_text(host) or DEFAULT_EXEC_APPROVAL_HOST
    normalized_timeout = max(int(timeout_sec or DEFAULT_EXEC_APPROVAL_TIMEOUT_SEC), 1)
    decisions = list(_ALLOWED_DECISIONS)
    decision_commands = _decision_commands(normalized_approval_id)

    raw_payload = _clone_json_object(raw_approval_data)
    raw_payload["approval_id"] = normalized_approval_id
    raw_payload["command"] = normalized_command
    raw_payload["description"] = normalized_description
    raw_payload["host"] = normalized_host
    raw_payload["expires_in_seconds"] = normalized_timeout
    raw_payload["allowed_decisions"] = list(decisions)
    raw_payload["decision_commands"] = dict(decision_commands)

    biz_payload: Dict[str, Any] = {
        "approval_id": normalized_approval_id,
        "approval_slug": normalized_approval_id,
        "approval_command_id": normalized_approval_id,
        "command": normalized_command,
        "host": normalized_host,
        "allowed_decisions": list(decisions),
        "decision_commands": dict(decision_commands),
        "expires_in_seconds": normalized_timeout,
    }
    if normalized_description:
        biz_payload["warning_text"] = normalized_description

    fallback_lines = [
        f"[Exec Approval] {_compact_text(normalized_command, 160)} ({normalized_host})",
        decision_commands["allow-once"],
    ]
    if normalized_description:
        fallback_lines.append(f"Reason: {normalized_description}")

    return HermesStructuredMessage(
        content="\n".join(line for line in fallback_lines if line),
        biz_card={
            "version": 1,
            "type": "exec_approval",
            "payload": biz_payload,
        },
        channel_data={
            "hermes": {
                "execApprovalPending": raw_payload,
            }
        },
    )
