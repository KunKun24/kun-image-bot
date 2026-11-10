#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
"""数据库访问层：SQLite、配置读写、用户/订单/任务，以及克隆租户隔离。"""
import os
import sys
import json
import time
import random
import string
import pathlib
import sqlite3
import secrets
import datetime
import logging
import re
from contextlib import closing

from .config import *
from .text import *

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

# ============================================================
# 默认配置（全局常量，用于重置功能）
# ============================================================
def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE,
        username TEXT,
        first_name TEXT,
        bot_user_id INTEGER UNIQUE,
        balance REAL DEFAULT 0,
        total_draws INTEGER DEFAULT 0,
        invite_code TEXT UNIQUE,
        invited_by INTEGER,
        invite_earnings REAL DEFAULT 0,
        status TEXT DEFAULT 'normal',
        created_at TEXT,
        checkin_at TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT UNIQUE,
        user_id INTEGER,
        amount REAL,
        coin TEXT DEFAULT 'USDT',
        status TEXT DEFAULT 'pending',
        pay_method TEXT,
        pay_url TEXT,
        pay_address TEXT,
        txid TEXT,
        created_at TEXT,
        paid_at TEXT,
        expire_at TEXT
    );
    CREATE TABLE IF NOT EXISTS draw_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT UNIQUE,
        user_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER,
        draw_type TEXT,
        preset_name TEXT,
        preset_data TEXT,
        prompt TEXT,
        ref_images TEXT,
        size TEXT,
        quality TEXT DEFAULT 'high',
        cost REAL,
        status TEXT DEFAULT 'pending',
        result_url TEXT,
        result_file_id TEXT,
        error_msg TEXT,
        created_at TEXT,
        completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS presets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        price REAL DEFAULT 0.15,
        template_image TEXT,
        fields TEXT,
        enabled INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS invite_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER,
        invitee_id INTEGER,
        order_id TEXT,
        amount REAL,
        reward REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    );
    """)
    conn.commit()
    # 订单表字段迁移（兼容旧版）
    for col, typ in [("ton_memo","TEXT"),("ton_amount","TEXT"),("ton_checked","INTEGER DEFAULT 0"),
                     ("tron_expected_amount","TEXT"),("tron_suffix","INTEGER"),("tron_checked","INTEGER DEFAULT 0")]:
        try:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
        except Exception:
            pass
    # draw_tasks表字段迁移（兼容旧版，加chat_id和message_id用于重启恢复）
    for col, typ in [("chat_id","INTEGER"),("message_id","INTEGER")]:
        try:
            c.execute(f"ALTER TABLE draw_tasks ADD COLUMN {col} {typ}")
        except Exception:
            pass
    # presets表字段迁移（加prompt_template/detail_image/demand_image）
    for col, typ in [("prompt_template","TEXT"),("detail_image","TEXT"),("demand_image","TEXT")]:
        try:
            c.execute(f"ALTER TABLE presets ADD COLUMN {col} {typ}")
        except Exception:
            pass
    conn.commit()
    conn.close()
    init_default_config()
    init_default_presets()

def init_default_config():
    conn = get_db()
    for k, v in DEFAULT_CONFIG.items():
        conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", (k, v))
    conn.commit()
    conn.close()

def init_default_presets():
    presets = [
        ("价格表", 0.15, "", json.dumps([
            {"key": "name", "label": "网站名称", "placeholder": "请输入网站名称"},
            {"key": "url", "label": "网站链接", "placeholder": "请输入网站链接"},
            {"key": "title", "label": "标题", "placeholder": "请输入标题"},
            {"key": "service", "label": "客服", "placeholder": "请输入客服"},
            {"key": "prices", "label": "价格表", "placeholder": "请输入价格表内容"},
        ]), 1),
        ("简约蓝", 0.15, "", json.dumps([
            {"key": "title", "label": "标题", "placeholder": "请输入标题"},
            {"key": "content", "label": "内容", "placeholder": "请输入内容"},
            {"key": "link", "label": "链接", "placeholder": "请输入链接"},
        ]), 2),
        ("收款图", 0.15, "", json.dumps([
            {"key": "title", "label": "标题", "placeholder": "请输入标题"},
            {"key": "amount", "label": "金额", "placeholder": "请输入金额"},
            {"key": "address", "label": "收款地址", "placeholder": "请输入收款地址"},
        ]), 3),
        ("封面图", 0.15, "", json.dumps([
            {"key": "title", "label": "标题", "placeholder": "请输入标题"},
            {"key": "subtitle", "label": "副标题", "placeholder": "请输入副标题"},
            {"key": "content", "label": "内容", "placeholder": "请输入内容"},
        ]), 4),
    ]
    conn = get_db()
    for name, price, img, fields, sort in presets:
        conn.execute(
            "INSERT OR IGNORE INTO presets(name,price,template_image,fields,sort_order) VALUES(?,?,?,?,?)",
            (name, price, img, fields, sort)
        )
    conn.commit()
    conn.close()

def get_config(key: str, default: str = "") -> str:
    """获取配置，自动修复不匹配HTML标签+解析会员图标"""
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    val = row["value"] if row else default
    return safe_html(val)

def get_config_raw(key: str, default: str = "") -> str:
    """获取原始配置（不解析emoji，用于按钮文字等不支持HTML的场景）"""
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_config(key: str, value: str):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (key, value))
    conn.commit()
    conn.close()

def get_draw_line() -> str:
    """获取当前生图线路：line1=KK大模型1.0(ApiNebula), line2=KK大模型2.0(TransitAI)"""
    return get_config_raw("draw_line", "line1") or "line1"

def get_line_base_url(line: str = None) -> str:
    """获取指定线路的API base URL"""
    if line is None:
        line = get_draw_line()
    return get_config_raw(f"{line}_base_url", "") or (LINE2_BASE_URL if line == "line2" else APINEBULA_BASE_URL)

def get_line_models(line: str = None) -> List[str]:
    """获取指定线路支持的模型列表"""
    if line is None:
        line = get_draw_line()
    return LINE2_MODELS if line == "line2" else LINE1_MODELS

def get_line_tokens_key(line: str = None) -> str:
    """获取指定线路的token配置key"""
    if line is None:
        line = get_draw_line()
    return "line2_tokens" if line == "line2" else "apinebula_tokens"

def get_line_model(line):
    model = get_config_raw(f"{line}_draw_model", "") or get_config_raw("draw_model", DRAW_MODEL_DEFAULT)
    return model if model in get_line_models(line) else DRAW_MODEL_DEFAULT

def get_line_timeout(line):
    try:
        return max(120, min(1800, int(get_config_raw(f"{line}_timeout", str(DRAW_TIMEOUT_SECONDS)))))
    except (ValueError, TypeError):
        return DRAW_TIMEOUT_SECONDS

def migrate_line_configs():
    # 旧模型迁移到各线路（不支持的模型回退默认）；既有独立配置不覆盖。
    for line in ("line1", "line2"):
        values = {f"{line}_draw_model": get_line_model(line),
                  f"{line}_base_url": get_line_base_url(line),
                  f"{line}_timeout": str(get_line_timeout(line))}
        conn = get_db()
        try:
            with conn:
                for key, value in values.items():
                    conn.execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", (key, value))
        finally:
            conn.close()


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
def get_free_draw_price(line: str = None) -> float:
    """获取自由作图价格（按线路，可后台配置）"""
    if line is None:
        line = get_draw_line()
    key = "price_free_line2" if line == "line2" else "price_free_line1"
    default = PRICE_FREE_LINE2 if line == "line2" else PRICE_FREE_LINE1
    try:
        return float(get_config_raw(key, str(default)) or default)
    except (ValueError, TypeError):
        return default

def get_preset_base_price(line: str = None) -> float:
    """获取预设基础费（按线路，可后台配置）"""
    if line is None:
        line = get_draw_line()
    key = "price_preset_line2" if line == "line2" else "price_preset_line1"
    default = PRICE_PRESET_LINE2 if line == "line2" else PRICE_PRESET_LINE1
    try:
        return float(get_config_raw(key, str(default)) or default)
    except (ValueError, TypeError):
        return default

def get_preset_price(line: str = None) -> float:
    """获取预设总价=基础费+协议费（按线路，可后台配置）"""
    return get_preset_base_price(line) + get_protocol_price()

def get_protocol_price() -> float:
    """获取协议叠加费（可后台配置）"""
    try:
        return float(get_config_raw("price_protocol", str(PRICE_PROTOCOL)) or PRICE_PROTOCOL)
    except (ValueError, TypeError):
        return PRICE_PROTOCOL

def get_checkin_reward() -> float:
    """获取签到奖励金额（可后台配置）"""
    try:
        return float(get_config_raw("checkin_reward_amount", str(CHECKIN_REWARD)) or CHECKIN_REWARD)
    except (ValueError, TypeError):
        return CHECKIN_REWARD

def get_all_config() -> Dict[str, str]:
    conn = get_db()
    rows = conn.execute("SELECT key,value FROM config").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

# ============================================================
# 工具函数
# ============================================================
def get_or_create_user(tg_user) -> Dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_user.id,)).fetchone()
    if row:
        # 更新用户名等
        conn.execute(
            "UPDATE users SET username=?, first_name=? WHERE tg_id=?",
            (tg_user.username, tg_user.first_name, tg_user.id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_user.id,)).fetchone()
        conn.close()
        return dict(row)
    # 新建
    bot_uid = 1000 + conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    invite_code = gen_invite_code()
    while conn.execute("SELECT 1 FROM users WHERE invite_code=?", (invite_code,)).fetchone():
        invite_code = gen_invite_code()
    conn.execute(
        """INSERT INTO users(tg_id,username,first_name,bot_user_id,invite_code,created_at)
           VALUES(?,?,?,?,?,?)""",
        (tg_user.id, tg_user.username, tg_user.first_name, bot_uid, invite_code, now_str())
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_user.id,)).fetchone()
    conn.close()
    return dict(row)

def get_user_by_tg_id(tg_id: int) -> Optional[Dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_invite_code(code: str) -> Optional[Dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE invite_code=?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_balance(tg_id: int, delta: float):
    conn = get_db()
    conn.execute("UPDATE users SET balance=ROUND(balance+?, 8) WHERE tg_id=?", (delta, tg_id))
    conn.commit()
    conn.close()

def increment_draws(tg_id: int):
    conn = get_db()
    conn.execute("UPDATE users SET total_draws=total_draws+1 WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()

def set_user_status(tg_id: int, status: str):
    conn = get_db()
    conn.execute("UPDATE users SET status=? WHERE tg_id=?", (status, tg_id))
    conn.commit()
    conn.close()

def is_banned(tg_id: int) -> bool:
    u = get_user_by_tg_id(tg_id)
    return u and u.get("status") == "banned"

# ============================================================
# 底部键盘
# ============================================================
def update_draw_task(task_id: str, **kwargs):
    conn = get_db()
    sets = ",".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [task_id]
    conn.execute(f"UPDATE draw_tasks SET {sets} WHERE task_id=?", vals)
    conn.commit()
    conn.close()

def get_draw_task(task_id: str) -> Optional[Dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM draw_tasks WHERE task_id=?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_history(tg_id: int, limit: int = 20) -> List[Dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM draw_tasks WHERE user_id=? AND status='completed' ORDER BY id DESC LIMIT ?",
        (tg_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ============================================================
# 订单DB操作
# ============================================================
def create_order(user_id: int, amount: float, pay_method: str, coin: str = "USDT") -> str:
    order_id = gen_order_id()
    expire = (datetime.datetime.now() + datetime.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute(
        """INSERT INTO orders(order_id,user_id,amount,coin,status,pay_method,created_at,expire_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (order_id, user_id, amount, coin, "pending", pay_method, now_str(), expire)
    )
    conn.commit()
    conn.close()
    return order_id

def get_order(order_id: str) -> Optional[Dict]:
    conn = get_db()
    row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_order(order_id: str, **kwargs):
    conn = get_db()
    sets = ",".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [order_id]
    conn.execute(f"UPDATE orders SET {sets} WHERE order_id=?", vals)
    conn.commit()
    conn.close()

def get_pending_orders() -> List[Dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders WHERE status='pending'").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ============================================================
# OkPay 支付
# ============================================================
def platform_db():
    c = sqlite3.connect(PLATFORM_DB, timeout=20)
    c.row_factory = sqlite3.Row
    # 原单文件版在 get_db 里开启过 WAL，重定义 get_db 后丢失，这里补回，降低并发写锁冲突
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA busy_timeout=20000')
    return c

class TenantCursor:
    def __init__(self, cursor): self.cursor = cursor
    def execute(self, sql, args=()):
        self.cursor.execute(tenant_sql(sql), args)
        return self
    def executescript(self, sql):
        self.cursor.executescript(tenant_sql(sql))
        return self
    def __getattr__(self, name): return getattr(self.cursor, name)
    def __iter__(self): return iter(self.cursor)

class TenantConnection:
    def __init__(self, connection): self.connection = connection
    def execute(self, sql, args=()): return TenantCursor(self.connection.cursor()).execute(sql, args)
    def executescript(self, sql): return TenantCursor(self.connection.cursor()).executescript(sql)
    def cursor(self): return TenantCursor(self.connection.cursor())
    def __enter__(self): self.connection.__enter__(); return self
    def __exit__(self, *args): return self.connection.__exit__(*args)
    def __getattr__(self, name): return getattr(self.connection, name)

def tenant_sql(sql):
    # Replace only bare SQL identifiers; leave quoted strings/identifiers intact.
    if not CLONE_ID: return sql
    pattern = r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\b(?:" + '|'.join(TENANT_TABLES) + r")\b"
    return re.sub(pattern, lambda m: f'c{CLONE_ID}_{m[0]}' if m[0] in TENANT_TABLES else m[0], sql)

def get_db(): return TenantConnection(platform_db())

def platform_config(key, default=''):
    with closing(platform_db()) as c:
        row = c.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
    return row[0] if row else default

_local_config_raw = get_config_raw

def get_config_raw(key, default=''):
    if CLONE_ID and key in UPSTREAM_KEYS: return platform_config(key, default)
    return _local_config_raw(key, default)

def get_config(key, default=''): return safe_html(get_config_raw(key, default))


def clone_record(cid=None):
    with closing(platform_db()) as c:
        r = c.execute('SELECT * FROM clone_registry WHERE id=?', (CLONE_ID if cid is None else cid,)).fetchone()
    return dict(r) if r else None

def platform_init():
    with closing(platform_db()) as c, c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS clone_registry(
          id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, bot_id INTEGER UNIQUE NOT NULL,
          username TEXT NOT NULL, token_cipher TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          runtime TEXT DEFAULT '等待启动', heartbeat REAL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS clone_credits(owner_id INTEGER PRIMARY KEY, credits INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS clone_purchases(request_id TEXT PRIMARY KEY, owner_id INTEGER, amount_micro INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS clone_charges(tenant_id INTEGER, task_id TEXT, owner_id INTEGER,
          line TEXT, amount_micro INTEGER, status TEXT, created_at TEXT, PRIMARY KEY(tenant_id,task_id));
        CREATE TABLE IF NOT EXISTS clone_audit(id INTEGER PRIMARY KEY AUTOINCREMENT, actor INTEGER, tenant_id INTEGER,
          action TEXT, created_at TEXT);
        ''')
        for k,v in {'clone_fee':'10','clone_cost_line1':'0.2','clone_cost_line2':'0.3',
                    'moderation_enabled':'1','moderation_words':'','moderation_policy':'只拦截明确的未成年人性内容、明确的严重违法伤害指令。普通艺术、商业设计和含糊表达放行。',
                    'moderation_fail_closed':'1'}.items():
            c.execute('INSERT OR IGNORE INTO config(key,value) VALUES(?,?)',(k,v))
    try: os.chmod(PLATFORM_DB,0o600)
    except OSError: pass

def tenant_migrate():
    with closing(get_db()) as c, c:
        cols = {r[1] for r in c.execute('PRAGMA table_info(draw_tasks)')}
        if 'line' not in cols: c.execute("ALTER TABLE draw_tasks ADD COLUMN line TEXT DEFAULT 'line1'")

def clone_cipher():
    from cryptography.fernet import Fernet
    path = pathlib.Path(PLATFORM_DB).with_name('clone_secret.key')
    if not path.exists():
        fd = os.open(str(path), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd,'wb') as f: f.write(Fernet.generate_key())
    return Fernet(path.read_bytes())

def audit(c, actor, cid, action):
    c.execute('INSERT INTO clone_audit(actor,tenant_id,action,created_at) VALUES(?,?,?,?)',(actor,cid,action,now_str()))

def money_micro(value):
    from decimal import Decimal
    v=Decimal(str(value))
    if not v.is_finite() or v<0 or v>1000000: raise ValueError('金额无效')
    return int((v*1000000).quantize(Decimal('1')))

def buy_clone_credit(uid, request_id):
    fee=money_micro(platform_config('clone_fee','10'))
    with closing(platform_db()) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM clone_purchases WHERE request_id=? AND owner_id=?',(request_id,uid)).fetchone(): return '该笔开通已支付，请提交 Token。'
        if c.execute("UPDATE users SET balance=ROUND(balance-?,8) WHERE tg_id=? AND status='normal' AND balance>=?",(fee/1e6,uid,fee/1e6)).rowcount!=1:
            return '主站余额不足，请先余额充值，到账后再确认开通。'
        c.execute('INSERT INTO clone_purchases VALUES(?,?,?,?)',(request_id,uid,fee,now_str()))
        c.execute('INSERT INTO clone_credits VALUES(?,1) ON CONFLICT(owner_id) DO UPDATE SET credits=credits+1',(uid,))
        audit(c,uid,0,'购买克隆名额')
    return '支付成功，获得一个克隆名额。点击提交 Token 创建机器人。'

def clone_credit(uid):
    with closing(platform_db()) as c:
        r=c.execute('SELECT credits FROM clone_credits WHERE owner_id=?',(uid,)).fetchone()
    return r[0] if r else 0


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创