# 部署说明

面向 Ubuntu 20.04+ / Debian 11+（其他 Linux 发行版同理）。Windows 可运行，但克隆平台的进程守护依赖 `fcntl`/`prctl`，仅限 Linux。

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | 3.10 及以上（推荐 3.11 / 3.12） |
| 系统 | Linux（克隆平台需 `fcntl`） |
| 内存 | 1 GB 起（生图任务并发默认 5） |
| 磁盘 | 生成图片占空间，建议 ≥ 20 GB |
| 网络 | 可直连 Telegram API 与生图上游 |

## 2. 安装

```bash
cd /opt
git clone <你的仓库地址> shengtu-bot
cd shengtu-bot

python3 -m venv .venv
source .venv/bin/activate

# 注意：必须带 [job-queue]，否则启动时报 "No JobQueue set up"
pip install -U pip
pip install -r requirements.txt
```

`requirements.txt` 已声明 `python-telegram-bot[job-queue]>=20.10,<23`。

## 3. 配置

```bash
cp .env.example .env
chmod 600 .env
vim .env
```

```ini
BOT_TOKEN=123456789:AA...
BOT_USERNAME=your_bot
ADMIN_IDS=123456789
# WEB_PORT=5003
```

说明：

- `BOT_TOKEN` / `BOT_USERNAME` / `ADMIN_IDS` 三个必填；
- 管理员**必须**先给机器人发过 `/start`，否则 Telegram 不允许机器人主动发消息（`post_init` 会打 warning）；
- 不要用主账号 Token 硬编码进源码，`.env` 已在 `.gitignore` 中。

## 4. 启动

```bash
python main.py
```

首次运行会：

1. 创建 `image_bot.db` 并建表；
2. 写入默认配置与预设；
3. 生成 `clone_secret.key`（克隆平台 Token 加密密钥）；
4. 启动 Flask 回调服务（`0.0.0.0:WEB_PORT`）与 Telegram 轮询。

验证：给机器人发 `/start`，应出现底部键盘；管理员发 `/admin` 应进入后台。

## 5. systemd 常驻

`/etc/systemd/system/shengtu-bot.service`：

```ini
[Unit]
Description=KunKun Image Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=www
WorkingDirectory=/opt/shengtu-bot
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/shengtu-bot/.venv/bin/python /opt/shengtu-bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now shengtu-bot
journalctl -u shengtu-bot -f
```

## 6. OkPay 异步回调（可选）

只有使用 OkPay 在线支付时才需要公网可达：

1. 在 Nginx / 宝塔中把 `https://你的域名/okpay/notify` 反代到 `http://127.0.0.1:5003/okpay/notify`；
2. 在 OkPay 商户后台把回调地址填成上面的 HTTPS 地址；
3. 若前面有 CDN/WAF，确认不要缓存 `/okpay/notify`，并允许 POST。

TON / TRON 是链上查单，不需要回调端口。

## 7. 克隆平台（可选）

主站管理员在 `/clone` 面板：

1. 先充值克隆额度；
2. 到 @BotFather 创建子机器人，拿到 Token；
3. 在面板里创建子机器人并填入 Token；
4. 平台会以 `python main.py --clone-id N` 方式拉起独立进程，各自独立数据库表前缀 `cN_`。

注意：

- 子机器人进程由主站守护，主站停止时子进程会被一并结束；
- `clone_secret.key` 必须备份，丢失后已有子机器人 Token 无法解密；
- 子机器人共用主站上游 API，按次从主站额度扣费。

## 8. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `No JobQueue set up` | 依赖缺 extras：`pip install "python-telegram-bot[job-queue]"` |
| 启动即退出：实例已运行 | 同目录已有 `.bot_0.lock` 且被占用，确认没有重复进程 |
| 管理员收不到消息 / Chat not found | 该 TGID 从未与机器人对话，先用该账号发 `/start` |
| 生图一直失败 | 后台「线路配置」检查 API 地址、模型名、Token 池与超时 |
| 历史作品看不到图 | v2.0 已修复（改为 file_id / 显式上传本地文件）；旧库记录若无文件请重新生成 |
| 回调不到账 | 检查 `WEB_PORT` 是否放行、OkPay 回调地址与签名配置 |

## 9. 升级

```bash
git pull
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart shengtu-bot
```

数据库表结构变更由代码内的 `init_db()` / `tenant_migrate()` 自动补齐（`ALTER TABLE` 幂等）。
