#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
"""支付对接：OkPay、TON-USDT、TRON-USDT 的地址校验与链上查询。"""
import re
import time
import base64
import hashlib
import hmac
import secrets
import binascii
import asyncio
import logging
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, List, Any

import aiohttp
import requests
import urllib3

from .config import *
from .text import *
from .db import *
from .utils import *

class OkayPay:
    def __init__(self, appid, token, api_url):
        self.appid = str(appid)
        self.token = str(token)
        self.api_url = api_url.rstrip("/") + "/"

    def _flatten(self, data, prefix=""):
        out = {}
        for k, v in data.items():
            key = str(k) if prefix == "" else f"{prefix}.{k}"
            if isinstance(v, dict):
                out.update(self._flatten(v, key))
                continue
            if isinstance(v, bool):
                out[key] = "true" if v else "false"
                continue
            if v is None or v == "":
                continue
            out[key] = str(v)
        return out

    def _build_base(self, params):
        data = {k: v for k, v in params.items() if k != "sign"}
        flat = self._flatten(data)
        sorted_keys = sorted(flat.keys())
        return "&".join(f"{k}={flat[k]}" for k in sorted_keys)

    def _sign(self, params):
        base = self._build_base(params).encode("utf-8")
        return hmac.new(self.token.encode("utf-8"), base, hashlib.sha256).hexdigest().upper()

    def _signed_request(self, params):
        p = dict(params)
        p["id"] = self.appid
        p["timestamp"] = int(time.time())
        p["nonce"] = secrets.token_hex(8)
        p["sign"] = self._sign(p)
        return p

    def _request(self, endpoint, params):
        submit_params = self._signed_request(params)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        url = self.api_url + endpoint
        try:
            log.info(f"[OkPay] 请求 {endpoint}")
            resp = requests.post(url, data=submit_params, headers=headers, timeout=15, verify=False)
            result = resp.json()
            log.info(f"[OkPay] 响应: {result}")
            return result
        except Exception as e:
            log.error(f"[OkPay] 请求异常: {e}")
            return {"code": -1, "error": str(e)}

    async def pay_link(self, amount, unique_id, name="余额充值", coin="USDT", callback_url=None, return_url=None):
        params = {"unique_id": unique_id, "name": name, "amount": str(amount), "coin": coin}
        if callback_url:
            params["callback_url"] = callback_url
        if return_url:
            params["return_url"] = return_url
        return await asyncio.to_thread(self._request, "payLink", params)

    async def check_deposit(self, unique_id):
        return await asyncio.to_thread(self._request, "checkDeposit", {"unique_id": unique_id})

def get_okpay_client() -> Optional[OkayPay]:
    if get_config("okpay_enabled") != "1":
        return None
    appid = get_config("okpay_appid")
    token = get_config("okpay_token")
    if not appid or not token:
        return None
    return OkayPay(appid, token, get_config("okpay_api_url", "https://api.okaypay.me/shop/"))

# ============================================================
# TON / TRON 支付（完整区块监听，从商城移植）
# ============================================================
TON_USDT_JETTON_MASTER = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
TONCENTER_BASE = "https://toncenter.com"
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONGRID_BASE = "https://api.trongrid.io"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def _b58decode(s: str) -> bytes:
    num = 0
    for ch in s:
        num = num * 58 + _B58.index(ch)
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body

def is_valid_tron_address(addr: str) -> bool:
    if not addr or len(addr) != 34 or not addr.startswith("T"):
        return False
    try:
        raw = _b58decode(addr)
    except (ValueError, IndexError):
        return False
    if len(raw) != 25 or raw[0] != 0x41:
        return False
    import hashlib
    checksum = hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4]
    return checksum == raw[21:]

def ton_raw_address(addr):
    import binascii
    import re
    if re.fullmatch(r"(?:0|-1):[0-9a-fA-F]{64}", addr or ""):
        return addr.lower()
    raw = base64.b64decode(addr, altchars=b"-_", validate=True)
    if len(raw) != 36 or raw[0] not in (0x11, 0x51):
        raise ValueError("非TON主网地址")
    if binascii.crc_hqx(raw[:34], 0) != int.from_bytes(raw[34:], "big"):
        raise ValueError("TON地址校验和错误")
    return f"{int.from_bytes(raw[1:2], 'big', signed=True)}:{raw[2:34].hex()}"

def is_valid_ton_address(addr):
    try:
        ton_raw_address(addr)
        return True
    except Exception:
        return False

def ton_same_address(a, b):
    try:
        return ton_raw_address(a) == ton_raw_address(b)
    except Exception:
        return False

def chain_order_window(order):
    try:
        start = datetime.datetime.strptime(order["created_at"], "%Y-%m-%d %H:%M:%S").timestamp()
        end = datetime.datetime.strptime(order["expire_at"], "%Y-%m-%d %H:%M:%S").timestamp()
        return start, end
    except (ValueError, KeyError, TypeError):
        return 0, 0

async def fetch_ton_usdt_incoming(address: str, api_key: str = "", limit: int = 100, start_time: int = 0) -> list:
    from decimal import Decimal
    params = {"destination_wallet": address, "jetton_master": TON_USDT_JETTON_MASTER,
              "direction": "in", "limit": str(limit), "sort": "asc", "start_utime": int(start_time)}
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{TONCENTER_BASE}/api/v3/jetton/transfers", params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                payload = await resp.json()
            records = list(payload.get("jetton_transfers") or [])
            for page in range(1, 20):
                if len(payload.get("jetton_transfers") or []) < limit:
                    break
                params["offset"] = page * limit
                await asyncio.sleep(1.1)
                async with session.get(f"{TONCENTER_BASE}/api/v3/jetton/transfers", params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()
                records.extend(payload.get("jetton_transfers") or [])
            else:
                raise RuntimeError("TON扫描达到分页上限，需要人工核对或缩小扫描窗口")
            payload = {"jetton_transfers": records}
    except Exception as e:
        log.warning(f"[TON-USDT] 拉取入账失败: {e}")
        return []
    out = []
    for item in payload.get("jetton_transfers") or []:
        if not ton_same_address(item.get("jetton_master", ""), TON_USDT_JETTON_MASTER):
            continue
        # destination是接收方owner；destination_wallet可能指Jetton钱包，不能混用。
        destination = item.get("destination")
        if destination and not ton_same_address(destination, address):
            continue
        if item.get("transaction_aborted") is not False:
            # 缺失执行状态也不冒险自动入账。
            continue
        out.append({
            "txid": item["transaction_hash"],
            "amount": Decimal(str(item["amount"])) / Decimal(10) ** 6,
            "sender": item.get("source", ""),
            "timestamp": int(item.get("transaction_now", 0)),
            "memo": str(item.get("comment") or (item.get("decoded_forward_payload") or {}).get("comment") or "").strip(),
        })
    out.reverse()
    return out


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
async def fetch_tron_usdt_incoming(address: str, api_key: str = "", limit: int = 200, start_time: int = 0) -> list:
    from decimal import Decimal
    params = {"only_to": "true", "contract_address": TRON_USDT_CONTRACT,
              "limit": str(limit), "order_by": "block_timestamp,asc", "only_confirmed": "true", "min_timestamp": int(start_time * 1000)}
    headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{TRONGRID_BASE}/v1/accounts/{address}/transactions/trc20", params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                payload = await resp.json()
            records = list(payload.get("data") or [])
            for page in range(20):
                fingerprint = (payload.get("meta") or {}).get("fingerprint")
                if not fingerprint:
                    break
                params["fingerprint"] = fingerprint
                await asyncio.sleep(0.3)
                async with session.get(f"{TRONGRID_BASE}/v1/accounts/{address}/transactions/trc20", params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()
                records.extend(payload.get("data") or [])
            else:
                raise RuntimeError("TRON扫描达到分页上限，请人工核对")
            payload = {"data": records}
    except Exception as e:
        log.warning(f"[TRON-USDT] 拉取入账失败: {e}")
        return []
    out = []
    for item in payload.get("data") or []:
        if (item.get("token_info") or {}).get("address") != TRON_USDT_CONTRACT:
            continue
        if item.get("to") != address:
            continue
        decimals = int(item["token_info"].get("decimals", 6))
        if decimals != 6 or item.get("type", "Transfer") != "Transfer":
            continue
        out.append({
            "txid": item["transaction_id"],
            "amount": Decimal(str(item["value"])) / (Decimal(10) ** decimals),
            "sender": item.get("from", ""),
            "timestamp": int(item.get("block_timestamp", 0)) // 1000,
        })
    return out

def init_chain_payments():
    conn = get_db()
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS processed_txs(txid TEXT PRIMARY KEY, order_id TEXT NOT NULL, created_at TEXT NOT NULL)")
            # 历史已付订单也加入去重，避免升级后重放已入账交易。
            conn.execute("INSERT OR IGNORE INTO processed_txs SELECT pay_method || ':' || txid, order_id, COALESCE(paid_at, created_at) FROM orders WHERE status='paid' AND COALESCE(txid,'')<>''")
    finally:
        conn.close()

def reserve_tron_amount(order_id, address):
    from decimal import Decimal
    conn = get_db()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE order_id=? AND status='pending'", (order_id,)).fetchone()
            if not row:
                raise ValueError("订单不存在或已失效")
            if row["tron_expected_amount"]:
                return row["tron_expected_amount"]
            base = Decimal(str(row["amount"]))
            if not base.is_finite() or base <= 0:
                raise ValueError("订单金额无效")
            # 保留最近24小时金额，降低过期订单迟付误匹配新订单的风险。
            cutoff = (datetime.datetime.now()-datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            used = {Decimal(r[0]) for r in conn.execute("SELECT tron_expected_amount FROM orders WHERE pay_method='tron_usdt' AND COALESCE(tron_expected_amount,'')<>'' AND (status='pending' OR created_at>=?)", (cutoff,))}
            choices = list(range(1,1000))
            random.SystemRandom().shuffle(choices)
            for suffix in choices:
                expected = base + Decimal(suffix)/1000
                if expected in used:
                    continue
                value = format(expected, 'f')
                conn.execute("UPDATE orders SET pay_address=?,tron_expected_amount=?,tron_suffix=?,tron_checked=0 WHERE order_id=?", (address,value,suffix,order_id))
                return value
            raise ValueError("当前支付金额池已满，请稍后再试")
    finally:
        conn.close()

async def send_ton_usdt_order(update, context, order, amount, msg_id):
    """TON链USDT支付：显示收款地址+金额+备注单号"""
    from decimal import Decimal
    query = update.callback_query
    address = get_config("ton_usdt_address", "")
    if not is_valid_ton_address(address):
        await safe_edit_text(context, update.effective_chat.id, msg_id, "❌ TON-USDT 支付未配置收款地址，请联系管理员")
        return
    memo = f"TON_{order['order_id']}"
    update_order(order["order_id"], pay_address=address, ton_memo=memo, ton_amount=str(amount), ton_checked=0)
    text = fmt(get_config("ton_pay_text"), order_id=order["order_id"], amount=format_amount(amount),
               memo=memo, address=address)
    keyboard = InlineKeyboardMarkup([
        [IButtonCfg("btn_refresh_pay", callback_data=f"pay_refresh?oid={order['order_id']}")],
        [IButtonCfg("btn_close_pay", callback_data=f"pay_close?oid={order['order_id']}")],
    ])
    from .handlers import show_pay_page
    from .handlers import show_pay_page
    from .handlers import show_pay_page
    from .handlers import show_pay_page
    await show_pay_page(context, update.effective_chat.id, msg_id, "ton_pay_text", text, keyboard)
    log.info(f"[TON-USDT] 订单 {order['order_id']} 创建，金额={amount} USDT，memo={memo}")

async def send_tron_usdt_order(update, context, order, amount, msg_id):
    """TRON链USDT支付：分配金额尾数，显示收款地址+金额"""
    from decimal import Decimal
    query = update.callback_query
    address = get_config("tron_usdt_address", "")
    if not is_valid_tron_address(address):
        await safe_edit_text(context, update.effective_chat.id, msg_id, "❌ TRON-USDT 支付未配置收款地址，请联系管理员")
        return
    try:
        expected_str = reserve_tron_amount(order["order_id"], address)
    except ValueError as exc:
        await safe_edit_text(context, update.effective_chat.id, msg_id, str(exc))
        return
    text = fmt(get_config("tron_pay_text"), order_id=order["order_id"], amount=expected_str, address=address)
    keyboard = InlineKeyboardMarkup([
        [IButtonCfg("btn_refresh_pay", callback_data=f"pay_refresh?oid={order['order_id']}")],
        [IButtonCfg("btn_close_pay", callback_data=f"pay_close?oid={order['order_id']}")],
    ])
    from .handlers import show_pay_page
    from .handlers import show_pay_page
    from .handlers import show_pay_page
    from .handlers import show_pay_page
    await show_pay_page(context, update.effective_chat.id, msg_id, "tron_pay_text", text, keyboard)
    log.info(f"[TRON-USDT] 订单 {order['order_id']} 创建，金额={expected_str} USDT")

async def check_chain_order(context, order_id, method, chat_id=None, msg_id=None):
    from decimal import Decimal
    order = get_order(order_id)
    if not order or order.get("status") != "pending" or order.get("pay_method") != method:
        return False
    start, end = chain_order_window(order)
    if not start or time.time() > end:
        return False
    address = order.get("pay_address")  # 使用下单时快照，而非可能被管理员改过的新地址。
    if not address:
        return False
    if method == "ton_usdt":
        if not order.get("ton_memo") or not order.get("ton_amount"):
            return False
        txs = await fetch_ton_usdt_incoming(address, get_config_raw("ton_api_key", ""), start_time=int(start))
        expected = Decimal(str(order["ton_amount"]))
    else:
        if not order.get("tron_expected_amount"):
            return False
        txs = await fetch_tron_usdt_incoming(address, get_config_raw("tron_api_key", ""), start_time=int(start))
        expected = Decimal(str(order["tron_expected_amount"]))
    for tx in txs:
        if not tx.get("txid") or not start <= tx["timestamp"] <= end:
            continue
        if method == "ton_usdt":
            if tx["memo"] != order["ton_memo"] or tx["amount"] < expected:
                continue
        elif tx["amount"] != expected:
            continue
        from .handlers import on_payment_success_raw
        from .handlers import on_payment_success_raw
        from .handlers import on_payment_success_raw
        from .handlers import on_payment_success_raw
        if await on_payment_success_raw(context, order, chat_id, msg_id, tx["txid"]):
            return True
    return False

async def check_ton_usdt_order(context, order_id, chat_id=None, msg_id=None):
    return await check_chain_order(context, order_id, "ton_usdt", chat_id, msg_id)

async def check_tron_usdt_order(context, order_id, chat_id=None, msg_id=None):
    return await check_chain_order(context, order_id, "tron_usdt", chat_id, msg_id)

# ============================================================
# 生图执行（后台任务）
# ============================================================

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创