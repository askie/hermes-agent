# AIBOT Agent API v1

## 1. 适用范围

本文档定义 Hermes 对外使用的标准协议。

- 协议名固定为 `aibot-agent-api-v1`
- 对外只使用一套包结构：`cmd + seq + payload`
- 对外只保留一套语义命令
- 平台私有传输细节不属于本协议

协议演进和兼容边界见：

- `AIBOT_PROTOCOL_GOVERNANCE.md`

以下字段名不是标准协议字段，不应出现在对外接口中：

- `chatid`
- `req_id`
- `markdown`
- `stream`
- `media_id`
- `upload_id`

## 2. 标准包结构

所有报文统一使用：

```json
{
  "cmd": "command_name",
  "seq": 123,
  "payload": {}
}
```

约束：

- `cmd` 使用小写下划线风格
- `seq` 为请求关联号
- 主动请求使用正整数 `seq`
- 事件推送可使用 `seq = 0`
- 响应必须原样回显请求的 `seq`
- `payload` 必须是对象

## 3. 命令总表

| 命令 | 方向 | 用途 |
| --- | --- | --- |
| `auth` | Client -> Server | 建立连接后的认证请求 |
| `auth_ack` | Server -> Client | 认证结果 |
| `ping` | 双向 | 保活请求 |
| `pong` | 双向 | 保活响应 |
| `event_msg` | Server -> Client | 新消息事件 |
| `event_ack` | Client -> Server | 已收到消息事件 |
| `event_result` | Client -> Server | 消息事件处理完成 |
| `event_stop` | Server -> Client | 停止事件 |
| `event_stop_ack` | Client -> Server | 已收到停止事件 |
| `event_stop_result` | Client -> Server | 停止处理完成 |
| `event_edit` | Server -> Client | 消息编辑事件 |
| `event_revoke` | Server -> Client | 消息撤回事件 |
| `send_msg` | Client -> Server | 发送消息 |
| `send_ack` | Server -> Client | 发送成功 |
| `send_nack` | Server -> Client | 发送失败 |
| `edit_msg` | Client -> Server | 编辑已发送消息 |
| `session_activity_set` | Client -> Server | 设置会话活动状态 |
| `local_action` | Server -> Client | 本地动作请求 |
| `local_action_result` | Client -> Server | 本地动作执行结果 |
| `session_route_bind` | Client -> Server | 绑定会话路由 |
| `session_route_resolve` | Client -> Server | 查询会话路由 |
| `error` | 双向 | 通用错误响应 |

## 4. 连接与认证

### 4.1 `auth`

请求示例：

```json
{
  "cmd": "auth",
  "seq": 1,
  "payload": {
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
      "local_action_v1"
    ],
    "local_actions": [
      "exec_approve",
      "exec_reject"
    ]
  }
}
```

字段要求：

- `agent_id` 必填
- `api_key` 必填
- `protocol_version` 必须为 `aibot-agent-api-v1`
- `contract_version` 当前为 `1`

### 4.2 `auth_ack`

成功示例：

```json
{
  "cmd": "auth_ack",
  "seq": 1,
  "payload": {
    "code": 0,
    "heartbeat_sec": 30,
    "protocol": "aibot-agent-api-v1"
  }
}
```

失败示例：

```json
{
  "cmd": "auth_ack",
  "seq": 1,
  "payload": {
    "code": 10401,
    "msg": "bad key"
  }
}
```

判定规则：

- `payload.code == 0` 表示成功
- `payload.code != 0` 表示失败

## 5. 保活

### 5.1 `ping`

```json
{
  "cmd": "ping",
  "seq": 44,
  "payload": {
    "ts": 1710000000000
  }
}
```

### 5.2 `pong`

```json
{
  "cmd": "pong",
  "seq": 44,
  "payload": {
    "ts": 1710000000001
  }
}
```

规则：

- `pong.seq` 必须等于 `ping.seq`

## 6. 事件模型

### 6.1 `event_msg`

用途：投递一条新消息事件。

必填字段：

- `event_id`
- `session_id`
- `msg_id`

常用字段：

- `event_type`
- `session_type`
- `sender_id`
- `sender_name`
- `content`
- `thread_id`
- `quoted_message_id`
- `attachments`
- `mention_user_ids`
- `biz_card`
- `channel_data`

示例：

```json
{
  "cmd": "event_msg",
  "seq": 0,
  "payload": {
    "event_id": "evt-1",
    "session_id": "g_1001",
    "msg_id": "55",
    "sender_id": "u_8",
    "sender_name": "alice",
    "content": "hello",
    "thread_id": "topic-a"
  }
}
```

处理顺序：

1. 先回 `event_ack`
2. 处理完成后回 `event_result`

### 6.2 `event_ack`

```json
{
  "cmd": "event_ack",
  "seq": 0,
  "payload": {
    "event_id": "evt-1",
    "session_id": "g_1001",
    "msg_id": "55",
    "received_at": 1710000000100
  }
}
```

### 6.3 `event_result`

```json
{
  "cmd": "event_result",
  "seq": 0,
  "payload": {
    "event_id": "evt-1",
    "status": "responded",
    "updated_at": 1710000001100
  }
}
```

`event_result.payload.status` 允许值：

- `responded`
- `failed`

### 6.4 `event_stop`

用途：请求停止某条正在进行的任务或输出。

示例：

```json
{
  "cmd": "event_stop",
  "seq": 0,
  "payload": {
    "event_id": "stop-1",
    "session_id": "g_1001",
    "stop_id": "stop-token-1",
    "reason": "user_cancel"
  }
}
```

### 6.5 `event_stop_ack`

```json
{
  "cmd": "event_stop_ack",
  "seq": 0,
  "payload": {
    "event_id": "stop-1",
    "accepted": true,
    "stop_id": "stop-token-1",
    "updated_at": 1710000000100
  }
}
```

### 6.6 `event_stop_result`

```json
{
  "cmd": "event_stop_result",
  "seq": 0,
  "payload": {
    "event_id": "stop-1",
    "status": "stopped",
    "stop_id": "stop-token-1",
    "updated_at": 1710000000200
  }
}
```

`event_stop_result.payload.status` 允许值：

- `stopped`
- `already_finished`
- `failed`

### 6.7 `event_edit`

用途：投递消息编辑事件。

最小字段集合：

- `session_id`
- `msg_id`
- `content`

### 6.8 `event_revoke`

用途：投递消息撤回事件。

最小字段集合：

- `event_id`
- `session_id`
- `msg_id`

## 7. 发送模型

### 7.1 `send_msg`

用途：发送一条消息。

请求示例：

```json
{
  "cmd": "send_msg",
  "seq": 16,
  "payload": {
    "session_id": "g_1001",
    "msg_type": 1,
    "content": "hello",
    "quoted_message_id": "54",
    "thread_id": "topic-a",
    "event_id": "evt-1"
  }
}
```

字段说明：

- `session_id` 必填
- `msg_type` 当前文本消息使用 `1`
- `content` 必填
- `quoted_message_id` 可选
- `thread_id` 可选
- `event_id` 可选

### 7.2 `send_ack`

成功响应示例：

```json
{
  "cmd": "send_ack",
  "seq": 16,
  "payload": {
    "session_id": "g_1001",
    "msg_id": "56"
  }
}
```

规则：

- `payload.session_id` 必填
- `payload.msg_id` 只在下游平台提供真实消息编号时返回
- 如果下游平台没有真实消息编号，`msg_id` 应省略
- 不允许使用请求关联号、链路追踪号或临时占位值冒充 `msg_id`

### 7.3 `send_nack`

失败响应示例：

```json
{
  "cmd": "send_nack",
  "seq": 16,
  "payload": {
    "code": 10500,
    "msg": "send failed"
  }
}
```

## 8. 编辑模型

### 8.1 `edit_msg`

请求示例：

```json
{
  "cmd": "edit_msg",
  "seq": 17,
  "payload": {
    "session_id": "g_1001",
    "msg_id": "56",
    "content": "hello again"
  }
}
```

成功响应仍使用 `send_ack`。

## 9. 本地动作

### 9.1 `local_action`

用途：请求宿主侧执行本地动作。

请求示例：

```json
{
  "cmd": "local_action",
  "seq": 0,
  "payload": {
    "action_id": "act-1",
    "action_type": "exec_approve",
    "params": {
      "approval_id": "approval-1"
    }
  }
}
```

当前标准动作：

- `exec_approve`
- `exec_reject`

### 9.2 `local_action_result`

结果示例：

```json
{
  "cmd": "local_action_result",
  "seq": 0,
  "payload": {
    "action_id": "act-1",
    "status": "ok",
    "result": "approved"
  }
}
```

失败示例：

```json
{
  "cmd": "local_action_result",
  "seq": 0,
  "payload": {
    "action_id": "act-1",
    "status": "failed",
    "error_code": "missing_approval_id",
    "error_msg": "approval_id is required"
  }
}
```

`local_action_result.payload.status` 允许值：

- `ok`
- `failed`
- `unsupported`

当前标准错误码：

- `invalid_local_action`
- `unsupported_local_action`
- `missing_approval_id`
- `unsupported_decision`
- `approval_not_found`
- `stop_handler_failed`

## 10. 会话路由

### 10.1 `session_route_bind`

用途：把内部会话键绑定到标准 `session_id`。

请求示例：

```json
{
  "cmd": "session_route_bind",
  "seq": 18,
  "payload": {
    "channel": "grix",
    "account_id": "main",
    "route_session_key": "agent:main:grix:group:g_1001:topic-a",
    "session_id": "g_1001"
  }
}
```

成功响应使用 `send_ack`。

### 10.2 `session_route_resolve`

用途：根据内部会话键查询绑定结果。

请求示例：

```json
{
  "cmd": "session_route_resolve",
  "seq": 19,
  "payload": {
    "channel": "grix",
    "account_id": "main",
    "route_session_key": "agent:main:grix:group:g_1001:topic-a"
  }
}
```

响应示例：

```json
{
  "cmd": "send_ack",
  "seq": 19,
  "payload": {
    "channel": "grix",
    "account_id": "main",
    "route_session_key": "agent:main:grix:group:g_1001:topic-a",
    "session_id": "g_1001"
  }
}
```

## 11. 会话活动状态

### `session_activity_set`

用途：设置会话活动状态，例如“正在输入”。

请求示例：

```json
{
  "cmd": "session_activity_set",
  "seq": 20,
  "payload": {
    "session_id": "g_1001",
    "kind": "typing",
    "active": true,
    "ttl_ms": 8000,
    "ref_msg_id": "55",
    "ref_event_id": "evt-1"
  }
}
```

## 12. 通用错误

### `error`

当请求无法按预期处理时，可返回通用错误：

```json
{
  "cmd": "error",
  "seq": 16,
  "payload": {
    "code": 10500,
    "msg": "internal error"
  }
}
```

## 13. 标准字段约束

推荐统一字段名：

- `agent_id`
- `session_id`
- `event_id`
- `msg_id`
- `thread_id`
- `route_session_key`
- `content`
- `quoted_message_id`
- `attachments`
- `mention_user_ids`
- `status`
- `code`
- `msg`
- `error_code`
- `error_msg`

字段规则：

- `msg_id` 表示真实消息编号，不表示链路关联号
- `event_id` 表示事件编号，不表示消息编号
- `approval_id` 是本地动作审批编号，不使用别名

## 14. 能力声明

当前标准能力：

- `session_route`
- `thread_v1`
- `inbound_media_v1`
- `local_action_v1`

当前认证阶段要求至少声明：

- `local_action_v1`

## 15. 一致性要求

- 同一件事只保留一个标准命令名
- 同一字段只保留一个标准字段名
- 同一请求只能有一个标准响应模型
- 平台原生报文不与标准协议并列对外发布
