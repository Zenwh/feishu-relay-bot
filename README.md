# feishu-relay-bot

> 飞书 Bot 隧道 — 把外网的 OpenAI / Anthropic 客户端，桥接到内网的 LLM 网关。

📚 **[5 分钟快速部署](docs/QUICKSTART.md)** · **[完整部署手册](docs/DEPLOY.md)** · **[xpage 接口契约](xpage_mock/README.md)**

## 这是什么

一个 Python 服务，用**飞书 IM 消息**当 NAT 穿透通道：

```
外网                     飞书公网                 内网
─────────                ─────────              ─────────
client (OpenAI SDK)
   │
   ▼
relay HTTP server  ──→   飞书消息   ──websocket──→  feishu-relay-bot
                                                       │
                                                       ▼
                                                  xpage / MP / 自建 LLM 网关
```

**适用场景**：
- 公司内网有 LLM 网关（如自建模型代理），但不想/不能开公网
- 想给团队/客户提供 OpenAI 兼容 API，但又不想做正经云部署
- 一个或多个飞书 bot 接到同一个 LLM 后端

**不适用**：
- 已经有公网 LLM 网关（直接对外即可，不需要这套）
- 高 QPS / SLA 要求高的生产服务（飞书 IM 不适合做高频通道）

## 特性

- ✅ 支持 OpenAI Chat Completions / Responses + Anthropic Messages 三种协议
- ✅ 一进程多 bot（同一个 Python 进程接多个飞书 bot 同时跑）
- ✅ Bot 端配置 + 模型白名单 + 端点路由可配
- ✅ 三种部署形式：PyPI / Docker / 直接 git clone
- ✅ 配套 [xpage mock](xpage_mock/README.md)：内网 LLM 网关接口契约 + 参考实现

## 快速开始

### 方式 1: PyPI

```bash
pip install feishu-relay-bot

# 单 bot 快速启动（不写配置文件）
feishu-relay-bot run \
  --app-id cli_xxx \
  --app-secret xxx \
  --upstream-url http://xpage:8800 \
  --upstream-key sk-xxx

# 或者用配置文件
feishu-relay-bot run --config config.yaml
```

### 方式 2: Docker

```bash
docker run -d --name feishu-relay-bot \
  -v $PWD/config.yaml:/etc/feishu-relay-bot/config.yaml:ro \
  -e UPSTREAM_API_KEY=sk-xxx \
  -e FEISHU_APP_SECRET=xxx \
  feishu-relay-bot:0.1.0
```

或者用 docker compose（见 `examples/docker-compose.yaml`）。

### 方式 3: git clone + venv

```bash
git clone https://github.com/Zenwh/feishu-relay-bot
cd feishu-relay-bot
python3 -m venv .venv && .venv/bin/pip install -e .

# 跑
.venv/bin/feishu-relay-bot run --config examples/config.example.yaml
```

## 配置

最小配置：

```yaml
# config.yaml
upstream:
  base_url: http://xpage:8800
  api_key: ${UPSTREAM_API_KEY}

bots:
  - name: my-bot
    app_id: cli_xxx
    app_secret: ${FEISHU_APP_SECRET}
```

完整配置见：
- [examples/config.example.yaml](examples/config.example.yaml)
- [examples/config.multi-bot.yaml](examples/config.multi-bot.yaml)

### 环境变量

YAML 里的 `${VAR_NAME:-default}` 占位会从环境读取。常用快捷 env：

| 变量 | 说明 |
|------|------|
| `FEISHU_RELAY_BOT_CONFIG` | config.yaml 路径 |
| `UPSTREAM_BASE_URL` | 上游 base URL（会覆盖 yaml）|
| `UPSTREAM_API_KEY` | 上游 API key（会覆盖 yaml）|
| `LOG_LEVEL` | DEBUG/INFO/WARNING |

## 命令

```bash
feishu-relay-bot run [-c CONFIG]      # 启动
feishu-relay-bot check [-c CONFIG]    # 验证配置
feishu-relay-bot --version            # 版本
```

## 上游：xpage 接口契约

bot 不直连 LLM 厂商，而是调一层 **xpage**（内网 LLM 网关）。xpage 必须实现 OpenAI/Anthropic 三个标准端点。

详见 [xpage_mock/README.md](xpage_mock/README.md) — 这份文档**就是给 xpage 团队的需求规格**，同目录下的 `server.py` 是可运行的 mock 实现。

```bash
# 启动 xpage mock
pip install '.[xpage_mock]'
UPSTREAM_MP_BASE=https://your-mp.com \
UPSTREAM_MP_KEY=xxx \
XPAGE_LISTEN_KEYS=test-key-1 \
uvicorn xpage_mock.server:app --port 8800
```

## Relay 协议

bot 收到的飞书 IM 文本消息其实是 JSON：

```json
{
  "_relay_v": 1,
  "req_id": "<24位hex>",
  "model": "claude-opus-4-7",
  "messages": [{"role": "user", "content": "你好"}],
  "max_tokens": 1024
}
```

bot 调上游、把响应包成 JSON 发回飞书：

```json
{
  "_relay_v": 1,
  "req_id": "<同上>",
  "ok": true,
  "content": "你好！",
  "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
  "finish_reason": "stop"
}
```

详见 [`feishu_relay_bot/relay_protocol.py`](feishu_relay_bot/relay_protocol.py)。

## 配套客户端

bot 是协议的"内网端"。外网那一头需要一个 HTTP server 把客户端的标准 OpenAI/Anthropic 请求**包装成 relay 协议**发到飞书。这一头通常用 [Zenwh/ModelProxy](https://github.com/Zenwh/ModelProxy) 的 relay_server。

## License

MIT
