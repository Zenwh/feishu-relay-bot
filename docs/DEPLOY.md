# 部署手册

> 一份自包含的部署指南，覆盖 **Docker / Linux / macOS** 三种部署方式。
> 适用于：你拿到这个仓库 → 5 分钟跑起来一个连飞书的 bot，把外网消息隧道到内网 LLM 网关。

---

## 0. 部署前你需要准备好什么

只需要 4 样东西就能跑：

| 信息 | 说明 | 哪里拿 |
|------|------|--------|
| 飞书 `app_id` | bot 应用 ID，形如 `cli_xxxxxxxxxxxx` | 飞书开放平台 — 凭证与基础信息 |
| 飞书 `app_secret` | bot 应用密钥，形如 `xxxxxxxxxxxxxxxxxxxx` | 同上 |
| 上游 `base_url` | 内网 LLM 网关地址，形如 `http://your-mp:8080` | 你公司提供 |
| 上游 `api_key` | 调上游用的 token，形如 `ak-xxxxxxxxxxxxxxxxxxxx` | 你公司提供 |

> ⚠️ **飞书 bot 必须开启 websocket 长连接**：在飞书开放平台 → 你的 app → 「事件订阅」打开 websocket 模式，否则 bot 收不到消息。

> ⚠️ **飞书 bot 必须订阅 `im.message.receive_v1` 事件**：在「事件订阅」下添加这个事件，并加上 `im:message`、`im:message:send_as_bot` 权限。

---

## 1. 部署方式选择

```
┌─────────────────────────────────────────────────────────┐
│ 你的机器有 Docker？                                      │
│   是 → 推荐【方式 A：Docker】（最干净）                  │
│   否 ↓                                                   │
│ 你的机器是 Linux？                                       │
│   是 → 【方式 B：Linux + systemd】（最稳）              │
│   否 ↓                                                   │
│ 你的机器是 macOS？                                       │
│   → 【方式 C：macOS launchd】（开机自启）               │
│   或 → 【方式 D：直接 Python】（最简单，临时跑）         │
└─────────────────────────────────────────────────────────┘
```

---

## 方式 A：Docker（推荐）

最简单 + 跨平台 + 不污染系统。

### A.1 准备 config

```bash
# 任意目录，建议 ~/feishu-relay-bot
mkdir -p ~/feishu-relay-bot && cd ~/feishu-relay-bot
```

写 `config.yaml`（最小配置）：

```yaml
# config.yaml
upstream:
  base_url: ${UPSTREAM_BASE_URL}
  api_key: ${UPSTREAM_API_KEY}

# 模型白名单（按你公司 MP 实际支持的填）
models:
  - public: claude-opus-4-7
    upstream: claude-opus-4-7
    endpoint: messages
  - public: gpt-5-5
    upstream: gpt-5.5
    endpoint: responses
  - public: kimi-2.6
    upstream: kimi-k2.6
    endpoint: chat

bots:
  - name: my-bot
    app_id: ${FEISHU_APP_ID}
    app_secret: ${FEISHU_APP_SECRET}
```

### A.2 写 .env

```bash
cat > .env <<'EOF'
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxx
UPSTREAM_BASE_URL=http://your-mp.internal:8080
UPSTREAM_API_KEY=ak-xxxxxxxxxxxxxxxxxxxx
EOF

chmod 600 .env   # 别让别人读到
```

### A.3 跑

```bash
# 拉镜像 + 启动
docker run -d \
  --name feishu-relay-bot \
  --restart unless-stopped \
  --env-file .env \
  -v $PWD/config.yaml:/etc/feishu-relay-bot/config.yaml:ro \
  ghcr.io/zenwh/feishu-relay-bot:latest

# 看日志确认连上了
docker logs -f feishu-relay-bot
```

期望看到这几行：
```
[INFO] cli: feishu-relay-bot v0.1.0
[INFO] manager: configured 1 bot(s): my-bot
[INFO] bot.my-bot: starting bot my-bot app_id=cli_xxx
[INFO] manager: all bots started
```

> 如果还没发布到 ghcr.io，先在本地 build：
> ```bash
> git clone https://github.com/Zenwh/feishu-relay-bot.git
> docker build -t feishu-relay-bot:latest -f feishu-relay-bot/docker/Dockerfile feishu-relay-bot/
> docker run -d --name feishu-relay-bot \
>   --restart unless-stopped --env-file .env \
>   -v $PWD/config.yaml:/etc/feishu-relay-bot/config.yaml:ro \
>   feishu-relay-bot:latest
> ```

### A.4 验证

```bash
# 容器内做 config check
docker exec feishu-relay-bot feishu-relay-bot check
# 期望：✅ config OK
```

如果配套有 [Zenwh/ModelProxy](https://github.com/Zenwh/ModelProxy) 的外网 relay 服务，到 relay 那边发个测试请求即可看到 bot 的日志：

```
[INFO] bot.my-bot: ← req_id=xxx mode=openai_chat model=kimi-2.6 msgs=1
[INFO] bot.my-bot: → req_id=xxx ok=True len=217
```

### A.5 升级 / 停止

```bash
# 升级
docker pull ghcr.io/zenwh/feishu-relay-bot:latest
docker rm -f feishu-relay-bot
# 重新 docker run（同 A.3）

# 停止
docker stop feishu-relay-bot

# 删除（连容器一起）
docker rm -f feishu-relay-bot
```

### A.6 docker compose（多 bot 推荐）

如果一进程要跑多个 bot，用 `examples/docker-compose.yaml` 修改后跑：

```bash
cp examples/docker-compose.yaml ./docker-compose.yaml
# 编辑 docker-compose.yaml，按需调整
docker compose up -d
docker compose logs -f
```

---

## 方式 B：Linux + systemd

适合：长期常驻、要开机自启、要 systemctl 管理的服务器。

### B.1 装 Python 3.9+

```bash
# Alibaba Cloud Linux / RHEL / CentOS
sudo dnf install -y python3.11

# Ubuntu / Debian
sudo apt update && sudo apt install -y python3.11 python3.11-venv

# 验证
python3.11 --version    # 期望 ≥ 3.9
```

### B.2 拉代码 + 装包

```bash
# 选个目录（这里用 /opt）
sudo mkdir -p /opt/feishu-relay-bot
sudo chown $USER /opt/feishu-relay-bot

git clone https://github.com/Zenwh/feishu-relay-bot.git /opt/feishu-relay-bot
cd /opt/feishu-relay-bot

# 装到 venv，避免污染系统
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
```

### B.3 写 config 和环境变量文件

```bash
sudo mkdir -p /etc/feishu-relay-bot
sudo cp examples/config.example.yaml /etc/feishu-relay-bot/config.yaml
sudo chown $USER /etc/feishu-relay-bot/config.yaml
chmod 600 /etc/feishu-relay-bot/config.yaml
# 编辑：vim /etc/feishu-relay-bot/config.yaml

# env 单独放：
sudo bash -c 'cat > /etc/feishu-relay-bot/env <<EOF
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxx
UPSTREAM_BASE_URL=http://your-mp.internal:8080
UPSTREAM_API_KEY=ak-xxxxxxxxxxxxxxxxxxxx
EOF'
sudo chmod 600 /etc/feishu-relay-bot/env
```

### B.4 写 systemd unit

```bash
sudo bash -c 'cat > /etc/systemd/system/feishu-relay-bot.service <<UNIT
[Unit]
Description=Feishu Relay Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/feishu-relay-bot
EnvironmentFile=/etc/feishu-relay-bot/env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/feishu-relay-bot/.venv/bin/feishu-relay-bot run --config /etc/feishu-relay-bot/config.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT'
```

### B.5 启动

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now feishu-relay-bot

# 看状态 / 日志
sudo systemctl status feishu-relay-bot
sudo journalctl -u feishu-relay-bot -f
```

期望日志（同 A.3）。

### B.6 升级

```bash
cd /opt/feishu-relay-bot
git pull
.venv/bin/pip install -e . --upgrade
sudo systemctl restart feishu-relay-bot
```

---

## 方式 C：macOS launchd（开机自启）

跟 systemd 等价，但跑在 macOS 上。

### C.1 装包（同方式 B 的 B.1 + B.2，路径换成 `~/feishu-relay-bot`）

```bash
# Homebrew
brew install python@3.11

# 装包
git clone https://github.com/Zenwh/feishu-relay-bot.git ~/feishu-relay-bot
cd ~/feishu-relay-bot
python3.11 -m venv .venv
.venv/bin/pip install -e .
```

### C.2 写 config

```bash
cp examples/config.example.yaml config.yaml
# vim config.yaml 改成你的实际值（直接写死也行，env 也行）
chmod 600 config.yaml
```

### C.3 写 launchd plist

```bash
cat > ~/Library/LaunchAgents/com.zenwh.feishu-relay-bot.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.zenwh.feishu-relay-bot</string>

    <key>ProgramArguments</key>
    <array>
        <string>$HOME/feishu-relay-bot/.venv/bin/feishu-relay-bot</string>
        <string>run</string>
        <string>--config</string>
        <string>$HOME/feishu-relay-bot/config.yaml</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$HOME/feishu-relay-bot</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$HOME/feishu-relay-bot/runtime.log</string>

    <key>StandardErrorPath</key>
    <string>$HOME/feishu-relay-bot/runtime.err.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/bin:/usr/local/bin:/opt/homebrew/bin</string>
        <key>FEISHU_APP_ID</key>
        <string>cli_xxxxxxxxxxxx</string>
        <key>FEISHU_APP_SECRET</key>
        <string>xxxxxxxxxxxxxxxxxxxx</string>
        <key>UPSTREAM_BASE_URL</key>
        <string>http://your-mp.internal:8080</string>
        <key>UPSTREAM_API_KEY</key>
        <string>ak-xxxxxxxxxxxxxxxxxxxx</string>
    </dict>
</dict>
</plist>
PLIST
```

> ⚠️ launchd 不展开 `$HOME`，要么手动替换为绝对路径，要么用 `envsubst`：
> ```bash
> envsubst < ~/Library/LaunchAgents/com.zenwh.feishu-relay-bot.plist > /tmp/p && \
> mv /tmp/p ~/Library/LaunchAgents/com.zenwh.feishu-relay-bot.plist
> ```

### C.4 加载启动

```bash
launchctl load ~/Library/LaunchAgents/com.zenwh.feishu-relay-bot.plist

# 看是否在跑
launchctl list | grep feishu

# 看日志
tail -f ~/feishu-relay-bot/runtime.log
```

### C.5 停止 / 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.zenwh.feishu-relay-bot.plist
```

---

## 方式 D：直接 Python（临时 / 调试）

最简单，不要后台守护，跑就跑、关就关。适合本地调试 / 短时间 PoC。

```bash
git clone https://github.com/Zenwh/feishu-relay-bot.git
cd feishu-relay-bot

python3 -m venv .venv
.venv/bin/pip install -e .

cp examples/config.example.yaml config.yaml
# vim config.yaml

export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export UPSTREAM_BASE_URL=http://your-mp:8080
export UPSTREAM_API_KEY=ak-xxx

.venv/bin/feishu-relay-bot run --config config.yaml
# Ctrl+C 退出
```

后台运行用 `nohup`：
```bash
nohup .venv/bin/feishu-relay-bot run --config config.yaml > runtime.log 2>&1 &
echo $! > pid    # 记 pid
# 想停: kill $(cat pid)
```

---

## 排错

### 1. bot 启动后没反应、没看到 ws 连接日志

可能原因：
- 飞书后台没开 websocket 模式 → 在飞书开放平台 → 事件订阅 → 长连接模式打开
- app_secret 错了 → `feishu-relay-bot check` 会通过（不验证 secret 有效性），但实际连接会失败。看 `journalctl` 找 lark sdk 报错
- 网络出不去公网 → bot 必须能访问 `wss://msg-frontier.feishu.cn`

测试 ws 连接：
```bash
curl -v https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal \
  -d '{"app_id":"cli_xxx","app_secret":"xxx"}' \
  -H "Content-Type: application/json"
# 期望 code: 0，否则你的 app_id/secret 有问题
```

### 2. bot 收到消息但调上游失败

看 bot 日志会有 `→ req_id=xxx ok=False status=502`。检查：
- bot 容器 / 主机能不能直接 curl 上游：
  ```bash
  curl http://your-mp:8080/health    # 应该返回 200
  ```
- 上游 key 对不对（直接 curl 测）

### 3. config 校验错误

```bash
.venv/bin/feishu-relay-bot check --config config.yaml
```
如果报错，按提示修，特别注意：
- `${VAR}` 占位的 env 变量真的设了吗（用 `printenv VAR` 验证）
- `endpoint` 必须是 `messages` / `responses` / `chat` 之一
- bot.app_secret 不能为空

### 4. 飞书 IM 里能看到 bot 收到 JSON 消息但不回复

可能 bot 处理时报错了。看日志：
```bash
docker logs feishu-relay-bot     # docker
journalctl -u feishu-relay-bot   # systemd
tail ~/feishu-relay-bot/runtime.err.log    # launchd
```

通常是上游连不通 / 模型不在白名单。

---

## 安全性

- **app_secret / api_key 不要写进代码或 commit 到 git**：用 .env 或 systemd EnvironmentFile，权限 600
- **机器最好放在内网**：bot 只需要能：
  - 出公网到 `wss://msg-frontier.feishu.cn` + `https://open.feishu.cn`
  - 内网到上游 LLM 网关
- 不需要任何入站端口（websocket 是主动外连）

---

## FAQ

**Q: 一台机器能跑多个 bot 吗？**
A: 能。配置文件 `bots:` 列表里加多个就行，一进程同时跑。看 `examples/config.multi-bot.yaml`。

**Q: 需要公网入口吗？**
A: 不需要。bot 主动外连飞书，**入站端口一个不开**。这是这个产品的关键设计。

**Q: 飞书 bot 怎么申请？**
A: https://open.feishu.cn/app → 创建企业自建应用 → 在「凭证与基础信息」拿 App ID / App Secret → 在「权限管理」加 `im:message` + `im:message:send_as_bot` → 在「事件与回调」开启「使用长连接」并订阅 `im.message.receive_v1`。

**Q: 升级到新版本要怎么做？**
A: Docker `docker pull` + 重启；systemd `git pull` + `pip install -e . --upgrade` + `systemctl restart`。

**Q: 健康检查怎么做？**
A: `feishu-relay-bot check --quiet` 返回 exit 0 / 2。Docker / K8s 健康检查直接用：
```yaml
healthcheck:
  test: ["CMD", "feishu-relay-bot", "check", "-q"]
  interval: 30s
```

---

需要更多帮助：[github.com/Zenwh/feishu-relay-bot/issues](https://github.com/Zenwh/feishu-relay-bot/issues)
