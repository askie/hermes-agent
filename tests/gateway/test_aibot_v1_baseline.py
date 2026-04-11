"""Frozen regression coverage for the minimal public AIBOT v1 flow."""

from __future__ import annotations

import asyncio

import pytest

from gateway.platforms.aibot_contract import (
    AIBOT_PROTOCOL_VERSION,
    CMD_AUTH,
    CMD_AUTH_ACK,
    CMD_EVENT_ACK,
    CMD_EVENT_RESULT,
    CMD_EVENT_STOP_ACK,
    CMD_EVENT_STOP_RESULT,
    CMD_SEND_ACK,
    CMD_SEND_MSG,
    STATUS_RESPONDED,
    STATUS_STOPPED,
)
from gateway.platforms.grix_protocol import (
    GrixConnectionConfig,
    build_packet,
    decode_packet,
    encode_packet,
)
from gateway.platforms.grix_transport import GrixTransportClient


class FakeSocket:
    def __init__(self):
        self.sent_text: list[str] = []
        self._frames: asyncio.Queue = asyncio.Queue()

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def receive(self):
        return await self._frames.get()

    async def close(self, reason: str = "") -> None:
        return None

    async def push_packet(self, packet: dict) -> None:
        await self._frames.put({"kind": "text", "text": encode_packet(packet)})


async def _wait_for(predicate, timeout: float = 1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("timed out waiting for condition")


async def _connect_client(socket: FakeSocket) -> GrixTransportClient:
    config = GrixConnectionConfig(
        endpoint="wss://example.invalid/ws",
        agent_id="9001",
        api_key="secret",
        host_version="optional",
    )

    async def _connector(_config):
        return socket

    client = GrixTransportClient(config, connector=_connector)
    connect_task = asyncio.create_task(client.connect())
    await _wait_for(lambda: len(socket.sent_text) >= 1)
    auth_packet = decode_packet(socket.sent_text[0])
    await socket.push_packet(
        build_packet(
            CMD_AUTH_ACK,
            {"code": 0, "heartbeat_sec": 30, "protocol": AIBOT_PROTOCOL_VERSION},
            auth_packet["seq"],
        )
    )
    await connect_task
    return client


class TestAibotV1Baseline:
    @pytest.mark.asyncio
    async def test_auth_and_send_text_packets_stay_on_v1_baseline(self):
        socket = FakeSocket()
        client = await _connect_client(socket)

        auth_packet = decode_packet(socket.sent_text[0])
        assert auth_packet["cmd"] == CMD_AUTH
        assert auth_packet["payload"] == {
            "agent_id": "9001",
            "api_key": "secret",
            "client": "hermes-agent",
            "client_type": "hermes",
            "client_version": "0.8.0",
            "protocol_version": "aibot-agent-api-v1",
            "contract_version": 1,
            "host_type": "hermes",
            "host_version": "optional",
            "capabilities": [
                "session_route",
                "thread_v1",
                "inbound_media_v1",
                "local_action_v1",
            ],
            "local_actions": [
                "exec_approve",
                "exec_reject",
            ],
        }

        send_task = asyncio.create_task(
            client.send_text(
                "g_1001",
                "hello",
                reply_to_message_id="54",
                thread_id="topic-a",
                event_id="evt-1",
            )
        )
        await _wait_for(lambda: len(socket.sent_text) >= 2)

        send_packet = decode_packet(socket.sent_text[1])
        assert send_packet["cmd"] == CMD_SEND_MSG
        assert send_packet["payload"] == {
            "session_id": "g_1001",
            "msg_type": 1,
            "content": "hello",
            "quoted_message_id": "54",
            "thread_id": "topic-a",
            "event_id": "evt-1",
        }

        await socket.push_packet(
            build_packet(
                CMD_SEND_ACK,
                {"session_id": "g_1001", "msg_id": "56"},
                send_packet["seq"],
            )
        )
        receipt = await send_task
        assert receipt["message_id"] == "56"

        await client.disconnect("done")

    @pytest.mark.asyncio
    async def test_event_ack_and_stop_result_packets_keep_frozen_shapes(self):
        socket = FakeSocket()
        client = await _connect_client(socket)

        await client.acknowledge_event(
            event_id="evt-1",
            session_id="g_1001",
            message_id="55",
            received_at=1710000000100,
        )
        ack_packet = decode_packet(socket.sent_text[1])
        assert ack_packet == {
            "cmd": CMD_EVENT_ACK,
            "seq": ack_packet["seq"],
            "payload": {
                "event_id": "evt-1",
                "received_at": 1710000000100,
                "session_id": "g_1001",
                "msg_id": "55",
            },
        }

        await client.complete_event(
            event_id="evt-1",
            status=STATUS_RESPONDED,
            updated_at=1710000001100,
        )
        result_packet = decode_packet(socket.sent_text[2])
        assert result_packet == {
            "cmd": CMD_EVENT_RESULT,
            "seq": result_packet["seq"],
            "payload": {
                "event_id": "evt-1",
                "status": STATUS_RESPONDED,
                "updated_at": 1710000001100,
            },
        }

        await client.acknowledge_stop(
            event_id="stop-1",
            accepted=True,
            stop_id="stop-token-1",
            updated_at=1710000000100,
        )
        stop_ack_packet = decode_packet(socket.sent_text[3])
        assert stop_ack_packet == {
            "cmd": CMD_EVENT_STOP_ACK,
            "seq": stop_ack_packet["seq"],
            "payload": {
                "event_id": "stop-1",
                "accepted": True,
                "stop_id": "stop-token-1",
                "updated_at": 1710000000100,
            },
        }

        await client.complete_stop(
            event_id="stop-1",
            stop_id="stop-token-1",
            status=STATUS_STOPPED,
            updated_at=1710000000200,
        )
        stop_result_packet = decode_packet(socket.sent_text[4])
        assert stop_result_packet == {
            "cmd": CMD_EVENT_STOP_RESULT,
            "seq": stop_result_packet["seq"],
            "payload": {
                "event_id": "stop-1",
                "status": STATUS_STOPPED,
                "stop_id": "stop-token-1",
                "updated_at": 1710000000200,
            },
        }

        await client.disconnect("done")
