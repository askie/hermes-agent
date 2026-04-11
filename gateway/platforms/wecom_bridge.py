"""Compatibility bridge for the WeCom websocket transport.

WeCom still speaks its platform-native ``aibot_*`` frames. Those details stay
inside this bridge module so the rest of the gateway can converge on the
standard AIBOT contract without carrying transport-specific baggage.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from gateway.platforms.aibot_contract import CMD_PING

WECOM_CMD_SUBSCRIBE = "aibot_subscribe"
WECOM_CMD_CALLBACK = "aibot_msg_callback"
WECOM_CMD_LEGACY_CALLBACK = "aibot_callback"
WECOM_CMD_EVENT_CALLBACK = "aibot_event_callback"
WECOM_CMD_SEND = "aibot_send_msg"
WECOM_CMD_RESPONSE = "aibot_respond_msg"
WECOM_CMD_PING = CMD_PING
WECOM_CMD_UPLOAD_MEDIA_INIT = "aibot_upload_media_init"
WECOM_CMD_UPLOAD_MEDIA_CHUNK = "aibot_upload_media_chunk"
WECOM_CMD_UPLOAD_MEDIA_FINISH = "aibot_upload_media_finish"

WECOM_CALLBACK_COMMANDS = {WECOM_CMD_CALLBACK, WECOM_CMD_LEGACY_CALLBACK}
WECOM_NON_RESPONSE_COMMANDS = WECOM_CALLBACK_COMMANDS | {WECOM_CMD_EVENT_CALLBACK}


def build_wecom_frame(cmd: str, req_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build a WeCom transport frame."""
    return {
        "cmd": cmd,
        "headers": {"req_id": req_id},
        "body": body,
    }


def extract_wecom_req_id(payload: Dict[str, Any]) -> str:
    """Read the request correlation id from a WeCom frame."""
    headers = payload.get("headers")
    if isinstance(headers, dict):
        return str(headers.get("req_id") or "")
    return ""


def wecom_response_error(response: Dict[str, Any]) -> Optional[str]:
    """Return a normalized error string when WeCom reports failure."""
    errcode = response.get("errcode", 0)
    if errcode in (0, None):
        return None
    errmsg = str(response.get("errmsg") or "unknown error")
    return f"WeCom errcode {errcode}: {errmsg}"


def ensure_wecom_success(response: Dict[str, Any], operation: str) -> None:
    """Raise when a WeCom response carries a non-zero errcode."""
    error = wecom_response_error(response)
    if error:
        raise RuntimeError(f"{operation} failed: {error}")
