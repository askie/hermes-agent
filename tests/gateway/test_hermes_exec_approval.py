from gateway.platforms.hermes_exec_approval import build_exec_approval_message


def test_build_exec_approval_message_preserves_raw_payload_and_builds_card():
    message = build_exec_approval_message(
        approval_id="req_123",
        command="rm -rf /tmp/demo",
        description="dangerous deletion",
        raw_approval_data={
            "approval_id": "req_123",
            "pattern_key": "dangerous deletion",
            "pattern_keys": ["dangerous deletion", "filesystem mutation"],
        },
    )

    assert "/approve req_123 allow-once" in message.content
    assert message.biz_card == {
        "version": 1,
        "type": "exec_approval",
        "payload": {
            "approval_id": "req_123",
            "approval_slug": "req_123",
            "approval_command_id": "req_123",
            "command": "rm -rf /tmp/demo",
            "host": "hermes",
            "allowed_decisions": ["allow-once", "allow-always", "deny"],
            "decision_commands": {
                "allow-once": "/approve req_123 allow-once",
                "allow-always": "/approve req_123 allow-always",
                "deny": "/approve req_123 deny",
            },
            "expires_in_seconds": 300,
            "warning_text": "dangerous deletion",
        },
    }
    assert message.channel_data == {
        "hermes": {
            "execApprovalPending": {
                "approval_id": "req_123",
                "pattern_key": "dangerous deletion",
                "pattern_keys": ["dangerous deletion", "filesystem mutation"],
                "command": "rm -rf /tmp/demo",
                "description": "dangerous deletion",
                "host": "hermes",
                "expires_in_seconds": 300,
                "allowed_decisions": ["allow-once", "allow-always", "deny"],
                "decision_commands": {
                    "allow-once": "/approve req_123 allow-once",
                    "allow-always": "/approve req_123 allow-always",
                    "deny": "/approve req_123 deny",
                },
            }
        }
    }
