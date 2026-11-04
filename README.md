# NapCat Grok Bridge

面向**单机器人、单私聊对象**的 NapCat ↔ Grok Bot 双向桥接模板，支持固定 HTTPS、事件去重、失败重试和防循环。

> 非官方项目。QQ 私聊内容会发送到你自己的 Grok Bot Webhook Routine，请确认隐私与用量影响。

## 链路

```text
私聊对象 → NapCat → HTTP 事件 → 桥接队列 → Grok Webhook Routine
Grok Routine → 固定 HTTPS + BRIDGE_TOKEN → 桥接 → NapCat → 私聊对象
```

`v0.3` 支持文本、图片私聊和可选的 GitHub 私有图床，不支持群聊、多账号或多私聊对象。普通图片通过公网 HTTPS URL 传递，Grok 生成的 Base64 图片可上传到私有仓库后由桥接代理。

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

## 图片消息

QQ → Grok 的事件升级为 `schema_version=2`。`text` 保留 `[图片]` 占位，`images` 按消息顺序包含有效的 HTTPS 图片 URL：

```json
{
  "type": "qq_private_message",
  "schema_version": 2,
  "text": "看这张图[图片]",
  "images": [{"url": "https://multimedia.example/photo.png"}]
}
```

Grok → QQ 继续使用 `/send_private_msg`。纯文本请求不变；发送图片或图文时，`message` 使用 OneBot 消息段：

```json
{
  "user_id": 123456789,
  "message": [
    {"type": "text", "data": {"text": "这是生成的图片"}},
    {"type": "image", "data": {"file": "https://images.example/photo.png"}}
  ]
}
```

- 每条消息最多 4 张图片，只接受 `text` 和 `image` 消息段，文字总长度仍由 `MAX_MESSAGE_LENGTH` 限制。
- 出站图片必须是无需登录即可访问的公网 HTTPS URL，且域名解析地址均为公网地址；不支持本地路径、`data:` URI 或内网地址。
- NapCat 必须能够访问出站图片 URL。QQ 入站图片 URL 可能有时效，Grok 应尽快处理。
- 入站图片缺少有效 HTTPS `url` 时仍保留 `[图片]` 占位，但不会出现在 `images` 中。

### GitHub 私有图床

`v0.3` 可将 Grok 生成的 Base64 图片上传到一个 GitHub 私有仓库，并通过桥接代理给 NapCat，不需要 jsDelivr。Token 应使用 fine-grained personal access token，只允许访问图床仓库，并仅授予 **Contents: Read and write**。

| 变量 | 说明 |
| --- | --- |
| `ENABLE_IMAGE_HOST` | 设为 `true` 启用图床，默认关闭 |
| `IMAGE_HOST_PUBLIC_BASE_URL` | 桥接公网 HTTPS 地址，默认复用 `PUBLIC_BASE_URL` |
| `IMAGE_HOST_MAX_BYTES` | 单张图片解码后的最大字节数，默认 10 MiB |
| `GITHUB_IMAGE_TOKEN` | 仅限图床仓库的 GitHub Token |
| `GITHUB_IMAGE_REPOSITORY` | 私有仓库，格式为 `owner/repository` |
| `GITHUB_IMAGE_BRANCH` | 上传分支，默认 `main` |
| `GITHUB_IMAGE_TIMEOUT_SECONDS` | GitHub 请求超时，默认 30 秒 |

Grok 使用桥接 Token 上传纯 Base64 或图片 data URL：

```text
POST https://napcat-api.example.com/grok/images
Authorization: Bearer <BRIDGE_TOKEN>
Content-Type: application/json

{"data":"data:image/png;base64,..."}
```

支持 PNG、JPEG、GIF 和 WebP。成功响应包含 `url`、`path`、`media_type` 和 `size`，将 `url` 原样用于 `send_private_msg` 的 `image.file` 即可。桥接会将文件提交到 `images/YYYY/MM/` 下的随机路径。

`GET /images/...` 是供 NapCat 使用的公开代理，桥接在后台读取私有仓库，不向 NapCat 暴露 GitHub Token。虽然仓库是私有的，但知道随机图片 URL 的人都能访问该图片，因此不要上传秘密或敏感内容。

### 从 v0.1 升级

```bash
docker compose up -d --build
docker compose run --rm bridge napcat-grok-routine
```

将重新生成的指令更新到现有 Grok Routine，或按输出创建新的 Routine；`schema_version=1` 的旧指令不会处理新版事件。SQLite 会自动增加 `images` 字段并保留旧事件，无需删除数据卷。

## API

| 接口 | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `GET /bridge/status` | 队列和 Grok 配置状态 |
| `POST /get_login_info` | 查询 NapCat 登录状态 |
| `POST /send_private_msg` | 仅向允许的 QQ 发文本或图片私聊 |
| `POST /grok/images` | 将 Base64 图片上传到 GitHub 私有图床 |
| `GET /images/{path}` | 无需 Token 的随机图片代理地址 |
| `POST /napcat/event` | 接收 NapCat 事件 |
| `POST /grok/config` | 登记 Grok Webhook |

除 `/health` 和 `/images/{path}` 外，接口均需对应 Token。

## 数据与安全

- SQLite 和 Grok 配置保存在 `bridge-data` 卷中，容器重建不会丢失。
- 事件会保存文本和图片 URL 用于投递及重试，不保存图片二进制内容。
- 上传的图片及其 Git 历史保存在 GitHub 私有仓库中，删除文件不会自动清除历史版本。
- `docker compose down -v` 会永久删除数据卷，谨慎使用。
- 不要提交真实 Token、Webhook key、QQ 私聊内容或带签名参数的图片 URL。
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
