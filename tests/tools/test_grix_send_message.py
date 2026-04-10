"""Grix-specific send_message tool routing tests."""

from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from tools.send_message_tool import _parse_target_ref, _send_grix, _send_to_platform


def test_parse_grix_target_ref():
    assert _parse_target_ref("grix", "g_1001:topic-a") == ("g_1001", "topic-a", True)
    assert _parse_target_ref("grix", "u_88") == ("u_88", None, True)
    assert _parse_target_ref(
        "grix",
        "agent:main:grix:group:g_1001:topic-a",
    ) == ("agent:main:grix:group:g_1001:topic-a", None, True)


@pytest.mark.asyncio
async def test_send_to_platform_routes_grix():
    pconfig = PlatformConfig(
        enabled=True,
        api_key="secret",
        extra={"endpoint": "wss://example.invalid/ws", "agent_id": "9001"},
    )
    with patch(
        "tools.send_message_tool._send_grix",
        new=AsyncMock(return_value={"success": True, "platform": "grix", "chat_id": "g_1001"}),
    ) as send_mock:
        result = await _send_to_platform(
            Platform.GRIX,
            pconfig,
            "g_1001",
            "hello",
            thread_id="topic-a",
        )

    send_mock.assert_awaited_once_with(pconfig, "g_1001", "hello", thread_id="topic-a")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_send_grix_resolves_route_session_key(monkeypatch):
    pconfig = PlatformConfig(
        enabled=True,
        api_key="secret",
        extra={
            "endpoint": "wss://example.invalid/ws",
            "agent_id": "9001",
            "account_id": "main",
        },
    )

    class FakeClient:
        instances = []

        def __init__(self, connection):
            self.connection = connection
            self.calls = []
            FakeClient.instances.append(self)

        async def connect(self):
            return None

        async def resolve_session_route(self, **kwargs):
            self.calls.append(("resolve", kwargs))
            return {
                "channel": kwargs["channel"],
                "account_id": kwargs["account_id"],
                "route_session_key": kwargs["route_session_key"],
                "session_id": "g_2002",
            }

        async def send_text(self, session_id, text, **kwargs):
            self.calls.append(("send", {"session_id": session_id, "text": text, **kwargs}))
            return {"ok": True, "message_id": "out-1"}

        async def disconnect(self):
            return None

    monkeypatch.setattr("gateway.platforms.grix_transport.GrixTransportClient", FakeClient)

    result = await _send_grix(
        pconfig,
        "agent:main:grix:group:g_2002:topic-a:user-1",
        "hello",
    )

    assert result["success"] is True
    fake = FakeClient.instances[0]
    assert fake.calls[0][0] == "resolve"
    assert fake.calls[1][0] == "send"
    assert fake.calls[1][1]["session_id"] == "g_2002"
    assert fake.calls[1][1]["thread_id"] == "topic-a"


@pytest.mark.asyncio
async def test_send_grix_uses_saved_session_origin_for_ambiguous_group_key(monkeypatch, tmp_path):
    pconfig = PlatformConfig(
        enabled=True,
        api_key="secret",
        extra={
            "endpoint": "wss://example.invalid/ws",
            "agent_id": "9001",
            "account_id": "main",
        },
    )
    hermes_home = tmp_path / ".hermes"
    sessions_dir = hermes_home / "sessions"
    sessions_dir.mkdir(parents=True)
    session_key = "agent:main:grix:group:g_3001:topic-a"
    (sessions_dir / "sessions.json").write_text(
        (
            "{\n"
            f'  "{session_key}": {{\n'
            '    "session_key": "agent:main:grix:group:g_3001:topic-a",\n'
            '    "session_id": "s_1",\n'
            '    "created_at": "2026-04-10T00:00:00",\n'
            '    "updated_at": "2026-04-10T00:00:00",\n'
            '    "platform": "grix",\n'
            '    "chat_type": "group",\n'
            '    "origin": {\n'
            '      "platform": "grix",\n'
            '      "chat_id": "g_3001",\n'
            '      "chat_type": "group",\n'
            '      "thread_id": "topic-a"\n'
            "    }\n"
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    class FakeClient:
        instances = []

        def __init__(self, connection):
            self.connection = connection
            self.calls = []
            FakeClient.instances.append(self)

        async def connect(self):
            return None

        async def resolve_session_route(self, **kwargs):
            self.calls.append(("resolve", kwargs))
            return {
                "channel": kwargs["channel"],
                "account_id": kwargs["account_id"],
                "route_session_key": kwargs["route_session_key"],
                "session_id": "g_3001",
            }

        async def send_text(self, session_id, text, **kwargs):
            self.calls.append(("send", {"session_id": session_id, "text": text, **kwargs}))
            return {"ok": True, "message_id": "out-1"}

        async def disconnect(self):
            return None

    monkeypatch.setattr("gateway.platforms.grix_transport.GrixTransportClient", FakeClient)

    result = await _send_grix(
        pconfig,
        session_key,
        "hello",
    )

    assert result["success"] is True
    fake = FakeClient.instances[0]
    assert fake.calls[1][1]["session_id"] == "g_3001"
    assert fake.calls[1][1]["thread_id"] == "topic-a"
