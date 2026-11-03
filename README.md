# NapCat Grok Bridge

面向**单机器人、单私聊对象**的 NapCat ↔ Grok Bot 双向桥接模板，支持固定 HTTPS、事件去重、失败重试和防循环。

> 非官方项目。QQ 私聊内容会发送到你自己的 Grok Bot Webhook Routine，请确认隐私与用量影响。

## 链路

```text
私聊对象 → NapCat → HTTP 事件 → 桥接队列 → Grok Webhook Routine
Grok Routine → 固定 HTTPS + BRIDGE_TOKEN → 桥接 → NapCat → 私聊对象
```

`v0.1` 仅支持文本私聊，不支持群聊、多账号或多私聊对象。

## 快速开始

### 1. 准备环境

需要 Docker Compose v2、已登录并开启 OneBot HTTP 的 NapCat，以及 Grok Bot 的 Webhook Routine 能力。

```bash
cp .env.example .env
openssl rand -hex 32  # 填入 BRIDGE_TOKEN
openssl rand -hex 32  # 填入不同的 EVENT_TOKEN
```

至少修改以下变量：

| 变量 | 说明 |
| --- | --- |
| `ONEBOT_URL` | NapCat OneBot HTTP 地址，例如 `http://napcat:3000` |
| `ONEBOT_TOKEN` | NapCat HTTP 服务 Token |
| `BRIDGE_TOKEN` | Grok 调用桥接的独立 Token |
| `EVENT_TOKEN` | NapCat 上报事件的另一独立 Token |
| `ALLOWED_PEER_ID` | 唯一允许私聊的对方 QQ |
| `BOT_USER_ID` | NapCat 登录的机器人 QQ，必须与对方不同 |
| `NAPCAT_NETWORK` | NapCat 所在 Docker 网络 |
| `NAPCAT_NETWORK_EXTERNAL` | 使用已有网络时设为 `true` |

### 2. 启动桥接

```bash
docker compose up -d --build
```

桥接端口只映射到 `127.0.0.1:18080`，不直接暴露公网。

### 3. 配置 NapCat

HTTP 服务监听容器网络的 `0.0.0.0:3000` 并设置 `ONEBOT_TOKEN`，不要把该端口映射到公网。

新增 **HTTP 客户端**：

```text
名称：grok-private-events
URL：http://napcat-grok-bridge:18080/napcat/event?access_token=<EVENT_TOKEN>
消息格式：Array
上报自身消息：开启
启用：开启
```

将 `<EVENT_TOKEN>` 替换为 `.env` 中的真实值。访问日志不记录查询字符串。

### 4. 可选：配置固定 HTTPS

在 Cloudflare 命名隧道中添加 Published application：

```text
Public hostname：napcat-api.example.com
Service URL：http://127.0.0.1:18080
```

将隧道 Token 填入 `CLOUDFLARED_TOKEN`，同步修改 `PUBLIC_BASE_URL`，然后启动：

```bash
docker compose --profile tunnel up -d
```

Cloudflare 自动提供受信任证书。Grok 请求必须使用 `BRIDGE_USER_AGENT` 指定的明确 User-Agent。

### 5. 创建 Grok Routine

生成包含实际 QQ、域名和 User-Agent 的完整指令：

```bash
docker compose run --rm bridge napcat-grok-routine
```

将输出发送给 Grok Bot。它会创建 Webhook Routine，并通过 `/grok/config` 安全登记 `POST to` 和 `key`，无需在普通聊天中粘贴密钥。

也可直接设置 `GROK_WEBHOOK_URL` 与 `GROK_WEBHOOK_KEY`，两者必须一起设置。`GROK_ALLOWED_HOSTS` 默认只允许 `api2.cursor.sh`，如需其他官方域名请显式添加。

### 6. 验证

```bash
# 容器网络检查
docker compose run --rm bridge napcat-grok-doctor --require-grok

# 公网 HTTPS 检查
docker compose run --rm bridge \
  napcat-grok-doctor --base-url https://napcat-api.example.com --require-grok
```

只有明确需要发送一条真实测试私聊时才执行：

```bash
docker compose run --rm bridge napcat-grok-doctor --send-test
```

## API

| 接口 | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /bridge/status` | 队列和 Grok 配置状态 |
| `POST /get_login_info` | 查询 NapCat 登录状态 |
| `POST /send_private_msg` | 仅向允许的 QQ 发私聊 |
| `POST /napcat/event` | 接收 NapCat 事件 |
| `POST /grok/config` | 登记 Grok Webhook |

除 `/health` 外，接口均需对应 Token。

## 数据与安全

- SQLite 和 Grok 配置保存在 `bridge-data` 卷中，容器重建不会丢失。
- `docker compose down -v` 会永久删除数据卷，谨慎使用。
- 不要提交真实 Token、Webhook key 或 QQ 私聊内容。
- 已投递事件和出站消息 ID 默认保留 7 天，可用 `RETENTION_DAYS` 调整。

## 本地开发

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest -q
```

测试使用模拟 NapCat 和 Grok，不需要真实账号。生产联调应另外执行一次真实私聊验证。

推送 `v*` 标签会触发 AMD64/ARM64 镜像发布到 GHCR；部署时可通过 `BRIDGE_IMAGE` 指定预构建镜像。

## License

MIT
