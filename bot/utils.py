#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
"""通用工具：时间、金额、按钮、会话状态与安全编辑。"""
import os
import re
import json
import time
import random
import string
import logging
import unicodedata
from typing import Optional, Tuple, List, Dict, Any

from telegram import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest, TimedOut

from .config import *
from .text import *
from .text import _strip_html_tags
from .db import *
from .db import _local_config_raw

def home_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KButtonCfg("btn_profile"), KButtonCfg("btn_free_draw")],
        [KButtonCfg("btn_checkin"), KButtonCfg("btn_recharge")],
        [KButtonCfg("btn_example")],
    ], resize_keyboard=True, is_persistent=True, input_field_placeholder=get_config_raw("input_placeholder"))

def draw_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KButtonCfg("btn_preset"), KButtonCfg("btn_free")],
        [KButtonCfg("btn_my_design"), KButtonCfg("btn_recharge")],
        [KButtonCfg("btn_back_home")],
    ], resize_keyboard=True, is_persistent=True, input_field_placeholder=get_config_raw("input_placeholder"))

# ============================================================
# 内联按钮构建
# ============================================================
def parse_action(data: str) -> Tuple[str, Dict[str, str]]:
    if "?" in data:
        action, qs = data.split("?", 1)
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
    else:
        action, params = data, {}
    return action, params

def IButton(text: str, color: str = "", **kwargs) -> InlineKeyboardButton:
    """内联按钮包装：自动提取 [emoji:ID] 设置 icon_custom_emoji_id，支持按钮颜色
    color: primary/secondary/success/danger（Telegram Bot API 7.0+，仅内联按钮有效）"""
    clean_text, emoji_id = extract_button_emoji(text)
    api_kwargs = dict(kwargs.pop("api_kwargs", None) or {})
    if emoji_id:
        api_kwargs["icon_custom_emoji_id"] = str(emoji_id)
    if color in ("primary", "success", "danger"):
        api_kwargs["style"] = color
    if api_kwargs:
        kwargs["api_kwargs"] = api_kwargs
    return InlineKeyboardButton(text=clean_text or "按钮", **kwargs)

def KButton(text: str, color: str = "", **kwargs) -> KeyboardButton:
    """底部键盘按钮包装：自动提取 [emoji:ID] 设置 icon_custom_emoji_id，支持按钮颜色
    color: primary/secondary/success/danger（通过api_kwargs传递style）
    底部键盘不支持HTML，自动去掉所有HTML标签（如blockquote、b、i等）"""
    clean_text, emoji_id = extract_button_emoji(text)
    # 底部键盘按钮不支持HTML标签，去掉所有标签（保留标签内文字）
    clean_text = _strip_html_tags(clean_text)
    api_kwargs = dict(kwargs.pop("api_kwargs", None) or {})
    if emoji_id:
        api_kwargs["icon_custom_emoji_id"] = str(emoji_id)
    if color in ("primary", "success", "danger"):
        api_kwargs["style"] = color
    if api_kwargs:
        kwargs["api_kwargs"] = api_kwargs
    return KeyboardButton(text=clean_text or "按钮", **kwargs)


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
def KButtonCfg(key: str, **kwargs) -> KeyboardButton:
    """从配置读取底部键盘按钮：文字+颜色+会员图标"""
    text = get_config_raw(key, key)
    color = kwargs.pop("color", get_config_raw(f"{key}_color", ""))
    return KButton(text, color=color, **kwargs)

def IButtonCfg(key: str, **kwargs) -> InlineKeyboardButton:
    """从配置读取按钮：文字=btn_xxx，颜色=btn_xxx_color，链接=btn_xxx_url（如有）
    自动应用会员图标和颜色。如果配置了url且没有传callback_data，则用url按钮。"""
    text = get_config_raw(key, key)
    color = kwargs.pop("color", get_config_raw(f"{key}_color", ""))
    url = get_config_raw(f"{key}_url", "")
    # 如果传了callback_data就用callback，否则如果有url就用url
    if "callback_data" not in kwargs and "url" not in kwargs and url:
        kwargs["url"] = url
    return IButton(text, color=color, **kwargs)

# ============================================================
# 状态管理（用户会话状态）
# ============================================================
user_states: Dict[int, Dict] = {}

def set_state(tg_id: int, state: str, **kwargs):
    user_states[tg_id] = {"state": state, **kwargs}

def get_state(tg_id: int) -> Optional[Dict]:
    return user_states.get(tg_id)

def clear_state(tg_id: int):
    user_states.pop(tg_id, None)

# ============================================================
# 生图任务队列（5Token并发）
# ============================================================
async def safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

def _mask_user_id(uid: int) -> str:
    """用户ID脱敏：前3后3，中间***"""
    s = str(uid)
    if len(s) <= 6:
        return s
    return f"{s[:3]}***{s[-3:]}"

def is_admin(uid):
    if not CLONE_ID: return uid in PLATFORM_ADMINS
    if uid == CLONE_OWNER: return True
    try: return uid in json.loads(_local_config_raw('tenant_admin_ids', '[]'))
    except (ValueError, TypeError): return False

_original_home_keyboard = home_keyboard

def _clone_label(key):
    """惰性导入 platform，避免 utils ↔ platform 循环依赖。"""
    from .platform import clone_label
    return clone_label(key)


def home_keyboard():
    if CLONE_ID: return _original_home_keyboard()
    return ReplyKeyboardMarkup([[KButtonCfg('btn_profile'),KButtonCfg('btn_free_draw')],
        [KButton(_clone_label('btn_clone_entry')),KButtonCfg('btn_recharge')],[KButtonCfg('btn_example')]],resize_keyboard=True,is_persistent=True)


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询