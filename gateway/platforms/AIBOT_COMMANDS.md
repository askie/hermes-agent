# AIBOT / 插件侧命令总表

这份清单只整理 Hermes 当前代码里真实在用、真实在处理的命令，方便插件侧和 aibot 侧对齐。

源码依据：
- `gateway/platforms/grix_protocol.py`
- `gateway/platforms/grix_transport.py`
- `gateway/platforms/grix.py`
- `gateway/platforms/wecom.py`

## 1. Grix / `aibot-agent-api-v1`

### 1.1 报文外层

所有包统一使用下面的外层结构：

```json
{
  "cmd": "command_name",
  "seq": 123,
  "payload": {}
}
```

说明：
- `seq` 用来做请求-响应关联。
- 连接认证时，Hermes 发 `auth`，期望回 `auth_ack`。
- 普通请求类命令通常期望回 `send_ack`，失败时可能回 `send_nack` 或 `error`。
- 服务端主动推送事件时，通常 `seq = 0`。

### 1.2 Hermes 发出的命令

| 命令 | 方向 | 何时发送 | 关键字段 | 期望返回 | 当前处理 |
| --- | --- | --- | --- | --- | --- |
| `auth` | Hermes -> aibot | 建连后第一条 | `agent_id`, `api_key`, `client`, `client_type`, `client_version`, `protocol_version`, `contract_version`, `host_type`, `host_version?`, `capabilities`, `local_actions` | `auth_ack` | `payload.code == 0` 视为成功；否则认证失败 |
| `pong` | Hermes -> aibot | 收到服务端 `ping` 后自动回 | `ts` | 无 | 直接回包，不等业务返回 |
| `send_msg` | Hermes -> aibot | 发送文本消息 | `session_id`, `msg_type=1`, `content`, `quoted_message_id?`, `thread_id?`, `event_id?` | `send_ack` | 成功时取 `payload.msg_id` 或 `payload.client_msg_id` 作为消息 ID |
| `edit_msg` | Hermes -> aibot | 编辑已发消息 | `session_id`, `msg_id`, `content` | `send_ack` | 失败按 `send_nack/error` 处理 |
| `session_activity_set` | Hermes -> aibot | 发送“正在输入”等状态 | `session_id`, `kind`, `active`, `ttl_ms?`, `ref_msg_id?`, `ref_event_id?` | 无强依赖 | 当前用于 `kind=\"composing\"` |
| `local_action_result` | Hermes -> aibot | 回本地动作处理结果 | `action_id`, `status`, `result?`, `error_code?`, `error_msg?` | 无强依赖 | 用于 `exec_approve` / `exec_reject` 结果回传 |
| `event_ack` | Hermes -> aibot | 确认已收到事件 | `event_id`, `received_at`, `session_id?`, `msg_id?` | 无强依赖 | 当前会对 `event_msg`、`event_revoke` 做 ack |
| `event_result` | Hermes -> aibot | 告知一条消息事件处理完成 | `event_id`, `status`, `updated_at`, `code?`, `msg?` | 无强依赖 | 当前状态值见下文 |
| `event_stop_ack` | Hermes -> aibot | 确认收到停止事件 | `event_id`, `accepted`, `updated_at`, `stop_id?` | 无强依赖 | 当前固定发 `accepted=true` |
| `event_stop_result` | Hermes -> aibot | 告知停止事件处理结果 | `event_id`, `status`, `updated_at`, `stop_id?`, `code?`, `msg?` | 无强依赖 | 当前状态值见下文 |
| `session_route_bind` | Hermes -> aibot | 把 Hermes 会话 key 绑定到远端会话 | `channel`, `account_id`, `route_session_key`, `session_id` | `send_ack` | 在收到 `event_msg` 后尝试绑定 |
| `session_route_resolve` | Hermes -> aibot | 把 Hermes 路由 key 解析回远端会话 | `channel`, `account_id`, `route_session_key` | `send_ack` | 返回里必须带 `session_id`，否则当失败处理 |

### 1.3 aibot 发给 Hermes 的命令

| 命令 | 方向 | 用途 | Hermes 当前读取的关键字段 | Hermes 当前响应 |
| --- | --- | --- | --- | --- |
| `ping` | aibot -> Hermes | 心跳 | `seq` | 自动回 `pong`，沿用同一个 `seq` |
| `event_msg` | aibot -> Hermes | 新消息事件 | 必填：`event_id`, `session_id`, `msg_id`。常用：`event_type`, `session_type`, `sender_id`, `sender_name`, `content`, `thread_id`, `quoted_message_id`, `attachments`, `mention_user_ids`, `biz_card`, `channel_data` | 先发 `event_ack`；消息处理结束后发 `event_result` |
| `event_stop` | aibot -> Hermes | 停止当前处理 | 必填：`event_id`, `session_id`。可选：`stop_id`, `reason`, `trigger_msg_id`, `stream_msg_id` | 先发 `event_stop_ack`，再发 `event_stop_result` |
| `event_edit` | aibot -> Hermes | 编辑一条尚在处理中/已记录的消息 | 必填：`session_id`, `msg_id`。常用：`content`, `quoted_message_id`, `thread_id`, `sender_id`, `sender_type`, `msg_type` | 不单独回 ack；只更新 Hermes 内存里的待处理事件 |
| `event_revoke` | aibot -> Hermes | 撤回消息 | 必填：`event_id`, `session_id`, `msg_id`。可选：`sender_id`, `is_revoked`, `system_event.text`, `system_event.context_key` | 发 `event_ack`，并清理本地挂起消息状态 |
| `local_action` | aibot -> Hermes | 触发本地动作 | 必填：`action_id`, `action_type`, `params`。当前只支持 `exec_approve`、`exec_reject` | 回 `local_action_result` |

### 1.4 当前实际使用的状态值

`local_action_result.status`
- `ok`
- `failed`
- `unsupported`

`event_result.status`
- `responded`
- `failed`

`event_stop_result.status`
- `stopped`
- `already_finished`
- `failed`

### 1.5 `local_action` 当前支持范围

当前 Hermes 只支持下面两种动作：

| `action_type` | 必要参数 | 当前接受的决策值 | 返回说明 |
| --- | --- | --- | --- |
| `exec_approve` | `approval_id`，也兼容 `approval_command_id` / `exec_context_id` | `allow-once`, `allow-always`, `deny` | 成功时 `status=ok`，`result` 回原始决策值 |
| `exec_reject` | `approval_id`，也兼容 `approval_command_id` / `exec_context_id` | 不需要额外 `decision` | 成功时 `status=ok`，`result=deny` |

当前已落地的失败码：
- `invalid_local_action`
- `unsupported_local_action`
- `missing_approval_id`
- `unsupported_decision`
- `approval_not_found`

## 2. WeCom `aibot_*` WebSocket 命令

### 2.1 报文外层

企微这条链路当前用的是下面这种结构：

```json
{
  "cmd": "aibot_xxx",
  "headers": {
    "req_id": "request-id"
  },
  "body": {}
}
```

说明：
- `req_id` 是请求-响应关联键。
- 当前代码把成功/失败判断放在返回里的顶层字段：`errcode` / `errmsg`。
- 除回调类命令外，Hermes 都是按 `req_id` 等待对应响应。

### 2.2 Hermes 发出的命令

| 命令 | 方向 | 何时发送 | `body` 关键字段 | 期望返回 | 当前处理 |
| --- | --- | --- | --- | --- | --- |
| `aibot_subscribe` | Hermes -> aibot | 建连认证 | `bot_id`, `secret` | 同 `req_id` 的响应，顶层 `errcode=0` | 非 0 直接视为认证失败 |
| `ping` | Hermes -> aibot | 应用层心跳 | 空对象 `{}` | 当前不依赖业务返回 | 只是保活；收不收响应都不影响主流程 |
| `aibot_send_msg` | Hermes -> aibot | 主动发消息 | 文本时：`chatid`, `msgtype=\"markdown\"`, `markdown.content`。媒体时：`chatid`, `msgtype`, `<type>.media_id` | 同 `req_id` 的响应，`errcode=0` | 失败统一按 `errcode/errmsg` 处理 |
| `aibot_respond_msg` | Hermes -> aibot | 对回调消息做被动回复 | 文本流回复：`msgtype=\"stream\"`, `stream.id`, `stream.finish`, `stream.content`。媒体回复：`msgtype`, `<type>.media_id` | 复用入站回调的 `req_id`，响应里 `errcode=0` | 只有拿得到原始回调 `req_id` 才会走这条 |
| `aibot_upload_media_init` | Hermes -> aibot | 上传媒体初始化 | `type`, `filename`, `total_size`, `total_chunks`, `md5` | `errcode=0`，且 `body.upload_id` 必须存在 | 缺 `upload_id` 视为失败 |
| `aibot_upload_media_chunk` | Hermes -> aibot | 上传媒体分片 | `upload_id`, `chunk_index`, `base64_data` | `errcode=0` | 当前 `chunk_index` 用 0 开始 |
| `aibot_upload_media_finish` | Hermes -> aibot | 上传媒体收尾 | `upload_id` | `errcode=0`，且 `body.media_id` 必须存在 | 返回里还会读取 `body.type`, `body.created_at` |

### 2.3 aibot 发给 Hermes 的命令

| 命令 | 方向 | 用途 | Hermes 当前读取的关键字段 | Hermes 当前处理 |
| --- | --- | --- | --- | --- |
| `aibot_msg_callback` | aibot -> Hermes | 新版消息回调 | 常用：`body.msgid`, `body.chatid`, `body.chattype`, `body.from.userid`, `body.msgtype`, `body.text.content`, `body.voice.content`, `body.quote`, `body.image`, `body.file`, `body.mixed.msg_item` | 正常收消息，且会把这次回调的 `req_id` 记下来，供后续 `aibot_respond_msg` 使用 |
| `aibot_callback` | aibot -> Hermes | 旧版消息回调 | 同上 | 和 `aibot_msg_callback` 一样处理 |
| `aibot_event_callback` | aibot -> Hermes | 其他事件回调 | 当前未消费字段 | 当前直接忽略 |
| `ping` | aibot -> Hermes | 心跳/保活 | 无 | 当前直接忽略 |

### 2.4 企微回调里当前实际读取的消息内容

文本相关：
- `body.text.content`
- `body.voice.content`
- `body.quote.text.content`
- `body.quote.voice.content`
- `body.mixed.msg_item[*].text.content`

媒体相关：
- `body.image`
- `body.file`
- `body.quote.image`
- `body.quote.file`
- `body.mixed.msg_item[*].image`

媒体字段里当前会处理这些键：
- `url`
- `base64`
- `aeskey`
- `filename` / `name`

### 2.5 当前返回判定规则

企微链路当前统一按下面的逻辑判定：

- 成功：`errcode == 0` 或 `errcode` 不存在
- 失败：`errcode != 0`
- 失败消息：读取顶层 `errmsg`

代码里的错误文本格式当前是：

```text
WeCom errcode <errcode>: <errmsg>
```

### 2.6 当前回复模式说明

Hermes 当前有两种发消息路径：

1. 被动回复模式  
   条件：当前回复的是一条已经收到过回调的消息，且手里有那条消息对应的 `req_id`。  
   走法：发送 `aibot_respond_msg`。

2. 主动发送模式  
   条件：没有可用的回调 `req_id`。  
   走法：发送 `aibot_send_msg`。

这点需要插件侧和 aibot 侧保持一致，否则会出现“明明是在回复消息，却被当成新消息主动发送”的错位。

## 3. 对齐时最容易出问题的点

- Grix 链路里，`event_msg` / `event_stop` / `local_action` 都不是普通请求响应，不能简单按 `send_ack` 理解。
- Grix 的 `local_action` 目前只支持执行审批，不支持任意动作。
- Grix 的 `session_route_resolve` 返回里，`session_id` 不能为空。
- 企微链路里，`aibot_callback` 旧命令现在仍然兼容，不能直接删。
- 企微媒体上传分片目前按 0 开始编号。
- 企微回复模式必须复用原回调的 `req_id`，不是新生成一个。
