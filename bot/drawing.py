#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
"""生图核心：上游 API 调用、图片规范化、任务执行与交付。"""
import os
import io
import json
import time
import uuid
import base64
import random
import asyncio
import logging
from io import BytesIO
from pathlib import Path
from pathlib import Path
from pathlib import Path
from pathlib import Path
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import aiohttp
import requests
import urllib3
from PIL import Image, ImageOps

from telegram import InputMediaPhoto
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError, BadRequest, TimedOut

from .config import *
from .text import *
from .text import _strip_html_tags
from .db import *
from .utils import *
from .utils import _mask_user_id

async def read_draw_response(response):
    """保留非JSON上游错误摘要，不将HTML网关错误误报为图片解析失败。"""
    try:
        result = await response.json(content_type=None)
    except (ValueError, UnicodeError):
        body = (await response.text(errors="replace"))[:500]
        raise RuntimeError(f"生图接口HTTP {response.status}，返回非JSON内容: {body}")
    if not isinstance(result, dict):
        raise RuntimeError(f"生图接口HTTP {response.status}，响应格式不是JSON对象")
    return result


def normalize_generated_photo(raw, destination):
    """所有URL/base64输出统一转换为RGB JPEG，限制字节数、尺寸及长宽比。"""
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片为空或超过50MB处理上限")
    with Image.open(BytesIO(raw)) as original:
        im = ImageOps.exif_transpose(original)
        im.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
        if im.mode != "RGB":
            rgba = im.convert("RGBA")
            im = Image.new("RGB", rgba.size, "white")
            im.paste(rgba, mask=rgba.getchannel("A"))
        # Telegram照片长宽比不能超过20，留出余量，不裁剪用户图片。
        w, h = im.size
        nw, nh = max(w, (h + 18)//19), max(h, (w + 18)//19)
        if (nw, nh) != (w, h):
            padded = Image.new("RGB", (nw, nh), "white")
            padded.paste(im, ((nw-w)//2, (nh-h)//2))
            im = padded
        for _ in range(8):
            for quality in (90, 80, 70, 60):
                buf = BytesIO()
                im.save(buf, "JPEG", quality=quality, optimize=True)
                payload = buf.getvalue()
                if len(payload) <= PHOTO_TARGET_BYTES:
                    Path(destination).write_bytes(payload)
                    log.info("[生图] 上传图片 %sx%s，%.2fMB", *im.size, len(payload)/1024/1024)
                    return str(destination)
            im = im.resize((max(1, im.width*3//4), max(1, im.height*3//4)), Image.Resampling.LANCZOS)
    raise ValueError("图片压缩后仍超过上传上限")


async def prepare_generated_photo(source):
    if isinstance(source, bytes):
        raw = source
    elif isinstance(source, str) and source.startswith(("https://", "http://")):
        timeout = aiohttp.ClientTimeout(total=IMAGE_DOWNLOAD_TIMEOUT, connect=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(source) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_IMAGE_BYTES:
                        raise ValueError("下载图片超过50MB处理上限")
                raw = bytes(raw)
    else:
        path = Path(source)
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("图片超过50MB处理上限")
        raw = await asyncio.to_thread(path.read_bytes)
    destination = Path(SAVE_DIR) / (uuid.uuid4().hex + ".jpg")
    return await asyncio.to_thread(normalize_generated_photo, raw, destination)


def refund_draw_once(task_id, reason):
    """失败状态和退款同一事务提交，防止异常路径重复退款。"""
    conn = get_db()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT * FROM draw_tasks WHERE task_id=?", (task_id,)).fetchone()
            if not task or task["status"] not in ("pending", "processing"):
                return False
            conn.execute("UPDATE users SET balance=ROUND(balance+?,8) WHERE tg_id=?", (task["cost"], task["user_id"]))
            conn.execute("UPDATE draw_tasks SET status='failed', error_msg=?, completed_at=? WHERE task_id=?", (reason, now_str(), task_id))
        return True
    finally:
        conn.close()


def photo_source(record):
    """把历史记录转换成可以直接交给 Telegram 的图片源。

    优先级：已保存的 file_id > 本地文件对象 > http(s) URL。
    原实现直接把本地路径字符串当 photo 传，PTB 不会上传本地文件，
    导致「我的设计」里永远看不到图；这里改为显式打开文件。
    """
    if not record:
        return None
    if isinstance(record, dict):
        fid = record.get("result_file_id") or ""
        path = record.get("result_url") or ""
    else:
        fid = (record["result_file_id"] if "result_file_id" in record.keys() else "") or ""
        path = (record["result_url"] if "result_url" in record.keys() else "") or ""
    if fid:
        return fid
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if os.path.isfile(path):
        return open(path, "rb")
    return None


def photo_source(record):
    """把历史记录转换成可以直接交给 Telegram 的图片源。

    优先级：已保存的 file_id > 本地文件对象 > http(s) URL。
    原实现直接把本地路径字符串当 photo 传，PTB 不会上传本地文件，
    导致「我的设计」里永远看不到图；这里改为显式打开文件。
    """
    if not record:
        return None
    if isinstance(record, dict):
        fid = record.get("result_file_id") or ""
        path = record.get("result_url") or ""
    else:
        fid = (record["result_file_id"] if "result_file_id" in record.keys() else "") or ""
        path = (record["result_url"] if "result_url" in record.keys() else "") or ""
    if fid:
        return fid
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if os.path.isfile(path):
        return open(path, "rb")
    return None


def photo_source(record):
    """把历史记录转换成可以直接交给 Telegram 的图片源。

    优先级：已保存的 file_id > 本地文件对象 > http(s) URL。
    原实现直接把本地路径字符串当 photo 传，PTB 不会上传本地文件，
    导致「我的设计」里永远看不到图；这里改为显式打开文件。
    """
    if not record:
        return None
    if isinstance(record, dict):
        fid = record.get("result_file_id") or ""
        path = record.get("result_url") or ""
    else:
        fid = (record["result_file_id"] if "result_file_id" in record.keys() else "") or ""
        path = (record["result_url"] if "result_url" in record.keys() else "") or ""
    if fid:
        return fid
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if os.path.isfile(path):
        return open(path, "rb")
    return None


def photo_source(record):
    """把历史记录转换成可以直接交给 Telegram 的图片源。

    优先级：已保存的 file_id > 本地文件对象 > http(s) URL。
    原实现直接把本地路径字符串当 photo 传，PTB 不会上传本地文件，
    导致「我的设计」里永远看不到图；这里改为显式打开文件。
    """
    if not record:
        return None
    if isinstance(record, dict):
        fid = record.get("result_file_id") or ""
        path = record.get("result_url") or ""
    else:
        fid = (record["result_file_id"] if "result_file_id" in record.keys() else "") or ""
        path = (record["result_url"] if "result_url" in record.keys() else "") or ""
    if fid:
        return fid
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if os.path.isfile(path):
        return open(path, "rb")
    return None


def photo_source(record):
    """把历史记录转换成可以直接交给 Telegram 的图片源。

    优先级：已保存的 file_id > 本地文件对象 > http(s) URL。
    原实现直接把本地路径字符串当 photo 传，PTB 不会上传本地文件，
    导致「我的设计」里永远看不到图；这里改为显式打开文件。
    """
    if not record:
        return None
    if isinstance(record, dict):
        fid = record.get("result_file_id") or ""
        path = record.get("result_url") or ""
    else:
        fid = (record["result_file_id"] if "result_file_id" in record.keys() else "") or ""
        path = (record["result_url"] if "result_url" in record.keys() else "") or ""
    if fid:
        return fid
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if os.path.isfile(path):
        return open(path, "rb")
    return None


async def deliver_generated_photo(bot, chat_id, path, caption, keyboard, message_id=None):
    """每次上传重新打开文件；照片失败后按文件发送，不能静默丢失结果。"""
    kwargs = dict(read_timeout=120, write_timeout=120, connect_timeout=30, pool_timeout=30)
    if message_id:
        try:
            with open(path, "rb") as photo:
                return await bot.edit_message_media(
                    chat_id=chat_id, message_id=message_id,
                    media=InputMediaPhoto(photo, caption=caption, parse_mode=ParseMode.HTML),
                    reply_markup=keyboard, **kwargs)
        except TelegramError as exc:
            log.warning("编辑结果图片失败，改为发送: %s", exc)
    try:
        with open(path, "rb") as photo:
            sent = await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption,
                                       parse_mode=ParseMode.HTML, reply_markup=keyboard, **kwargs)
    except TelegramError as exc:
        log.warning("发送照片失败，改为文件发送: %s", exc)
        with open(path, "rb") as photo:
            sent = await bot.send_document(chat_id=chat_id, document=photo, filename=Path(path).name,
                                           caption=_strip_html_tags(caption)[:900], reply_markup=keyboard, **kwargs)
    if message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass
    return sent


class DrawWorker:
    def __init__(self):
        self.tokens: List[str] = []
        self.line2_tokens: List[str] = []
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_DRAWS)
        self.token_index = 0
        self.line2_token_index = 0
        self._reload_tokens()

    def _reload_tokens(self):
        try:
            raw = get_config_raw("apinebula_tokens", "")
        except Exception:
            raw = ""
        self.tokens = [t.strip() for t in raw.split(",") if t.strip()]
        if not self.tokens:
            log.warning("未配置KK大模型1.0 API Token")
        try:
            raw2 = get_config_raw("line2_tokens", "")
        except Exception:
            raw2 = ""
        self.line2_tokens = [t.strip() for t in raw2.split(",") if t.strip()]
        if not self.line2_tokens:
            log.warning("未配置KK大模型2.0 API Token")

    def _next_token(self, line: str = None) -> Optional[str]:
        if line is None:
            line = get_draw_line()
        if line == "line2":
            if not self.line2_tokens:
                self._reload_tokens()
            if not self.line2_tokens:
                return None
            tok = self.line2_tokens[self.line2_token_index % len(self.line2_tokens)]
            self.line2_token_index += 1
            return tok
        else:
            if not self.tokens:
                self._reload_tokens()
            if not self.tokens:
                return None
            tok = self.tokens[self.token_index % len(self.tokens)]
            self.token_index += 1
            return tok

    async def generate_image(
        self,
        prompt: str,
        size: str = "auto",
        quality: str = "high",
        ref_image_urls: List[str] = None,
        n: int = 1,
        model_override: str = None,
        line_override: str = None,
    ) -> Dict[str, Any]:
        """调用生图API，返回 {ok, url, error}。line_override可强制指定线路"""
        async with self.semaphore:
            self._reload_tokens()
            line = line_override or get_draw_line()
            token = self._next_token(line)
            if not token:
                line_name = "KK大模型2.0" if line == "line2" else "KK大模型1.0"
                log.error(f"[生图] 未配置{line_name} API Token")
                return {"ok": False, "error": f"未配置{line_name} API Token"}
            request_timeout = get_line_timeout(line)
            try:
                model_name = model_override or get_line_model(line)
                base_url = get_line_base_url(line)
                headers = {"Authorization": f"Bearer {token}"}
                if ref_image_urls:
                    # 图生图：用 /images/edits 接口，multipart/form-data 格式
                    import io
                    form = aiohttp.FormData()
                    form.add_field("model", model_name)
                    form.add_field("prompt", prompt)
                    form.add_field("n", str(n))
                    form.add_field("quality", quality)
                    form.add_field("input_fidelity", "high")
                    form.add_field("response_format", "b64_json")
                    if size and size != "auto":
                        form.add_field("size", size)
                    # 参考图：data URL 解码后作为文件上传
                    for i, ref in enumerate(ref_image_urls):
                        if ref.startswith("data:"):
                            header, b64data = ref.split(",", 1)
                            img_bytes = base64.b64decode(b64data)
                        else:
                            img_bytes = ref.encode() if isinstance(ref, str) else ref
                        form.add_field(f"image", io.BytesIO(img_bytes), filename=f"ref_{i}.jpg", content_type="image/jpeg")
                    url = base_url.rstrip("/") + "/images/edits"
                    log.info(f"[生图] 调用API(图生图edits) line={line} model={model_name} size={size} quality={quality} ref={len(ref_image_urls)} prompt={prompt[:50]}")
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=request_timeout, connect=30, sock_read=request_timeout)) as r:
                            result = await read_draw_response(r)
                else:
                    # 文生图：用 /images/generations 接口，JSON格式
                    payload = {
                        "model": model_name,
                        "prompt": prompt,
                        "n": n,
                        "quality": quality,
                    }
                    if size and size != "auto":
                        payload["size"] = size
                    headers["Content-Type"] = "application/json"
                    url = base_url.rstrip("/") + "/images/generations"
                    log.info(f"[生图] 调用API(文生图) line={line} model={model_name} size={size} quality={quality} ref=0 prompt={prompt[:50]}")
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=request_timeout, connect=30, sock_read=request_timeout)) as r:
                            result = await read_draw_response(r)
                log.info(f"[生图] API响应 status={r.status}")
                if r.status != 200:
                    log.error(f"[生图] API错误 {r.status}: {result}")
                    return {"ok": False, "error": f"API错误 {r.status}: {result}"}
                data = result.get("data", [])
                if not data:
                    log.error(f"[生图] API返回空: {result}")
                    return {"ok": False, "error": f"API返回空: {result}"}
                item = data[0]
                img_url = item.get("url")
                b64 = item.get("b64_json")
                if b64:
                    encoded = b64.split(",", 1)[-1] if b64.startswith("data:") else b64
                    if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4096:
                        raise ValueError("生图结果超过本地图片处理上限50MB")
                    source = base64.b64decode(encoded, validate=True)
                elif img_url:
                    source = img_url
                else:
                    return {"ok": False, "error": "返回中无图片"}
                img_path = await prepare_generated_photo(source)
                log.info("[生图] 成功，图片已保存并处理: %s", img_path)
                return {"ok": True, "url": img_path}
            except asyncio.TimeoutError:
                log.error("[生图] 请求超时，上限%s秒", request_timeout)
                return {"ok": False, "error": f"生图请求超时（上限{request_timeout}秒）；上游可能仍在处理，请勿连续重复提交"}
            except Exception as e:
                log.exception(f"[生图] 异常: {e}")
                return {"ok": False, "error": str(e)}

draw_worker = DrawWorker()

# ============================================================
# 提示词优化（调用gpt-5.5语言模型）
# ============================================================

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询
async def optimize_prompt_api(prompt: str) -> Optional[str]:
    """调用语言模型优化提示词，返回优化后的提示词，失败返回None"""
    if get_config("optimize_enabled", "1") != "1":
        return None
    model = get_config("optimize_model", "gpt-5.5")
    if not model:
        return None
    system_prompt = get_config("optimize_prompt", "")
    # 优化提示词用独立Token池（optimize_tokens），和生图Token分开
    raw = get_config("optimize_tokens", "")
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        log.warning("[优化提示词] 未配置optimize_tokens，无法优化")
        return None
    import random
    token = random.choice(tokens)
    log.info(f"[优化提示词] 开始调用 model={model} tokens={len(tokens)}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
        "temperature": 0.7,
    }
    # 重试3次，每次超时60秒
    for attempt in range(3):
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            connector = aiohttp.TCPConnector()
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.post(f"{APINEBULA_BASE_URL}/chat/completions", headers=headers, json=payload) as resp:
                    data = await resp.json()
                    if resp.status == 200 and data.get("choices"):
                        result = data["choices"][0]["message"]["content"].strip()
                        result = result.strip('"').strip("'").strip()
                        import re
                        result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
                        log.info(f"[优化提示词] 成功(第{attempt+1}次) 原始={prompt[:30]}... 优化后={result[:50]}...")
                        return result
                    else:
                        err_msg = data.get("error", {}).get("message", str(data))
                        log.warning(f"[优化提示词] API错误 status={resp.status} msg={err_msg}")
                        if resp.status in (401, 403):
                            return None  # 权限错误不重试
                        # 其他错误重试
        except Exception as e:
            log.warning(f"[优化提示词] 第{attempt+1}次调用失败 type={type(e).__name__} str={str(e) or repr(e)}")
            if attempt < 2:
                await asyncio.sleep(2)
                continue
    log.warning("[优化提示词] 3次重试均失败")
    return None

# ============================================================
# 生图任务DB操作
# ============================================================
async def show_draw_loading(context, chat_id: int, old_msg_id: int, text: str, keyboard) -> int:
    """显示生图加载消息：有配图则用editMessageMedia直接把文字消息编辑成图片消息（官方支持add media to text messages）
    全程纯编辑，不删消息不重发。返回 message_id（不变）
    """
    loading_img = get_config_raw("draw_loading_image", "")
    if loading_img:
        try:
            await context.bot.edit_message_media(
                chat_id=chat_id, message_id=old_msg_id,
                media=InputMediaPhoto(media=loading_img, caption=text, parse_mode=ParseMode.HTML),
                reply_markup=keyboard
            )
        except Exception as e:
            log.warning(f"draw_loading edit_message_media失败，降级edit_text: {e}")
            await safe_edit_text(context, chat_id, old_msg_id, text, keyboard)
    else:
        await safe_edit_text(context, chat_id, old_msg_id, text, keyboard)
    return old_msg_id

async def run_draw_task(task_id: str, chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE):
    task = get_draw_task(task_id)
    if not task:
        return
    conn = get_db()
    try:
        with conn:
            claimed = conn.execute("UPDATE draw_tasks SET status='processing' WHERE task_id=? AND status='pending'", (task_id,)).rowcount
    finally:
        conn.close()
    if not claimed:
        return
    user_id = task["user_id"]
    try:
        ref_urls = json.loads(task.get("ref_images") or "[]")
        draw_type = task.get("draw_type", "free")
        result = await draw_worker.generate_image(
            prompt=task.get("prompt", ""), size=task.get("size", "auto"),
            quality=task.get("quality", "high"), ref_image_urls=ref_urls,
            model_override="gpt-image-2" if draw_type == "preset" else None,
            line_override=task.get("line") or TASK_LINE_MAP.get(task_id) or get_draw_line())
        if not result["ok"]:
            raise RuntimeError(result["error"])
        img_path = result["url"]
        # 保存路径后尝试交付；只有实际交付成功才标记完成。
        update_draw_task(task_id, result_url=img_path)
        done_text = fmt(get_config("draw_done"), task_id=task_id)
        keyboard = InlineKeyboardMarkup([
            [IButtonCfg("btn_again", callback_data=f"draw_again?task_id={task_id}")],
            [IButtonCfg("btn_view_works", url=get_config("works_url"))],
            [IButtonCfg("btn_back_home", callback_data="back_home")],
        ])
        sent = await deliver_generated_photo(context.bot, chat_id, img_path, done_text, keyboard, message_id)
        # 回写 Telegram 的 file_id：历史回看优先用它，换机/清缓存后本地路径会失效
        file_id = ""
        if sent is not None:
            photos = getattr(sent, "photo", None) or []
            if photos:
                file_id = photos[-1].file_id
            elif getattr(sent, "document", None) is not None:
                file_id = sent.document.file_id
        update_draw_task(task_id, status="completed", completed_at=now_str(), result_file_id=file_id)
        increment_draws(user_id)
        app = getattr(context, "application", context)
        app.create_task(send_to_channel(context, user_id, task.get("cost", 0), task.get("prompt", ""), img_path, draw_type, task.get("preset_data", "")))
    except Exception as exc:
        log.exception("生图任务失败 task=%s: %s", task_id, exc)
        if refund_draw_once(task_id, str(exc)):
            import html
            log.info("[生图] 失败，已退回余额 %s task=%s", task["cost"], task_id)
            fail_text = fmt(get_config("draw_failed"), error=html.escape(str(exc)[:600]))
            await safe_edit_text(context, chat_id, message_id, fail_text,
                                 InlineKeyboardMarkup([[IButtonCfg("btn_back_home", callback_data="back_home")]]))
    finally:
        TASK_LINE_MAP.pop(task_id, None)
# ============================================================
# 消息安全删除
# ============================================================
async def send_config_photo(update_or_bot, context, text_key: str, text: str = None,
                            reply_markup=None, chat_id: int = None, parse_html: bool = True):
    """发送文案，自动检查 {text_key}_image 是否有配图
    有配图 → send_photo(caption=text)，无配图 → send_message(text)
    用法1: await send_config_photo(update, context, "welcome_text", reply_markup=kb)
    用法2: await send_config_photo(context.bot, context, "profile_text", text=自定义文案, chat_id=123)
    """
    img = get_config_raw(f"{text_key}_image", "")
    if text is None:
        text = get_config(text_key)
    pm = ParseMode.HTML if parse_html else None
    # 统一用bot发送，自动从update提取chat_id
    bot = context.bot if hasattr(context, 'bot') else update_or_bot
    if chat_id is None:
        if hasattr(update_or_bot, 'effective_chat') and update_or_bot.effective_chat:
            chat_id = update_or_bot.effective_chat.id
        elif hasattr(update_or_bot, 'callback_query') and update_or_bot.callback_query:
            chat_id = update_or_bot.callback_query.message.chat.id
        elif hasattr(update_or_bot, 'message') and update_or_bot.message:
            chat_id = update_or_bot.message.chat.id
    try:
        if img:
            await bot.send_photo(chat_id=chat_id, photo=img, caption=text, parse_mode=pm, reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=pm, reply_markup=reply_markup)
    except Exception as e:
        log.warning(f"send_config_photo失败({text_key}): {e}，降级纯文本")
        try:
            await bot.send_message(chat_id=chat_id, text=_strip_html_tags(text), reply_markup=reply_markup)
        except Exception:
            pass

async def send_config_photo_msg(update_or_bot, context, text_key: str, text: str = None,
                                reply_markup=None, chat_id: int = None, parse_html: bool = True):
    """同send_config_photo，但返回发送的message对象（用于获取message_id）"""
    img = get_config_raw(f"{text_key}_image", "")
    if text is None:
        text = get_config(text_key)
    pm = ParseMode.HTML if parse_html else None
    bot = context.bot if hasattr(context, 'bot') else update_or_bot
    if chat_id is None:
        if hasattr(update_or_bot, 'effective_chat') and update_or_bot.effective_chat:
            chat_id = update_or_bot.effective_chat.id
        elif hasattr(update_or_bot, 'callback_query') and update_or_bot.callback_query:
            chat_id = update_or_bot.callback_query.message.chat.id
        elif hasattr(update_or_bot, 'message') and update_or_bot.message:
            chat_id = update_or_bot.message.chat.id
    try:
        if img:
            return await bot.send_photo(chat_id=chat_id, photo=img, caption=text, parse_mode=pm, reply_markup=reply_markup)
        else:
            return await bot.send_message(chat_id=chat_id, text=text, parse_mode=pm, reply_markup=reply_markup)
    except Exception as e:
        log.warning(f"send_config_photo_msg失败({text_key}): {e}，降级纯文本")
        try:
            return await bot.send_message(chat_id=chat_id, text=_strip_html_tags(text), reply_markup=reply_markup)
        except Exception:
            return None

# ============================================================
# 自动发频道
# ============================================================
async def send_to_channel(context: ContextTypes.DEFAULT_TYPE, user_id: int, cost: float, prompt: str, photo_path: str, draw_type: str = "free", preset_data: str = ""):
    """生图成功后自动发到频道，失败只记日志不影响用户"""
    try:
        if get_config_raw("channel_enabled", "0") != "1":
            return
        channel = get_config_raw("channel_id", "").strip()
        if not channel:
            return
        # 预设生图：只发用户填的字段值，不发管理员提示词模板
        import html
        if draw_type == "preset" and preset_data:
            try:
                values = json.loads(preset_data)
                # 字段值拼成 "名称：xxx" 格式
                user_content = "\n".join([f"{k}：{v}" for k, v in values.items() if v])
                display_prompt = user_content
            except Exception:
                display_prompt = prompt
        else:
            display_prompt = prompt or ""
        # 提示词HTML转义 + 超长截断
        prompt_escaped = html.escape(display_prompt)
        max_prompt = 700
        if len(prompt_escaped) > max_prompt:
            prompt_escaped = prompt_escaped[:max_prompt] + "..."
        text = fmt(get_config("channel_text"),
                   user_id=_mask_user_id(user_id),
                   cost=format_amount(cost),
                   prompt=prompt_escaped)
        btn_text = get_config_raw("channel_btn_text", "我也要做")
        btn_url = get_config_raw("channel_btn_url", "")
        keyboard = InlineKeyboardMarkup([[IButton(btn_text, url=btn_url)]]) if btn_url else None
        if not os.path.isfile(photo_path):
            photo_path = await prepare_generated_photo(photo_path)
        await deliver_generated_photo(context.bot, channel, photo_path, text, keyboard)
        log.info(f"[频道] 已发送 task user={user_id} cost={cost}")
    except Exception as e:
        log.warning(f"[频道] 发送失败: {e}")

# ============================================================
# /start 命令
# ============================================================

# 困困生图机器人 / 由 TG 困困开源 @sad1881 困困承接各种业务 欢迎咨询