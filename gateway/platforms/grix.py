"""Native Hermes gateway adapter for the Grix/aibot protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.platforms.grix_protocol import (
    GrixConnectionConfig,
    GrixEditEvent,
    GrixInboundMessage,
    GrixRevokeEvent,
    GrixStopEvent,
    build_connection_config,
    normalize_edit_event,
    normalize_inbound_message,
    normalize_revoke_event,
    normalize_stop_event,
)
from gateway.platforms.grix_transport import (
    AIOHTTP_AVAILABLE,
    GrixAuthRejectedError,
    GrixConnectionClosedError,
    GrixDependencyError,
    GrixTransportClient,
    GrixTransportError,
)
from gateway.session import build_session_key
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 4000
_ROUTE_SESSION_KEY_PREFIX = "agent:main:grix:"
_STRIP_MARKDOWN_REPLACEMENTS = (
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"\*(.+?)\*", re.DOTALL), r"\1"),
    (re.compile(r"__(.+?)__", re.DOTALL), r"\1"),
    (re.compile(r"_(.+?)_", re.DOTALL), r"\1"),
    (re.compile(r"```[a-zA-Z0-9_+-]*\n?"), ""),
    (re.compile(r"`(.+?)`", re.DOTALL), r"\1"),
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"\[([^\]]+)\]\(([^\)]+)\)"), r"\1"),
)


def check_grix_requirements() -> bool:
    return AIOHTTP_AVAILABLE


def _strip_markdown(text: str) -> str:
    cleaned = text
    for pattern, replacement in _STRIP_MARKDOWN_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def build_grix_connection_config(config: PlatformConfig) -> GrixConnectionConfig:
    return build_connection_config(config.extra or {}, config.api_key or config.token)


def _coerce_retryable(error: Exception) -> bool:
    if isinstance(error, (GrixConnectionClosedError, GrixDependencyError)):
        return True
    lowered = str(error).lower()
    return any(
        token in lowered
        for token in (
            "connect",
            "connection",
            "network",
            "timeout",
            "temporarily unavailable",
            "closing transport",
            "not connected",
            "not authenticated",
            "broken pipe",
            "reset by peer",
        )
    )


def _resolve_message_type(message: GrixInboundMessage) -> MessageType:
    first = message.attachments[0] if message.attachments else None
    if not first:
        return MessageType.TEXT

    kind = (first.kind or "").lower()
    mime_type = (first.mime_type or "").lower()
    if kind == "image" or mime_type.startswith("image/"):
        return MessageType.PHOTO
    if kind == "video" or mime_type.startswith("video/"):
        return MessageType.VIDEO
    if kind == "voice" or mime_type in ("audio/ogg", "audio/opus", "audio/x-opus"):
        return MessageType.VOICE
    if kind == "audio" or mime_type.startswith("audio/"):
        return MessageType.AUDIO
    return MessageType.DOCUMENT


def _source_field(source: Any, field: str) -> Optional[str]:
    if source is None:
        return None
    if isinstance(source, dict):
        value = source.get(field)
    else:
        value = getattr(source, field, None)
    return str(value).strip() if value else None


def _lookup_grix_session_origin(session_key: str) -> Optional[Dict[str, Optional[str]]]:
    sessions_path = get_hermes_home() / "sessions" / "sessions.json"
    if not sessions_path.exists():
        return None
    try:
        with open(sessions_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.debug("[grix] Failed loading sessions.json for route lookup: %s", exc)
        return None

    entry = data.get(session_key) or {}
    origin = entry.get("origin") or {}
    if str(origin.get("platform") or "").strip() != Platform.GRIX.value:
        return None
    chat_id = str(origin.get("chat_id") or "").strip()
    if not chat_id:
        return None
    thread_id = str(origin.get("thread_id") or "").strip() or None
    return {"chat_id": chat_id, "thread_id": thread_id}


def _parse_route_session_key(value: str) -> Optional[Dict[str, Optional[str]]]:
    parts = str(value or "").strip().split(":")
    if len(parts) < 5 or parts[:3] != ["agent", "main", "grix"]:
        return None

    chat_type = parts[3]
    session_id = parts[4].strip()
    if not session_id:
        return None

    if chat_type == "dm":
        thread_id = ":".join(part for part in parts[5:] if part).strip() or None
        return {"chat_type": chat_type, "session_id": session_id, "thread_id": thread_id}

    if chat_type != "group":
        return None

    thread_id = None
    if len(parts) >= 7:
        thread_id = parts[5].strip() or None
    return {"chat_type": chat_type, "session_id": session_id, "thread_id": thread_id}


async def resolve_grix_target(
    client: Optional[GrixTransportClient],
    connection: GrixConnectionConfig,
    target: str,
    *,
    thread_id: Optional[str] = None,
    source_hint: Optional[Any] = None,
) -> tuple[str, Optional[str]]:
    raw_target = str(target or "").strip()
    if not raw_target:
        return raw_target, thread_id

    resolved_thread_id = str(thread_id).strip() if thread_id else None
    if source_hint is not None and not resolved_thread_id:
        hinted_thread_id = _source_field(source_hint, "thread_id")
        if hinted_thread_id:
            resolved_thread_id = hinted_thread_id

    if raw_target.startswith(_ROUTE_SESSION_KEY_PREFIX):
        if source_hint is None:
            persisted_source_hint = _lookup_grix_session_origin(raw_target)
            if persisted_source_hint is not None:
                source_hint = persisted_source_hint
                if not resolved_thread_id:
                    resolved_thread_id = _source_field(persisted_source_hint, "thread_id")

        parsed = _parse_route_session_key(raw_target)
        if parsed and not resolved_thread_id:
            resolved_thread_id = parsed.get("thread_id") or None

        if client:
            try:
                resolved = await client.resolve_session_route(
                    channel=Platform.GRIX.value,
                    account_id=connection.account_id,
                    route_session_key=raw_target,
                )
                resolved_session_id = str(resolved.get("session_id") or "").strip()
                if resolved_session_id:
                    return resolved_session_id, resolved_thread_id
            except Exception as exc:
                logger.debug("[grix] session_route_resolve failed for %s: %s", raw_target, exc)

        hinted_chat_id = _source_field(source_hint, "chat_id")
        if hinted_chat_id:
            return hinted_chat_id, resolved_thread_id
        if parsed:
            return str(parsed["session_id"]), resolved_thread_id
        return raw_target, resolved_thread_id

    if ":" in raw_target and not resolved_thread_id:
        session_id, inline_thread_id = raw_target.split(":", 1)
        session_id = session_id.strip()
        inline_thread_id = inline_thread_id.strip()
        if session_id and inline_thread_id:
            return session_id, inline_thread_id

    return raw_target, resolved_thread_id


class GrixAdapter(BasePlatformAdapter):
    platform = Platform.GRIX
    MAX_MESSAGE_LENGTH = MAX_TEXT_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.GRIX)
        self.connection = build_grix_connection_config(config)
        self._client: Optional[GrixTransportClient] = None
        self._connector = None
        self._disconnect_requested = False
        self._token_lock_identity: Optional[str] = None
        self._completed_event_ids: set[str] = set()
        self._reply_event_ids: Dict[tuple[str, str], str] = {}
        self._latest_sources: Dict[str, Any] = {}
        self._message_sources: Dict[tuple[str, str], Any] = {}
        self._message_session_keys: Dict[tuple[str, str], str] = {}

    def format_message(self, content: str) -> str:
        return _strip_markdown(content)

    async def connect(self) -> bool:
        if not self.connection.endpoint or not self.connection.agent_id or not self.connection.api_key:
            logger.error("[%s] Missing GRIX_ENDPOINT, GRIX_AGENT_ID, or GRIX_API_KEY", self.name)
            self._set_fatal_error(
                "grix_config_missing",
                "Missing GRIX_ENDPOINT, GRIX_AGENT_ID, or GRIX_API_KEY",
                retryable=False,
            )
            return False

        try:
            from gateway.status import acquire_scoped_lock

            self._token_lock_identity = (
                f"{self.connection.endpoint}|{self.connection.agent_id}|{self.connection.api_key}"
            )
            acquired, existing = acquire_scoped_lock(
                "grix-agent-credentials",
                self._token_lock_identity,
                metadata={"platform": self.platform.value, "endpoint": self.connection.endpoint},
            )
            if not acquired:
                owner_pid = existing.get("pid") if isinstance(existing, dict) else None
                message = "Grix connection settings already in use"
                if owner_pid:
                    message += f" (PID {owner_pid})"
                message += ". Stop the other gateway first."
                self._set_fatal_error("grix_token_lock", message, retryable=False)
                logger.error("[%s] %s", self.name, message)
                return False
        except Exception as exc:
            logger.warning("[%s] Failed to acquire GRIX lock: %s", self.name, exc)

        self._disconnect_requested = False
        self._client = GrixTransportClient(
            self.connection,
            connector=self._connector,
            on_packet=self._handle_protocol_packet,
            on_status=self._handle_transport_status,
        )
        try:
            await self._client.connect()
        except GrixAuthRejectedError as exc:
            self._set_fatal_error("grix_auth_rejected", str(exc), retryable=False)
            await self._safe_release_lock()
            return False
        except Exception as exc:
            self._set_fatal_error("grix_connect_failed", str(exc), retryable=_coerce_retryable(exc))
            await self._safe_release_lock()
            return False

        self._mark_connected()
        logger.info("[%s] Connected to %s", self.name, self.connection.endpoint)
        return True

    async def disconnect(self) -> None:
        self._disconnect_requested = True
        client = self._client
        self._client = None
        if client:
            try:
                await client.disconnect("adapter disconnect")
            except Exception as exc:
                logger.debug("[%s] GRIX disconnect failed: %s", self.name, exc)
        await self._safe_release_lock()
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="GRIX transport is not connected", retryable=True)

        source_hint = self._latest_sources.get(str(chat_id))
        session_id, thread_id = await resolve_grix_target(
            self._client,
            self.connection,
            str(chat_id),
            thread_id=self._metadata_thread_id(metadata),
            source_hint=source_hint,
        )
        event_id = None
        if reply_to:
            event_id = self._reply_event_ids.get((str(session_id), str(reply_to)))
        if not event_id and metadata:
            raw_event_id = metadata.get("event_id")
            if isinstance(raw_event_id, str) and raw_event_id.strip():
                event_id = raw_event_id.strip()

        try:
            receipt = await self._client.send_text(
                str(session_id),
                self.format_message(content),
                reply_to_message_id=reply_to,
                thread_id=thread_id,
                event_id=event_id,
            )
            if event_id:
                await self._complete_event_if_needed(event_id, status="responded")
            return SendResult(
                success=bool(receipt.get("ok")),
                message_id=receipt.get("message_id"),
                raw_response=receipt,
                retryable=False,
            )
        except Exception as exc:
            if event_id:
                await self._complete_event_if_needed(
                    event_id,
                    status="failed",
                    message=str(exc),
                )
            return SendResult(
                success=False,
                error=str(exc),
                raw_response=exc,
                retryable=_coerce_retryable(exc),
            )

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="GRIX transport is not connected", retryable=True)
        try:
            source_hint = self._latest_sources.get(str(chat_id))
            session_id, _thread_id = await resolve_grix_target(
                self._client,
                self.connection,
                str(chat_id),
                source_hint=source_hint,
            )
            receipt = await self._client.edit_message(
                str(session_id),
                str(message_id),
                self.format_message(content),
            )
            return SendResult(
                success=bool(receipt.get("ok")),
                message_id=receipt.get("message_id"),
                raw_response=receipt,
                retryable=False,
            )
        except Exception as exc:
            return SendResult(
                success=False,
                error=str(exc),
                raw_response=exc,
                retryable=_coerce_retryable(exc),
            )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if not self._client:
            return
        try:
            await self._client.set_session_activity(
                session_id=str(chat_id),
                kind="composing",
                active=True,
                ttl_ms=self._metadata_ttl_ms(metadata),
                ref_message_id=self._metadata_ref_message_id(metadata),
                ref_event_id=self._metadata_ref_event_id(metadata),
            )
        except Exception as exc:
            logger.debug("[%s] GRIX typing update failed: %s", self.name, exc)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        source = self._latest_sources.get(str(chat_id))
        if source:
            return {
                "id": source.chat_id,
                "name": source.chat_name or source.chat_id,
                "type": source.chat_type,
            }

        base_chat_id, _, thread_id = str(chat_id).partition(":")
        source = self._latest_sources.get(chat_id) or self._latest_sources.get(base_chat_id)
        if source:
            return {
                "id": source.chat_id,
                "name": source.chat_name or source.chat_id,
                "type": source.chat_type,
                **({"thread_id": thread_id} if thread_id else {}),
            }

        return {"id": str(chat_id), "name": str(chat_id), "type": "dm"}

    async def on_processing_complete(self, event: MessageEvent, success: bool) -> None:
        raw_message = event.raw_message if isinstance(event.raw_message, dict) else {}
        if raw_message.get("_grix_kind") != "message":
            return
        event_id = raw_message.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            return

        status = "responded" if success else "failed"
        message = None if success else "message processing failed"
        await self._complete_event_if_needed(event_id.strip(), status=status, message=message)

    async def _handle_transport_status(self, status: Dict[str, Any]) -> None:
        if self._disconnect_requested:
            return
        if status.get("connected", True):
            return
        if not self.is_connected:
            return

        message = str(status.get("last_error") or "grix websocket disconnected")
        self._set_fatal_error("grix_connection_lost", message, retryable=True)
        await self._notify_fatal_error()

    async def _handle_protocol_packet(self, packet: Dict[str, Any]) -> None:
        cmd = packet.get("cmd")
        payload = packet.get("payload") or {}
        try:
            if cmd == "event_msg":
                await self._handle_message_packet(payload)
            elif cmd == "event_stop":
                await self._handle_stop_packet(payload)
            elif cmd == "event_edit":
                await self._handle_edit_packet(payload)
            elif cmd == "event_revoke":
                await self._handle_revoke_packet(payload)
            else:
                logger.debug("[%s] Ignoring unknown GRIX packet %s", self.name, cmd)
        except Exception as exc:
            logger.error("[%s] Failed handling GRIX packet %s: %s", self.name, cmd, exc, exc_info=True)

    async def _handle_message_packet(self, payload: Dict[str, Any]) -> None:
        message = normalize_inbound_message(payload)
        source = self.build_source(
            chat_id=message.session_id,
            chat_name=message.chat_name,
            chat_type=message.chat_type,
            user_id=message.sender_id or None,
            user_name=message.sender_name or None,
            thread_id=message.thread_id,
            chat_topic=message.chat_topic,
        )
        session_key = build_session_key(
            source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        self._latest_sources[message.session_id] = source
        self._latest_sources[session_key] = source
        if message.thread_id:
            self._latest_sources[f"{message.session_id}:{message.thread_id}"] = source
        self._reply_event_ids[(message.session_id, message.message_id)] = message.event_id
        self._message_sources[(message.session_id, message.message_id)] = source
        self._message_session_keys[(message.session_id, message.message_id)] = session_key

        if self._client:
            try:
                await self._client.bind_session_route(
                    channel=self.platform.value,
                    account_id=self.connection.account_id,
                    route_session_key=session_key,
                    session_id=message.session_id,
                )
            except Exception as exc:
                logger.debug("[%s] GRIX session_route_bind failed: %s", self.name, exc)
            await self._client.acknowledge_event(
                event_id=message.event_id,
                session_id=message.session_id,
                message_id=message.message_id,
            )

        event = MessageEvent(
            text=message.text,
            message_type=_resolve_message_type(message),
            source=source,
            raw_message={**message.raw, "_grix_kind": "message"},
            message_id=message.message_id,
            media_urls=[attachment.url for attachment in message.attachments],
            media_types=[
                attachment.mime_type or attachment.kind or ""
                for attachment in message.attachments
            ],
            reply_to_message_id=message.reply_to_message_id,
        )

        try:
            await self.handle_message(event)
        except Exception as exc:
            if self._client:
                await self._complete_event_if_needed(
                    message.event_id,
                    status="failed",
                    message=str(exc),
                )
            raise

    async def _handle_edit_packet(self, payload: Dict[str, Any]) -> None:
        edit: GrixEditEvent = normalize_edit_event(payload)
        session_key = self._message_session_keys.get((edit.session_id, edit.message_id))
        if not session_key:
            source = self._latest_sources.get(edit.session_id)
            if source:
                session_key = build_session_key(
                    source,
                    group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
                    thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
                )

        pending_event = self._pending_messages.get(session_key or "")
        if not pending_event or pending_event.message_id != edit.message_id:
            logger.debug(
                "[%s] GRIX edit for %s/%s has no pending Hermes event to update",
                self.name,
                edit.session_id,
                edit.message_id,
            )
            return

        pending_event.text = edit.text
        pending_event.reply_to_message_id = edit.reply_to_message_id
        pending_event.raw_message = {**edit.raw, "_grix_kind": "edit"}
        logger.debug(
            "[%s] Updated pending Hermes event from GRIX edit for %s/%s",
            self.name,
            edit.session_id,
            edit.message_id,
        )

    async def _handle_revoke_packet(self, payload: Dict[str, Any]) -> None:
        revoke: GrixRevokeEvent = normalize_revoke_event(payload)
        source = self._message_sources.get((revoke.session_id, revoke.message_id))
        session_key = self._message_session_keys.get((revoke.session_id, revoke.message_id))
        if not session_key and source is not None:
            session_key = build_session_key(
                source,
                group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
                thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
            )

        if self._client:
            await self._client.acknowledge_event(
                event_id=revoke.event_id,
                session_id=revoke.session_id,
                message_id=revoke.message_id,
            )

        pending_event = self._pending_messages.get(session_key or "")
        if pending_event and pending_event.message_id == revoke.message_id:
            self._pending_messages.pop(session_key, None)
            logger.debug(
                "[%s] Dropped pending Hermes event from GRIX revoke for %s/%s",
                self.name,
                revoke.session_id,
                revoke.message_id,
            )

        self._reply_event_ids.pop((revoke.session_id, revoke.message_id), None)

    async def _handle_stop_packet(self, payload: Dict[str, Any]) -> None:
        stop = normalize_stop_event(payload)
        source = self._resolve_stop_source(stop)
        session_key = build_session_key(
            source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        was_active = session_key in self._active_sessions

        if self._client:
            await self._client.acknowledge_stop(
                event_id=stop.event_id,
                stop_id=stop.stop_id,
                accepted=True,
            )

        event = MessageEvent(
            text="/stop",
            message_type=MessageType.COMMAND,
            source=source,
            raw_message={**stop.raw, "_grix_kind": "stop"},
            message_id=stop.trigger_message_id or stop.event_id,
        )

        try:
            await self.handle_message(event)
            if self._client:
                await self._client.complete_stop(
                    event_id=stop.event_id,
                    stop_id=stop.stop_id,
                    status="stopped" if was_active else "already_finished",
                )
        except Exception as exc:
            if self._client:
                await self._client.complete_stop(
                    event_id=stop.event_id,
                    stop_id=stop.stop_id,
                    status="failed",
                    code="stop_handler_failed",
                    message=str(exc),
                )
            raise

    def _resolve_stop_source(self, stop: GrixStopEvent):
        source = self._latest_sources.get(stop.session_id)
        if source:
            return source
        return self.build_source(
            chat_id=stop.session_id,
            chat_type=stop.chat_type,
        )

    async def _complete_event_if_needed(
        self,
        event_id: str,
        *,
        status: str,
        message: Optional[str] = None,
    ) -> None:
        if not self._client or not event_id or event_id in self._completed_event_ids:
            return
        try:
            await self._client.complete_event(
                event_id=event_id,
                status=status,
                message=message,
            )
        except Exception as exc:
            logger.debug(
                "[%s] GRIX complete_event failed for %s: %s",
                self.name,
                event_id,
                exc,
            )
            return
        self._completed_event_ids.add(event_id)

    async def _safe_release_lock(self) -> None:
        if not self._token_lock_identity:
            return
        try:
            from gateway.status import release_scoped_lock

            release_scoped_lock("grix-agent-credentials", self._token_lock_identity)
        except Exception as exc:
            logger.debug("[%s] Failed releasing GRIX lock: %s", self.name, exc)
        finally:
            self._token_lock_identity = None

    @staticmethod
    def _metadata_thread_id(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        value = metadata.get("thread_id")
        return str(value).strip() if value else None

    @staticmethod
    def _metadata_ttl_ms(metadata: Optional[Dict[str, Any]]) -> int:
        if not metadata:
            return 8_000
        try:
            value = int(metadata.get("ttl_ms", 8_000))
        except (TypeError, ValueError):
            value = 8_000
        return max(1_000, min(60_000, value))

    @staticmethod
    def _metadata_ref_message_id(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        value = metadata.get("ref_msg_id")
        return str(value).strip() if value else None

    @staticmethod
    def _metadata_ref_event_id(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
        if not metadata:
            return None
        value = metadata.get("ref_event_id")
        return str(value).strip() if value else None
