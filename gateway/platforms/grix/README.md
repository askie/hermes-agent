# Grix 接入说明

这个子目录只放 Grix 接入说明，不放 Python 模块实现。当前 Grix 相关代码仍在同级文件里：

- `../grix.py`
- `../grix_protocol.py`
- `../grix_transport.py`

这样做是为了补充文档，同时不改现有导入路径和模块结构。

## 去哪里拿 Grix 参数

Hermes 当前需要的 Grix 参数，来自 Grix 控制台里的 Agent API 信息。

1. 打开 [https://grix.dhf.pub](https://grix.dhf.pub) 并登录。
2. 进入 Agent 页面。
   站点前端公开资源里暴露了 Agent 列表路由 `/home/agents`，对应的就是 Agent 管理入口。
3. 新建一个给 Hermes 使用的 Agent，或打开已经存在的 Agent。
4. 在 Agent 的 API 信息区域复制下面 3 个值：
   - `endpoint`
   - `agent_id`
   - `api_key`
5. 如果页面上只显示掩码后的 key，直接在同一页重新轮换或重置 `api_key`，然后把新值配到 Hermes。

如果你还要限制谁能给 Hermes 发消息，额外需要确认 Grix 事件里的 `sender_id`。
如果你要给定默认会话，还需要确认目标会话的 `session_id`。

## Hermes 侧必须配置的参数

下面 3 个变量必须一起配置，少一个都不行：

| 变量 | 含义 | 从哪里拿 |
| --- | --- | --- |
| `GRIX_ENDPOINT` | Grix websocket 地址 | Agent API 信息里的 `endpoint` |
| `GRIX_AGENT_ID` | Hermes 登录 Grix 时使用的 agent 标识 | Agent API 信息里的 `agent_id` |
| `GRIX_API_KEY` | Hermes 登录 Grix 时使用的密钥 | Agent API 信息里的 `api_key` |

## 常用可选参数

| 变量 | 是否常用 | 说明 |
| --- | --- | --- |
| `GRIX_ALLOWED_USERS` | 常用 | 允许给 Hermes 发消息的 Grix 用户 ID，多个值用逗号分隔 |
| `GRIX_ALLOW_ALL_USERS` | 谨慎使用 | 设为 `true` 后，放开 Grix 用户限制 |
| `GRIX_HOME_CHANNEL` | 常用 | 默认会话 `session_id`，用于 cron 或 `send_message('grix')` 这类没有显式目标的发送 |
| `GRIX_HOME_CHANNEL_NAME` | 可选 | 默认会话的显示名，不配时默认为 `Home` |
| `GRIX_ACCOUNT_ID` | 可选 | 账号域，默认值是 `main` |
| `GRIX_CLIENT` | 可选 | 上报给 Grix 的客户端名，默认值是 `hermes-agent` |
| `GRIX_CLIENT_TYPE` | 可选 | 上报给 Grix 的客户端类型，默认值是 `hermes` |
| `GRIX_CAPABILITIES` | 一般不用配 | 覆盖能力声明；不配时 Hermes 会使用内置稳定能力集合：`session_route,thread_v1,inbound_media_v1,local_action_v1` |
| `GRIX_CONNECT_TIMEOUT_MS` | 可选 | websocket 连接超时，默认 `10000` |
| `GRIX_REQUEST_TIMEOUT_MS` | 可选 | 请求超时，默认 `20000` |

`GRIX_CAPABILITIES` 如果确实要手工覆盖，建议至少保证 `local_action_v1` 可用。大多数场景保持默认即可。

## 推荐配置方式

把参数写进 `~/.hermes/.env`：

```dotenv
GRIX_ENDPOINT=wss://your-grix-gateway.example/ws
GRIX_AGENT_ID=9001
GRIX_API_KEY=replace-with-real-key

# 建议至少配置一种访问控制
GRIX_ALLOWED_USERS=user_1001,user_1002
# 或者仅在你明确接受开放访问时使用
# GRIX_ALLOW_ALL_USERS=true

# 可选：默认会话
GRIX_HOME_CHANNEL=g_1001

# 可选：以下通常保持默认即可
# GRIX_ACCOUNT_ID=main
# GRIX_CLIENT=hermes-agent
# GRIX_CLIENT_TYPE=hermes
# GRIX_CONNECT_TIMEOUT_MS=10000
# GRIX_REQUEST_TIMEOUT_MS=20000
```

如果你使用的是 Hermes 的交互式 gateway 配置界面，也要填写同样这几个核心字段：

- `GRIX_ENDPOINT`
- `GRIX_AGENT_ID`
- `GRIX_API_KEY`
- `GRIX_ALLOWED_USERS`
- `GRIX_HOME_CHANNEL`

## 配完后怎么自查

1. `GRIX_ENDPOINT` 必须是 websocket 地址，通常以 `ws://` 或 `wss://` 开头。
2. `GRIX_ENDPOINT`、`GRIX_AGENT_ID`、`GRIX_API_KEY` 必须同时存在。
3. 如果启用了 `GRIX_ALLOWED_USERS`，里面填的必须是 Grix 事件真实出现过的 `sender_id`。
4. 如果配置了 `GRIX_HOME_CHANNEL`，里面填的必须是目标会话真实的 `session_id`。
5. 改完 `~/.hermes/.env` 后，重启 Hermes gateway 让新参数生效。
