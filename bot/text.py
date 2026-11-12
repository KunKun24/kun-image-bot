#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
"""底层工具：时间/编号生成、文本截断、金额格式化、HTML 转义与 Telegram 实体转换。

本模块不允许依赖 db / utils / drawing 等上层模块，避免循环导入。
"""
import re
import html
import time
import random
import string
import secrets
import datetime
import unicodedata
from typing import Optional, Tuple, List, Dict, Any

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest, BadRequest, BadRequest, BadRequest

from .config import *

def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def now_ts() -> int:
    return int(time.time())

def gen_order_id() -> str:
    return "PAY" + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + \
           f"{now_ts() % 100000:05d}" + secrets.token_hex(3)

def gen_task_id() -> str:
    return "DRAW" + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + \
           f"{random.randint(0, 999999):06d}"

def gen_invite_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=10))

def truncate_text(text: str, max_len: int = 20) -> str:
    if not text:
        return ""
    return text[:max_len] + "..." if len(text) > max_len else text

def truncate_first_name(name: str, max_len: int = 10) -> str:
    if not name:
        return ""
    return name[:max_len] + "..." if len(name) > max_len else name

def safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def format_amount(v) -> str:
    return f"{safe_float(v):.2f}"

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def build_user_info(user) -> str:
    name = user.first_name or ""
    uname = f"@{user.username}" if user.username else "无用户名"
    return f"{name} ({uname} - {user.id})"

def size_to_resolution(size: str, ref_size: str = "") -> str:
    mapping = {
        "1:1": "1024x1024",
        "3:2": "1536x1024",
        "2:3": "1024x1536",
        "auto": "auto",
        "original": ref_size or "auto",
    }
    return mapping.get(size, "auto")

# ============================================================
# 用户管理
# ============================================================
def fmt(text: str, **kwargs) -> str:
    """安全格式化：用replace替代format，避免文案中含{ }导致报错
    值自动HTML转义（防止XSS），如需传入HTML标签请用 fmt_raw()"""
    if not text:
        return text
    import html as _html
    for k, v in kwargs.items():
        text = text.replace("{" + k + "}", _html.escape(str(v)))
    return text

_fmt_html_cache = {}

def fmt_html(text: str) -> str:
    """HTML格式化：先转义特殊字符，再解析 [emoji:ID] 会员图标，带缓存
    用于纯文本内容（非管理员配置文案）需要显示为HTML的场景"""
    if not text:
        return text
    if text in _fmt_html_cache:
        return _fmt_html_cache[text]
    import html as _html
    result = _html.escape(text)
    import re
    def _replace(m):
        eid = m.group(1).strip()
        return f'<tg-emoji emoji-id="{eid}">✨</tg-emoji>'
    result = re.sub(r'\[emoji:+([^\]]+)\]', _replace, result)
    result = re.sub(r'emoji:{1,2}(\d+)(?::[^\s]+)?', _replace, result)
    if len(_fmt_html_cache) > 500:
        _fmt_html_cache.clear()
    _fmt_html_cache[text] = result
    return result

def render_custom_emoji(text: str) -> str:
    """仅解析 [emoji:ID] 会员图标，不做HTML转义
    用于管理员配置文案（文案本身已含合法HTML标签）"""
    if not text:
        return text
    import re
    def _replace(m):
        eid = m.group(1).strip()
        return f'<tg-emoji emoji-id="{eid}">✨</tg-emoji>'
    text = re.sub(r'\[emoji:+([^\]]+)\]', _replace, text)
    text = re.sub(r'emoji:{1,2}(\d+)(?::[^\s]+)?', _replace, text)
    return text

# 兼容旧调用名

def message_to_html(message) -> str:
    """将Telegram消息的所有格式实体转换为HTML标签字符串
    支持：bold/italic/code/pre/quote/spoiler/underline/strikethrough/text_link/custom_emoji
    用UTF-16-LE编码处理offset，避免代理对字符导致的offset错位
    """
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if not entities:
        return text
    # 按offset从后往前替换
    encoded = text.encode('utf-16-le')
    # 收集所有实体替换
    replacements = []
    for e in entities:
        start_byte = e.offset * 2
        end_byte = (e.offset + e.length) * 2
        content_bytes = encoded[start_byte:end_byte]
        content = content_bytes.decode('utf-16-le')
        etype = e.type
        if etype == "bold":
            repl = f"<b>{content}</b>"
        elif etype == "italic":
            repl = f"<i>{content}</i>"
        elif etype == "code":
            repl = f"<code>{content}</code>"
        elif etype == "pre":
            repl = f"<pre>{content}</pre>"
        elif etype == "quote":
            repl = f"<blockquote>{content}</blockquote>"
        elif etype == "spoiler":
            repl = f"<tg-spoiler>{content}</tg-spoiler>"
        elif etype == "underline":
            repl = f"<u>{content}</u>"
        elif etype == "strikethrough":
            repl = f"<s>{content}</s>"
        elif etype == "text_link" and e.url:
            repl = f'<a href="{e.url}">{content}</a>'
        elif etype == "custom_emoji" and e.custom_emoji_id:
            repl = f"[emoji:{e.custom_emoji_id}]"
        else:
            continue
        replacements.append((start_byte, end_byte, repl.encode('utf-16-le')))
    # 从后往前替换
    for start, end, repl in sorted(replacements, key=lambda x: -x[0]):
        encoded = encoded[:start] + repl + encoded[end:]
    return encoded.decode('utf-16-le')

_emoji_strip_re = None

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
def _strip_leading_emoji(text: str) -> str:
    """去掉文字开头的普通emoji（避免和会员图标重复显示）"""
    global _emoji_strip_re
    if _emoji_strip_re is None:
        import re
        _emoji_strip_re = re.compile(
            r'^[\U0001F300-\U0001FAFF\U0001F000-\U0001F2FF\u2600-\u27BF\u2300-\u23FF\u2B00-\u2BFF\ufe0f\u200d]+'
        )
    return _emoji_strip_re.sub('', text).strip()

def extract_button_emoji(text: str) -> Tuple[str, Optional[str]]:
    """从按钮文字中提取 [emoji:ID]，返回 (清理后的文字, emoji_id)
    用于 InlineKeyboardButton / KeyboardButton 的 icon_custom_emoji_id
    兼容 [emoji:ID]、[emoji::ID]、emoji::ID（旧格式）、<tg-emoji emoji-id="ID">"""
    if not text:
        return text, None
    import re
    custom_emoji = ""
    m = re.search(r'\[emoji:+(\d+)\]', text)
    if not m:
        m = re.search(r'<tg-emoji[^>]*emoji-id="(\d+)"[^>]*>', text)
    if not m:
        m = re.search(r'emoji:{1,2}(\d+)', text)
    if m:
        custom_emoji = m.group(1)
        clean = text.replace(m.group(0), "").strip()
        clean = _strip_leading_emoji(clean)
        return clean, custom_emoji
    return text, None

def _strip_html_tags(text: str) -> str:
    """去除所有HTML标签，返回纯文本（终极降级）"""
    import re
    return re.sub(r'<[^>]+>', '', text)

# Telegram支持的HTML标签白名单
_ALLOWED_TAGS = {"b","strong","i","em","u","ins","s","strike","del","code","pre",
                 "blockquote","tg-spoiler","a","tg-emoji","br"}

def fix_html_tags(text: str) -> str:
    """自动修复不匹配的HTML标签：
    1. 补全缺失的结束标签
    2. 去掉多余的结束标签
    3. 防呆：遇到开始标签且栈顶已有同类型标签时，自动修正为结束标签（用户手写<code>xxx<code>的情况）
    确保Telegram HTML解析不会报错"""
    if not text or "<" not in text:
        return text
    import re
    tag_pattern = re.compile(r'<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>')
    stack = []  # tag_name
    result = []
    last_end = 0
    for m in tag_pattern.finditer(text):
        # 标签前的普通文本
        result.append(text[last_end:m.start()])
        last_end = m.end()
        is_close = bool(m.group(1))
        tag_name = m.group(2).lower()
        if tag_name not in _ALLOWED_TAGS:
            # 非白名单标签，原样保留（不处理）
            result.append(m.group(0))
            continue
        if is_close:
            # 结束标签：找栈中匹配的开始标签
            found = False
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == tag_name:
                    del stack[i]
                    found = True
                    break
            if found:
                result.append(m.group(0))
            # 多余的结束标签，丢弃
        else:
            # 开始标签
            if tag_name == "br":
                result.append("<br>")
                continue
            # 防呆：如果栈顶已经是同类型开始标签，说明用户把结束标签写成了开始标签（如<code>xxx<code>）
            if stack and stack[-1] == tag_name:
                result.append(f"</{tag_name}>")
                stack.pop()
            else:
                # 保留原始标签（含属性如 href、emoji-id），不要丢弃属性
                result.append(m.group(0))
                stack.append(tag_name)
    # 剩余文本
    result.append(text[last_end:])
    # 栈里剩下的开始标签，在末尾补全
    if stack:
        result.append(''.join(f'</{t}>' for t in reversed(stack)))
    return ''.join(result)

def safe_html(text: str) -> str:
    """安全HTML：修复不匹配标签 + 解析会员图标，用于管理员配置文案"""
    return render_custom_emoji(fix_html_tags(text))

_MESSAGE_EDIT_KIND = {}

async def safe_edit_text(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int,
                         text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    key = (chat_id, message_id)
    kind = _MESSAGE_EDIT_KIND.get(key, "text")
    fixed = fix_html_tags(text) if parse_mode == ParseMode.HTML else text
    variants = [text] if fixed == text else [text, fixed]
    for content in variants:
        for attempt in range(2):
            try:
                if kind == "caption":
                    await context.bot.edit_message_caption(chat_id=chat_id, message_id=message_id,
                        caption=content, parse_mode=parse_mode, reply_markup=reply_markup)
                else:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                        text=content, parse_mode=parse_mode, reply_markup=reply_markup)
                if len(_MESSAGE_EDIT_KIND) >= 10000:
                    _MESSAGE_EDIT_KIND.clear()
                _MESSAGE_EDIT_KIND[key] = kind
                return True
            except BadRequest as exc:
                err = str(exc).lower()
                if "message is not modified" in err:
                    _MESSAGE_EDIT_KIND[key] = kind
                    return True
                if "no text in the message" in err:
                    kind = "caption"
                    _MESSAGE_EDIT_KIND[key] = kind
                    continue
                if "no caption" in err:
                    kind = "text"
                    continue
                break
            except TelegramError as exc:
                log.warning("编辑消息失败: %s", exc)
                break
    try:
        await context.bot.send_message(chat_id=chat_id, text=fixed,
                                      parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except BadRequest:
        try:
            await context.bot.send_message(chat_id=chat_id, text=_strip_html_tags(fixed), reply_markup=reply_markup)
            return True
        except TelegramError as exc:
            log.error("纯文本降级发送失败: %s", exc)
    except TelegramError as exc:
        log.error("发送失败消息失败: %s", exc)
    return False
# ============================================================
# 通用：发送带可选配图的文案（所有文案都支持配图）
# ============================================================

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询