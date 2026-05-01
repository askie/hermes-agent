"""Grix agent_invoke tool -- call Grix backend APIs through the existing WS connection."""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Actions routed through agent_invoke (backend API proxy)
_INVOKE_ACTIONS = {
    "contact_search": "Search contacts by keyword or ID",
    "session_search": "Search sessions by keyword",
    "message_history": "Get message history for a session",
    "message_search": "Search messages by keyword in a session",
    "group_create": "Create a new group",
    "group_detail_read": "Read group details",
    "group_leave_self": "Leave a group",
    "group_member_add": "Add members to a group",
    "group_member_remove": "Remove members from a group",
    "group_member_role_update": "Update a group member's role",
    "group_all_members_muted_update": "Toggle mute-all-members for a group",
    "group_member_speaking_update": "Toggle speaking permission for a member",
    "group_dissolve": "Dissolve a group",
    "agent_api_create": "Create a new agent",
    "agent_category_list": "List agent categories",
    "agent_category_create": "Create an agent category",
    "agent_category_update": "Update an agent category",
    "agent_category_assign": "Assign an agent to a category",
    "agent_api_status": "Get agent API status",
    "agent_api_key_rotate": "Rotate an agent's API key",
}

# Actions routed directly to WS protocol commands (no backend proxy)
_PROTOCOL_ACTIONS = {
    "send_message": "Send a text message to a session",
    "delete_message": "Delete (unsend/recall) a message",
}

SUPPORTED_ACTIONS = {**_INVOKE_ACTIONS, **_PROTOCOL_ACTIONS}

GRIX_INVOKE_SCHEMA = {
    "name": "grix_invoke",
    "description": (
        "Unified Grix API — send messages, delete messages, query contacts/sessions/messages, "
        "manage groups, and administer agents. All through the existing Grix WebSocket connection.\n\n"
        "Supported actions:\n"
        "  Message: send_message, delete_message\n"
        "  Query: contact_search, session_search, message_history, message_search\n"
        "  Group: group_create, group_detail_read, group_leave_self, group_member_add, "
        "group_member_remove, group_member_role_update, group_all_members_muted_update, "
        "group_member_speaking_update, group_dissolve\n"
        "  Admin: agent_api_create, agent_category_list, agent_category_create, "
        "agent_category_update, agent_category_assign, agent_api_status, agent_api_key_rotate"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "The backend action to invoke.",
                "enum": list(SUPPORTED_ACTIONS.keys()),
            },
            "params": {
                "type": "object",
                "description": "Action-specific parameters as a JSON object.",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Optional request timeout in milliseconds (default: 15000).",
                "default": 15000,
            },
        },
        "required": ["action"],
    },
}


def _check_grix_invoke() -> bool:
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


async def _grix_invoke_handler(args: dict, **kwargs) -> str:
    from tools.registry import tool_error, tool_result

    action = (args.get("action") or "").strip()
    if not action:
        return tool_error("action is required")
    if action not in SUPPORTED_ACTIONS:
        return tool_error(f"Unknown action '{action}'. Supported: {', '.join(sorted(SUPPORTED_ACTIONS))}")

    params = args.get("params")
    if params is not None and not isinstance(params, dict):
        return tool_error("params must be a JSON object")

    timeout_ms = args.get("timeout_ms")
    if timeout_ms is not None:
        try:
            timeout_ms = int(timeout_ms)
        except (TypeError, ValueError):
            return tool_error("timeout_ms must be an integer")

    try:
        from gateway.run import _gateway_runner_ref
        from gateway.config import Platform

        runner = _gateway_runner_ref()
        if not runner:
            return tool_error("Gateway is not running")

        adapter = runner.adapters.get(Platform.GRIX)
        if not adapter:
            return tool_error("Grix adapter is not connected")

        # Route protocol-level actions directly (no backend proxy)
        if action == "send_message":
            return await _handle_send_message(adapter, params)
        if action == "delete_message":
            return await _handle_delete_message(adapter, params)

        # Default: proxy through agent_invoke
        result = await adapter.agent_invoke(
            action=action,
            params=params,
            timeout_ms=timeout_ms,
        )
        return tool_result(result)
    except Exception as exc:
        logger.warning("grix_invoke '%s' failed: %s", action, exc)
        return tool_error(f"grix_invoke failed: {exc}")


async def _handle_send_message(adapter, params: dict) -> str:
    from tools.registry import tool_error, tool_result

    if not params:
        return tool_error("send_message requires params with 'session_id' and 'content'")

    session_id = str(params.get("session_id") or "").strip()
    content = str(params.get("content") or params.get("message") or "").strip()
    if not session_id:
        return tool_error("send_message requires 'session_id'")
    if not content:
        return tool_error("send_message requires 'content'")

    quoted_message_id = str(params.get("quoted_message_id") or "").strip() or None
    msg_type = params.get("msg_type")

    result = await adapter.send_message(
        chat_id=session_id,
        content=content,
        quoted_message_id=quoted_message_id,
        msg_type=msg_type,
    )
    if result.success:
        return tool_result({"ok": True, "message_id": result.message_id})
    return tool_error(result.error or "send_message failed")


async def _handle_delete_message(adapter, params: dict) -> str:
    from tools.registry import tool_error, tool_result

    if not params:
        return tool_error("delete_message requires params with 'message_id' and 'session_id'")

    message_id = str(params.get("message_id") or "").strip()
    session_id = str(params.get("session_id") or "").strip()
    if not message_id:
        return tool_error("delete_message requires 'message_id'")
    if not session_id:
        return tool_error("delete_message requires 'session_id'")

    result = await adapter.delete_message(
        chat_id=session_id,
        message_id=message_id,
    )
    if result.success:
        return tool_result({"ok": True, "message_id": result.message_id})
    return tool_error(result.error or "delete_message failed")


from tools.registry import registry

registry.register(
    name="grix_invoke",
    toolset="grix",
    schema=GRIX_INVOKE_SCHEMA,
    handler=_grix_invoke_handler,
    check_fn=_check_grix_invoke,
    is_async=True,
    description="Unified Grix API: send/delete messages, query contacts, manage groups, admin agents.",
    emoji="🔗",
)
