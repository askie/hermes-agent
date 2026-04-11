"""Canonical definitions for the standard AIBOT protocol.

This module is the single source of truth for public AIBOT command names,
status values, error codes, and capability declarations. Platform adapters may
bridge legacy transports internally, but they should converge on these values
when implementing the standard contract.
"""

from __future__ import annotations

AIBOT_PROTOCOL_VERSION = "aibot-agent-api-v1"
AIBOT_DEFAULT_CONTRACT_VERSION = 1

# Packet commands
CMD_AUTH = "auth"
CMD_AUTH_ACK = "auth_ack"
CMD_PING = "ping"
CMD_PONG = "pong"
CMD_SEND_MSG = "send_msg"
CMD_SEND_ACK = "send_ack"
CMD_SEND_NACK = "send_nack"
CMD_ERROR = "error"
CMD_EDIT_MSG = "edit_msg"
CMD_SESSION_ACTIVITY_SET = "session_activity_set"
CMD_LOCAL_ACTION = "local_action"
CMD_LOCAL_ACTION_RESULT = "local_action_result"
CMD_EVENT_MSG = "event_msg"
CMD_EVENT_ACK = "event_ack"
CMD_EVENT_RESULT = "event_result"
CMD_EVENT_STOP = "event_stop"
CMD_EVENT_STOP_ACK = "event_stop_ack"
CMD_EVENT_STOP_RESULT = "event_stop_result"
CMD_EVENT_EDIT = "event_edit"
CMD_EVENT_REVOKE = "event_revoke"
CMD_SESSION_ROUTE_BIND = "session_route_bind"
CMD_SESSION_ROUTE_RESOLVE = "session_route_resolve"

# Capabilities
CAP_SESSION_ROUTE = "session_route"
CAP_THREAD_V1 = "thread_v1"
CAP_INBOUND_MEDIA_V1 = "inbound_media_v1"
CAP_LOCAL_ACTION_V1 = "local_action_v1"

REQUIRED_AUTH_CAPABILITIES = (CAP_LOCAL_ACTION_V1,)
STABLE_AUTH_CAPABILITIES = (
    CAP_SESSION_ROUTE,
    CAP_THREAD_V1,
    CAP_INBOUND_MEDIA_V1,
    CAP_LOCAL_ACTION_V1,
)

# Local actions
LOCAL_ACTION_EXEC_APPROVE = "exec_approve"
LOCAL_ACTION_EXEC_REJECT = "exec_reject"
STABLE_LOCAL_ACTIONS = (
    LOCAL_ACTION_EXEC_APPROVE,
    LOCAL_ACTION_EXEC_REJECT,
)

# Status values
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNSUPPORTED = "unsupported"
STATUS_RESPONDED = "responded"
STATUS_STOPPED = "stopped"
STATUS_ALREADY_FINISHED = "already_finished"

# Error codes
ERR_INVALID_LOCAL_ACTION = "invalid_local_action"
ERR_UNSUPPORTED_LOCAL_ACTION = "unsupported_local_action"
ERR_MISSING_APPROVAL_ID = "missing_approval_id"
ERR_UNSUPPORTED_DECISION = "unsupported_decision"
ERR_APPROVAL_NOT_FOUND = "approval_not_found"
ERR_STOP_HANDLER_FAILED = "stop_handler_failed"

