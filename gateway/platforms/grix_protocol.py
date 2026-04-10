"""Pure helpers for the Grix/aibot websocket protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

PROTOCOL_VERSION = "aibot-agent-api-v1"
DEFAULT_HEARTBEAT_SEC = 30
DEFAULT_CONNECT_TIMEOUT_MS = 10_000
DEFAULT_REQUEST_TIMEOUT_MS = 20_000
DEFAULT_CLIENT = "hermes-agent"
DEFAULT_CLIENT_TYPE = "hermes"
DEFAULT_CLIENT_VERSION = "0.8.0"
DEFAULT_HOST_TYPE = "hermes"
DEFAULT_CONTRACT_VERSION = 1
REQUIRED_AUTH_CAPABILITIES = ("local_action_v1",)
STABLE_AUTH_CAPABILITIES = (
    "session_route",
    "thread_v1",
    "inbound_media_v1",
    "local_action_v1",
)
STABLE_LOCAL_ACTIONS = (
    "exec_approve",
    "exec_reject",
)


def clamp_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, numeric))


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    return ""


def normalize_names(values: Optional[Iterable[Any]] = None) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for raw_value in values or ():
        value = normalize_text(raw_value)
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def normalize_capabilities(values: Optional[Iterable[Any]] = None) -> List[str]:
    return normalize_names(values)


def _ensure_names(values: List[str], required: Iterable[str]) -> List[str]:
    ensured = list(values)
    seen = set(values)
    for raw_name in required:
        name = normalize_text(raw_name)
        if not name or name in seen:
            continue
        ensured.append(name)
        seen.add(name)
    return ensured


@dataclass(frozen=True)
class GrixConnectionConfig:
    endpoint: str
    agent_id: str
    api_key: str
    account_id: str = "main"
    client: str = DEFAULT_CLIENT
    client_type: str = DEFAULT_CLIENT_TYPE
    client_version: str = DEFAULT_CLIENT_VERSION
    host_type: str = DEFAULT_HOST_TYPE
    host_version: Optional[str] = None
    contract_version: int = DEFAULT_CONTRACT_VERSION
    capabilities: List[str] | tuple[str, ...] = tuple(STABLE_AUTH_CAPABILITIES)
    local_actions: List[str] | tuple[str, ...] = tuple(STABLE_LOCAL_ACTIONS)
    connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS


def build_connection_config(extra: Dict[str, Any], api_key: Optional[str]) -> GrixConnectionConfig:
    raw_capabilities = extra.get("capabilities")
    if isinstance(raw_capabilities, str):
        raw_capabilities = [entry.strip() for entry in raw_capabilities.split(",")]

    raw_local_actions = extra.get("local_actions")
    if isinstance(raw_local_actions, str):
        raw_local_actions = [entry.strip() for entry in raw_local_actions.split(",")]

    capabilities = normalize_capabilities(raw_capabilities)
    if not capabilities:
        capabilities = list(STABLE_AUTH_CAPABILITIES)
    else:
        capabilities = _ensure_names(capabilities, REQUIRED_AUTH_CAPABILITIES)

    local_actions = normalize_names(raw_local_actions)
    if not local_actions:
        local_actions = list(STABLE_LOCAL_ACTIONS)
    else:
        local_actions = _ensure_names(local_actions, STABLE_LOCAL_ACTIONS)

    return GrixConnectionConfig(
        endpoint=normalize_text(extra.get("endpoint")),
        agent_id=normalize_text(extra.get("agent_id")),
        api_key=normalize_text(api_key),
        account_id=normalize_text(extra.get("account_id")) or "main",
        client=normalize_text(extra.get("client")) or DEFAULT_CLIENT,
        client_type=normalize_text(extra.get("client_type")) or DEFAULT_CLIENT_TYPE,
        client_version=normalize_text(extra.get("client_version")) or DEFAULT_CLIENT_VERSION,
        host_type=normalize_text(extra.get("host_type")) or DEFAULT_HOST_TYPE,
        host_version=normalize_text(extra.get("host_version")) or None,
        contract_version=clamp_int(
            extra.get("contract_version"),
            DEFAULT_CONTRACT_VERSION,
            1,
            999_999,
        ),
        capabilities=capabilities,
        local_actions=local_actions,
        connect_timeout_ms=clamp_int(
            extra.get("connect_timeout_ms"),
            DEFAULT_CONNECT_TIMEOUT_MS,
            1_000,
            300_000,
        ),
        request_timeout_ms=clamp_int(
            extra.get("request_timeout_ms"),
            DEFAULT_REQUEST_TIMEOUT_MS,
            1_000,
            300_000,
        ),
    )


@dataclass(frozen=True)
class GrixInboundAttachment:
    url: str
    mime_type: Optional[str] = None
    kind: Optional[str] = None
    file_name: Optional[str] = None


@dataclass(frozen=True)
class GrixInboundMessage:
    event_id: str
    session_id: str
    sender_id: str
    sender_name: str
    chat_type: str
    text: str
    message_id: str
    event_type: Optional[str] = None
    session_type: Optional[int] = None
    chat_name: Optional[str] = None
    chat_topic: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    thread_id: Optional[str] = None
    root_message_id: Optional[str] = None
    thread_label: Optional[str] = None
    mentioned_user_ids: List[str] = None
    attachments: List[GrixInboundAttachment] = None
    biz_card: Optional[Dict[str, Any]] = None
    channel_data: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = None


@dataclass(frozen=True)
class GrixStopEvent:
    event_id: str
    session_id: str
    chat_type: str
    stop_id: Optional[str] = None
    reason: Optional[str] = None
    trigger_message_id: Optional[str] = None
    stream_message_id: Optional[str] = None
    raw: Dict[str, Any] = None


@dataclass(frozen=True)
class GrixRevokeEvent:
    event_id: str
    session_id: str
    chat_type: str
    message_id: str
    sender_id: Optional[str] = None
    is_revoked: bool = False
    system_text: Optional[str] = None
    system_context_key: Optional[str] = None
    raw: Dict[str, Any] = None


@dataclass(frozen=True)
class GrixEditEvent:
    session_id: str
    chat_type: str
    message_id: str
    text: str
    sender_id: Optional[str] = None
    sender_type: Optional[int] = None
    message_type: Optional[int] = None
    reply_to_message_id: Optional[str] = None
    thread_id: Optional[str] = None
    raw: Dict[str, Any] = None


@dataclass(frozen=True)
class GrixLocalAction:
    action_id: str
    action_type: str
    params: Dict[str, Any]
    event_id: Optional[str] = None
    timeout_ms: int = 0
    raw: Dict[str, Any] = None


def build_auth_payload(config: GrixConnectionConfig) -> Dict[str, Any]:
    capabilities = normalize_capabilities(config.capabilities)
    local_actions = normalize_names(config.local_actions)
    if capabilities:
        capabilities = _ensure_names(capabilities, REQUIRED_AUTH_CAPABILITIES)
    else:
        capabilities = list(STABLE_AUTH_CAPABILITIES)
    if local_actions:
        local_actions = _ensure_names(local_actions, STABLE_LOCAL_ACTIONS)
    else:
        local_actions = list(STABLE_LOCAL_ACTIONS)

    payload: Dict[str, Any] = {
        "agent_id": config.agent_id,
        "api_key": config.api_key,
        "client": config.client,
        "client_type": config.client_type,
        "client_version": config.client_version,
        "protocol_version": PROTOCOL_VERSION,
        "contract_version": config.contract_version,
        "host_type": config.host_type,
        "capabilities": capabilities,
        "local_actions": local_actions,
    }
    if config.host_version:
        payload["host_version"] = config.host_version
    return payload


def build_packet(cmd: str, payload: Dict[str, Any], seq: int = 0) -> Dict[str, Any]:
    return {
        "cmd": cmd,
        "seq": seq,
        "payload": payload,
    }


def encode_packet(packet: Dict[str, Any]) -> str:
    return json.dumps(packet)


def decode_packet(text: str) -> Dict[str, Any]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("grix packet must be an object")

    cmd = normalize_text(parsed.get("cmd"))
    if not cmd:
        raise ValueError("grix packet requires cmd")

    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("grix packet requires object payload")

    return {
        "cmd": cmd,
        "seq": clamp_int(parsed.get("seq"), 0, 0, 2**63 - 1),
        "payload": payload,
    }


def parse_code(payload: Dict[str, Any]) -> int:
    return clamp_int(payload.get("code"), 0, -999_999, 999_999)


def parse_message(payload: Dict[str, Any]) -> str:
    return normalize_text(payload.get("msg") or payload.get("message"))


def parse_heartbeat_sec(payload: Dict[str, Any]) -> int:
    return clamp_int(
        payload.get("heartbeat_sec"),
        DEFAULT_HEARTBEAT_SEC,
        5,
        300,
    )


def resolve_chat_type(payload: Dict[str, Any]) -> str:
    event_type = normalize_text(payload.get("event_type")).lower()
    session_type = clamp_int(payload.get("session_type"), 0, -999_999, 999_999)
    if session_type == 2 or event_type.startswith("group_"):
        return "group"
    return "dm"


def normalize_local_action(payload: Dict[str, Any]) -> GrixLocalAction:
    action_id = normalize_text(payload.get("action_id"))
    action_type = normalize_text(payload.get("action_type"))
    params = payload.get("params")
    if not isinstance(params, dict):
        params = {}
    return GrixLocalAction(
        action_id=action_id,
        action_type=action_type,
        params=params,
        event_id=normalize_text(payload.get("event_id")) or None,
        timeout_ms=clamp_int(payload.get("timeout_ms"), 0, 0, 2**31 - 1),
        raw=payload,
    )


def resolve_sender_name(payload: Dict[str, Any], sender_id: str) -> str:
    for key in ("sender_name", "sender_nickname", "nickname", "display_name"):
        value = normalize_text(payload.get(key))
        if value:
            return value
    sender_type = clamp_int(payload.get("sender_type"), 0, -999_999, 999_999)
    if sender_type == 2:
        return f"Agent {sender_id}" if sender_id else "Agent"
    if sender_type == 1:
        return f"User {sender_id}" if sender_id else "User"
    return sender_id or "unknown"


def normalize_attachment(value: Any) -> Optional[GrixInboundAttachment]:
    if not isinstance(value, dict):
        return None

    url = normalize_text(value.get("media_url") or value.get("url"))
    if not url:
        return None

    return GrixInboundAttachment(
        url=url,
        mime_type=normalize_text(value.get("content_type") or value.get("mime")) or None,
        kind=normalize_text(value.get("attachment_type") or value.get("kind")).lower() or None,
        file_name=normalize_text(value.get("file_name")) or None,
    )


def normalize_object(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    return dict(value)


def normalize_mentions(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []

    seen = set()
    mentions: List[str] = []
    for entry in value:
        mention = normalize_id(entry)
        if not mention or mention in seen:
            continue
        seen.add(mention)
        mentions.append(mention)
    return mentions


def normalize_inbound_message(payload: Dict[str, Any]) -> GrixInboundMessage:
    event_id = normalize_id(payload.get("event_id"))
    if not event_id:
        raise ValueError("inbound message requires event_id")

    session_id = normalize_id(payload.get("session_id"))
    if not session_id:
        raise ValueError("inbound message requires session_id")

    message_id = normalize_id(payload.get("msg_id"))
    if not message_id:
        raise ValueError("inbound message requires msg_id")

    sender_id = normalize_id(payload.get("sender_id"))
    attachments = [
        attachment
        for attachment in (
            normalize_attachment(entry)
            for entry in payload.get("attachments", []) or []
        )
        if attachment
    ]

    return GrixInboundMessage(
        event_id=event_id,
        session_id=session_id,
        sender_id=sender_id,
        sender_name=resolve_sender_name(payload, sender_id),
        chat_type=resolve_chat_type(payload),
        text=str(payload.get("content") or ""),
        message_id=message_id,
        event_type=normalize_text(payload.get("event_type")).lower() or None,
        session_type=clamp_int(payload.get("session_type"), 0, -999_999, 999_999) or None,
        chat_name=(
            normalize_text(payload.get("session_name"))
            or normalize_text(payload.get("conversation_name"))
            or normalize_text(payload.get("name"))
            or None
        ),
        chat_topic=(
            normalize_text(payload.get("chat_topic"))
            or normalize_text(payload.get("thread_label"))
            or None
        ),
        reply_to_message_id=normalize_id(payload.get("quoted_message_id")) or None,
        thread_id=normalize_id(payload.get("thread_id")) or None,
        root_message_id=normalize_id(payload.get("root_msg_id")) or None,
        thread_label=normalize_text(payload.get("thread_label")) or None,
        mentioned_user_ids=normalize_mentions(payload.get("mention_user_ids")),
        attachments=attachments,
        biz_card=normalize_object(payload.get("biz_card")),
        channel_data=normalize_object(payload.get("channel_data")),
        raw=dict(payload),
    )


def normalize_stop_event(payload: Dict[str, Any]) -> GrixStopEvent:
    event_id = normalize_id(payload.get("event_id"))
    if not event_id:
        raise ValueError("stop event requires event_id")

    session_id = normalize_id(payload.get("session_id"))
    if not session_id:
        raise ValueError("stop event requires session_id")

    return GrixStopEvent(
        event_id=event_id,
        session_id=session_id,
        chat_type=resolve_chat_type(payload),
        stop_id=normalize_id(payload.get("stop_id")) or None,
        reason=normalize_text(payload.get("reason")) or None,
        trigger_message_id=normalize_id(payload.get("trigger_msg_id")) or None,
        stream_message_id=normalize_id(payload.get("stream_msg_id")) or None,
        raw=dict(payload),
    )


def normalize_revoke_event(payload: Dict[str, Any]) -> GrixRevokeEvent:
    event_id = normalize_id(payload.get("event_id"))
    if not event_id:
        raise ValueError("revoke event requires event_id")

    session_id = normalize_id(payload.get("session_id"))
    if not session_id:
        raise ValueError("revoke event requires session_id")

    message_id = normalize_id(payload.get("msg_id"))
    if not message_id:
        raise ValueError("revoke event requires msg_id")

    system_event = normalize_object(payload.get("system_event")) or {}
    return GrixRevokeEvent(
        event_id=event_id,
        session_id=session_id,
        chat_type=resolve_chat_type(payload),
        message_id=message_id,
        sender_id=normalize_id(payload.get("sender_id")) or None,
        is_revoked=bool(payload.get("is_revoked")),
        system_text=normalize_text(system_event.get("text")) or None,
        system_context_key=normalize_text(system_event.get("context_key")) or None,
        raw=dict(payload),
    )


def normalize_edit_event(payload: Dict[str, Any]) -> GrixEditEvent:
    session_id = normalize_id(payload.get("session_id"))
    if not session_id:
        raise ValueError("edit event requires session_id")

    message_id = normalize_id(payload.get("msg_id"))
    if not message_id:
        raise ValueError("edit event requires msg_id")

    return GrixEditEvent(
        session_id=session_id,
        chat_type=resolve_chat_type(payload),
        message_id=message_id,
        text=str(payload.get("content") or ""),
        sender_id=normalize_id(payload.get("sender_id")) or None,
        sender_type=clamp_int(payload.get("sender_type"), 0, -999_999, 999_999) or None,
        message_type=clamp_int(payload.get("msg_type"), 0, -999_999, 999_999) or None,
        reply_to_message_id=normalize_id(payload.get("quoted_message_id")) or None,
        thread_id=normalize_id(payload.get("thread_id")) or None,
        raw=dict(payload),
    )
