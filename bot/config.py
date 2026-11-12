#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
"""全局配置、常量与默认文案。"""
import os
import sys
import json
import logging
import re

# 项目根目录（bot/ 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path=None):
    """零依赖读取项目根目录的 .env，已存在的环境变量优先。"""
    path = path or os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()

# 机器人 Token：只能通过环境变量 / .env 注入（参考 .env.example），源码中不保留任何密钥
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
# 机器人用户名（不含 @）
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip()
# 管理员 TGID，多个用英文逗号分隔，例如 ADMIN_IDS=123456789,987654321
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").replace("，", ",").split(",") if x.strip().lstrip("-").isdigit()]
# Web 服务端口（OkPay 异步回调）
WEB_PORT = int(os.environ.get("WEB_PORT", "5003"))
# 数据库和生成图片都放项目根目录
DB_PATH = os.path.join(BASE_DIR, "image_bot.db")
SAVE_DIR = os.path.join(BASE_DIR, "generated_images")
os.makedirs(SAVE_DIR, exist_ok=True)

# ApiNebula (KK大模型1.0)
APINEBULA_BASE_URL = "https://apinebula.ai/v1"
# TransitAI (KK大模型2.0)
LINE2_BASE_URL = "https://transitai.chat/v1"
DRAW_MODEL_DEFAULT = "gpt-image-2"  # 默认模型，实际从配置draw_model读取
MAX_CONCURRENT_DRAWS = 5
DRAW_TIMEOUT_SECONDS = max(120, int(os.environ.get("DRAW_TIMEOUT_SECONDS", "600")))
IMAGE_DOWNLOAD_TIMEOUT = 120
MAX_IMAGE_BYTES = 50 * 1024 * 1024
PHOTO_TARGET_BYTES = 4 * 1024 * 1024
# 各线路支持的模型列表
LINE1_MODELS = ["gpt-image-2", "gpt-image-2.5"]
LINE2_MODELS = ["gpt-image-2", "gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"]

# 生图定价（默认值，后台可修改）
PRICE_FREE_LINE1 = 0.30      # 我来发挥 KK1.0
PRICE_FREE_LINE2 = 0.40      # 我来发挥 KK2.0
PRICE_PRESET_LINE1 = 0.15    # 官方预设基础费 KK1.0
PRICE_PRESET_LINE2 = 0.25    # 官方预设基础费 KK2.0
PRICE_PROTOCOL = 0.30       # 协议叠加费
CHECKIN_REWARD = 0.30       # 签到奖励

# 充值预设
RECHARGE_PRESETS = [10, 30, 50, 70, 100, 150]

# 日志
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("image_bot")
# HTTPX INFO 日志包含 Telegram Token；同时对所有根日志输出脱敏。
logging.getLogger("httpx").setLevel(logging.WARNING)
class SecretFilter(logging.Filter):
    def filter(self, record):
        import re
        record.msg = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<REDACTED>", record.getMessage())
        record.args = ()
        return True
for _handler in logging.getLogger().handlers:
    _handler.addFilter(SecretFilter())

# 生图任务线路映射（task_id -> line），重启后丢失不影响（预设固定用当前线路）
TASK_LINE_MAP = {}

# ============================================================
# 数据库
# ============================================================
DEFAULT_CONFIG = {
    # 文案
    "welcome_text": "Hi 有问题联系 @sad1881\n发送 /qd 可免费作图一次",
    "checkin_success": "签到成功\n获得奖励：0.30 USDT\n当前余额：{balance} USDT",
    "checkin_already": "签到奖励已经领取\n\n每个账号仅可领取一次，不可重复签到\n已领取奖励：0.30 USDT",
    "balance_insufficient": "余额不足，请先充值",
    "recharge_title": "请选择下面带充值订单金额\n\n请严格按照小数点金额转账！",
    "recharge_order_text": "充值订单\n\n订单编号：{order_id}\n充值金额：{amount} USDT\n预计到账：{amount} USDT\n{user_info}\n当前时间：{time}\n\n请选择下方支付方式",
    "recharge_creating": "正在发起订单请求中....\n\n订单编号：{order_id}\n充值金额：{amount} USDT\n\n请稍等，不要重复点击支付方式。",
    "okpay_pay_text": "OkPay 充值\n\n{user_info}\n付款金额：{amount} USDT\n到期时间：{expire}\n\n请点击下方按钮授权支付",
    "ton_pay_text": "💎 TokenPay-USDT (TON链) 充值\n\n订单编号：{order_id}\n付款金额：<b>{amount} USDT</b>\n收款地址：\n<code>{address}</code>\n转账备注(Comment)：\n<code>{memo}</code>\n\n⚠️ 备注必须填写订单号，否则无法自动识别！",
    "tron_pay_text": "🔶 TokenPay-TRX (波场) 充值\n\n订单编号：{order_id}\n付款金额：<code>{amount}</code> USDT\n收款地址：\n<code>{address}</code>\n\n⚠️ 金额必须完全一致，否则无法自动识别！",
    "recharge_success": "支付成功！\n\n{user_info}\n付款金额：{amount}\n付款时间：{time}\n当前余额：{balance} USDT\n支付方式：{method}",
    "draw_loading": "正在生成图片\n\n任务编号：{task_id}\n\n请稍等，生成结果将在此处原位更新。",
    "draw_done": "作图完成\n\n任务编号：{task_id}",
    "draw_failed": "作图失败：{error}\n\n已退回扣除的余额",
    "preset_select": "官方预设\n\n请选择下方预设模型",
    "preset_detail": "{name}\n\n预设费用：{price} U\n\n请发送预设需求\n\n{fields_hint}\n\n预设费用：{price} U\n\n请点击确认选择作图协议",
    "preset_preview": "{name}\n\n预设费用：{price} U",
    "protocol_select": "选择作图协议\n官方预设：{name}\n预设费用：{price} U\n\n选择协议后将叠加协议费用，按钮显示本次总扣费",
    "free_step1": "第一步 · 参考图片\n\n请上传合法参考图！\n\n支持格式：JPG / PNG / WebP\n上传限制：最多 10 张，单张不超过 20MB。",
    "free_step2": "第二步 · 图片描述\n\n请描述图片生成词！",
    "free_step3": "第三步 · 图片分辨率\n\n请选择下方常用分辨率",
    "free_summary": "作图参数已完成\n\n参考图：{ref_count} 张\n包含提示词：{has_prompt}\n分辨率：{size}",
    "history_text": "以下是你的作图设计！\n\n点击下方提示词预览图片",
    "profile_text": "个人信息\n\n用户ID：{bot_uid}\nTGUID：{tg_id}\n用户名：{username}\n用户名称：{first_name}\n账户余额：{balance} USDT\n累计作图：{draws} 次\n下级数量：{invites}\n账户状态：{status}\n注册时间：{created}\n邀请链接：{invite_link}",
    "example_text": "作图示例\n\n<a href=\"{url}\">点击查看作图示例</a>",
    "draw_type_select": "全能作图\n\n请选择下方作图方式",
    "invite_reward_text": "您邀请的用户 {name} 购买了商品 {item}，您获得了 {amount} USDT，可用于提现或购买",
    # 按钮
    "btn_profile": "个人信息",
    "btn_free_draw": "全能作图",
    "btn_checkin": "新用户签到",
    "btn_recharge": "余额充值",
    "btn_example": "作图示例",
    "btn_preset": "官方预设",
    "btn_free": "我来发挥",
    "btn_my_design": "我的设计",
    "btn_back_home": "返回首页",
    "btn_share_invite": "分享邀请链接",
    "btn_custom_amount": "自定义金额",
    "btn_recharge_10": "10USDT", "btn_recharge_30": "30USDT", "btn_recharge_50": "50USDT", "btn_recharge_70": "70USDT", "btn_recharge_100": "100USDT", "btn_recharge_150": "150USDT",
    "btn_cancel_recharge": "取消充值",
    "btn_okpay": "OkPay-USDT",
    "btn_ton": "TokenPay-USDT",
    "btn_tron": "TokenPay-TRX",
    "btn_close_pay": "关闭支付",
    "btn_go_pay": "前往支付",
    "btn_refresh_pay": "我已付款，刷新状态",
    "btn_preview": "预览预设",
    "btn_back": "返回",
    "btn_confirm": "确认",
    "btn_back_preset": "返回预设",
    "btn_cancel_draw": "取消作图",
    "btn_prev_step": "上一步",
    "btn_next_step": "下一步",
    "btn_no_ref": "无参考图",
    "btn_again": "再次作图",
    "btn_view_works": "去看看大家的作品",
    "btn_recharge_small": "充值余额",
    # 按钮颜色
    "btn_profile_color": "", "btn_free_draw_color": "", "btn_checkin_color": "", "btn_recharge_color": "",
    "btn_example_color": "", "btn_preset_color": "", "btn_free_color": "", "btn_my_design_color": "",
    "btn_back_home_color": "", "btn_share_invite_color": "", "btn_custom_amount_color": "", "btn_cancel_recharge_color": "",
    "btn_optimize_color": "", "btn_use_optimized_color": "", "btn_reoptimize_color": "", "btn_use_original_color": "",
    "btn_okpay_color": "", "btn_ton_color": "", "btn_tron_color": "", "btn_close_pay_color": "",
    "btn_go_pay_color": "", "btn_refresh_pay_color": "", "btn_preview_color": "", "btn_back_color": "",
    "btn_confirm_color": "", "btn_back_preset_color": "", "btn_cancel_draw_color": "", "btn_prev_step_color": "",
    "btn_next_step_color": "", "btn_no_ref_color": "", "btn_again_color": "", "btn_view_works_color": "",
    "btn_recharge_small_color": "",
    # 文案配图
    "welcome_text_image": "", "profile_text_image": "", "recharge_title_image": "", "draw_type_select_image": "", "free_step1_image": "",
    # 链接
    "example_url": "https://t.me/your_channel",
    "works_url": "https://t.me/your_channel",
    "service_link": "@sad1881",
    # 支付配置
    "okpay_enabled": "1", "okpay_appid": "", "okpay_token": "", "okpay_api_url": "https://api.okaypay.me/shop/", "okpay_callback_url": "",
    "ton_usdt_enabled": "1", "ton_usdt_address": "", "ton_api_key": "", "ton_usdt_rate": "7.0",
    "tron_usdt_enabled": "1", "tron_usdt_address": "", "tron_api_key": "", "tron_usdt_rate": "7.0",
    # API Tokens
    "apinebula_tokens": "",
    # 生图
    "draw_quality": "high", "draw_size_default": "auto", "draw_model": "gpt-image-2",
    # 占位符
    "input_placeholder": "请选择功能",
    # 自动发频道
    "channel_id": "@your_channel",
    "channel_enabled": "1",
    "channel_btn_text": "我也要做",
    "channel_btn_url": "https://t.me/your_bot",
    "channel_text": "来自用户ID：{user_id}  花费 {cost} USDT 制作\n\n提示词：\n<blockquote expandable>{prompt}</blockquote>",
    # 价格配置（后台可修改）
    "price_free_line1": "0.30",
    "price_free_line2": "0.40",
    "price_preset_line1": "0.15",
    "price_preset_line2": "0.25",
    "price_protocol": "0.30",
    "checkin_reward_amount": "0.30",
    # 生图线路
    "draw_line": "line1",
    "line2_tokens": "",
    # 系统提示
    "banned_text": "您的账号已被封禁，如有疑问联系客服。",
    "no_history_text": "暂无作图记录",
    "unknown_command_text": "请选择下方功能",
    "back_home_text": "已返回首页",
    "invite_self_text": "这是你自己的邀请链接，你不能邀请自己",
    "invite_already_text": "您已被邀请，无法再次被他人邀请",
    "invite_success_text": "邀请成功！欢迎加入",
    # 优化提示词功能
    "optimize_enabled": "1",
    "optimize_model": "gpt-5.5",
    "optimize_tokens": "",
    "optimize_prompt": "你是一个专业的AI绘画提示词优化专家。用户会给你一个简单的生图提示词，请将其扩写为详细、高质量、适合AI图像生成模型的英文提示词。要求：1.补充画面主体、场景环境、光影氛围、构图视角、风格流派、画质细节等元素；2.输出纯提示词，不要任何解释、引号或前缀；3.长度控制在50-150个英文单词；4.如果用户输入已经是英文，直接优化增强；5.不要包含任何文字说明，只输出优化后的提示词本身。",
    "optimize_title": "提示词优化",
    "optimizing_text": "正在AI优化提示词，请稍候...",
    "optimize_result_text": "原始提示词：\n{original}\n\n优化后：\n{optimized}",
    "optimize_failed_text": "提示词优化失败，将使用原始提示词生成",
    "btn_optimize": "✨ AI优化提示词",
    "btn_use_optimized": "✅ 用优化后生成",
    "btn_reoptimize": "🔄 重新优化",
    "btn_use_original": "用原始提示词生成",
    "optimize_limit_text": "本次生图已优化{count}次，达到上限5次，请直接生成",
    # 预设功能文案
    "preset_detail_text": "{name}\n\n预设费用：{price} U\n\n{prompt}\n\n点击下方按钮开始制作",
    "preset_demand_text": "请发送你的需求/内容\n\n例如：网站名、联系方式等需要修改的信息",
    "preset_confirm_text": "{name}\n\n预设费用：{price} U\n\n你的需求：\n{demand}\n\n确认后选择作图协议",
}

PAY_GROUPS = {
    "ton": ("TON-USDT", "ton_usdt_enabled", [("收款地址", "ton_usdt_address")], "ton_pay_text"),
    "tron": ("TRON-USDT", "tron_usdt_enabled", [("收款地址", "tron_usdt_address")], "tron_pay_text"),
    "ok": ("OkPay", "okpay_enabled", [("AppID", "okpay_appid"), ("Token", "okpay_token"), ("接口地址", "okpay_api_url")], "okpay_pay_text"),
}
SCOPED_FIELDS = {
    "ton": [("支付文案", "ton_pay_text", "text"), ("刷新按钮", "btn_refresh_pay", "button"), ("关闭按钮", "btn_close_pay", "button")],
    "tron": [("支付文案", "tron_pay_text", "text"), ("刷新按钮", "btn_refresh_pay", "button"), ("关闭按钮", "btn_close_pay", "button")],
    "ok": [("支付文案", "okpay_pay_text", "text"), ("付款按钮", "btn_go_pay", "button"), ("刷新按钮", "btn_refresh_pay", "button"), ("关闭按钮", "btn_close_pay", "button")],
    "preset": [("列表按钮模板", "btn_ps_item", "button"), ("协议按钮模板", "btn_ps_total", "button"),
               ("支付金额按钮模板", "btn_ps_amount", "button"),
               ("预览按钮", "btn_preview", "button"), ("返回预设", "btn_back_preset", "button"), ("取消作图", "btn_cancel_draw", "button")],
}
SCOPED_DEFAULTS = {"btn_ps_item": "{name} {price}U", "btn_ps_total": "KK大模型 · 总计 {total}U", "btn_ps_amount": "支付 {total}U"}

PLATFORM_DB = DB_PATH
CLONE_ID = int(sys.argv[sys.argv.index('--clone-id') + 1]) if '--clone-id' in sys.argv else 0
CLONE_OWNER = 0
PLATFORM_ADMINS = tuple(ADMIN_IDS)
TENANT_TABLES = ('users','orders','draw_tasks','config','presets','invite_records','processed_txs')
CLONE_PROCESSES = {}
CLONE_RETRY = {}
CLONE_LOCKS = {}
INSTANCE_LOCK = None

UPSTREAM_KEYS = {'apinebula_tokens','line2_tokens','line1_base_url','line2_base_url',
    'line1_draw_model','line2_draw_model','line1_timeout','line2_timeout','draw_model',
    'optimize_tokens','optimize_model','optimize_prompt','optimize_enabled'}
CLONE_UI_DEFAULTS = {
    'btn_clone_entry':'克隆机器人', 'btn_clone_manage':'管理我的',
    'btn_clone_balance':'查看余额', 'btn_clone_recharge':'余额充值', 'btn_clone_home':'返回首页',
    'btn_clone_buy':'确认余额支付', 'btn_clone_token':'提交机器人 Token',
    'clone_service_text':'克隆服务：独立机器人、独立收款和文案。\n开通费 {fee}U / 个。\n生图成本：KK1.0 {cost1}U，KK2.0 {cost2}U。\n成本从您在主机器人的余额扣除，余额不足停止新作图。',
    'clone_purchase_text':'可用克隆名额：{credits}\n请支付 {fee}U 解锁一个机器人。已有名额可直接提交 Token。',
}
SCOPED_DEFAULTS.update(CLONE_UI_DEFAULTS)
SCOPED_FIELDS['clone']=[('克隆入口','btn_clone_entry','button'),('管理我的','btn_clone_manage','button'),
    ('查看余额','btn_clone_balance','button'),('余额充值','btn_clone_recharge','button'),('返回首页','btn_clone_home','button'),
    ('确认支付','btn_clone_buy','button'),('提交Token','btn_clone_token','button'),
    ('服务介绍','clone_service_text','text'),('开通提示','clone_purchase_text','text')]


# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询