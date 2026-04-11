"""Tests for the native Grix gateway adapter and transport."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig, _apply_env_overrides
from gateway.platforms.aibot_contract import (
    AIBOT_PROTOCOL_VERSION,
    CAP_LOCAL_ACTION_V1,
    CMD_AUTH,
    CMD_AUTH_ACK,
    CMD_PING,
    CMD_PONG,
    CMD_SEND_ACK,
    CMD_SEND_MSG,
    CMD_SESSION_ROUTE_BIND,
    LOCAL_ACTION_EXEC_APPROVE,
    LOCAL_ACTION_EXEC_REJECT,
    STATUS_RESPONDED,
    STATUS_STOPPED,
    ERR_APPROVAL_NOT_FOUND,
    ERR_UNSUPPORTED_LOCAL_ACTION,
)
from gateway.platforms.base import MessageEvent
from gateway.platforms.grix import GrixAdapter, build_grix_connection_config, check_grix_requirements
from gateway.platforms.grix_protocol import (
    GrixConnectionConfig,
    build_auth_payload,
    build_packet,
    decode_packet,
    encode_packet,
)
from gateway.platforms.grix_transport import GrixAuthRejectedError, GrixTransportClient
from gateway.session import build_session_key


class FakeSocket:
    def __init__(self):
        self.sent_text: list[str] = []
        self._frames: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def receive(self):
        return await self._frames.get()

    async def close(self, reason: str = "") -> None:
        self.closed = True

    async def push_packet(self, packet: dict) -> None:
        await self._frames.put({"kind": "text", "text": encode_packet(packet)})


async def _wait_for(predicate, timeout: float = 1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("timed out waiting for condition")


def _transport_config() -> GrixConnectionConfig:
    return GrixConnectionConfig(
        endpoint="wss://example.invalid/ws",
        agent_id="9001",
        api_key="secret",
        capabilities=["session_route"],
    )


async def _connect_client(client: GrixTransportClient, socket: FakeSocket) -> None:
    connect_task = asyncio.create_task(client.connect())
    await _wait_for(lambda: len(socket.sent_text) >= 1)
    auth_packet = decode_packet(socket.sent_text[0])
    assert auth_packet["cmd"] == CMD_AUTH
    await socket.push_packet(
        build_packet(
            CMD_AUTH_ACK,
            {
                "code": 0,
                "heartbeat_sec": 30,
                "protocol": AIBOT_PROTOCOL_VERSION,
            },
            auth_packet["seq"],
        )
    )
    await connect_task


class FakeProtocolClient:
    def __init__(self):
        self.bound_routes = []
        self.resolved_routes = []
        self.acknowledged_events = []
        self.completed_events = []
        self.acknowledged_stops = []
        self.completed_stops = []
        self.local_action_results = []
        self.sent = []
        self.edits = []
        self.activities = []
        self.resolved_session_id = "g_resolved"

    async def bind_session_route(self, **kwargs):
        self.bound_routes.append(kwargs)

    async def resolve_session_route(self, **kwargs):
        self.resolved_routes.append(kwargs)
        return {
            "channel": kwargs["channel"],
            "account_id": kwargs["account_id"],
            "route_session_key": kwargs["route_session_key"],
            "session_id": self.resolved_session_id,
        }

    async def acknowledge_event(self, **kwargs):
        self.acknowledged_events.append(kwargs)

    async def complete_event(self, **kwargs):
        self.completed_events.append(kwargs)

    async def acknowledge_stop(self, **kwargs):
        self.acknowledged_stops.append(kwargs)

    async def complete_stop(self, **kwargs):
        self.completed_stops.append(kwargs)

    async def send_text(self, session_id: str, text: str, **kwargs):
        self.sent.append(
            {
                "session_id": session_id,
                "text": text,
                **kwargs,
            }
        )
        return {"ok": True, "message_id": "out-1"}

    async def edit_message(self, session_id: str, message_id: str, text: str, **kwargs):
        self.edits.append({"session_id": session_id, "message_id": message_id, "text": text, **kwargs})
        return {"ok": True, "session_id": session_id, "message_id": message_id}

    async def set_session_activity(self, **kwargs):
        self.activities.append(kwargs)

    async def send_local_action_result(self, **kwargs):
        self.local_action_results.append(kwargs)


class SlowBindProtocolClient(FakeProtocolClient):
    def __init__(self):
        super().__init__()
        self.bind_started = asyncio.Event()
        self.release_bind = asyncio.Event()

    async def bind_session_route(self, **kwargs):
        self.bound_routes.append(kwargs)
        self.bind_started.set()
        await self.release_bind.wait()


class TestGrixConfig:
    def test_grix_enum_exists(self):
        assert Platform.GRIX.value == "grix"

    def test_apply_env_overrides_grix(self, monkeypatch):
        monkeypatch.setenv("GRIX_ENDPOINT", "wss://example.invalid/ws")
        monkeypatch.setenv("GRIX_AGENT_ID", "9001")
        monkeypatch.setenv("GRIX_API_KEY", "secret")
        monkeypatch.setenv("GRIX_ACCOUNT_ID", "main")
        monkeypatch.setenv("GRIX_CAPABILITIES", "session_route,thread_v1")
        monkeypatch.setenv("GRIX_HOME_CHANNEL", "g_1001")

        config = GatewayConfig()
        _apply_env_overrides(config)

        assert Platform.GRIX in config.platforms
        grix = config.platforms[Platform.GRIX]
        assert grix.enabled is True
        assert grix.api_key == "secret"
        assert grix.extra["endpoint"] == "wss://example.invalid/ws"
        assert grix.extra["agent_id"] == "9001"
        assert grix.extra["capabilities"] == ["session_route", "thread_v1"]
        assert grix.home_channel is not None
        assert grix.home_channel.chat_id == "g_1001"

    def test_connected_platforms_includes_grix(self, monkeypatch):
        monkeypatch.setenv("GRIX_ENDPOINT", "wss://example.invalid/ws")
        monkeypatch.setenv("GRIX_AGENT_ID", "9001")
        monkeypatch.setenv("GRIX_API_KEY", "secret")

        config = GatewayConfig()
        _apply_env_overrides(config)
        assert Platform.GRIX in config.get_connected_platforms()


class TestGrixTooling:
    def test_toolset_exists(self):
        from toolsets import TOOLSETS

        assert "hermes-grix" in TOOLSETS
        assert "hermes-grix" in TOOLSETS["hermes-gateway"]["includes"]

    def test_platform_hint_exists(self):
        from agent.prompt_builder import PLATFORM_HINTS

        assert "grix" in PLATFORM_HINTS
        assert "plain-text" in PLATFORM_HINTS["grix"] or "plain text" in PLATFORM_HINTS["grix"]

    def test_requirement_check(self):
        assert check_grix_requirements() is True

    def test_build_connection_config(self):
        cfg = PlatformConfig(
            enabled=True,
            api_key="secret",
            extra={
                "endpoint": "wss://example.invalid/ws",
                "agent_id": "9001",
                "account_id": "main",
            },
        )
        built = build_grix_connection_config(cfg)
        assert built.endpoint == "wss://example.invalid/ws"
        assert built.agent_id == "9001"
        assert built.api_key == "secret"
        assert built.account_id == "main"
        assert CAP_LOCAL_ACTION_V1 in built.capabilities
        assert built.local_actions == [LOCAL_ACTION_EXEC_APPROVE, LOCAL_ACTION_EXEC_REJECT]

    def test_build_auth_payload_includes_local_actions(self):
        payload = build_auth_payload(_transport_config())
        assert CAP_LOCAL_ACTION_V1 in payload["capabilities"]
        assert payload["local_actions"] == [LOCAL_ACTION_EXEC_APPROVE, LOCAL_ACTION_EXEC_REJECT]


class TestGrixTransport:
    @pytest.mark.asyncio
    async def test_connect_and_send_text(self):
        socket = FakeSocket()
        client = GrixTransportClient(
            _transport_config(),
            connector=lambda _config: asyncio.sleep(0, result=socket),
        )

        await _connect_client(client, socket)
        auth_packet = decode_packet(socket.sent_text[0])
        assert auth_packet["payload"] == build_auth_payload(_transport_config())

        send_task = asyncio.create_task(client.send_text("g_1001", "hello"))
        await _wait_for(lambda: len(socket.sent_text) >= 2)
        send_packet = decode_packet(socket.sent_text[-1])
        assert send_packet["cmd"] == CMD_SEND_MSG
        await socket.push_packet(build_packet(CMD_SEND_ACK, {"msg_id": "55"}, send_packet["seq"]))

        receipt = await send_task
        assert receipt["ok"] is True
        assert receipt["message_id"] == "55"

        await socket.push_packet(build_packet(CMD_PING, {"ts": 1}, 44))
        await _wait_for(lambda: len(socket.sent_text) >= 3)
        pong_packet = decode_packet(socket.sent_text[-1])
        assert pong_packet["cmd"] == CMD_PONG
        assert pong_packet["seq"] == 44

        await client.disconnect()

    @pytest.mark.asyncio
    async def test_auth_rejected(self):
        socket = FakeSocket()
        client = GrixTransportClient(
            _transport_config(),
            connector=lambda _config: asyncio.sleep(0, result=socket),
        )

        connect_task = asyncio.create_task(client.connect())
        await _wait_for(lambda: len(socket.sent_text) >= 1)
        auth_packet = decode_packet(socket.sent_text[0])
        await socket.push_packet(build_packet(CMD_AUTH_ACK, {"code": 10401, "msg": "bad key"}, auth_packet["seq"]))
        with pytest.raises(GrixAuthRejectedError):
            await connect_task

    @pytest.mark.asyncio
    async def test_send_timeout_disconnects_transport(self):
        socket = FakeSocket()
        statuses = []
        client = GrixTransportClient(
            _transport_config(),
            connector=lambda _config: asyncio.sleep(0, result=socket),
            on_status=statuses.append,
        )

        await _connect_client(client, socket)

        with pytest.raises(TimeoutError, match="send_msg timeout"):
            await client.send_text("g_1001", "hello", timeout_ms=20)

        assert socket.closed is True
        assert client.status["connected"] is False
        assert client.status["authed"] is False
        assert client.status["last_error"] == "send_msg timeout"
        assert any(status["connected"] is False for status in statuses)

    @pytest.mark.asyncio
    async def test_reader_close_disconnects_without_self_cancel_recursion(self):
        socket = FakeSocket()
        client = GrixTransportClient(
            _transport_config(),
            connector=lambda _config: asyncio.sleep(0, result=socket),
        )

        await _connect_client(client, socket)
        await socket._frames.put({"kind": "closed", "reason": "server closed"})

        await _wait_for(lambda: socket.closed and client.status["connected"] is False)
        assert client.status["last_error"] == "server closed"

    @pytest.mark.asyncio
    async def test_unsolicited_packet_handler_can_issue_followup_request(self):
        socket = FakeSocket()
        handled = asyncio.Event()
        client: GrixTransportClient

        async def on_packet(packet: dict) -> None:
            if packet["cmd"] != "event_msg":
                return
            await client.request(
                CMD_SESSION_ROUTE_BIND,
                {
                    "channel": "grix",
                    "account_id": "main",
                    "route_session_key": "agent:main:grix:dm:g_1001",
                    "session_id": "g_1001",
                },
                expected=(CMD_SEND_ACK,),
                timeout_ms=100,
            )
            handled.set()

        client = GrixTransportClient(
            _transport_config(),
            connector=lambda _config: asyncio.sleep(0, result=socket),
            on_packet=on_packet,
        )

        await _connect_client(client, socket)
        await socket.push_packet(
            build_packet(
                "event_msg",
                {
                    "event_id": "evt-1",
                    "session_id": "g_1001",
                    "msg_id": "55",
                    "content": "hello",
                },
                0,
            )
        )
        await _wait_for(lambda: len(socket.sent_text) >= 2)
        bind_packet = decode_packet(socket.sent_text[-1])
        assert bind_packet["cmd"] == CMD_SESSION_ROUTE_BIND

        await socket.push_packet(build_packet(CMD_SEND_ACK, {"ok": True}, bind_packet["seq"]))
        await _wait_for(handled.is_set)


class TestGrixAdapter:
    @pytest.mark.asyncio
    async def test_event_msg_dispatches_and_completes(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client

        seen_events = []

        async def handler(event):
            seen_events.append(event)
            return "**hello back**"

        adapter.set_message_handler(handler)
        await adapter._handle_protocol_packet(
            {
                "cmd": "event_msg",
                "seq": 0,
                "payload": {
                    "event_id": "evt-1",
                    "event_type": "group_message",
                    "session_type": 2,
                    "session_id": "g_1001",
                    "thread_id": "topic-a",
                    "msg_id": "55",
                    "sender_id": "u_8",
                    "sender_name": "alice",
                    "content": "hello",
                    "attachments": [
                        {
                            "media_url": "https://cdn.example.com/one.png",
                            "content_type": "image/png",
                            "attachment_type": "image",
                        }
                    ],
                },
            }
        )

        await _wait_for(lambda: len(fake_client.sent) == 1)
        await _wait_for(lambda: len(fake_client.completed_events) == 1)

        assert len(seen_events) == 1
        assert seen_events[0].source.platform == Platform.GRIX
        assert seen_events[0].source.thread_id == "topic-a"
        assert seen_events[0].media_urls == ["https://cdn.example.com/one.png"]
        assert fake_client.acknowledged_events[0]["event_id"] == "evt-1"
        assert fake_client.bound_routes[0]["route_session_key"].startswith("agent:main:grix:group:g_1001:topic-a")
        assert fake_client.sent[0]["event_id"] == "evt-1"
        assert fake_client.sent[0]["reply_to_message_id"] == "55"
        assert fake_client.sent[0]["text"] == "hello back"
        assert fake_client.completed_events[0]["status"] == STATUS_RESPONDED

    @pytest.mark.asyncio
    async def test_event_msg_acknowledges_before_route_bind_completes(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = SlowBindProtocolClient()
        adapter._client = fake_client

        seen_events = []

        async def handler(event):
            seen_events.append(event)
            return "hello back"

        adapter.set_message_handler(handler)
        packet_task = asyncio.create_task(
            adapter._handle_protocol_packet(
                {
                    "cmd": "event_msg",
                    "seq": 0,
                    "payload": {
                        "event_id": "evt-2",
                        "event_type": "private_message",
                        "session_type": 1,
                        "session_id": "u_1001",
                        "msg_id": "56",
                        "sender_id": "u_8",
                        "sender_name": "alice",
                        "content": "hello",
                    },
                }
            )
        )

        await _wait_for(lambda: len(fake_client.acknowledged_events) == 1)
        await _wait_for(lambda: fake_client.bind_started.is_set())
        await _wait_for(lambda: len(seen_events) == 1)
        await _wait_for(lambda: len(fake_client.sent) == 1)
        await _wait_for(lambda: len(fake_client.completed_events) == 1)

        assert fake_client.acknowledged_events[0]["event_id"] == "evt-2"
        assert fake_client.bound_routes[0]["session_id"] == "u_1001"

        fake_client.release_bind.set()
        await packet_task
        await adapter.cancel_background_tasks()

    @pytest.mark.asyncio
    async def test_event_stop_dispatches_stop_command(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client
        source = adapter.build_source(
            chat_id="g_1001",
            chat_type="group",
            user_id="u_8",
            user_name="alice",
            thread_id="topic-a",
        )
        session_key = build_session_key(source)
        adapter._latest_sources["g_1001"] = source
        adapter._active_sessions[session_key] = asyncio.Event()

        seen_commands = []

        async def handler(event):
            seen_commands.append(event.text)
            return "stopped"

        adapter.set_message_handler(handler)
        await adapter._handle_protocol_packet(
            {
                "cmd": "event_stop",
                "seq": 0,
                "payload": {
                    "event_id": "stop-1",
                    "session_id": "g_1001",
                    "reason": "user_stop",
                },
            }
        )

        assert seen_commands == ["/stop"]
        assert fake_client.acknowledged_stops[0]["event_id"] == "stop-1"
        assert fake_client.completed_stops[0]["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_duplicate_event_msg_is_acknowledged_without_duplicate_delivery(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client

        seen_events = []

        async def handler(event):
            seen_events.append(event.text)
            return "hello back"

        adapter.set_message_handler(handler)
        packet = {
            "cmd": "event_msg",
            "seq": 0,
            "payload": {
                "event_id": "evt-dup-1",
                "event_type": "group_message",
                "session_type": 2,
                "session_id": "g_1001",
                "thread_id": "topic-a",
                "msg_id": "55",
                "sender_id": "u_8",
                "sender_name": "alice",
                "content": "hello",
            },
        }

        await adapter._handle_protocol_packet(packet)
        await _wait_for(lambda: len(fake_client.sent) == 1)
        await _wait_for(lambda: len(fake_client.completed_events) == 1)

        await adapter._handle_protocol_packet(packet)

        assert seen_events == ["hello"]
        assert len(fake_client.sent) == 1
        assert len(fake_client.bound_routes) == 1
        assert len(fake_client.acknowledged_events) == 2
        assert len(fake_client.completed_events) == 2
        assert fake_client.completed_events[1]["status"] == STATUS_RESPONDED

    @pytest.mark.asyncio
    async def test_duplicate_event_stop_replays_completion_without_duplicate_command(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client
        source = adapter.build_source(
            chat_id="g_1001",
            chat_type="group",
            user_id="u_8",
            user_name="alice",
            thread_id="topic-a",
        )
        session_key = build_session_key(source)
        adapter._latest_sources["g_1001"] = source
        adapter._active_sessions[session_key] = asyncio.Event()

        seen_commands = []

        async def handler(event):
            seen_commands.append(event.text)
            return "stopped"

        adapter.set_message_handler(handler)
        packet = {
            "cmd": "event_stop",
            "seq": 0,
            "payload": {
                "event_id": "stop-dup-1",
                "session_id": "g_1001",
                "reason": "user_stop",
                "stop_id": "stop-token-1",
            },
        }

        await adapter._handle_protocol_packet(packet)
        await adapter._handle_protocol_packet(packet)

        assert seen_commands == ["/stop"]
        assert len(fake_client.acknowledged_stops) == 2
        assert len(fake_client.completed_stops) == 2
        assert fake_client.completed_stops[0]["status"] == STATUS_STOPPED
        assert fake_client.completed_stops[1]["status"] == STATUS_STOPPED

    @pytest.mark.asyncio
    async def test_send_resolves_bound_route_session_key(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        fake_client.resolved_session_id = "g_2002"
        adapter._client = fake_client
        source = adapter.build_source(
            chat_id="g_2002",
            chat_type="group",
            user_id="u_8",
            user_name="alice",
            thread_id="topic-a",
        )
        session_key = build_session_key(source)
        adapter._latest_sources[session_key] = source

        result = await adapter.send(session_key, "hello from route key")

        assert result.success is True
        assert fake_client.resolved_routes[0]["route_session_key"] == session_key
        assert fake_client.sent[0]["session_id"] == "g_2002"
        assert fake_client.sent[0]["thread_id"] == "topic-a"

    @pytest.mark.asyncio
    async def test_send_exec_approval_emits_backend_compatible_prompt(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client

        result = await adapter.send_exec_approval(
            chat_id="g_1001",
            command="rm -rf /tmp/demo",
            session_key="agent:main:grix:group:g_1001:topic-a",
            description="dangerous deletion",
            approval_id="req_123",
        )

        assert result.success is True
        assert "req_123" in adapter._approval_state
        sent_text = fake_client.sent[0]["text"]
        assert sent_text.startswith("🔒 Exec approval required")
        assert "ID: req_123" in sent_text
        assert "Command: rm -rf /tmp/demo" in sent_text
        assert "Host: Hermes Grix" in sent_text
        assert "Expires in: 300s" in sent_text

    @pytest.mark.asyncio
    async def test_local_action_approve_resolves_specific_approval(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client
        adapter._approval_state["req_123"] = {
            "session_key": "agent:main:grix:group:g_1001:topic-a",
            "chat_id": "g_1001",
            "thread_id": "topic-a",
        }
        adapter.pause_typing_for_chat("g_1001")

        with patch("tools.approval.resolve_gateway_approval_by_id", return_value="agent:main:grix:group:g_1001:topic-a") as mock_resolve:
            await adapter._handle_protocol_packet(
                {
                    "cmd": "local_action",
                    "seq": 0,
                    "payload": {
                        "action_id": "act-1",
                        "action_type": LOCAL_ACTION_EXEC_APPROVE,
                        "params": {
                            "approval_id": "req_123",
                            "decision": "allow-once",
                        },
                    },
                }
            )

        mock_resolve.assert_called_once_with("req_123", "once")
        assert fake_client.local_action_results == [
            {"action_id": "act-1", "status": "ok", "result": "allow-once"}
        ]
        assert "g_1001" not in adapter._typing_paused
        assert "req_123" not in adapter._approval_state

    @pytest.mark.asyncio
    async def test_local_action_reports_stale_approval(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client
        adapter._approval_state["req_404"] = {
            "session_key": "agent:main:grix:group:g_1001:topic-a",
            "chat_id": "g_1001",
            "thread_id": None,
        }

        with patch("tools.approval.resolve_gateway_approval_by_id", return_value=None):
            await adapter._handle_protocol_packet(
                {
                    "cmd": "local_action",
                    "seq": 0,
                    "payload": {
                        "action_id": "act-404",
                        "action_type": LOCAL_ACTION_EXEC_APPROVE,
                        "params": {
                            "approval_id": "req_404",
                            "decision": "allow-once",
                        },
                    },
                }
            )

        assert fake_client.local_action_results == [
            {
                "action_id": "act-404",
                "status": "failed",
                "error_code": ERR_APPROVAL_NOT_FOUND,
                "error_message": "unknown or expired approval id",
            }
        ]

    @pytest.mark.asyncio
    async def test_local_action_reports_unsupported_action(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client

        await adapter._handle_protocol_packet(
            {
                "cmd": "local_action",
                "seq": 0,
                "payload": {
                    "action_id": "act-unsupported",
                    "action_type": "open_url",
                    "params": {},
                },
            }
        )

        assert fake_client.local_action_results == [
            {
                "action_id": "act-unsupported",
                "status": "unsupported",
                "error_code": ERR_UNSUPPORTED_LOCAL_ACTION,
                "error_message": "unsupported local action: open_url",
            }
        ]

    @pytest.mark.asyncio
    async def test_event_edit_updates_pending_message(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client
        source = adapter.build_source(
            chat_id="g_1001",
            chat_type="group",
            user_id="u_8",
            user_name="alice",
            thread_id="topic-a",
        )
        session_key = build_session_key(source)
        adapter._message_session_keys[("g_1001", "55")] = session_key
        adapter._pending_messages[session_key] = MessageEvent(
            text="old text",
            source=source,
            message_id="55",
        )

        await adapter._handle_protocol_packet(
            {
                "cmd": "event_edit",
                "seq": 0,
                "payload": {
                    "session_id": "g_1001",
                    "session_type": 2,
                    "thread_id": "topic-a",
                    "msg_id": "55",
                    "content": "new text",
                    "quoted_message_id": "54",
                },
            }
        )

        assert adapter._pending_messages[session_key].text == "new text"
        assert adapter._pending_messages[session_key].reply_to_message_id == "54"
        assert adapter._pending_messages[session_key].raw_message["_grix_kind"] == "edit"
        assert fake_client.acknowledged_events == []
        assert fake_client.completed_events == []

    @pytest.mark.asyncio
    async def test_event_revoke_acknowledges_and_drops_pending_message(self):
        adapter = GrixAdapter(
            PlatformConfig(
                enabled=True,
                api_key="secret",
                extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
            )
        )
        fake_client = FakeProtocolClient()
        adapter._client = fake_client
        source = adapter.build_source(
            chat_id="g_1001",
            chat_type="group",
            user_id="u_8",
            user_name="alice",
            thread_id="topic-a",
        )
        session_key = build_session_key(source)
        adapter._message_sources[("g_1001", "55")] = source
        adapter._message_session_keys[("g_1001", "55")] = session_key
        adapter._reply_event_ids[("g_1001", "55")] = "evt-1"
        adapter._pending_messages[session_key] = MessageEvent(
            text="old text",
            source=source,
            message_id="55",
        )

        await adapter._handle_protocol_packet(
            {
                "cmd": "event_revoke",
                "seq": 0,
                "payload": {
                    "event_id": "evt-revoke-1",
                    "session_id": "g_1001",
                    "session_type": 2,
                    "msg_id": "55",
                    "sender_id": "u_8",
                    "is_revoked": True,
                },
            }
        )

        assert session_key not in adapter._pending_messages
        assert ("g_1001", "55") not in adapter._reply_event_ids
        assert fake_client.acknowledged_events[0]["event_id"] == "evt-revoke-1"
        assert fake_client.completed_events == []
