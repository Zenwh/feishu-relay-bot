# Changelog

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
