# 5 分钟快速部署

> 给同事 / 第三方部署的最简版本。完整指南见 [DEPLOY.md](DEPLOY.md)。

## 你需要

1. 一台能上**飞书公网** + 能上**你公司 MP** 的机器（Linux/macOS/Docker 都行）
2. 飞书 bot 凭据：`app_id` + `app_secret`
3. MP（或 xpage）凭据：`base_url` + `api_key`

> ⚠️ 飞书 bot 必须：开「事件订阅 → 使用长连接」+ 订阅 `im.message.receive_v1` + 加 `im:message` 权限

## 选一个方式

### Docker（最简单）

```bash
mkdir feishu-relay-bot && cd feishu-relay-bot

# 拉 config 模板
curl -O https://raw.githubusercontent.com/Zenwh/feishu-relay-bot/main/examples/config.example.yaml
mv config.example.yaml config.yaml
# 不用改 config.yaml，env 里覆盖就行

# 写 .env
cat > .env <<'EOF'
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxx
UPSTREAM_BASE_URL=http://your-mp:8080
UPSTREAM_API_KEY=ak-xxxxxxxxxxxxxxxxxxxx
EOF
chmod 600 .env

# build & run
docker build -t feishu-relay-bot \
  https://github.com/Zenwh/feishu-relay-bot.git#main:docker
# 上面这行有点 trick，等价于:
# git clone https://github.com/Zenwh/feishu-relay-bot.git
# docker build -t feishu-relay-bot feishu-relay-bot/docker -f feishu-relay-bot/docker/Dockerfile feishu-relay-bot/

docker run -d \
  --name feishu-relay-bot \
  --restart unless-stopped \
  --env-file .env \
  -v $PWD/config.yaml:/etc/feishu-relay-bot/config.yaml:ro \
  feishu-relay-bot

# 看日志
docker logs -f feishu-relay-bot
```

期望日志：
```
[INFO] cli: feishu-relay-bot v0.1.0
[INFO] manager: configured 1 bot(s): my-bot
[INFO] bot.my-bot: starting bot my-bot app_id=cli_xxx
[INFO] manager: all bots started
```

### Linux / macOS（不用 Docker）

```bash
# 装 Python 3.9+ (Linux: dnf install python3.11; macOS: brew install python@3.11)

# 拉代码
git clone https://github.com/Zenwh/feishu-relay-bot.git
cd feishu-relay-bot

# venv + 装包
python3.11 -m venv .venv
.venv/bin/pip install -e .

# config（按你的实际值填）
cp examples/config.example.yaml config.yaml

# 跑（前台跑试试）
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export UPSTREAM_BASE_URL=http://your-mp:8080
export UPSTREAM_API_KEY=ak-xxx

.venv/bin/feishu-relay-bot run --config config.yaml
# Ctrl+C 退出
```

确认能跑后再上 systemd / launchd 做开机自启（见 [DEPLOY.md](DEPLOY.md) 方式 B / C）。

## 验证连通性

```bash
# 1. config 是否合法
.venv/bin/feishu-relay-bot check --config config.yaml
# 期望: ✅ config OK

# 2. bot 能不能连飞书
# 启动后看到 "manager: all bots started" 即 OK

# 3. 真发个请求过来（前提：你有外网 relay 服务）
# 用 ModelProxy 仓库的 relay_server 当客户端入口，发请求看 bot 日志
```

## 出错了？

最常见 4 个问题：

| 症状 | 可能原因 |
|------|---------|
| 启动报 `Field required` | env 变量没设 / config.yaml 字段拼错 |
| 启动后无 ws 日志 | 飞书 app 没开长连接模式 / app_secret 错 |
| bot 收到消息但 `ok=False status=502` | 机器连不通 MP / MP key 不对 |
| 报 `unsupported model: xxx` | model 没在 config.yaml 的 `models:` 列表里 |

详细排查见 [DEPLOY.md 排错章节](DEPLOY.md#排错)。
