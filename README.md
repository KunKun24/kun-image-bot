# 困困生图机器人

**中文** · [English](README.en.md)

基于 Telegram 的 AI 生图机器人：双上游线路、预设模板、余额与签到、USDT 充值（OkPay / TON / TRON）、邀请返利、管理员后台，并自带**克隆平台**（把同一个机器人复制成多个独立子机器人对外运营）。

- 作者 / 开源：**TG 困困 [@sad1881](https://t.me/sad1881)**
- 版本：v2.0.0（模块化重构版）
- 许可证：MIT

> 本仓库由单文件 5000 行脚本重构为 9 个模块，修复了历史图片回看失效、Token 硬编码、数据库 WAL 丢失、依赖声明缺失等问题，并补齐注释与文档。

---

## 功能一览

### 用户端
| 功能 | 说明 |
| --- | --- |
| 我来发挥 | 发送文字或参考图 → 提示词优化 → 生成图片，支持尺寸/清晰度选择 |
| 官方预设 | 多套预设模板（模板图 + 字段 + 需求图），按预设价格扣费 |
| 我的设计 | 查看历史作品，支持「再画一张」与回看原图 |
| 每日签到 | `/qd` 领取签到奖励，余额到账 |
| 充值 | OkPay 在线支付、TON-USDT、TRON-USDT 链上转账，自动查单到账 |
| 邀请返利 | 专属邀请码与邀请记录，被邀请人充值后按比例返佣 |
| 失败退款 | 生图失败自动退回本次消耗，幂等防重复退款 |

### 管理端（`/admin`）
- **数据统计**：用户数、订单、生图量、收入
- **用户管理**：搜索、封禁/解封、调整余额、查看明细
- **文案与按钮**：欢迎语、提示语、按钮文字全部可改，支持自定义 emoji
- **预设管理**：新增/编辑预设，上传模板图、详情图，配置输入字段
- **支付配置**：OkPay 商户参数、TON/TRON 收款地址与汇率
- **线路配置**：两路上游 API（地址、模型、Token 池、超时），可切换默认线路
- **价格配置**：自由作图 / 预设基础费 / 协议叠加费 / 签到奖励
- **频道发布**：生成结果自动同步到指定频道并附按钮
- **手动清理**：过期订单、卡单补偿

### 克隆平台（SaaS 模式）
- 主站 `/clone` 面板：购买额度 → 创建子机器人 → 进程守护
- 租户隔离：每个子机器人独立的 `c{id}_users / c{id}_orders / c{id}_draw_tasks...` 表
- 共享上游：子机器人复用主站 API 线路，按次计费并记入审计日志
- 子机器人 Token 用 Fernet 加密存储（`clone_secret.key`，权限 600）
- 内容审核：调用主站模型对提示词做敏感词/策略审核，可配置失败策略

---

## 目录结构

```
.
├── main.py                  # 启动入口（python main.py）
├── requirements.txt
├── .env.example             # 环境变量模板
├── LICENSE
├── README.md
├── INSTALL.md               # 部署说明
├── docs/
│   └── ARCHITECTURE.md      # 架构与流程说明
└── bot/
    ├── __init__.py
    ├── config.py            # 配置、常量、默认文案、.env 读取
    ├── text.py              # 底层工具：时间/编号、截断、金额、HTML 与实体转换
    ├── db.py                # SQLite 访问、配置读写、用户/订单/任务、租户隔离
    ├── utils.py             # 按钮、会话状态、消息安全编辑
    ├── drawing.py           # 生图核心：上游调用、图片规范化、任务执行与交付
    ├── payments.py          # OkPay / TON-USDT / TRON-USDT
    ├── handlers.py          # 用户端、管理端指令与回调、定时任务
    ├── platform.py          # 克隆平台：进程守护、共享账务、审核
    └── main.py              # 装配覆盖链、注册 handler、启动轮询
```

模块依赖方向（禁止反向导入）：

```
config → text → db → utils → drawing → payments → handlers → platform → main
```

---

## 快速开始

```bash
# 1. 安装依赖（务必带 [job-queue] 额外依赖，否则定时任务无法启用）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
vim .env          # 填 BOT_TOKEN / BOT_USERNAME / ADMIN_IDS

# 3. 启动
python main.py
```

首次启动会自动建库、写入默认配置与预设。进入机器人发送 `/start`，管理员发送 `/admin` 打开后台。

详细部署（systemd、Nginx 反代、开机自启、常见故障）见 [INSTALL.md](INSTALL.md)。

---

## 配置项

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `BOT_TOKEN` | 是 | BotFather 生成的 Token |
| `BOT_USERNAME` | 是 | 机器人用户名（不含 @） |
| `ADMIN_IDS` | 是 | 管理员 TGID，逗号分隔；管理员需先 `/start` 过机器人 |
| `WEB_PORT` | 否 | OkPay 回调端口，默认 5003 |
| `DRAW_TIMEOUT_SECONDS` | 否 | 生图超时，默认 600 秒 |

其余业务配置（API 线路、价格、支付、文案）全部在管理员后台修改，落库 `config` 表。

---

## 数据与备份

运行期数据都在项目根目录，**不在仓库内**：

| 文件/目录 | 说明 |
| --- | --- |
| `image_bot.db` | SQLite 主库（WAL 模式，另有 `-wal` / `-shm`） |
| `generated_images/` | 生成图片本地副本 |
| `clone_secret.key` | 克隆 Token 加密密钥，丢失将无法解密子机器人 Token |

备份建议：`sqlite3 image_bot.db ".backup backup.db"` + 打包 `generated_images/` 与 `clone_secret.key`。

---

## 免责声明

本项目仅供学习与技术研究。请遵守所在地法律法规与 Telegram 平台规则，不得用于生成、传播违法违规内容；因使用本项目产生的一切后果由使用者自行承担。

---

## 版权

```
困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
```
