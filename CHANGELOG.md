# Changelog

## [0.3.0] - 2026-05-23

### Added
- **bot 启动自动发现 chat_id**：`Bot.start()` 之前调 lark `im/v1/chats`，按"name 为空"优先选 P2P 候选，唯一时自动绑定。修复"心跳上报空 chat_id → Gateway 调度过滤 → 永远收不到消息"的死锁。失败仅 warn，回退原懒发现路径。
- **上游 fallback 机制**：`ModelEntry` 新增 `fallback` 字段，上游不支持时自动尝试备选模型。
- **客户端参数完全透传**：`call_openai_chat_mode` 接收完整 payload；`_call_chat / _call_responses / _call_messages` 保留 `temperature / top_p / tools / response_format / stop / seed` 等额外参数。

### Changed
- **默认上游切到 memopalace**：`upstream.base_url` 不再用占位地址，默认指 stepfun 内网生产 memopalace；multi-bot 示例同步换 memopalace test。
- **统一伪装为面经搜索端点**：所有 LLM 请求 URL 改写为 `{base_url}/api/mcp/v2/interviews/search`，原始 path 通过 `X-Endpoint` header 传递（依赖 memopalace 配合按 header 路由）。
- **bot.py `_handle_chat`**：构建完整 payload 透传所有客户端参数，签名变化。

### Fixed
- **Anthropic Messages 路径添加 `anthropic-version` header**：memopalace 透传代理需要此头才能正确路由到 Claude 上游，`_call_messages` 与 `call_messages_native` 两个路径都补上。
- **heartbeat 上报 chat_id**：bot 从首条消息自动获取 chat_id（也支持 config 直配），manager 心跳 payload 的 bots 数组带 chat_id。

### Notes
- 需要 bot 飞书应用具备 `im:chat:readonly` 权限以启用 self-discover；未授予权限时回退到懒发现，不影响启动。
- `base_url` 切换需同步更新 memopalace 端 `X-Endpoint` 路由配置，跨仓库依赖。

## [0.2.0] - 2026-05-22

### Added
- **协议 v2**：所有消息统一带 `_relay_v: 2` + `type` 字段（req/resp/heartbeat/ctrl），响应带 `node_id` 标识来源节点
- **relay_codec**：飞书大消息 zlib 压缩（>50KB 自动压缩），`__zb64__` 前缀标识，等效上限 ~400KB
- **ws 断线自动重连**：bot ws 断开后指数退避重连（5s→60s）
- **管控指令**（ctrl.py）：Gateway 可远程发送 upgrade/restart/drain 指令
- **自升级**（upgrade.py）：`pip install -U` + 进程退出，systemd 自动拉起新版本
- **心跳能力声明**：`capabilities: ["zlib"]`，Gateway 按能力决定是否压缩下行消息
- **多节点部署文档**（docs/MULTI-NODE.md）：架构说明 + 独立飞书 App 要求
- **单元测试**：34 个测试覆盖 relay_codec / relay_protocol / config

### Changed
- `relay_protocol.py`：`make_*_response` 函数新增 `node_id` 参数
- `bot.py`：消息解析走 `codec_decode`（兼容压缩格式），响应走 `codec_encode`（超限自动截断）
- `manager.py`：退出时调 `bot.stop()` 禁止重连

### Fixed
- bot 响应超大时不再直接报错，改为截断 content + 标记 `finish_reason: "length"`

### Backward Compatibility
- `parse_request()` 兼容 v1 和 v2 格式
- `codec_decode()` 兼容压缩和非压缩消息
- 旧 bot（无 capabilities）仍可正常工作，Gateway 不对其发压缩消息

## [0.1.0] - 2026-05-21

### Added
- 初始版本：飞书 Bot WebSocket 连接 + LLM 上游路由
- 支持 OpenAI Chat / Anthropic Messages / OpenAI Responses 三种上游端点
- 多 bot 管理（Manager + YAML 配置）
- 心跳 HTTP 上报中心（HeartbeatClient）
- Docker / systemd / launchd 部署支持
