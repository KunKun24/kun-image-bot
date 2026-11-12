#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
"""Telegram 处理层：命令、键盘、回调、管理员后台与定时任务。"""
import os
import re
import html
import json
import time
import string
import asyncio
import logging
from typing import Optional, Tuple, List, Dict, Any

from flask import Flask, request, jsonify

from telegram import (
    Update, Bot, InputMediaPhoto, BotCommand, BotCommandScopeChat,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError, BadRequest, TimedOut

from .config import *
from .text import *
from .db import *
from .utils import *
from .drawing import *
from .payments import *

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text(get_config("banned_text"))
        return
    u = get_or_create_user(user)
    # 处理邀请参数
    args = context.args
    invite_msg = None
    if args and len(args) == 1:
        invite_code = args[0]
        if invite_code == u.get("invite_code"):
            # 自己邀请自己
            invite_msg = get_config("invite_self_text")
        elif u.get("invited_by"):
            # 已被邀请过
            invite_msg = get_config("invite_already_text")
        else:
            inviter = get_user_by_invite_code(invite_code)
            if inviter:
                conn = get_db()
                conn.execute("UPDATE users SET invited_by=? WHERE tg_id=?", (inviter["tg_id"], user.id))
                conn.commit()
                conn.close()
                invite_msg = get_config("invite_success_text")
    text = get_config("welcome_text")
    if invite_msg:
        # 邀请提示作为单独消息发送，然后显示欢迎页
        await update.message.reply_text(invite_msg, parse_mode=ParseMode.HTML)
    await send_config_photo(update, context, "welcome_text", text=text, reply_markup=home_keyboard())

# ============================================================
# /qd 签到
# ============================================================
async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text(get_config("banned_text"))
        return
    u = get_or_create_user(user)
    if u.get("checkin_at"):
        text = get_config("checkin_already")
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return
    # 签到
    update_balance(user.id, get_checkin_reward())
    conn = get_db()
    conn.execute("UPDATE users SET checkin_at=? WHERE tg_id=?", (now_str(), user.id))
    conn.commit()
    conn.close()
    u2 = get_user_by_tg_id(user.id)
    text = fmt(get_config("checkin_success"), balance=format_amount(u2["balance"]))
    await send_config_photo(update, context, "checkin_success", text=text)

# ============================================================
# 底部键盘消息处理
# ============================================================
async def handle_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message or not update.message.text:
        return
    text = update.message.text
    if is_banned(user.id):
        await update.message.reply_text(get_config("banned_text"))
        return
    get_or_create_user(user)

    # 检查是否在输入状态（自定义金额、预设字段、提示词等）
    state = get_state(user.id)
    if state:
        await handle_state_input_full(update, context, state)
        return

    # 底部键盘按钮映射字典（统一数据源，创建和匹配都用这里，避免不一致）
    async def _back_home(u, c):
        await u.message.reply_text(get_config("back_home_text"), reply_markup=home_keyboard())
    BOTTOM_BTN_MAP = {
        "btn_profile": show_profile,
        "btn_free_draw": show_draw_menu,
        "btn_checkin": cmd_checkin,
        "btn_recharge": show_recharge,
        "btn_example": show_example,
        "btn_preset": show_presets,
        "btn_free": start_free_draw,
        "btn_my_design": show_history,
        "btn_back_home": _back_home,
    }
    def btn_display(key):
        raw = get_config_raw(key, key)
        t, _ = extract_button_emoji(raw)
        return t
    for key, handler in BOTTOM_BTN_MAP.items():
        if text == btn_display(key):
            await handler(update, context)
            return
    await update.message.reply_text(get_config("unknown_command_text"), reply_markup=home_keyboard())

# ============================================================
# 个人信息
# ============================================================
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user_by_tg_id(user.id)
    if not u:
        u = get_or_create_user(user)
    conn = get_db()
    invite_count = conn.execute("SELECT COUNT(*) as c FROM users WHERE invited_by=?", (user.id,)).fetchone()["c"]
    conn.close()
    status_text = "正常" if u.get("status") == "normal" else "异常"
    invite_link = f"https://t.me/{BOT_USERNAME}?start={u['invite_code']}"
    text = get_config("profile_text")
    text = text.replace("{bot_uid}", str(u["bot_user_id"]))
    text = text.replace("{tg_id}", str(user.id))
    text = text.replace("{username}", f"@{user.username}" if user.username else "无")
    text = text.replace("{first_name}", truncate_first_name(user.first_name or ""))
    text = text.replace("{balance}", format_amount(u["balance"]))
    text = text.replace("{draws}", str(u["total_draws"]))
    text = text.replace("{invites}", str(invite_count))
    text = text.replace("{status}", status_text)
    text = text.replace("{created}", u["created_at"])
    text = text.replace("{invite_link}", invite_link)
    # 分享按钮
    share_url = f"https://t.me/share/url?url={invite_link}"
    keyboard = InlineKeyboardMarkup([
        [IButtonCfg("btn_share_invite", url=share_url)]
    ])
    await send_config_photo(update, context, "profile_text", text=text, reply_markup=keyboard)

# ============================================================
# 作图示例
# ============================================================
async def show_example(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = fmt(get_config("example_text"), url=get_config("example_url"))
    await send_config_photo(update, context, "example_text", text=text)

# ============================================================
# 全能作图菜单
# ============================================================
async def show_draw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_config("draw_type_select")
    await send_config_photo(update, context, "draw_type_select", text=text, reply_markup=draw_keyboard())

# ============================================================
# 官方预设列表
# ============================================================
async def show_presets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.execute("SELECT * FROM presets WHERE enabled=1 ORDER BY sort_order").fetchall()
    conn.close()
    keyboard = []
    for r in rows:
        name = r["name"]
        price = r["price"]
        keyboard.append([
            preset_display_button("btn_ps_item", values={"name":name,"price":price}, callback_data=f"preset_select?id={r['id']}"),
            IButtonCfg("btn_preview", callback_data=f"preset_preview?id={r['id']}"),
        ])
    keyboard.append([IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")])
    await send_config_photo(update, context, "preset_select", reply_markup=InlineKeyboardMarkup(keyboard))

# ============================================================
# 预设工具函数：解析字段列表（兼容旧JSON格式和新逗号分隔格式）
# ============================================================
def parse_preset_fields(fields_raw: str) -> list:
    """返回字段名列表，如 ['网站名称','网站链接','标题','客服','价格表']"""
    if not fields_raw:
        return []
    fields_raw = fields_raw.strip()
    # 旧格式：JSON数组
    if fields_raw.startswith("["):
        try:
            arr = json.loads(fields_raw)
            return [f.get("label", f.get("key", str(f))) for f in arr]
        except Exception:
            pass
    # 新格式：逗号分隔
    return [f.strip() for f in fields_raw.replace("，", ",").split(",") if f.strip()]

def build_preset_fields_block(fields: list, values: dict) -> str:
    """构建折叠引用块显示字段填写情况"""
    lines = []
    for f in fields:
        v = values.get(f, "")
        lines.append(f"{f}：{v}")
    return f"<blockquote expandable>{chr(10).join(lines)}</blockquote>"

# ============================================================
# 预设详情（选预设后直接进入依次输入模式）
# ============================================================
async def preset_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, preset_id: int, message_id: int = None):
    conn = get_db()
    p = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    conn.close()
    if not p:
        return
    fields = parse_preset_fields(p["fields"])
    if not fields:
        fields = ["内容"]
    chat_id = update.effective_chat.id
    # 设置状态：依次输入字段
    set_state(update.effective_user.id, "preset_demand", preset_id=preset_id,
              field_index=0, fields=fields, values={}, message_id=message_id)
    # 构建消息：预设名 + 请发送预设需求 + 空字段折叠块 + 价格 + 提示
    preset_price = get_preset_price()
    fields_block = build_preset_fields_block(fields, {})
    text = (f"{p['name']}\n\n"
            f"请发送预设需求\n\n"
            f"{fields_block}\n\n"
            f"预设费用：{preset_price} U\n\n"
            f"请依次发送以上内容，发送完后点击确认")
    keyboard = InlineKeyboardMarkup([
        [IButtonCfg("btn_back", callback_data="preset_back"), IButtonCfg("btn_confirm", callback_data=f"preset_confirm?id={preset_id}")],
    ])
    # 显示模板图
    detail_img = p["detail_image"] or p["template_image"]
    if message_id:
        if detail_img:
            try:
                await context.bot.edit_message_media(
                    chat_id=chat_id, message_id=message_id,
                    media=InputMediaPhoto(media=detail_img, caption=text, parse_mode=ParseMode.HTML),
                    reply_markup=keyboard
                )
            except Exception:
                await safe_edit_text(context, chat_id, message_id, text, keyboard)
        else:
            await safe_edit_text(context, chat_id, message_id, text, keyboard)
    else:
        if detail_img:
            try:
                msg = await context.bot.send_photo(chat_id=chat_id, photo=detail_img, caption=text,
                                                   parse_mode=ParseMode.HTML, reply_markup=keyboard)
                message_id = msg.message_id
            except Exception:
                msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
                message_id = msg.message_id
        else:
            msg = await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            message_id = msg.message_id
        user_states[update.effective_user.id]["message_id"] = message_id

# ============================================================
# 预设-依次接收用户输入（不删除用户消息，自动编辑上面的消息）
# ============================================================
async def handle_preset_demand(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    user = update.effective_user
    input_text = update.message.text
    preset_id = state["preset_id"]
    msg_id = state["message_id"]
    fields = state["fields"]
    idx = state.get("field_index", 0)
    values = state.get("values", {})
    conn = get_db()
    p = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    conn.close()
    if not p:
        return
    # 填入当前字段
    if idx < len(fields):
        values[fields[idx]] = input_text
        idx += 1
        state["field_index"] = idx
        state["values"] = values
    # 构建更新后的消息
    fields_block = build_preset_fields_block(fields, values)
    if idx < len(fields):
        # 还有字段没填，提示下一个
        hint = f"请发送：{fields[idx]}"
    else:
        # 全部填完
        hint = "全部填写完成，点击确认选择作图协议"
    preset_price = get_preset_price()
    text = (f"{p['name']}\n\n"
            f"请发送预设需求\n\n"
            f"{fields_block}\n\n"
            f"预设费用：{preset_price} U\n\n"
            f"{hint}")
    keyboard = InlineKeyboardMarkup([
        [IButtonCfg("btn_back", callback_data="preset_back"), IButtonCfg("btn_confirm", callback_data=f"preset_confirm?id={preset_id}")],
    ])
    state["state"] = "preset_confirm" if idx >= len(fields) else "preset_demand"
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

# ============================================================
# 我来发挥 - 第一步
# ============================================================
async def start_free_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    set_state(user.id, "free_step1", ref_images=[], message_id=None)
    text = get_config("free_step1")
    keyboard = InlineKeyboardMarkup([
        [
            IButtonCfg("btn_prev_step", callback_data="free_prev"),
            IButton("1/3", callback_data="noop"),
            IButtonCfg("btn_next_step", callback_data="free_next"),
        ],
        [IButtonCfg("btn_no_ref", callback_data="free_no_ref")],
        [IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")],
    ])
    msg = await send_config_photo_msg(update, context, "free_step1", text=text, reply_markup=keyboard)
    user_states[user.id]["message_id"] = msg.message_id if msg else None

# ============================================================
# 自由作图状态输入处理
# ============================================================
async def handle_free_input(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    user = update.effective_user
    st = state["state"]
    if st == "free_step1":
        # 接收图片
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            # 下载转base64 data URL（API无法访问Telegram相对路径）
            import io, base64
            buf = io.BytesIO()
            await file.download_to_memory(buf)
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode()
            data_url = f"data:image/jpeg;base64,{b64}"
            ref_urls = state.get("ref_images", [])
            ref_urls.append(data_url)
            state["ref_images"] = ref_urls[:10]
            await safe_delete(context, update.effective_chat.id, update.message.message_id)
            # 更新消息
            text = get_config("free_step1") + f"\n\n已接收：{len(ref_urls)} 张"
            keyboard = InlineKeyboardMarkup([
                [
                    IButtonCfg("btn_prev_step", callback_data="free_prev"),
                    IButton("1/3", callback_data="noop"),
                    IButtonCfg("btn_next_step", callback_data="free_next"),
                ],
                [IButtonCfg("btn_no_ref", callback_data="free_no_ref")],
                [IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")],
            ])
            await safe_edit_text(context, update.effective_chat.id, state["message_id"], text, keyboard)
    elif st == "free_step2":
        # 接收提示词（不删除用户消息，方便用户复制修改）
        prompt = update.message.text
        state["prompt"] = prompt
        state["original_prompt"] = prompt  # 保存原始提示词
        state["optimize_count"] = 0  # 优化次数计数
        # 直接进入第三步
        await show_free_step3(update, context, state)

# ============================================================
# 自由作图第三步
# ============================================================
async def show_free_step3(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    user = update.effective_user
    has_ref = len(state.get("ref_images", [])) > 0
    ref_size = state.get("ref_size", "")
    current_prompt = state.get("prompt", "")
    original_prompt = state.get("original_prompt", current_prompt)
    opt_count = state.get("optimize_count", 0)
    is_optimized = current_prompt != original_prompt
    # 页面文案：显示当前提示词
    base_text = get_config("free_step3")
    if is_optimized:
        text = f"{base_text}\n\n当前提示词（已优化{opt_count}次）：\n{current_prompt}"
    else:
        text = f"{base_text}\n\n当前提示词：\n{current_prompt}"
    buttons = [
        [
            IButtonCfg("btn_prev_step", callback_data="free_prev"),
            IButton("3/3", callback_data="noop"),
            IButtonCfg("btn_next_step", callback_data="free_next"),
        ],
        [
            IButton("1:1 正方形", callback_data="free_size?size=1:1"),
            IButton("3:2 横图", callback_data="free_size?size=3:2"),
            IButton("2:3 竖图", callback_data="free_size?size=2:3"),
        ],
        [IButton("自动尺寸", callback_data="free_size?size=auto")],
    ]
    if has_ref and ref_size:
        buttons.append([IButton(f"原图片分辨率 · {ref_size}", callback_data="free_size?size=original")])
    # 优化提示词按钮（如果开启了优化功能）
    if get_config("optimize_enabled", "1") == "1":
        if is_optimized:
            buttons.append([
                IButtonCfg("btn_reoptimize", callback_data="free_optimize"),
                IButtonCfg("btn_use_original", callback_data="free_use_original"),
            ])
        else:
            buttons.append([IButtonCfg("btn_optimize", callback_data="free_optimize")])
    buttons.append([IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")])
    state["state"] = "free_step3"
    await safe_edit_text(context, update.effective_chat.id, state["message_id"], text,
                         InlineKeyboardMarkup(buttons))

# ============================================================
# 自由作图-优化提示词
# ============================================================
async def handle_free_optimize(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    """点击AI优化提示词按钮"""
    user = update.effective_user
    state = get_state(user.id)
    if not state or state.get("state") != "free_step3":
        return
    opt_count = state.get("optimize_count", 0)
    if opt_count >= 5:
        # 达到上限，提示
        text = fmt(get_config("optimize_limit_text"), count=str(opt_count))
        await safe_edit_text(context, update.effective_chat.id, msg_id, text,
                             InlineKeyboardMarkup([[IButton("返回", callback_data="free_back_step3")]]))
        return
    # 显示优化中
    await safe_edit_text(context, update.effective_chat.id, msg_id, get_config("optimizing_text"),
                         InlineKeyboardMarkup([[IButton("优化中...", callback_data="noop")]]))
    # 异步调用优化（用当前prompt，如果已优化过则基于优化后的继续优化）
    current_prompt = state.get("prompt", "")
    optimized = await optimize_prompt_api(current_prompt)
    if optimized:
        state["prompt"] = optimized
        state["optimize_count"] = opt_count + 1
        # 回到step3页面（显示优化后的提示词）
        await show_free_step3(update, context, state)
    else:
        # 优化失败，回到step3（用原始提示词）
        await show_free_step3(update, context, state)

# ============================================================
# 历史记录
# ============================================================
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    history = get_user_history(user.id, limit=30)
    if not history:
        await send_config_photo(update, context, "no_history_text", reply_markup=home_keyboard())
        return
    # 显示最近一张
    latest = history[0]
    text = get_config("history_text")
    buttons = []
    row = []
    for i, h in enumerate(history[:10]):
        prompt = truncate_text(h.get("prompt", "无提示词"), 15)
        row.append(IButton(prompt, callback_data=f"history_view?id={h['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    src = photo_source(latest)
    if src:
        try:
            await update.message.reply_photo(
                photo=src, caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return
        except Exception as exc:
            log.warning("历史图片发送失败，降级为文本: %s", exc)
        finally:
            if hasattr(src, "close"):
                src.close()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))

# ============================================================
# 充值页面
# ============================================================
async def show_recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = get_config("recharge_title")
    buttons = []
    row = []
    for amt in RECHARGE_PRESETS:
        row.append(IButton(get_config_raw(f"btn_recharge_{amt}", f"{amt}USDT"), callback_data=f"recharge_amount?amount={amt}", color="primary"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # 自定义金额单独一行（绿色）
    buttons.append([IButtonCfg("btn_custom_amount", callback_data="recharge_custom", color="success")])
    # 取消充值单独一行（红色）
    buttons.append([IButtonCfg("btn_cancel_recharge", callback_data="recharge_cancel", color="danger")])
    kb = InlineKeyboardMarkup(buttons)
    await send_config_photo(update, context, "recharge_title", text=text, reply_markup=kb)

# ============================================================
# 通用状态输入分发
# ============================================================
async def handle_state_input(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    st = state["state"]
    if st == "preset_demand":
        await handle_preset_demand(update, context, state)
    elif st in ("free_step1", "free_step2"):
        await handle_free_input(update, context, state)
    elif st == "recharge_custom":
        await handle_recharge_custom(update, context, state)
    elif st == "admin_input":
        await handle_admin_input(update, context, state)
    else:
        clear_state(update.effective_user.id)


# ============================================================
# 回调查询处理
# ============================================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if is_banned(user.id):
        await query.answer("您的账号已被封禁", show_alert=True)
        await query.edit_message_text("您的账号已被封禁。")
        return
    data = query.data
    action, params = parse_action(data)
    chat_id = query.message.chat_id
    msg_id = query.message.message_id
    # 管理员回调
    if action.startswith("admin_"):
        await query.answer()
        await handle_admin_callback(update, context, action, params)
        return

    if action == "noop":
        await query.answer()
        return
    elif action == "back_home":
        clear_state(user.id)
        await safe_edit_text(context, chat_id, msg_id, "已返回首页")
        await context.bot.send_message(chat_id, "请选择功能", reply_markup=home_keyboard())
    elif action == "cancel_draw":
        clear_state(user.id)
        await safe_edit_text(context, chat_id, msg_id, "已取消作图")
    elif action == "recharge_cancel":
        clear_state(user.id)
        await safe_delete(context, chat_id, msg_id)
    elif action == "recharge_amount":
        await show_pay_methods(update, context, float(params["amount"]), msg_id)
    elif action == "recharge_custom":
        set_state(user.id, "recharge_custom", message_id=msg_id)
        text = "请输入自定义充值金额\n\n金额范围：1 - 1000000 USDT\n发送 取消充值 可退出。"
        await safe_edit_text(context, chat_id, msg_id, text)
    elif action == "pay_okpay":
        await create_okpay_payment(update, context, params, msg_id)
    elif action == "pay_ton":
        await create_crypto_payment(update, context, "ton", params, msg_id)
    elif action == "pay_tron":
        await create_crypto_payment(update, context, "tron", params, msg_id)
    elif action == "pay_close":
        order_id = params.get("oid") or params.get("order_id", "")
        if order_id:
            update_order(order_id, status="cancelled")
        await safe_delete(context, chat_id, msg_id)
    elif action == "pay_refresh":
        await check_payment(update, context, params.get("oid") or params.get("order_id", ""), msg_id)
    elif action == "preset_select":
        await preset_detail(update, context, int(params["id"]), msg_id)
    elif action == "preset_preview":
        await show_preset_preview(update, context, int(params["id"]), msg_id)
    elif action == "preset_back":
        clear_state(user.id)
        await show_presets(update, context)
        await safe_delete(context, chat_id, msg_id)
    elif action == "preset_confirm":
        await confirm_preset(update, context, int(params["id"]), msg_id)
    elif action == "preset_protocol":
        await start_preset_draw(update, context, int(params["id"]), msg_id)
    elif action == "free_prev":
        await free_prev_step(update, context, msg_id)
    elif action == "free_next":
        await free_next_step(update, context, msg_id)
    elif action == "free_no_ref":
        state = get_state(user.id)
        if state:
            state["ref_images"] = []
            state["state"] = "free_step2"
            text = get_config("free_step2")
            keyboard = InlineKeyboardMarkup([
                [
                    IButtonCfg("btn_prev_step", callback_data="free_prev"),
                    IButton("2/3", callback_data="noop"),
                    IButtonCfg("btn_next_step", callback_data="free_next"),
                ],
                [IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")],
            ])
            await safe_edit_text(context, chat_id, msg_id, text, keyboard)
    elif action == "free_size":
        await confirm_free_draw(update, context, params["size"], msg_id)
    elif action == "free_optimize":
        await handle_free_optimize(update, context, msg_id)
    elif action == "free_use_original":
        state = get_state(update.effective_user.id)
        if state and state.get("original_prompt"):
            state["prompt"] = state["original_prompt"]
            await show_free_step3(update, context, state)
    elif action == "free_back_step3":
        state = get_state(update.effective_user.id)
        if state:
            await show_free_step3(update, context, state)
    elif action == "free_start":
        # 用户选择线路，存入state
        line = params.get("line", get_draw_line())
        state = get_state(user.id)
        if state:
            state["draw_line"] = line
        await start_free_draw_task(update, context, msg_id)
    elif action == "history_view":
        await view_history_image(update, context, int(params["id"]), msg_id)
    elif action == "draw_again":
        await draw_again(update, context, params.get("task_id", ""), msg_id)
    else:
        log.warning(f"未处理的回调: {action}")
    # 兜底answer：消除按钮加载状态（具体处理函数已answer的话这里会被忽略）
    try:
        await query.answer()
    except Exception:
        pass

# ============================================================
# 预设预览
# ============================================================
async def show_preset_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, preset_id: int, msg_id: int):
    conn = get_db()
    p = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    conn.close()
    if not p:
        return
    text = fmt(get_config("preset_preview"), name=p["name"], price=get_preset_price())
    keyboard = InlineKeyboardMarkup([[
        IButtonCfg("btn_back", callback_data="preset_back")
    ]])
    if p["template_image"]:
        try:
            await context.bot.edit_message_media(
                chat_id=update.effective_chat.id, message_id=msg_id,
                media=InputMediaPhoto(media=p["template_image"], caption=text),
                reply_markup=keyboard
            )
            return
        except Exception:
            pass
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

# ============================================================
# 预设确认 -> 选择协议
# ============================================================
async def confirm_preset(update: Update, context: ContextTypes.DEFAULT_TYPE, preset_id: int, msg_id: int):
    user = update.effective_user
    state = get_state(user.id)
    if not state or state["state"] not in ("preset_confirm", "preset_demand"):
        await query_answer(update, "请先填写预设需求", show_alert=False)
        return
    fields = state.get("fields", [])
    values = state.get("values", {})
    filled_count = sum(1 for f in fields if values.get(f))
    if filled_count == 0:
        await query_answer(update, "请先发送预设需求！", show_alert=False)
        return
    conn = get_db()
    p = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    conn.close()
    base_price = get_preset_base_price()
    protocol_price = get_protocol_price()
    total = base_price + protocol_price
    text = fmt(get_config("protocol_select"), name=p["name"], price=total)
    keyboard = InlineKeyboardMarkup([
        [preset_display_button("btn_ps_amount", values={"total":f"{total:.2f}"}, callback_data=f"preset_protocol?id={preset_id}")],
        [IButtonCfg("btn_back_preset", callback_data="preset_back")],
        [IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")],
    ])
    state["preset_name"] = p["name"]
    state["preset_price"] = base_price
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

async def query_answer(update: Update, text: str, show_alert: bool = False):
    try:
        from .platform import reliable_ack
        from .platform import reliable_ack
        from .platform import reliable_ack
        from .platform import reliable_ack
        await reliable_ack(update, text, show_alert=show_alert)
    except Exception:
        pass

# ============================================================
# 开始预设生图
# ============================================================
async def start_preset_draw(update: Update, context: ContextTypes.DEFAULT_TYPE, preset_id: int, msg_id: int):
    user = update.effective_user
    u = get_or_create_user(user)
    state = get_state(user.id)
    if not state:
        return
    cost = get_preset_base_price() + get_protocol_price()
    log.info(f"[生图] 用户{user.id} 请求预设作图 余额={u['balance']} 需要={cost}")
    if u["balance"] < cost:
        log.info(f"[生图] 余额不足，拦截")
        text = get_config("balance_insufficient")
        keyboard = InlineKeyboardMarkup([[
            IButtonCfg("btn_recharge_small", callback_data="recharge_cancel")
        ]])
        bal_img = get_config_raw("balance_insufficient_image", "")
        if bal_img:
            try:
                await context.bot.edit_message_media(
                    chat_id=update.effective_chat.id, message_id=msg_id,
                    media=InputMediaPhoto(media=bal_img, caption=text, parse_mode=ParseMode.HTML),
                    reply_markup=keyboard
                )
            except Exception:
                await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)
        else:
            await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)
        return
    # 扣余额
    log.info(f"[生图] 已扣费 {cost}，剩余 {u['balance'] - cost}")
    # 构建prompt：预设模板提示词 + 用户填写的各字段值
    fields = state.get("fields", [])
    values = dict(state.get("values", {}))
    # 方案B：用户名为空时自动从标题提取（去掉"收款图"后缀）
    if "用户名" in fields and not values.get("用户名"):
        title = values.get("标题", "")
        if title:
            # 去掉常见后缀：收款图、收款码、收款
            for suffix in ["收款图", "收款码", "收款"]:
                if title.endswith(suffix):
                    values["用户名"] = title[:-len(suffix)]
                    break
            if not values.get("用户名"):
                values["用户名"] = title
    demand_lines = [f"{f}：{values.get(f, '')}" for f in fields if values.get(f)]
    demand_text = "\n".join(demand_lines)
    conn = get_db()
    p = conn.execute("SELECT prompt_template, template_image FROM presets WHERE id=?", (preset_id,)).fetchone()
    conn.close()
    prompt_template = p["prompt_template"] if p and p["prompt_template"] else f"生成一张{state.get('preset_name', '预设')}风格的图片"
    prompt = f"{prompt_template}\n\n{demand_text}"
    # 预设模板图作为参考图（图生图）：下载转base64 data URL
    ref_images_json = ""
    if p and p["template_image"]:
        try:
            file = await context.bot.get_file(p["template_image"])
            # 下载图片字节
            import io
            buf = io.BytesIO()
            await file.download_to_memory(buf)
            buf.seek(0)
            img_bytes = buf.read()
            # 转base64 data URL
            import base64
            b64 = base64.b64encode(img_bytes).decode()
            data_url = f"data:image/jpeg;base64,{b64}"
            ref_images_json = json.dumps([data_url], ensure_ascii=False)
            log.info(f"[预设] 模板图已转base64作为参考图 size={len(img_bytes)} bytes")
        except Exception as e:
            log.warning(f"[预设] 模板图获取失败: {e}")
    from .platform import submit_draw
    from .platform import submit_draw
    from .platform import submit_draw
    from .platform import submit_draw
    task_id = await submit_draw(update, context, draw_type="preset", prompt=prompt, size="auto",
        cost=cost, preset_name=state.get("preset_name", ""),
        preset_data=json.dumps(values, ensure_ascii=False),
        ref_images=ref_images_json,
        line=state.get("draw_line", get_draw_line())
    )
    clear_state(user.id)
    # 显示加载（有配图则发图片消息，后续editMessageMedia编辑）
    text = fmt(get_config("draw_loading"), task_id=task_id)
    keyboard = InlineKeyboardMarkup([[
        IButton("生成中，请稍等...", callback_data="noop")
    ]])
    try:
        real_msg_id = await show_draw_loading(context, update.effective_chat.id, msg_id, text, keyboard)
        update_draw_task(task_id, message_id=real_msg_id)
    except Exception:
        refund_draw_once(task_id, "加载消息发送失败")
        raise
    # 后台生图
    asyncio.create_task(run_draw_task(task_id, update.effective_chat.id, real_msg_id, context))

# ============================================================
# 自由作图步骤切换
# ============================================================
async def free_prev_step(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    user = update.effective_user
    state = get_state(user.id)
    if not state:
        return
    st = state["state"]
    if st == "free_step2":
        state["state"] = "free_step1"
        ref_count = len(state.get("ref_images", []))
        text = get_config("free_step1")
        if ref_count:
            text += f"\n\n已接收：{ref_count} 张"
        keyboard = InlineKeyboardMarkup([
            [
                IButtonCfg("btn_prev_step", callback_data="free_prev"),
                IButton("1/3", callback_data="noop"),
                IButtonCfg("btn_next_step", callback_data="free_next"),
            ],
            [IButtonCfg("btn_no_ref", callback_data="free_no_ref")],
            [IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")],
        ])
        await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)
    elif st == "free_step3":
        state["state"] = "free_step2"
        text = get_config("free_step2")
        if state.get("prompt"):
            text += f"\n\n已输入提示词：{truncate_text(state['prompt'], 30)}"
        keyboard = InlineKeyboardMarkup([
            [
                IButtonCfg("btn_prev_step", callback_data="free_prev"),
                IButton("2/3", callback_data="noop"),
                IButtonCfg("btn_next_step", callback_data="free_next"),
            ],
            [IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")],
        ])
        await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

async def free_next_step(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    user = update.effective_user
    state = get_state(user.id)
    if not state:
        return
    st = state["state"]
    if st == "free_step1":
        state["state"] = "free_step2"
        text = get_config("free_step2")
        keyboard = InlineKeyboardMarkup([
            [
                IButtonCfg("btn_prev_step", callback_data="free_prev"),
                IButton("2/3", callback_data="noop"),
                IButtonCfg("btn_next_step", callback_data="free_next"),
            ],
            [IButtonCfg("btn_cancel_draw", callback_data="cancel_draw")],
        ])
        await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)
    elif st == "free_step2":
        if not state.get("prompt"):
            await query_answer(update, "请先输入图片描述提示词！", show_alert=False)
            return
        await show_free_step3(update, context, state)

# ============================================================
# 自由作图确认参数
# ============================================================
async def confirm_free_draw(update: Update, context: ContextTypes.DEFAULT_TYPE, size: str, msg_id: int):
    user = update.effective_user
    state = get_state(user.id)
    if not state:
        return
    ref_count = len(state.get("ref_images", []))
    has_prompt = "是" if state.get("prompt") else "否"
    res = size_to_resolution(size, state.get("ref_size", ""))
    state["size"] = size
    state["resolution"] = res
    text = fmt(get_config("free_summary"), 
        ref_count=ref_count, has_prompt=has_prompt, size=res
    )
    keyboard = InlineKeyboardMarkup([
        [
            IButton(f"KK大模型1.0 · {get_free_draw_price('line1')}U", callback_data="free_start?line=line1", color="primary"),
            IButton(f"KK大模型2.0 · {get_free_draw_price('line2')}U", callback_data="free_start?line=line2", color="success"),
        ],
        [
            IButtonCfg("btn_prev_step", callback_data="free_prev"),
            IButtonCfg("btn_cancel_draw", callback_data="cancel_draw"),
        ],
    ])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

# ============================================================
# 开始自由生图
# ============================================================
async def start_free_draw_task(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    user = update.effective_user
    u = get_or_create_user(user)
    state = get_state(user.id)
    if not state:
        return
    line = state.get("draw_line", get_draw_line())
    cost = get_free_draw_price(line)
    log.info(f"[生图] 用户{user.id} 请求自由作图 线路={line} 余额={u['balance']} 需要={cost}")
    if u["balance"] < cost:
        log.info(f"[生图] 余额不足，拦截")
        text = get_config("balance_insufficient")
        keyboard = InlineKeyboardMarkup([[
            IButtonCfg("btn_recharge_small", callback_data="recharge_cancel")
        ]])
        bal_img = get_config_raw("balance_insufficient_image", "")
        if bal_img:
            try:
                await context.bot.edit_message_media(
                    chat_id=update.effective_chat.id, message_id=msg_id,
                    media=InputMediaPhoto(media=bal_img, caption=text, parse_mode=ParseMode.HTML),
                    reply_markup=keyboard
                )
            except Exception:
                await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)
        else:
            await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)
        return
    log.info(f"[生图] 已扣费 {cost}，剩余 {u['balance'] - cost}")
    prompt = state.get("prompt", "")
    ref_images = json.dumps(state.get("ref_images", []))
    size = state.get("resolution", "auto")
    from .platform import submit_draw
    from .platform import submit_draw
    from .platform import submit_draw
    from .platform import submit_draw
    task_id = await submit_draw(update, context, draw_type="free", prompt=prompt, size=size,
        cost=cost, ref_images=ref_images,
        line=state.get("draw_line", get_draw_line())
    )
    # 记录任务使用的线路
    TASK_LINE_MAP[task_id] = line
    clear_state(user.id)
    text = fmt(get_config("draw_loading"), task_id=task_id)
    keyboard = InlineKeyboardMarkup([[
        IButton("生成中，请稍等...", callback_data="noop")
    ]])
    try:
        real_msg_id = await show_draw_loading(context, update.effective_chat.id, msg_id, text, keyboard)
        update_draw_task(task_id, message_id=real_msg_id)
    except Exception:
        refund_draw_once(task_id, "加载消息发送失败")
        raise
    asyncio.create_task(run_draw_task(task_id, update.effective_chat.id, real_msg_id, context))

# ============================================================
# 再次作图
# ============================================================
async def draw_again(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, msg_id: int):
    task = get_draw_task(task_id)
    if not task:
        return
    if task["draw_type"] == "free":
        set_state(update.effective_user.id, "free_step2",
                  prompt=task["prompt"], ref_images=json.loads(task["ref_images"] or "[]"),
                  size=task["size"], message_id=msg_id)
        await show_free_step3(update, context, get_state(update.effective_user.id))
    else:
        await show_presets(update, context)

# ============================================================
# 查看历史图片
# ============================================================
async def view_history_image(update: Update, context: ContextTypes.DEFAULT_TYPE, task_db_id: int, msg_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM draw_tasks WHERE id=?", (task_db_id,)).fetchone()
    conn.close()
    src = photo_source(dict(row)) if row else None
    if not src:
        await query_answer(update, "这条记录的原图已不可用，请重新生成", show_alert=True)
        return
    # 提示词里可能含 < & 等字符，进 HTML caption 前必须转义
    text = f"作图记录\n\n任务编号：{row['task_id']}\n提示词：{html.escape(truncate_text(row['prompt'], 50))}\n时间：{row['completed_at']}"
    try:
        await context.bot.edit_message_media(
            chat_id=update.effective_chat.id, message_id=msg_id,
            media=InputMediaPhoto(media=src, caption=text, parse_mode=ParseMode.HTML),
            reply_markup=InlineKeyboardMarkup([[
                IButtonCfg("btn_back_home", callback_data="back_home")
            ]])
        )
    except Exception as e:
        log.error(f"查看历史图片失败: {e}")
    finally:
        if hasattr(src, "close"):
            src.close()

# ============================================================
# 自定义充值金额输入
# ============================================================
async def handle_recharge_custom(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    user = update.effective_user
    text = update.message.text.strip()
    await safe_delete(context, update.effective_chat.id, update.message.message_id)
    if text == "取消充值":
        clear_state(user.id)
        await safe_delete(context, update.effective_chat.id, state["message_id"])
        return
    try:
        amount = float(text)
        if amount < 1 or amount > 1000000:
            raise ValueError
    except ValueError:
        await context.bot.send_message(update.effective_chat.id, "请输入有效的金额（1-1000000）")
        return
    clear_state(user.id)
    await show_pay_methods(update, context, amount, state["message_id"])

# ============================================================
# 支付方式选择
# ============================================================
async def show_pay_methods(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float, msg_id: int):
    user = update.effective_user
    u = get_or_create_user(user)
    order_id = create_order(user.id, amount, "")
    user_info = build_user_info(user)
    text = fmt(get_config("recharge_order_text"), 
        order_id=order_id, amount=format_amount(amount),
        user_info=user_info, time=now_str()
    )
    buttons = []
    if get_config("okpay_enabled") == "1":
        # OkPay单独一行（蓝色）
        buttons.append([IButtonCfg("btn_okpay", callback_data=f"pay_okpay?oid={order_id}", color="primary")])
    # TON和TRON合并一行（绿色）
    chain_row = []
    if get_config("ton_usdt_enabled") == "1":
        chain_row.append(IButtonCfg("btn_ton", callback_data=f"pay_ton?oid={order_id}", color="success"))
    if get_config("tron_usdt_enabled") == "1":
        chain_row.append(IButtonCfg("btn_tron", callback_data=f"pay_tron?oid={order_id}", color="success"))
    if chain_row:
        buttons.append(chain_row)
    # 关闭充值单独一行（红色）
    buttons.append([IButtonCfg("btn_close_pay", callback_data=f"pay_close?oid={order_id}", color="danger")])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, InlineKeyboardMarkup(buttons))

# ============================================================
# OkPay 创建支付
# ============================================================
async def create_okpay_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, params: Dict, msg_id: int):
    user = update.effective_user
    order_id = params.get("oid") or params.get("order_id", "")
    order = get_order(order_id)
    amount = float(order["amount"]) if order else float(params.get("amount", 0))
    client = get_okpay_client()
    if not client:
        await query_answer(update, "OkPay支付未配置，请联系管理员", show_alert=True)
        await safe_edit_text(context, update.effective_chat.id, msg_id, "OkPay支付未配置，请联系管理员")
        return
    # 顶部提示
    await query_answer(update, "正在创建订单...")
    # 显示加载
    user_info = build_user_info(user)
    text = fmt(get_config("recharge_creating"), order_id=order_id, amount=format_amount(amount))
    await safe_edit_text(context, update.effective_chat.id, msg_id, text,
                         InlineKeyboardMarkup([[IButton("[Loading....]", callback_data="noop")]]))
    # 回调URL：仅当管理员配置了合法HTTPS地址时才传，否则不传（靠轮询检测）
    callback_url = "" if CLONE_ID else get_config("okpay_callback_url", "").strip()
    result = await client.pay_link(amount, order_id, name="余额充值", coin="USDT", callback_url=callback_url or None)
    if result.get("code") != 0 and "data" not in result:
        await safe_edit_text(context, update.effective_chat.id, msg_id, f"创建支付订单失败：{result}")
        return
    pay_url = result.get("data", {}).get("pay_url") or result.get("pay_url") or ""
    update_order(order_id, pay_method="okpay", pay_url=pay_url)
    expire = (datetime.datetime.now() + datetime.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    text = fmt(get_config("okpay_pay_text"), 
        user_info=user_info, amount=format_amount(amount), expire=expire
    )
    keyboard = InlineKeyboardMarkup([
        [IButtonCfg("btn_go_pay", url=pay_url)],
        [IButtonCfg("btn_refresh_pay", callback_data=f"pay_refresh?oid={order_id}")],
        [IButtonCfg("btn_close_pay", callback_data=f"pay_close?oid={order_id}")],
    ])
    # 直接编辑支付页面，使用对应支付配图。
    await show_pay_page(context, update.effective_chat.id, msg_id, "okpay_pay_text", text, keyboard)

# ============================================================
# 加密货币支付
# ============================================================
async def create_crypto_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, chain: str, params: Dict, msg_id: int):
    user = update.effective_user
    order_id = params.get("oid") or params.get("order_id", "")
    order = get_order(order_id)
    amount = float(order["amount"]) if order else float(params.get("amount", 0))
    await query_answer(update, "正在创建订单...")
    if chain == "ton":
        await send_ton_usdt_order(update, context, order, amount, msg_id)
    else:
        await send_tron_usdt_order(update, context, order, amount, msg_id)

# ============================================================
# 支付状态查询
# ============================================================

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str, msg_id: int):
    order = get_order(order_id)
    if not order:
        return
    if order["user_id"] != update.effective_user.id:
        return
    if order["status"] == "paid":
        await safe_edit_text(context, update.effective_chat.id, msg_id, "该订单已支付并入账，请勿重复付款。")
        return
    if order["pay_method"] == "okpay":
        client = get_okpay_client()
        if client:
            result = await client.check_deposit(order_id)
            data = result.get("data", {})
            status = data.get("status", "")
            if status in ("success", "paid", "completed"):
                await on_payment_success_raw(context, order, update.effective_chat.id, msg_id, data.get("txid", ""))
                return
    elif order["pay_method"] == "ton_usdt":
        ok = await check_ton_usdt_order(context, order_id, update.effective_chat.id, msg_id)
        if ok:
            return
    elif order["pay_method"] == "tron_usdt":
        ok = await check_tron_usdt_order(context, order_id, update.effective_chat.id, msg_id)
        if ok:
            return
    await query_answer(update, "订单未支付，请完成支付后再查询", show_alert=False)

async def on_payment_success_raw(context, order: Dict, chat_id=None, msg_id=None, txid=""):
    """订单状态、交易去重、余额和返利同一事务提交，通知失败不回滚资金。"""
    if not order:
        return False
    conn = get_db()
    inviter_id = None
    reward = 0
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order["order_id"],)).fetchone()
            if not row or row["status"] != "pending":
                return False
            if row["pay_method"] in ("ton_usdt", "tron_usdt") and (not txid or row["expire_at"] < now_str()):
                return False
            user_id = row["user_id"]
            amount = float(row["amount"])
            import math
            if not math.isfinite(amount) or amount <= 0:
                return False
            user_row = conn.execute("SELECT * FROM users WHERE tg_id=?", (user_id,)).fetchone()
            if not user_row:
                return False
            if txid:
                key = row["pay_method"] + ":" + txid
                if conn.execute("SELECT 1 FROM processed_txs WHERE txid=?", (key,)).fetchone():
                    return False
                conn.execute("INSERT INTO processed_txs VALUES(?,?,?)", (key,row["order_id"],now_str()))
            conn.execute("UPDATE orders SET status='paid',paid_at=?,txid=? WHERE order_id=? AND status='pending'", (now_str(),txid,row["order_id"]))
            conn.execute("UPDATE users SET balance=ROUND(balance+?,8) WHERE tg_id=?", (amount,user_id))
            inviter_id = user_row["invited_by"]
            reward = round(amount * 0.05,2) if inviter_id and inviter_id != user_id else 0
            if reward > 0:
                conn.execute("UPDATE users SET balance=ROUND(balance+?,8) WHERE tg_id=?", (reward,inviter_id))
    finally:
        conn.close()
    chat_id = chat_id or user_id
    u = get_user_by_tg_id(user_id)
    if not u:
        return
    user_info = f"{u['first_name'] or ''} (@{u['username'] or '无'} - {u['tg_id']})"
    method_map = {"okpay": "OkPay-USDT", "ton_usdt": "USDT-TON", "tron_usdt": "USDT-TRC20"}
    text = fmt(get_config("recharge_success"), 
        user_info=user_info, amount=format_amount(amount),
        time=now_str(), balance=format_amount(u["balance"]),
        method=method_map.get(order["pay_method"], order["pay_method"])
    )
    if chat_id and msg_id:
        await safe_edit_text(context, chat_id, msg_id, text)
    elif chat_id:
        try:
            await context.bot.send_message(chat_id, text)
        except Exception:
            pass
    # 返利已在同一事务中发放，此处仅通知。
    if inviter_id and reward > 0:
        try:
            await context.bot.send_message(inviter_id, f"邀请充值返利已到账：{reward} USDT")
        except Exception:
            pass
    return True
async def on_payment_success(update: Update, context: ContextTypes.DEFAULT_TYPE, order: Dict, msg_id: int):
    await on_payment_success_raw(context, order, update.effective_chat.id, msg_id)


# ============================================================
# 管理员后台
# ============================================================

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return
    await show_admin_menu(update, context)

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    text = "🔧 管理员后台\n\n请选择管理功能"
    keyboard = InlineKeyboardMarkup([
        [IButton("👥 用户管理", callback_data="admin_users")],
        [IButton("📝 文案按钮配置", callback_data="admin_config")],
        [IButton("🎨 预设模板管理", callback_data="admin_presets")],
        [IButton("💳 支付配置", callback_data="admin_pay")],
        *([[IButton("🔑 API配置", callback_data="admin_api")]] if not CLONE_ID else []),
        [IButton("💰 作图售价设置", callback_data="admin_prices")],
        [IButton("📢 频道配置", callback_data="admin_channel")],
        [IButton("📊 数据统计", callback_data="admin_stats")],
        *([[IButton("管理员管理", callback_data="tenant_admin_panel"), IButton("克隆文案配置", callback_data="admin_scope?g=clone")]] if CLONE_ID and update.effective_user.id == CLONE_OWNER else []),
        *([[IButton("克隆与检测管理", callback_data="platform_settings"), IButton("克隆文案配置", callback_data="admin_scope?g=clone")]] if not CLONE_ID else []),
    ])
    if update.callback_query:
        await safe_edit_text(context, update.effective_chat.id, update.callback_query.message.message_id, text, keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, params: Dict):
    user = update.effective_user
    if not is_admin(user.id):
        return
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id

    if action == "admin_menu":
        await show_admin_menu(update, context)
    elif action == "admin_users":
        await admin_users(update, context, msg_id)
    elif action == "admin_user_search":
        set_state(user.id, "admin_input", admin_action="search_user", message_id=msg_id)
        await safe_edit_text(context, chat_id, msg_id, "请输入用户TGID进行搜索")
    elif action == "admin_user_action":
        await admin_user_action(update, context, params, msg_id)
    elif action == "admin_config":
        clear_state(update.effective_user.id)
        await admin_config_list(update, context, msg_id)
    elif action == "admin_config_edit":
        await admin_config_edit(update, context, params["key"], msg_id, int(params.get("page", 0)))
    elif action == "admin_presets":
        await admin_presets_list(update, context, msg_id)
    elif action == "admin_preset_edit":
        await admin_preset_edit(update, context, int(params["id"]), msg_id)
    elif action == "admin_scope":
        await scoped_callback(update, context, params, msg_id)
    elif action == "admin_pay_group":
        await admin_pay_group(update, context, msg_id, params.get("g", ""))
    elif action == "admin_pay":
        await admin_pay_config(update, context, msg_id)
    elif action == "admin_pay_edit":
        await admin_pay_edit(update, context, params["key"], msg_id)
    elif action == "admin_pay_toggle":
        key = params.get("key", "")
        group = next((g for g,v in PAY_GROUPS.items() if v[1] == key), None)
        if group is None:
            return
        cur = get_config(key, "0")
        set_config(key, "0" if cur == "1" else "1")
        await admin_pay_group(update, context, msg_id, group)
    elif action in ("admin_line_config", "admin_line_edit", "admin_lmodel"):
        line = params.get("line", "line1")
        if line not in ("line1", "line2"):
            return
        clear_state(user.id)
        if action == "admin_line_config":
            await admin_line_config(update, context, msg_id, line)
        elif action == "admin_lmodel":
            try:
                model = get_line_models(line)[int(params.get("i", "-1"))]
                if int(params.get("i", "-1")) < 0:
                    return
            except (ValueError, IndexError):
                return
            set_config(f"{line}_draw_model", model)
            await admin_line_config(update, context, msg_id, line)
        else:
            field = params.get("field", "")
            hints = {"tokens": "请输入本线路 API Token，多个用英文逗号或换行分隔；发送 清空 删除全部Token。",
                     "base_url": "请输入本线路 API 基础地址（https:// 开头，通常以 /v1 结尾，不包含 /images/generations）。",
                     "timeout": "请输入本线路生图超时秒数，范围120～1800。"}
            if field not in hints:
                return
            set_state(user.id, "admin_input", admin_action="edit_line_setting", edit_line=line, edit_field=field, message_id=msg_id)
            await safe_edit_text(context, chat_id, msg_id, hints[field], InlineKeyboardMarkup([[IButton("取消并返回", callback_data=f"admin_line_config?line={line}")]]))
    elif action == "admin_api":
        await admin_api_config(update, context, msg_id)
    elif action == "admin_preview":
        await admin_preview_config(update, context, params["key"], msg_id)
    elif action == "admin_api_edit":
        set_state(user.id, "admin_input", admin_action="edit_api_tokens", message_id=msg_id)
        await safe_edit_text(context, chat_id, msg_id, "请输入生图API Token，多个用英文逗号分隔")
    elif action == "admin_opt_tokens_edit":
        set_state(user.id, "admin_input", admin_action="edit_opt_tokens", message_id=msg_id)
        await safe_edit_text(context, chat_id, msg_id, "请输入优化提示词专用Token（需有gpt-5.5权限），多个用英文逗号分隔")
    elif action == "admin_line2_tokens_edit":
        set_state(user.id, "admin_input", admin_action="edit_line2_tokens", message_id=msg_id)
        await safe_edit_text(context, chat_id, msg_id, "请输入KK大模型2.0 API Token，多个用英文逗号分隔")
    elif action == "admin_draw_line_switch":
        current = get_draw_line()
        kb = [
            [IButton(f"{'✅' if current=='line1' else ''} KK大模型1.0 (ApiNebula)", callback_data="admin_draw_line_set?line=line1")],
            [IButton(f"{'✅' if current=='line2' else ''} KK大模型2.0 (TransitAI)", callback_data="admin_draw_line_set?line=line2")],
            [IButton("返回", callback_data="admin_api")],
        ]
        await safe_edit_text(context, chat_id, msg_id, "选择生图线路：", InlineKeyboardMarkup(kb))
    elif action == "admin_draw_line_set":
        new_line = params.get("line", "line1")
        if new_line in ("line1", "line2"):
            set_config("draw_line", new_line)
        await admin_api_config(update, context, msg_id)
    elif action == "admin_price_config":
        await admin_price_config(update, context, msg_id)
    elif action == "admin_price_edit":
        pkey = params["key"]
        set_state(user.id, "admin_input", admin_action="edit_price", price_key=pkey, message_id=msg_id)
        names = {"price_free_draw": "自由作图价格", "price_preset_line1": "预设1.0价格", "price_preset_line2": "预设2.0价格", "checkin_reward_amount": "签到奖励"}
        await safe_edit_text(context, chat_id, msg_id, f"修改{names.get(pkey, pkey)}\n\n当前值：{get_config_raw(pkey, '')}\n\n请输入新的金额（如 0.40）")
    elif action == "admin_draw_model_switch":
        await admin_line_models(update, context, msg_id, params.get("line", get_draw_line()))
    elif action == "admin_draw_model_set":
        line = params.get("line", get_draw_line())
        if line in ("line1", "line2") and params.get("model") in get_line_models(line):
            set_config(f"{line}_draw_model", params["model"])
        await admin_line_config(update, context, msg_id, line)
    elif action == "admin_stats":
        await admin_stats(update, context, msg_id)
    elif action == "admin_channel":
        await admin_channel_config(update, context, msg_id)
    elif action == "admin_channel_edit":
        key = params["key"]
        set_state(user.id, "admin_input", admin_action="edit_text", text_key=key, message_id=msg_id)
        await safe_edit_text(context, chat_id, msg_id, f"修改：{key}\n\n当前值：\n{get_config_raw(key, '')}\n\n请发送新内容（发送 取消 退出）")
    elif action == "admin_channel_toggle":
        key = params["key"]
        cur = get_config_raw(key, "0")
        set_config(key, "0" if cur == "1" else "1")
        await admin_channel_config(update, context, msg_id)
    elif action == "admin_back":
        clear_state(update.effective_user.id)
        await show_admin_menu(update, context)

async def admin_channel_config(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    enabled = get_config_raw("channel_enabled", "0") == "1"
    channel = get_config_raw("channel_id", "")
    btn_text = get_config_raw("channel_btn_text", "")
    btn_url = get_config_raw("channel_btn_url", "")
    text = (f"📢 频道配置\n\n"
            f"自动发频道：{'✅ 开启' if enabled else '❌ 关闭'}\n"
            f"频道：{channel or '未设置'}\n"
            f"按钮文字：{btn_text}\n"
            f"按钮链接：{btn_url}")
    keyboard = InlineKeyboardMarkup([
        [IButton(f"{'❌ 关闭' if enabled else '✅ 开启'}自动发频道", callback_data="admin_channel_toggle?key=channel_enabled")],
        [IButton("✏️ 修改频道ID", callback_data="admin_channel_edit?key=channel_id")],
        [IButton("✏️ 修改按钮文字", callback_data="admin_channel_edit?key=channel_btn_text")],
        [IButton("✏️ 修改按钮链接", callback_data="admin_channel_edit?key=channel_btn_url")],
        [IButton("✏️ 修改文案模板", callback_data="admin_channel_edit?key=channel_text")],
        [IButton("返回", callback_data="admin_back")],
    ])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int, page: int = 0):
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    rows = conn.execute("SELECT * FROM users ORDER BY id DESC LIMIT ? OFFSET ?", (15, page*15)).fetchall()
    conn.close()
    users = [dict(r) for r in rows]
    lines = [f"👥 用户管理（共 {total} 人）第{page+1}页\n"]
    for u in users:
        status = "🚫" if (u.get("status") == "banned") else "✅"
        lines.append(f"{status} {u['tg_id']} | {u.get('first_name','')[:12]} | 余额{u.get('balance',0):.2f} | 作图{u.get('total_draws',0)}次")
    text = "\n".join(lines)
    keyboard = []
    for u in users:
        keyboard.append([IButton(f"{'🚫' if (u.get('status') == 'banned') else '👤'} {u['tg_id']} {u.get('first_name','')[:10]}", callback_data=f"admin_user_detail?tg_id={u['tg_id']}")])
    nav = []
    if page > 0:
        nav.append(IButton("上一页", callback_data=f"admin_users_page?page={page-1}"))
    if (page+1)*15 < total:
        nav.append(IButton("下一页", callback_data=f"admin_users_page?page={page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([IButton("🔍 搜索用户", callback_data="admin_user_search")])
    keyboard.append([IButton("返回", callback_data="admin_back")])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, InlineKeyboardMarkup(keyboard))

async def admin_user_action(update: Update, context: ContextTypes.DEFAULT_TYPE, params: Dict, msg_id: int):
    tg_id = int(params["tg_id"])
    action_type = params["action"]
    u = get_user_by_tg_id(tg_id)
    if not u:
        await safe_edit_text(context, update.effective_chat.id, msg_id, "用户不存在")
        return
    if action_type == "toggle_ban":
        action_type = "unban" if u.get("status") == "banned" else "ban"
    if action_type in ("ban", "unban") and is_admin(tg_id):
        await safe_edit_text(context, update.effective_chat.id, msg_id, "不能在用户管理中封禁管理员，请先移除管理员权限。")
        return
    if action_type == "ban":
        set_user_status(tg_id, "banned")
        await safe_edit_text(context, update.effective_chat.id, msg_id, f"已封禁用户 {tg_id}")
    elif action_type == "unban":
        set_user_status(tg_id, "normal")
        await safe_edit_text(context, update.effective_chat.id, msg_id, f"已解封用户 {tg_id}")
    elif action_type == "add_balance":
        set_state(update.effective_user.id, "admin_input", admin_action="add_balance",
                  target_tg_id=tg_id, message_id=msg_id)
        await safe_edit_text(context, update.effective_chat.id, msg_id, f"请输入要增加的余额数量（USDT）\n当前余额：{format_amount(u['balance'])}")
    elif action_type == "sub_balance":
        set_state(update.effective_user.id, "admin_input", admin_action="sub_balance",
                  target_tg_id=tg_id, message_id=msg_id)
        await safe_edit_text(context, update.effective_chat.id, msg_id, f"请输入要扣除的余额数量（USDT）\n当前余额：{format_amount(u['balance'])}")

async def admin_config_list(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    configs = get_all_config()
    # 分类显示
    text_keys = [k for k in configs if k.startswith(("welcome", "checkin", "balance", "recharge", "okpay", "ton", "tron", "draw", "preset", "protocol", "free", "history", "profile", "example", "invite", "input", "channel", "banned", "no_history", "unknown", "back_home"))]
    btn_keys = [k for k in configs if k.startswith("btn_") and not k.endswith(("_color", "_url"))]
    link_keys = [k for k in configs if k.endswith("_url") or k == "service_link"]
    text = "📝 文案按钮配置\n\n选择分类："
    keyboard = InlineKeyboardMarkup([
        [IButton(f"📄 文案配置 ({len(text_keys)})", callback_data="admin_config_cat?cat=text")],
        [IButton(f"🔘 按钮配置 ({len(btn_keys)})", callback_data="admin_config_cat?cat=btn")],
        [IButton(f"🔗 链接配置 ({len(link_keys)})", callback_data="admin_config_cat?cat=link")],
        [IButton("返回", callback_data="admin_back")],
    ])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

# 扩展回调处理（配置分类）
async def admin_preview_config(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, msg_id: int):
    """只读预览，退出编辑状态。"""
    clear_state(update.effective_user.id)
    chat_id = update.effective_chat.id
    preview_msg = None
    try:
        if key.startswith("btn_"):
            btn_text = get_config(key)
            btn_url = get_config_raw(f"{key}_url", "")
            if btn_url:
                kb = InlineKeyboardMarkup([[IButtonCfg(key, url=btn_url)]])
            else:
                kb = InlineKeyboardMarkup([[IButtonCfg(key, callback_data="noop")]])
            preview_msg = await context.bot.send_message(chat_id=chat_id, text=f"👁️ 按钮预览（10秒后删除）", reply_markup=kb)
        else:
            # 文案预览：直接发真实效果（有配图发图片+caption，无图发文字）
            text = get_config(key)
            img = get_config_raw(f"{key}_image", "")
            if img:
                preview_msg = await context.bot.send_photo(chat_id=chat_id, photo=img, caption=text, parse_mode=ParseMode.HTML)
            else:
                preview_msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        # 10秒后删除
        if preview_msg:
            mid = preview_msg.message_id
            async def _del():
                await asyncio.sleep(10)
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                except Exception:
                    pass
            asyncio.create_task(_del())
    except Exception as e:
        log.warning(f"预览失败: {e}")

async def admin_config_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str, msg_id: int, page: int = 0):
    clear_state(update.effective_user.id)
    current = get_config_raw(key, "")
    is_btn = key.startswith("btn_")
    cat = "btn" if is_btn else ("link" if (key.endswith("_url") or key == "service_link") else "text")
    # 所有文案都支持配图（配图key = 文案key + _image）
    image_key = f"{key}_image"
    if is_btn:
        # 按钮编辑：文字+颜色+链接
        color_key = f"{key}_color"
        url_key = f"{key}_url"
        current_color = get_config_raw(color_key, "") or "默认"
        current_url = get_config_raw(url_key, "") or "（无）"
        text = (f"🔘 编辑按钮：{key}\n\n"
                f"文字：{current}\n"
                f"颜色：{current_color}\n"
                f"链接：{current_url}\n\n"
                f"请选择操作：")
        keyboard = InlineKeyboardMarkup([
            [IButton("✏️ 修改文字", callback_data=f"admin_btn_edit_text?key={key}&page={page}")],
            [IButton("🎨 修改颜色", callback_data=f"admin_btn_edit_color?key={key}&page={page}")],
            [IButton("🔗 修改链接", callback_data=f"admin_btn_edit_url?key={key}&page={page}")],
            [IButton("👁️ 预览效果", callback_data=f"admin_preview?key={key}")],
            [IButton("返回", callback_data=f"admin_config_cat?cat={cat}&page={page}")],
        ])
        await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)
    else:
        # 文案编辑：文字+配图（所有文案都支持）
        current_image = get_config_raw(image_key, "") or "（无）"
        text = (f"📝 编辑文案：{key}\n\n"
                f"当前内容：\n{current}\n\n"
                f"配图：{current_image}\n\n"
                f"请选择操作：")
        keyboard = InlineKeyboardMarkup([
            [IButton("✏️ 修改文字", callback_data=f"admin_text_edit_text?key={key}&page={page}")],
            [IButton("🖼️ 设置配图（发图片）", callback_data=f"admin_text_edit_image?key={key}&page={page}")],
            [IButton("🗑️ 清除配图", callback_data=f"admin_text_clear_image?key={key}&page={page}")],
            [IButton("👁️ 预览效果", callback_data=f"admin_preview?key={key}")],
            [IButton("返回", callback_data=f"admin_config_cat?cat={cat}&page={page}")],
        ])
        await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

async def admin_presets_list(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    conn = get_db()
    rows = conn.execute("SELECT * FROM presets ORDER BY sort_order").fetchall()
    conn.close()
    clear_state(update.effective_user.id)
    text = "🎨 预设模板管理\n\n点击编辑；列表按钮名称跟随预设名称。"
    buttons = [[IButton("预设页面按钮配置", callback_data="admin_scope?g=preset")]]
    for r in rows:
        status = "✅" if r["enabled"] else "❌"
        buttons.append([IButton(f"{status} {r['name']} ({r['price']}U)", callback_data=f"admin_preset_edit?id={r['id']}")])
    buttons.append([IButton("返回", callback_data="admin_back")])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, InlineKeyboardMarkup(buttons))

async def admin_preset_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, preset_id: int, msg_id: int):
    conn = get_db()
    p = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    conn.close()
    if not p:
        return
    prompt_preview = (p["prompt_template"] or "（未设置）")[:50]
    text = (f"预设：{p['name']}\n价格：{p['price']}U\n状态：{'启用' if p['enabled'] else '禁用'}\n"
            f"提示词模板：{prompt_preview}{'...' if len(p['prompt_template'] or '') > 50 else ''}\n\n"
            f"选择要修改的项：")
    keyboard = InlineKeyboardMarkup([
        [IButton("修改名称", callback_data=f"admin_preset_field?id={preset_id}&field=name")],
        [IButton("修改价格", callback_data=f"admin_preset_field?id={preset_id}&field=price")],
        [IButton("修改提示词模板", callback_data=f"admin_preset_field?id={preset_id}&field=prompt_template")],
        [IButton("修改字段列表", callback_data=f"admin_preset_field?id={preset_id}&field=fields")],
        [IButton("修改模板图(参考底图)", callback_data=f"admin_preset_field?id={preset_id}&field=image")],
        [IButton("修改详情页配图", callback_data=f"admin_preset_field?id={preset_id}&field=detail_image")],
        [IButton("修改需求页配图", callback_data=f"admin_preset_field?id={preset_id}&field=demand_image")],
        [IButton("启用/禁用", callback_data=f"admin_preset_toggle?id={preset_id}")],
        [IButton("返回", callback_data="admin_presets")],
    ])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

# 分组编辑器：支付参数和展示配置分离；业务按钮只允许修改外观。
def preset_display_button(key, **kwargs):
    values = kwargs.pop("values", {})
    template = get_config_raw(key, SCOPED_DEFAULTS[key])
    return IButton(fmt(template, **values), color=get_config_raw(key + "_color", ""), **kwargs)

async def show_pay_page(context, chat_id, msg_id, key, text, keyboard):
    image = get_config_raw(key + "_image", "")
    if image:
        try:
            await context.bot.edit_message_media(chat_id=chat_id, message_id=msg_id,
                media=InputMediaPhoto(image, caption=text, parse_mode=ParseMode.HTML), reply_markup=keyboard)
            return
        except TelegramError as exc:
            log.warning("支付配图编辑失败，降级文字: %s", exc)
    await safe_edit_text(context, chat_id, msg_id, text, keyboard)

async def scoped_panel(update, context, msg_id, group, index=None):
    import html
    clear_state(update.effective_user.id)
    if group not in SCOPED_FIELDS:
        return
    rows = SCOPED_FIELDS[group]
    if index is None:
        text = "官方预设按钮配置\n列表模板支持 {name} {price}，协议模板和支付金额按钮支持 {total}。\n预设名称在预设详情里修改；业务回调保持不变。"
        kb = [[IButton(label, callback_data=f"admin_scope?g={group}&i={i}")] for i,(label,key,kind) in enumerate(rows)]
        if group == "clone":
            text = "克隆服务文案与按钮配置\n变量：{fee} {cost1} {cost2} {credits}\n仅影响主站克隆服务，不影响克隆拥有者的独立文案。"
        kb.append([IButton("返回后台" if group == "clone" else "返回预设管理", callback_data="admin_back" if group == "clone" else "admin_presets")])
    else:
        if not 0 <= index < len(rows):
            return
        label,key,kind = rows[index]
        current = get_config_raw(key, SCOPED_DEFAULTS.get(key, ""))
        text = f"{label}\n\n当前内容：\n{current}\n\n只有点击修改后才接收一次输入。"
        if group == "clone":
            text += "\n可用变量：{fee} {cost1} {cost2} {credits}（按钮使用纯文字）"
        elif kind == "text":
            text += "\n可用变量：" + ("{amount} {expire} {user_info}" if group == "ok" else "{order_id} {amount} {address}" + (" {memo}" if group == "ton" else ""))
        else:
            text += "\n支持 [emoji:ID] 会员图标。按钮链接/回调不可修改。"
            if key in ("btn_refresh_pay", "btn_close_pay"):
                text += "\n注意：此按钮由三种支付方式共用，修改会同时生效。"
        kb = [[IButton("修改文字", callback_data=f"admin_scope?g={group}&i={index}&op=text")]]
        if group == "clone":
            pass
        elif kind == "text":
            kb += [[IButton("设置配图", callback_data=f"admin_scope?g={group}&i={index}&op=image"), IButton("清除配图", callback_data=f"admin_scope?g={group}&i={index}&op=clear")]]
        else:
            kb += [[IButton(label, callback_data=f"admin_scope?g={group}&i={index}&op=c{j}") for j,label in enumerate(("默认", "蓝", "绿", "红"))]]
        kb += [[IButton("预览", callback_data=f"admin_scope?g={group}&i={index}&op=preview")],
               [IButton("返回", callback_data=(f"admin_scope?g={group}" if group in ("preset","clone") else f"admin_pay_group?g={group}"))]]
    await safe_edit_text(context, update.effective_chat.id, msg_id, html.escape(text), InlineKeyboardMarkup(kb))

async def scoped_callback(update, context, params, msg_id):
    group = params.get("g", "")
    if group not in SCOPED_FIELDS:
        return
    if "i" not in params:
        return await scoped_panel(update, context, msg_id, group)
    try:
        index = int(params["i"])
        if index < 0:
            return
        label,key,kind = SCOPED_FIELDS[group][index]
    except (ValueError, IndexError):
        return
    op = params.get("op", "")
    clear_state(update.effective_user.id)
    if op in ("text", "image"):
        if op == "image" and kind != "text":
            return
        set_state(update.effective_user.id, "admin_input", admin_action="scoped_input", group=group, index=index, operation=op, message_id=msg_id)
        await safe_edit_text(context, update.effective_chat.id, msg_id, "请发送" + ("图片" if op=="image" else "新文字（支持格式和 [emoji:ID]）"),
            InlineKeyboardMarkup([[IButton("取消", callback_data=f"admin_scope?g={group}&i={index}")]]))
        return
    if op == "clear" and kind == "text":
        set_config(key + "_image", "")
    elif op in ("c0", "c1", "c2", "c3") and kind == "button":
        set_config(key + "_color", ("", "primary", "success", "danger")[int(op[1])])
    elif op == "preview":
        template = get_config_raw(key, SCOPED_DEFAULTS.get(key, ""))
        text = fmt(template, name="示例预设", price="0.25", total="0.55", amount="10.123", order_id="DEMO_ORDER", address="示例地址_请勿转账", memo="TON_DEMO_ORDER", expire="示例到期时间", user_info="示例用户",fee="10",cost1="0.2",cost2="0.3",credits="1")
        if kind == "button":
            kb=InlineKeyboardMarkup([[IButton(text, color=get_config_raw(key+"_color", ""), callback_data="noop")]])
            await context.bot.send_message(update.effective_chat.id, "按钮预览（仅展示）", reply_markup=kb)
        else:
            image=get_config_raw(key+"_image", "")
            if image:
                await context.bot.send_photo(update.effective_chat.id, image, caption=text, parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML)
    await scoped_panel(update, context, msg_id, group, index)

async def admin_pay_config(update, context, msg_id):
    clear_state(update.effective_user.id)
    kb=[[IButton(name, callback_data=f"admin_pay_group?g={group}")] for group,(name,enabled,fields,textkey) in PAY_GROUPS.items()]
    kb.append([IButton("返回", callback_data="admin_back")])
    await safe_edit_text(context, update.effective_chat.id, msg_id, "支付设置：选择对应支付方式", InlineKeyboardMarkup(kb))

async def admin_pay_group(update, context, msg_id, group):
    import html
    if group not in PAY_GROUPS:
        return
    clear_state(update.effective_user.id)
    name,enabled,fields,textkey=PAY_GROUPS[group]
    text=name+" 支付设置\n状态："+("启用" if get_config_raw(enabled,"0")=="1" else "禁用")
    kb=[[IButton("启用/禁用", callback_data=f"admin_pay_toggle?key={enabled}")]]
    for label,key in fields:
        value=get_config_raw(key, "")
        shown=("已配置（隐藏）" if value else "未配置") if "key" in key or "token" in key else (value or "未配置")
        text+=f"\n{label}：{shown}"
        kb.append([IButton("修改"+label, callback_data=f"admin_pay_edit?key={key}")])
    for i,(label,key,kind) in enumerate(SCOPED_FIELDS[group]):
        kb.append([IButton(label+"设置", callback_data=f"admin_scope?g={group}&i={i}")])
    kb.append([IButton("返回支付列表", callback_data="admin_pay")])
    await safe_edit_text(context, update.effective_chat.id, msg_id, html.escape(text), InlineKeyboardMarkup(kb))

async def admin_pay_edit(update, context, key, msg_id):
    for group,(_,_,fields,_) in PAY_GROUPS.items():
        for label,allowed in fields:
            if allowed == key:
                set_state(update.effective_user.id,"admin_input",admin_action="pay_parameter",config_key=key,pay_group=group,message_id=msg_id)
                await safe_edit_text(context, update.effective_chat.id, msg_id, f"请输入新的{label}（保存后返回支付设置，不生成预览）", InlineKeyboardMarkup([[IButton("取消", callback_data=f"admin_pay_group?g={group}")]]))
                return

def line_display_name(line):
    return "KK大模型2.0（TransitAI）" if line == "line2" else "KK大模型1.0（ApiNebula）"

async def admin_api_config(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    clear_state(update.effective_user.id)
    import html
    lines = ["🔑 API配置", "", "两条线路独立配置，进入页面不会切换默认线路。", ""]
    for line in ("line1", "line2"):
        count = len([t for t in get_config_raw(get_line_tokens_key(line), "").split(",") if t.strip()])
        lines.append(f"{line_display_name(line)}：Token {count} 个，模型 {get_line_model(line)}")
    lines.extend(["", f"默认线路：{line_display_name(get_draw_line())}", "用户指定线路优先；预设使用默认线路，模型固定 gpt-image-2。"])
    keyboard = InlineKeyboardMarkup([
        [IButton("配置 KK大模型1.0", callback_data="admin_line_config?line=line1")],
        [IButton("配置 KK大模型2.0", callback_data="admin_line_config?line=line2")],
        [IButton("设置默认生图线路", callback_data="admin_draw_line_switch")],
        [IButton("优化提示词 API（独立Token）", callback_data="admin_opt_tokens_edit")],
        [IButton("💰 价格与签到配置", callback_data="admin_price_config")],
        [IButton("返回", callback_data="admin_back")]])
    await safe_edit_text(context, update.effective_chat.id, msg_id, html.escape("\n".join(lines)), keyboard)

async def admin_line_config(update, context, msg_id, line):
    if line not in ("line1", "line2"):
        return
    clear_state(update.effective_user.id)
    import html
    count = len([t for t in get_config_raw(get_line_tokens_key(line), "").split(",") if t.strip()])
    text = (f"🔧 {line_display_name(line)}\n\n"
            f"接口地址：{get_line_base_url(line)}\n"
            f"API Token：已配置 {count} 个（不显示密钥）\n"
            f"自由作图模型：{get_line_model(line)}\n"
            f"请求超时：{get_line_timeout(line)} 秒\n"
            f"默认线路：{'是' if get_draw_line() == line else '否'}\n\n"
            "修改只影响本线路后续请求，不影响另一条线路。")
    keyboard = InlineKeyboardMarkup([
        [IButton("修改接口地址", callback_data=f"admin_line_edit?line={line}&field=base_url")],
        [IButton("管理 API Tokens", callback_data=f"admin_line_edit?line={line}&field=tokens")],
        [IButton("选择生图模型", callback_data=f"admin_draw_model_switch?line={line}")],
        [IButton("修改超时时间", callback_data=f"admin_line_edit?line={line}&field=timeout")],
        [IButton("设为默认线路", callback_data=f"admin_draw_line_set?line={line}")],
        [IButton("返回线路列表", callback_data="admin_api")]])
    await safe_edit_text(context, update.effective_chat.id, msg_id, html.escape(text), keyboard)

async def admin_line_models(update, context, msg_id, line):
    if line not in ("line1", "line2"):
        return
    clear_state(update.effective_user.id)
    current = get_line_model(line)
    # 用索引保持callback_data始终小于Telegram的64字节限制。
    keyboard = [[IButton(("✅ " if model == current else "") + model,
                         callback_data=f"admin_lmodel?line={line}&i={i}")]
                for i, model in enumerate(get_line_models(line))]
    keyboard.append([IButton("返回本线路", callback_data=f"admin_line_config?line={line}")])
    await safe_edit_text(context, update.effective_chat.id, msg_id,
                         f"选择{line_display_name(line)}自由作图模型（不修改另一线路）：", InlineKeyboardMarkup(keyboard))

async def admin_price_config(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    """价格与签到奖励配置"""
    text = (f"💰 价格与签到配置\n\n"
            f"自由作图1.0：{get_free_draw_price('line1')} U\n"
            f"自由作图2.0：{get_free_draw_price('line2')} U\n"
            f"预设1.0基础费：{get_preset_base_price('line1')} U（最终约{get_preset_price('line1'):.2f} U）\n"
            f"预设2.0基础费：{get_preset_base_price('line2')} U（最终约{get_preset_price('line2'):.2f} U）\n"
            f"协议叠加费：{get_protocol_price()} U\n"
            f"签到奖励：{get_checkin_reward()} U\n\n"
            f"点击修改对应项")
    keyboard = InlineKeyboardMarkup([
        [IButton(f"自由作图1.0价格（{get_free_draw_price('line1')}U）", callback_data="admin_price_edit?key=price_free_line1")],
        [IButton(f"自由作图2.0价格（{get_free_draw_price('line2')}U）", callback_data="admin_price_edit?key=price_free_line2")],
        [IButton(f"预设1.0基础费（{get_preset_base_price('line1')}U）", callback_data="admin_price_edit?key=price_preset_line1")],
        [IButton(f"预设2.0基础费（{get_preset_base_price('line2')}U）", callback_data="admin_price_edit?key=price_preset_line2")],
        [IButton(f"协议叠加费（{get_protocol_price()}U）", callback_data="admin_price_edit?key=price_protocol")],
        [IButton(f"签到奖励（{get_checkin_reward()}U）", callback_data="admin_price_edit?key=checkin_reward_amount")],
        [IButton("返回", callback_data="admin_api")],
    ])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, msg_id: int):
    conn = get_db()
    user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    order_count = conn.execute("SELECT COUNT(*) as c FROM orders").fetchone()["c"]
    paid_count = conn.execute("SELECT COUNT(*) as c FROM orders WHERE status='paid'").fetchone()["c"]
    total_amount = conn.execute("SELECT COALESCE(SUM(amount),0) as s FROM orders WHERE status='paid'").fetchone()["s"]
    draw_count = conn.execute("SELECT COUNT(*) as c FROM draw_tasks WHERE status='completed'").fetchone()["c"]
    total_balance = conn.execute("SELECT COALESCE(SUM(balance),0) as s FROM users").fetchone()["s"]
    conn.close()
    text = f"📊 数据统计\n\n用户总数：{user_count}\n订单总数：{order_count}\n已支付订单：{paid_count}\n充值总额：{format_amount(total_amount)} USDT\n生图总数：{draw_count}\n用户余额总和：{format_amount(total_balance)} USDT"
    keyboard = InlineKeyboardMarkup([[IButton("返回", callback_data="admin_back")]])
    await safe_edit_text(context, update.effective_chat.id, msg_id, text, keyboard)

# ============================================================
# 管理员输入处理
# ============================================================
def extract_custom_emojis_from_message(message) -> str:
    """兼容旧调用：等同于 message_to_html，转换所有格式实体+会员图标为HTML"""
    return message_to_html(message)

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    user = update.effective_user
    if not is_admin(user.id):
        clear_state(user.id)
        return
    action = state.get("admin_action")
    # 先取纯文本判断取消（避免用户给"取消"加了格式导致不匹配）
    raw_text = (update.message.text or update.message.caption or "").strip()
    # 自动提取会员表情ID（管理员直接粘贴会员表情即可）
    text = extract_custom_emojis_from_message(update.message).strip()
    msg_id = state.get("message_id")
    chat_id = update.effective_chat.id
    await safe_delete(context, chat_id, update.message.message_id)

    if raw_text in ("取消", "退出", "cancel"):
        clear_state(user.id)
        await show_admin_menu(update, context)
        return

    if action == "pay_parameter":
        key=state.get("config_key", "")
        group=state.get("pay_group", "")
        if group not in PAY_GROUPS or key not in [k for _,k in PAY_GROUPS[group][2]]:
            clear_state(user.id)
            return
        validator={"ton_usdt_address":is_valid_ton_address,"tron_usdt_address":is_valid_tron_address}.get(key)
        if not raw_text or (validator and not validator(raw_text)):
            await safe_edit_text(context, chat_id, msg_id, "输入为空或地址校验失败，请重新输入。")
            return
        if key == "okpay_api_url":
            from urllib.parse import urlsplit
            url=urlsplit(raw_text)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                await safe_edit_text(context, chat_id, msg_id, "请填写合法HTTPS接口基础地址。")
                return
        set_config(key,raw_text)
        clear_state(user.id)
        await admin_pay_group(update,context,msg_id,group)
        return
    if action == "search_user":
        try:
            tg_id = int(text)
            u = get_user_by_tg_id(tg_id)
            if not u:
                await safe_edit_text(context, chat_id, msg_id, "用户不存在")
                clear_state(user.id)
                return
            status = "正常" if u["status"] == "normal" else "已封禁"
            user_text = f"用户信息\n\nTGID：{u['tg_id']}\n用户名：@{u['username'] or '无'}\n名称：{u['first_name'] or ''}\n余额：{format_amount(u['balance'])} USDT\n累计作图：{u['total_draws']}次\n状态：{status}\n注册时间：{u['created_at']}"
            keyboard = InlineKeyboardMarkup([
                [IButton("➕ 加余额", callback_data=f"admin_user_action?tg_id={tg_id}&action=add_balance")],
                [IButton("➖ 减余额", callback_data=f"admin_user_action?tg_id={tg_id}&action=sub_balance")],
                [IButton("封禁" if u["status"]=="normal" else "解封", callback_data=f"admin_user_action?tg_id={tg_id}&action={'ban' if u['status']=='normal' else 'unban'}")],
                [IButton("返回", callback_data="admin_users")],
            ])
            await safe_edit_text(context, chat_id, msg_id, user_text, keyboard)
        except ValueError:
            await safe_edit_text(context, chat_id, msg_id, "请输入有效的数字TGID")
        clear_state(user.id)
    elif action == "add_balance":
        try:
            amount = float(text)
            target = state["target_tg_id"]
            update_balance(target, amount)
            u = get_user_by_tg_id(target)
            await safe_edit_text(context, chat_id, msg_id, f"已增加 {format_amount(amount)} USDT\n当前余额：{format_amount(u['balance'])} USDT")
        except ValueError:
            await safe_edit_text(context, chat_id, msg_id, "请输入有效数字")
        clear_state(user.id)
    elif action == "sub_balance":
        try:
            amount = float(text)
            target = state["target_tg_id"]
            update_balance(target, -amount)
            u = get_user_by_tg_id(target)
            await safe_edit_text(context, chat_id, msg_id, f"已扣除 {format_amount(amount)} USDT\n当前余额：{format_amount(u['balance'])} USDT")
        except ValueError:
            await safe_edit_text(context, chat_id, msg_id, "请输入有效数字")
        clear_state(user.id)
    elif action == "edit_config":
        key = state["config_key"]
        validator = {"ton_usdt_address": is_valid_ton_address, "tron_usdt_address": is_valid_tron_address}.get(key)
        if validator and not validator(raw_text):
            await safe_edit_text(context, chat_id, msg_id, "收款地址格式或校验和错误，请重新输入。")
            return
        page = state.get("page", 0)
        set_config(key, text)
        clear_state(user.id)
        # 回到编辑页面
        await admin_config_edit(update, context, key, msg_id, page)
        # 自动发预览，10秒后删除
        await admin_preview_config(update, context, key, msg_id)
    elif action == "edit_line_setting":
        line = state.get("edit_line")
        field = state.get("edit_field")
        if line not in ("line1", "line2") or field not in ("tokens", "base_url", "timeout"):
            clear_state(user.id)
            return
        try:
            value = raw_text.strip()
            if field == "timeout":
                seconds = int(value)
                if not 120 <= seconds <= 1800:
                    raise ValueError("超时必须为120～1800之间的整数秒")
                value = str(seconds)
            elif field == "base_url":
                from urllib.parse import urlsplit
                parsed = urlsplit(value)
                if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                        or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
                    raise ValueError("请填写合法的HTTPS基础地址，不包含账号、参数或空格")
                if parsed.path.rstrip("/").endswith(("/images/generations", "/images/edits")):
                    raise ValueError("请输入API基础地址，不是完整生图接口路径")
                value = value.rstrip("/")
            else:
                import re
                tokens = [] if value == "清空" else list(dict.fromkeys(t for t in re.split(r"[,，\s]+", value) if t))
                if not tokens and value != "清空":
                    raise ValueError("Token不能为空；删除全部Token请输入 清空")
                value = ",".join(tokens)
            key = get_line_tokens_key(line) if field == "tokens" else f"{line}_{field}"
            set_config(key, value)
            if field == "tokens":
                draw_worker._reload_tokens()
        except ValueError as exc:
            import html
            await safe_edit_text(context, chat_id, msg_id, html.escape(str(exc)) + "，请重新输入。",
                                 InlineKeyboardMarkup([[IButton("取消并返回", callback_data=f"admin_line_config?line={line}")]]))
            return
        clear_state(user.id)
        await admin_line_config(update, context, msg_id, line)
    elif action == "edit_api_tokens":
        set_config("apinebula_tokens", text)
        draw_worker._reload_tokens()
        count = len([t for t in text.split(",") if t.strip()])
        await safe_edit_text(context, chat_id, msg_id, f"已更新生图API Tokens，共 {count} 个")
        clear_state(user.id)
    elif action == "edit_opt_tokens":
        set_config("optimize_tokens", text)
        count = len([t for t in text.split(",") if t.strip()])
        await safe_edit_text(context, chat_id, msg_id, f"已更新优化提示词Tokens，共 {count} 个")
        clear_state(user.id)
    elif action == "edit_line2_tokens":
        set_config("line2_tokens", text)
        draw_worker._reload_tokens()
        count = len([t for t in text.split(",") if t.strip()])
        await safe_edit_text(context, chat_id, msg_id, f"已更新KK大模型2.0 Tokens，共 {count} 个")
        clear_state(user.id)
    elif action == "edit_price":
        pkey = state.get("price_key", "")
        try:
            val = float(text)
            set_config(pkey, str(val))
            names = {"price_free_line1": "自由作图1.0价格", "price_free_line2": "自由作图2.0价格", "price_preset_line1": "预设1.0基础费", "price_preset_line2": "预设2.0基础费", "price_protocol": "协议叠加费", "checkin_reward_amount": "签到奖励"}
            await safe_edit_text(context, chat_id, msg_id, f"已更新{names.get(pkey, pkey)}为 {val} U")
        except ValueError:
            await safe_edit_text(context, chat_id, msg_id, "输入格式错误，请输入数字（如 0.40）")
        clear_state(user.id)
    elif action == "edit_btn_text":
        btn_key = state.get("btn_key", "")
        page = state.get("page", 0)
        set_config(btn_key, text)
        clear_state(user.id)
        await admin_config_edit(update, context, btn_key, msg_id, page)
        await admin_preview_config(update, context, btn_key, msg_id)
    elif action == "edit_btn_url":
        btn_key = state.get("btn_key", "")
        page = state.get("page", 0)
        set_config(f"{btn_key}_url", text)
        clear_state(user.id)
        await admin_config_edit(update, context, btn_key, msg_id, page)
        await admin_preview_config(update, context, btn_key, msg_id)
    elif action == "edit_text_image":
        # 图片消息处理在 handle_admin_input_full 中，这里兜底
        txt_key = state.get("text_key", "")
        if update.message.photo:
            photo = update.message.photo[-1]
            file_id = photo.file_id
            set_config(f"{txt_key}_image", file_id)
            await safe_edit_text(context, chat_id, msg_id, f"配图已更新：{txt_key}")
        else:
            await safe_edit_text(context, chat_id, msg_id, "请发送图片")
        clear_state(user.id)
    else:
        clear_state(user.id)

# ============================================================
# 扩展回调（配置分类等）
# ============================================================
async def handle_callback_ext(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, params: Dict):
    """handle_callback中未覆盖的扩展回调"""
    if not is_admin(update.effective_user.id):
        return
    user = update.effective_user
    chat_id = update.effective_chat.id
    msg_id = update.callback_query.message.message_id

    if action in ("admin_btn_edit_text", "admin_btn_edit_color", "admin_btn_edit_url"):
        btn_key = params["key"]
        if action == "admin_btn_edit_text":
            set_state(user.id, "admin_input", admin_action="edit_btn_text", btn_key=btn_key, message_id=msg_id, page=int(params.get("page", 0)))
            await safe_edit_text(context, chat_id, msg_id, f"修改按钮文字：{btn_key}\n\n当前值：{get_config_raw(btn_key)}\n\n请输入新文字（支持会员图标 [emoji:ID]）")
        elif action == "admin_btn_edit_url":
            set_state(user.id, "admin_input", admin_action="edit_btn_url", btn_key=btn_key, message_id=msg_id, page=int(params.get("page", 0)))
            await safe_edit_text(context, chat_id, msg_id, f"修改按钮链接：{btn_key}\n\n当前值：{get_config_raw(f'{btn_key}_url') or '（无）'}\n\n请输入链接URL（留空=无链接）")
        elif action == "admin_btn_edit_color":
            # 颜色选择面板
            current = get_config_raw(f"{btn_key}_color", "") or ""
            colors = [
                ("默认（灰）", ""),
                ("🔵 Primary（蓝）", "primary"),
                ("⚪ Secondary（白）", "secondary"),
                ("🟢 Success（绿）", "success"),
                ("🔴 Danger（红）", "danger"),
            ]
            kb = []
            for name, val in colors:
                mark = "✅ " if current == val else ""
                kb.append([IButton(f"{mark}{name}", callback_data=f"admin_btn_set_color?key={btn_key}&color={val}")])
            kb.append([IButton("返回", callback_data=f"admin_config_edit?key={btn_key}")])
            await safe_edit_text(context, chat_id, msg_id, f"选择按钮颜色：{btn_key}", InlineKeyboardMarkup(kb))
        return

    if action == "admin_btn_set_color":
        btn_key = params["key"]
        color = params.get("color", "")
        set_config(f"{btn_key}_color", color)
        await admin_config_edit(update, context, btn_key, msg_id, int(params.get("page", 0)))
        return

    if action in ("admin_text_edit_text", "admin_text_edit_image", "admin_text_clear_image"):
        txt_key = params["key"]
        image_key = f"{txt_key}_image"
        if action == "admin_text_edit_text":
            set_state(user.id, "admin_input", admin_action="edit_config", config_key=txt_key, message_id=msg_id, page=int(params.get("page", 0)))
            await safe_edit_text(context, chat_id, msg_id, f"修改文案：{txt_key}\n\n当前值：\n{get_config(txt_key)}\n\n请输入新文案（支持客户端格式、会员图标）")
        elif action == "admin_text_edit_image":
            set_state(user.id, "admin_input", admin_action="edit_text_image", text_key=txt_key, message_id=msg_id, page=int(params.get("page", 0)))
            await safe_edit_text(context, chat_id, msg_id, f"设置配图：{txt_key}\n\n请直接发送一张图片（发送 取消 退出）")
        elif action == "admin_text_clear_image":
            set_config(image_key, "")
            await admin_config_edit(update, context, txt_key, msg_id, int(params.get("page", 0)))
        return

    if action == "admin_config_cat":
        # 进入配置列表时清除残留的编辑状态，防止返回后发文字仍被当作修改
        clear_state(user.id)
        cat = params["cat"]
        configs = get_all_config()
        if cat == "text":
            keys = sorted([k for k in configs if k.startswith(("welcome", "checkin", "balance", "recharge", "okpay", "ton", "tron", "draw", "preset", "protocol", "free", "history", "profile", "example", "invite", "input", "channel", "banned", "no_history", "unknown", "back_home")) and not k.endswith("_image")])
        elif cat == "btn":
            # 底部键盘按钮优先排前面，其余按字母排序
            bottom_keys = ["btn_profile", "btn_free_draw", "btn_checkin", "btn_recharge", "btn_example",
                           "btn_preset", "btn_free", "btn_my_design", "btn_back_home"]
            all_btn = sorted([k for k in configs if k.startswith("btn_") and not k.endswith(("_color", "_url"))])
            keys = [k for k in bottom_keys if k in all_btn] + [k for k in all_btn if k not in bottom_keys]
        else:
            keys = sorted([k for k in configs if k.endswith("_url") or k == "service_link"])
        # 分页显示，每页15个
        page = int(params.get("page", 0))
        page_size = 15
        page_keys = keys[page*page_size:(page+1)*page_size]
        buttons = []
        for k in page_keys:
            if cat == "btn":
                # 按钮列表：显示按钮文字（清理会员图标后）+ 配置key，确保文字完整可见
                raw = get_config_raw(k, k)
                clean, emoji_id = extract_button_emoji(raw)
                display = clean or "（无文字）"
                if emoji_id:
                    display = f"[图标] {display}"
                # 截断到40字符，避免按钮过长
                if len(display) > 40:
                    display = display[:37] + "..."
                buttons.append([IButton(f"{display}  · {k}", callback_data=f"admin_config_edit?key={k}&page={page}")])
            else:
                # 文案/链接列表：显示值预览（换行转空格，截断）
                val = truncate_text(configs[k].replace("\n", " "), 30)
                buttons.append([IButton(val or "（空）", callback_data=f"admin_config_edit?key={k}&page={page}")])
        nav = []
        if page > 0:
            nav.append(IButton("上一页", callback_data=f"admin_config_cat?cat={cat}&page={page-1}"))
        if (page+1)*page_size < len(keys):
            nav.append(IButton("下一页", callback_data=f"admin_config_cat?cat={cat}&page={page+1}"))
        if nav:
            buttons.append(nav)
        buttons.append([IButton("🔄 重置为默认", callback_data=f"admin_config_reset?cat={cat}")])
        buttons.append([IButton("返回", callback_data="admin_config")])
        text = f"配置列表（{cat}）第{page+1}页，共{len(keys)}项\n\n点击修改对应内容"
        await safe_edit_text(context, chat_id, msg_id, text, InlineKeyboardMarkup(buttons))
        return
    if action == "admin_config_reset":
        cat = params["cat"]
        configs = get_all_config()
        if cat == "text":
            keys = [k for k in configs if k.startswith(("welcome", "checkin", "balance", "recharge", "okpay", "ton", "tron", "draw", "preset", "protocol", "free", "history", "profile", "example", "invite", "input", "channel", "banned", "no_history", "unknown", "back_home")) and not k.endswith("_image")]
        elif cat == "btn":
            keys = [k for k in configs if k.startswith("btn_") and not k.endswith(("_color", "_url"))]
        else:
            keys = [k for k in configs if k.endswith("_url") or k == "service_link"]
        conn = get_db()
        count = 0
        for k in keys:
            if k in DEFAULT_CONFIG:
                conn.execute("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (k, DEFAULT_CONFIG[k]))
                count += 1
        conn.commit()
        conn.close()
        await safe_edit_text(context, chat_id, msg_id, f"已重置 {count} 项配置为默认值")
        return

    if action == "admin_users_page":
        page = int(params.get("page", 0))
        await admin_users(update, context, msg_id, page)
        return

    if action == "admin_user_detail":
        tg_id = int(params["tg_id"])
        u = get_user_by_tg_id(tg_id)
        if not u:
            await safe_edit_text(context, chat_id, msg_id, "用户不存在")
            return
        status = "🚫 已封禁" if (u.get("status") == "banned") else "✅ 正常"
        text = (f"👤 用户详情\n\n"
                f"TGID：{u['tg_id']}\n"
                f"用户名：{u.get('username','-')}\n"
                f"名称：{u.get('first_name','-')}\n"
                f"余额：{u.get('balance',0):.2f} USDT\n"
                f"累计作图：{u.get('total_draws',0)} 次\n"
                f"注册时间：{u.get('created_at','-')}\n"
                f"状态：{status}")
        ban_label = "✅ 解封" if (u.get("status") == "banned") else "🚫 封禁"
        keyboard = InlineKeyboardMarkup([
            [IButton("💰 加余额", callback_data=f"admin_user_action?tg_id={tg_id}&action=add_balance")],
            [IButton("💸 扣余额", callback_data=f"admin_user_action?tg_id={tg_id}&action=sub_balance")],
            [IButton(ban_label, callback_data=f"admin_user_action?tg_id={tg_id}&action=toggle_ban")],
            [IButton("返回用户列表", callback_data="admin_users")],
        ])
        await safe_edit_text(context, chat_id, msg_id, text, keyboard)
        return

    if action == "admin_preset_field":
        preset_id = int(params["id"])
        field = params["field"]
        set_state(user.id, "admin_input", admin_action="edit_preset_field",
                  preset_id=preset_id, field=field, message_id=msg_id)
        conn = get_db()
        p = conn.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
        conn.close()
        current = ""
        hint = "请输入新值"
        if field == "name": current = p["name"]
        elif field == "price": current = str(p["price"])
        elif field == "image": current = "（发送一张图片作为模板参考底图）"; hint = "请发送一张图片"
        elif field == "prompt_template": current = (p["prompt_template"] or "（未设置）")[:100]; hint = "请输入提示词模板（用户输入的内容会自动追加在下面）"
        elif field == "detail_image": current = "（发送一张图片，不传则用模板图）"; hint = "请发送一张图片"
        elif field == "demand_image": current = "（发送一张图片，可选）"; hint = "请发送一张图片"
        elif field == "fields": current = p["fields"] or "（逗号分隔，如：网站名称,网站链接,标题,客服,价格表）"; hint = "请输入字段列表，用英文逗号分隔"
        await safe_edit_text(context, chat_id, msg_id, f"修改预设 {field}\n当前值：{current}\n\n{hint}")
    elif action == "admin_preset_toggle":
        preset_id = int(params["id"])
        conn = get_db()
        conn.execute("UPDATE presets SET enabled=1-enabled WHERE id=?", (preset_id,))
        conn.commit()
        conn.close()
        await admin_presets_list(update, context, msg_id)
    else:
        log.warning(f"未处理扩展回调: {action}")

# 在handle_callback中扩展调用（覆盖admin_config_cat等）
# 重新定义handle_callback以包含扩展
_original_handle_callback = handle_callback
async def handle_callback_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    action, params = parse_action(data)
    if action in ("admin_config_cat", "admin_preset_field", "admin_preset_toggle",
                  "admin_btn_edit_text", "admin_btn_edit_color", "admin_btn_edit_url", "admin_btn_set_color",
                  "admin_text_edit_text", "admin_text_edit_image", "admin_text_clear_image",
                  "admin_config_reset",
                  "admin_users_page", "admin_user_detail"):
        await query.answer()
        await handle_callback_ext(update, context, action, params)
        return
    await _original_handle_callback(update, context)

# 管理员预设字段输入处理（在handle_admin_input中扩展）
_original_handle_admin_input = handle_admin_input
async def handle_admin_input_full(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    action = state.get("admin_action")
    if action == "scoped_input":
        if not is_admin(update.effective_user.id):
            return
        group=state.get("group"); index=state.get("index",-1)
        if group not in SCOPED_FIELDS or not isinstance(index,int) or not 0<=index<len(SCOPED_FIELDS[group]):
            clear_state(update.effective_user.id)
            return
        label,key,kind=SCOPED_FIELDS[group][index]
        msg_id=state["message_id"]
        if (update.message.text or "").strip() in ("取消","退出","cancel"):
            await scoped_panel(update,context,msg_id,group,index)
            return
        if state.get("operation")=="image":
            if not update.message.photo:
                await safe_edit_text(context,update.effective_chat.id,msg_id,"请发送图片，或发送 取消。")
                return
            value=update.message.photo[-1].file_id
            key+="_image"
        else:
            value=extract_custom_emojis_from_message(update.message).strip()
            if not value:
                return
        set_config(key,value)
        clear_state(update.effective_user.id)
        await safe_delete(context,update.effective_chat.id,update.message.message_id)
        await scoped_panel(update,context,msg_id,group,index)
        return
    # 图片消息：设置文案配图
    if action == "edit_text_image" and update.message.photo:
        user = update.effective_user
        txt_key = state.get("text_key", "")
        msg_id = state.get("message_id")
        chat_id = update.effective_chat.id
        photo = update.message.photo[-1]
        file_id = photo.file_id
        set_config(f"{txt_key}_image", file_id)
        await safe_delete(context, chat_id, update.message.message_id)
        clear_state(user.id)
        page = state.get("page", 0)
        await admin_config_edit(update, context, txt_key, msg_id, page)
        await admin_preview_config(update, context, txt_key, msg_id)
        return
    if action == "edit_preset_field":
        user = update.effective_user
        msg_id = state.get("message_id")
        chat_id = update.effective_chat.id
        await safe_delete(context, chat_id, update.message.message_id)
        preset_id = state["preset_id"]
        field = state["field"]
        conn = get_db()
        # 图片类型字段
        if field in ("image", "detail_image", "demand_image") and update.message.photo:
            file_id = update.message.photo[-1].file_id
            col = "template_image" if field == "image" else field
            conn.execute(f"UPDATE presets SET {col}=? WHERE id=?", (file_id, preset_id))
        else:
            text = extract_custom_emojis_from_message(update.message).strip()
            if field == "name":
                conn.execute("UPDATE presets SET name=? WHERE id=?", (text, preset_id))
            elif field == "price":
                conn.execute("UPDATE presets SET price=? WHERE id=?", (float(text), preset_id))
            elif field == "prompt_template":
                conn.execute("UPDATE presets SET prompt_template=? WHERE id=?", (text, preset_id))
            elif field == "image":
                conn.execute("UPDATE presets SET template_image=? WHERE id=?", (text, preset_id))
            elif field in ("detail_image", "demand_image"):
                conn.execute(f"UPDATE presets SET {field}=? WHERE id=?", (text, preset_id))
            elif field == "fields":
                conn.execute("UPDATE presets SET fields=? WHERE id=?", (text, preset_id))
        conn.commit()
        conn.close()
        await admin_preset_edit(update, context, preset_id, msg_id)
        clear_state(user.id)
        return
    await _original_handle_admin_input(update, context, state)

# 替换handle_state_input中的admin_input处理引用
async def handle_state_input_full(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict):
    if state["state"] == "admin_input":
        await handle_admin_input_full(update, context, state)
    else:
        await handle_state_input(update, context, state)


# ============================================================
# Web 回调服务（Flask）
# ============================================================
from flask import Flask, request, jsonify

web_app = Flask(__name__)
bot_instance: Optional[Bot] = None
main_loop: Optional[asyncio.AbstractEventLoop] = None

@web_app.route("/okpay/notify", methods=["POST", "GET"])
def okpay_notify():
    """OkPay异步回调"""
    try:
        data = request.form.to_dict() if request.method == "POST" else request.args.to_dict()
        order_id = data.get("unique_id") or data.get("out_trade_no") or ""
        status = data.get("status", "")
        log.info(f"[OkPay回调] {data}")
        if order_id and status in ("success", "paid", "completed"):
            order = get_order(order_id)
            if order and order["status"] == "pending":
                # 在线程中处理（因为是同步Flask上下文）
                if main_loop:
                    asyncio.run_coroutine_threadsafe(
                        _process_okpay_notify(order_id, data),
                        main_loop
                    )
        return jsonify({"code": 0, "msg": "success"})
    except Exception as e:
        log.error(f"OkPay回调异常: {e}")
        return jsonify({"code": -1, "msg": str(e)})

async def _process_okpay_notify(order_id: str, data: Dict):
    # 回调只作为查单触发器，不相信未验证的HTTP请求内容。
    order = get_order(order_id)
    if not order or order["status"] != "pending" or order["pay_method"] != "okpay":
        return
    client = get_okpay_client()
    if not client:
        return
    response = await client.check_deposit(order_id)
    verified = response.get("data") or {}
    if verified.get("status") not in ("success", "paid", "completed"):
        return
    from types import SimpleNamespace
    await on_payment_success_raw(SimpleNamespace(bot=bot_instance), order, txid=verified.get("txid", ""))
def run_web_server():
    """在独立线程运行Flask"""
    import threading
    def _run():
        web_app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    log.info(f"Web回调服务已启动: http://0.0.0.0:{WEB_PORT}")

# ============================================================
# 定时任务
# ============================================================
async def check_expired_orders(context: ContextTypes.DEFAULT_TYPE):
    """检查过期订单"""
    now = now_str()
    conn = get_db()
    rows = conn.execute("SELECT order_id FROM orders WHERE status='pending' AND expire_at < ?", (now,)).fetchall()
    for r in rows:
        conn.execute("UPDATE orders SET status='expired' WHERE order_id=? AND status='pending'", (r["order_id"],))
    conn.commit()
    conn.close()
    if rows:
        log.info(f"已过期 {len(rows)} 个订单")

async def check_pending_orders(context: ContextTypes.DEFAULT_TYPE):
    """轮询待支付订单（OkPay + TON + TRON）"""
    orders = get_pending_orders()
    for order in orders:
        try:
            if order["pay_method"] == "okpay":
                client = get_okpay_client()
                if not client:
                    continue
                result = await client.check_deposit(order["order_id"])
                data = result.get("data", {})
                status = data.get("status", "")
                if status in ("success", "paid", "completed"):
                    await on_payment_success_raw(context, get_order(order["order_id"]), txid=data.get("txid", ""))
                    log.info(f"[轮询] OkPay支付成功: {order['order_id']}")
            elif order["pay_method"] == "ton_usdt":
                ok = await check_ton_usdt_order(context, order["order_id"])
                if ok:
                    log.info(f"[轮询] TON支付成功: {order['order_id']}")
            elif order["pay_method"] == "tron_usdt":
                ok = await check_tron_usdt_order(context, order["order_id"])
                if ok:
                    log.info(f"[轮询] TRON支付成功: {order['order_id']}")
        except Exception as e:
            log.error(f"[轮询] 订单检测失败 {order['order_id']}: {e}")

# ============================================================
# 错误处理
# ============================================================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error=context.error
    log.error("handler_error tenant=%s update=%s type=%s", CLONE_ID, getattr(update,'update_id',None),type(error).__name__)
    if isinstance(update, Update) and update.effective_message:
        try:
            await context.bot.send_message(update.effective_chat.id,"本次操作未完成，请重试；如页面状态异常，发送 /start 返回首页。")
        except TelegramError:
            pass
# ============================================================
# 启动
# ============================================================
async def post_init(app: Application):
    global bot_instance
    bot_instance = app.bot
    log.info("生图机器人启动中...")
    # 设置快捷指令：普通用户只有两个，管理员额外可见 /admin
    user_commands = [
        BotCommand("start", "开始使用"),
        BotCommand("qd", "领取新用户签到奖励"),
    ]
    admin_commands = [
        BotCommand("start", "开始使用"),
        BotCommand("qd", "领取新用户签到奖励"),
        BotCommand("admin", "管理员后台"),
    ]
    try:
        await app.bot.set_my_commands(user_commands)
        # 给每个管理员单独设置含admin的指令列表
        for admin_id in ADMIN_IDS:
            try:
                await app.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as e2:
                log.warning(f"设置管理员{admin_id}指令失败: {e2}")
        log.info("快捷指令已设置（用户2个，管理员3个含/admin）")
    except Exception as e:
        log.error(f"设置快捷指令失败: {e}")
    # 启动Web服务
    if not CLONE_ID:
        run_web_server()
    log.info(f"Bot已连接: @{BOT_USERNAME}")
    # 恢复重启前未完成的生图任务（持久化）
    asyncio.create_task(resume_pending_tasks(app))

async def resume_pending_tasks(app):
    """启动时恢复生图任务：
    - pending（还没开始调API）→ 安全恢复，重新执行（不会重复消耗额度）
    - processing（执行到一半被中断）→ 退余额+标记失败+通知用户，不重新执行（避免重复消耗API额度）
    """
    await asyncio.sleep(3)  # 等bot完全启动
    conn = get_db()
    # 1. 处理被中断的processing任务：退余额+标记失败
    interrupted = conn.execute("SELECT * FROM draw_tasks WHERE status='processing'").fetchall()
    for row in interrupted:
        try:
            # 退还余额
            if not refund_draw_once(row["task_id"], "服务器重启导致任务中断，余额已退回"):
                continue
            log.info(f"[生图恢复] 任务{row['task_id']}因重启中断，已退余额{row['cost']}")
            # 通知用户
            if row["chat_id"]:
                try:
                    await app.bot.send_message(
                        chat_id=row["chat_id"],
                        text=f"⚠️ 作图任务因服务器重启中断\n\n任务编号：{row['task_id']}\n已退还 {row['cost']} USDT 到您的余额，请重新生成",
                        parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"[生图恢复] 处理中断任务{row['task_id']}失败: {e}")
    # 2. 恢复pending任务（还没调API，安全恢复）
    pending = conn.execute("SELECT * FROM draw_tasks WHERE status='pending' AND chat_id>0").fetchall()
    conn.close()
    if not pending and not interrupted:
        return
    log.info(f"[生图恢复] pending任务{len(pending)}个恢复执行，interrupted任务{len(interrupted)}个已退余额")
    for row in pending:
        try:
            asyncio.create_task(run_draw_task(row["task_id"], row["chat_id"], row["message_id"], app))
            await asyncio.sleep(0.5)  # 错开避免并发冲击
        except Exception as e:
            log.warning(f"[生图恢复] 任务{row['task_id']}恢复失败: {e}")


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询