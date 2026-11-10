# 架构说明

## 1. 为什么是「覆盖链」结构

原项目是单文件 5000 行脚本，靠 Python「后定义覆盖先定义」实现功能扩展：

```python
def handle_keyboard(...): ...          # 第一版
...
_original_home_keyboard = home_keyboard # 保存旧引用
def home_keyboard(...): ...             # 平台版覆盖
...
handle_keyboard = _platform.handle_keyboard  # 第三版覆盖
```

拆分后每个模块只保留自己那一层定义，**覆盖关系在 `bot/main.py` 顶部显式装配**，等价于原来的隐式覆盖：

```python
_handlers.handle_keyboard = _platform.handle_keyboard
_handlers.handle_callback_full = _platform.handle_callback_full
_handlers.handle_admin_input_full = _platform.handle_admin_input_full
_handlers.show_admin_menu = _platform.show_admin_menu
_handlers.post_init = _platform.post_init
```

各平台层函数内部通过 `_platform_old_*` / `_original_*` 引用上一层实现，形成「终版 → 平台版 → 基础版」的包装链。

## 2. 模块分层

| 模块 | 职责 | 可依赖 |
| --- | --- | --- |
| `config` | 常量、默认文案、`.env` 读取 | 无 |
| `text` | 时间/编号生成、截断、金额、HTML 与 TG 实体转换 | config |
| `db` | SQLite 访问、config/presets 读写、用户/订单/任务、租户隔离 | config, text |
| `utils` | 按钮、会话状态、消息安全编辑 | config, text, db |
| `drawing` | 上游生图 API、图片规范化、任务执行、交付 | config, text, db, utils |
| `payments` | OkPay / TON / TRON 支付 | config, text, db |
| `handlers` | 用户端、管理端、定时任务、Flask 回调 | 以上全部 |
| `platform` | 克隆平台进程管理、共享账务、内容审核 | 以上全部 |
| `main` | 覆盖链装配、handler 注册、轮询启动 | 全部 |

约束：

- **只允许向下依赖**，不允许 `db → utils`、`text → db` 之类反向依赖；
- 反向调用（如 `payments → handlers` 展示支付页、`handlers → platform` 提交任务）一律在**函数内部惰性 import**，避免循环导入；
- star import 不会带出下划线开头的名字，跨模块使用 `_strip_html_tags`、`_mask_user_id`、`_local_config_raw` 时必须显式导入。

## 3. 启动流程

```
main() [bot/main.py]
 ├─ 解析 --clone-id（默认 0 = 主站）
 ├─ 文件锁 .bot_{clone_id}.lock（防重复启动）
 ├─ 克隆实例：读 clone_registry → 解密 Token → 清空商户配置
 │  主站实例：init_db → platform_init → clone_cipher
 ├─ tenant_migrate()（补齐迁移列）
 ├─ _original_main() [基础版]
 │   ├─ 校验 BOT_TOKEN
 │   ├─ Application.builder().token(...).concurrent_updates(...)
 │   ├─ 注册 /clone /start /qd /admin、文本、图片、回调、错误处理
 │   ├─ JobQueue：check_expired_orders(60s)、check_pending_orders(15s)
 │   └─ run_polling()
 └─ post_init（平台版）：设置命令菜单、启动克隆进程守护
```

## 4. 生图流程

```
用户选择功能 → start_free_draw / start_preset_draw
   → submit_draw()（平台版）：创建任务、预扣余额、启动 DrawWorker
   → DrawWorker.generate_image()
        ├─ 选线路 line1/line2 与 Token（轮询 token 池）
        ├─ 文生图 POST /images/generations
        ├─ 图生图 POST /images/edits（multipart，参考图转 BytesIO）
        ├─ 解析 data[0].url / b64_json（>50MB 拒绝）
        └─ 超时按线路读取，失败换 Token 重试
   → prepare_generated_photo()：下载/压缩到 generated_images/
   → deliver_generated_photo()：编辑消息图片，失败降级为文件
   → 回写 draw_tasks.result_file_id（历史回看用 file_id）
   → 成功：状态 completed、累计作图数、同步频道
   → 失败：refund_draw_once() 幂等退款
```

失败退款用 `UPDATE orders/draw_tasks ... WHERE status != 'refunded'` 保证只退一次。

## 5. 支付流程

### OkPay
```
创建订单 → pay_link 跳转 → 用户支付
→ OkPay 回调 /okpay/notify（Flask 线程）
→ check_deposit 查单 → run_coroutine_threadsafe 投递到 bot 事件循环
→ _process_okpay_notify → on_payment_success（幂等入账）
```
签名：参数拍平 → 字典序 → `k=v&` 拼接 → HMAC-SHA256（大写）。
另有 `check_pending_orders` 每 15 秒轮询补偿，防回调丢失。

### TON-USDT
`toncenter /api/v3/jetton/transfers` 查转账，用 `ton_raw_address`（CRC16 校验）验证地址；订单会分配唯一 memo 后缀便于匹配。

### TRON-USDT
`trongrid` 查交易，`is_valid_tron_address` 做 base58 + SHA256 双校验；金额按十进制精确比较（`Decimal`），避免浮点误差。

## 6. 多租户（克隆平台）

- 表隔离：`tenant_sql()` 把裸表名替换为 `c{CLONE_ID}_表名`，正则跳过引号内字符串；
- 配置隔离：子机器人只读自己的 `c{id}_config`，上游 API Key 从主站 `config` 共享；
- 进程隔离：主站 `clone_supervise()` 每 10 秒扫描 `clone_registry`，拉起/重启/清理子进程；
- 安全：子机器人 Token 用 Fernet 加密，密钥 `clone_secret.key`（600）；子进程不写入主站商户支付凭据；
- 计费：`clone_charges` 按次记录，`clone_credits` 扣减，`clone_audit` 留痕。

## 7. 数据库

| 表 | 说明 |
| --- | --- |
| `users` | TG 用户、余额、邀请码、状态 |
| `orders` | 充值订单（OkPay / TON / TRON 字段） |
| `draw_tasks` | 生图任务与结果（`result_url` 本地路径、`result_file_id`） |
| `config` | 键值配置（文案、价格、API、支付） |
| `presets` | 预设模板 |
| `invite_records` | 邀请返利记录 |
| 克隆表 | `clone_registry` / `clone_credits` / `clone_purchases` / `clone_charges` / `clone_audit` |

SQLite 启用 WAL，`busy_timeout=20000`，写冲突时自动等待。

## 8. 已知限制

- 克隆平台依赖 `fcntl`、`prctl`，仅 Linux；
- 单进程轮询模式，推荐单实例部署；多实例需自行改为 webhook + 外部队列；
- 图片本地缓存不会自动清理，长期运行建议加定时清理脚本；
- `tenant_sql` 基于正则替换，若新 SQL 使用反引号或复杂子查询需复核。
