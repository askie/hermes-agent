"""Grix message unsend tool -- delete a message through the existing WS connection."""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

GRIX_UNSEND_SCHEMA = {
    "name": "grix_unsend",
    "description": (
        "Delete (unsend/recall) a Grix message through the existing WebSocket connection. "
        "Requires the message_id to delete and the session_id or target where the message was sent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "The ID of the message to delete.",
            },
            "session_id": {
                "type": "string",
                "description": "The session ID where the message was sent.",
            },
        },
        "required": ["message_id", "session_id"],
    },
}


def _check_grix_unsend() -> bool:
    try:
        from gateway.run import _gateway_runner_ref
        from gateway.config import Platform
        runner = _gateway_runner_ref()
        if not runner:
            return False
        adapter = runner.adapters.get(Platform.GRIX)
        return adapter is not None
    except Exception:
        return False


async def _grix_unsend_handler(args: dict, **kwargs) -> str:
    from tools.registry import tool_error, tool_result

    message_id = (args.get("message_id") or "").strip()
    session_id = (args.get("session_id") or "").strip()

    if not message_id:
        return tool_error("message_id is required")
    if not session_id:
        return tool_error("session_id is required")

    try:
        from gateway.run import _gateway_runner_ref
        from gateway.config import Platform

        runner = _gateway_runner_ref()
        if not runner:
            return tool_error("Gateway is not running")

        adapter = runner.adapters.get(Platform.GRIX)
        if not adapter:
            return tool_error("Grix adapter is not connected")

        result = await adapter.delete_message(
            chat_id=session_id,
            message_id=message_id,
        )
        if result.success:
            return tool_result({"ok": True, "message_id": result.message_id})
        return tool_error(result.error or "unsend failed")
    except Exception as exc:
        logger.warning("grix_unsend failed: %s", exc)
        return tool_error(f"unsend failed: {exc}")


from tools.registry import registry

registry.register(
    name="grix_unsend",
    toolset="grix",
    schema=GRIX_UNSEND_SCHEMA,
    handler=_grix_unsend_handler,
    check_fn=_check_grix_unsend,
    is_async=True,
    description="Delete (unsend) a Grix message through the existing WebSocket connection.",
    emoji="🗑️",
)
