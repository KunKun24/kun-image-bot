#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创
"""困困生图机器人 · 模块化版本。

包结构：
    config   全局配置、常量、默认文案
    text     底层文本/时间/编号工具
    db       SQLite 访问、配置读写、用户/订单/任务、克隆租户隔离
    utils    按钮、会话状态、消息安全编辑
    drawing  生图核心（上游 API、图片规范化、任务执行与交付）
    payments 支付（OkPay / TON-USDT / TRON-USDT）
    handlers 用户端与管理员端的指令、回调、定时任务
    platform 克隆平台（租户进程管理、共享账务、内容审核）
    main     装配覆盖链并启动
"""
__version__ = "2.0.0"

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询 / 本源码由 DeepSeek V4.1 / V4 Pro 开发 转载请尊重原创