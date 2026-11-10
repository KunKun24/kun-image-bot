#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
"""程序入口：装配覆盖链、注册 handler 并启动轮询。"""
import os
import sys
import pathlib
import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, TypeHandler, filters,
)

from .config import *
from .text import *
from .db import *
from .utils import *
from .drawing import *
from .payments import *
from .handlers import *
from . import handlers as _handlers
from . import platform as _platform
from .platform import *


# ============================================================
# 覆盖链装配（等价于原单文件中“后定义覆盖先定义”的效果）
# ============================================================
_handlers.handle_keyboard = _platform.handle_keyboard
_handlers.handle_callback_full = _platform.handle_callback_full
_handlers.handle_admin_input_full = _platform.handle_admin_input_full
_handlers.show_admin_menu = _platform.show_admin_menu
_handlers.post_init = _platform.post_init

def main():
    global main_loop
    if not BOT_TOKEN:
        raise SystemExit("请先设置 BOT_TOKEN 环境变量（使用 BotFather 新生成的Token）")
    init_db()
    migrate_line_configs()
    init_chain_payments()
    log.info("数据库初始化完成")
    main_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(main_loop)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(32)
        .post_init(post_init)
        .post_shutdown(platform_shutdown)
        .build()
    )

    from telegram.ext import TypeHandler
    app.add_handler(TypeHandler(Update, platform_guard), group=-100)
    if not CLONE_ID:
        app.add_handler(CommandHandler("clone", clone_home))
    # 命令
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("qd", cmd_checkin))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # 底部键盘消息
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_keyboard))

    # 图片消息（自由作图第一步接收图片）
    async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        state = get_state(user.id)
        if state and state["state"] == "admin_input" and state.get("admin_action") in ("edit_text_image", "edit_preset_field", "scoped_input"):
            # 管理员设置文案配图 / 预设模板图片
            await handle_admin_input_full(update, context, state)
        elif state and state["state"] == "free_step1":
            await handle_free_input(update, context, state)
        else:
            await update.message.reply_text("请先选择作图功能")
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # 回调
    app.add_handler(CallbackQueryHandler(handle_callback_full))

    # 错误
    app.add_error_handler(error_handler)

    # 定时任务
    job_queue = app.job_queue
    if job_queue is None:
        raise SystemExit('缺少 JobQueue 依赖，请执行：pip install "python-telegram-bot[job-queue]"')
    job_queue.run_repeating(check_expired_orders, interval=60, first=10)
    job_queue.run_repeating(check_pending_orders, interval=15, first=15)

    log.info("生图机器人启动完成")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

_original_main=main


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
def main():
    global BOT_TOKEN,BOT_USERNAME,ADMIN_IDS,CLONE_OWNER,INSTANCE_LOCK
    import fcntl
    lockpath=pathlib.Path(PLATFORM_DB).with_name(f'.bot_{CLONE_ID}.lock')
    INSTANCE_LOCK=open(lockpath,'a')
    try: fcntl.flock(INSTANCE_LOCK,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('该机器人实例已经运行，请勿重复启动')
    if CLONE_ID:
        r=clone_record()
        if not r or r['status']=='deleted': raise SystemExit('克隆已删除')
        CLONE_OWNER=r['owner_id']; ADMIN_IDS=[CLONE_OWNER]
        BOT_TOKEN=clone_cipher().decrypt(r['token_cipher'].encode()).decode(); BOT_USERNAME=r['username']
        # Never insert primary merchant credentials into a new tenant.
        for k in list(DEFAULT_CONFIG):
            if k in UPSTREAM_KEYS or (k.startswith(('okpay','ton_','tron_','channel_')) and k not in ('okpay_pay_text','ton_pay_text','tron_pay_text')):
                DEFAULT_CONFIG[k]='0' if k.endswith('_enabled') else ''
    else:
        init_db(); platform_init(); clone_cipher()
    init_db();tenant_migrate()
    _original_main()



# ==================== 克隆后台交互修订 ====================

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创