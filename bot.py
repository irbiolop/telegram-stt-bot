#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🤖 ربات تلگرام «تبدیل صوت / ویدیو به متن فارسی»
================================================
بازنویسی‌شده از برنامه دسکتاپ Tkinter به یک ربات تلگرام،
با همان موتور Google Speech API v2 و همان روش «قطعه‌قطعه کردن» صدا.

امکانات:
  ✅ پذیرش voice / audio / video / video_note / document تا ۲ گیگابایت (MTProto — Telethon)
  ✅ پردازش خوردخوردِ صدا (chunk) دقیقاً مثل برنامه اصلی — ۳۰/۶۰/۹۰ ثانیه
  ✅ صف پردازش ترتیبی + اعلام نوبت به کاربران
  ✅ قابلیت لغو پردازش با دستور /cancel
  ✅ خروجی هوشمند: متن کوتاه در چت — متن طولانی به‌صورت فایل txt با تایم‌استمپ
  ✅ فقط فارسی (fa-IR) — قابل تغییر در تنظیمات

متغیرهای محیطی موردنیاز (یا مقادیر را مستقیم در بخش تنظیمات بگذارید):
  API_ID    ← از my.telegram.org  (رایگان)
  API_HASH  ← از my.telegram.org  (رایگان)
  BOT_TOKEN ← از @BotFather       (رایگان)
"""

import os
import io
import sys
import time
import wave
import uuid
import math
import json
import shutil
import logging
import asyncio
import datetime
import tempfile
import subprocess
import threading
import re

import requests
from telethon import TelegramClient, events

# =========================================================
#  تنظیمات (پیش‌فرض‌ها قابل تغییر با متغیر محیطی)
# =========================================================
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# --- همان سرویس Google Speech API برنامه اصلی ---
GOOGLE_API_URL = "https://www.google.com/speech-api/v2/recognize"
API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw")

LANGUAGE = "fa-IR"                                    # فقط فارسی
CHUNK_SECONDS = int(os.getenv("CHUNK_SECONDS", "60"))  # طول هر قطعه به ثانیه (۳۰/۶۰/۹۰)
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "2000"))    # سقف حجم فایل ورودی (مگابایت)
GOOGLE_RETRIES = int(os.getenv("GOOGLE_RETRIES", "3"))  # تعداد تلاش مجدد برای هر قطعه
NORMALIZE = os.getenv("NORMALIZE", "1") == "1"         # تقویت خودکار بلندی صدا (مثل normalize در برنامه اصلی)
SESSION_NAME = os.getenv("SESSION_NAME", "stt_bot_session")

MAX_TEXT_IN_CHAT = 3500  # بالاتر از این مقدار، متن به‌صورت فایل txt فرستاده می‌شود

if not (API_ID and API_HASH and BOT_TOKEN):
    print("=" * 62)
    print("❌ خطا: API_ID / API_HASH / BOT_TOKEN تنظیم نشده‌اند!")
    print("   ۱) از my.telegram.org → API development tools مقدار API_ID و API_HASH بگیرید.")
    print("   ۲) از @BotFather توکن BOT_TOKEN بسازید.")
    print("   ۳) یا متغیر محیطی ست کنید، یا مقادیر را در بخش تنظیمات همین فایل وارد کنید.")
    print("=" * 62)
    sys.exit(1)

if shutil.which("ffmpeg") is None:
    print("❌ ffmpeg پیدا نشد! نصبش کنید:")
    print("   لینوکس/Colab :  apt install -y ffmpeg")
    print("   ویندوز       :  ffmpeg را دانلود و به PATH اضافه کنید")
    sys.exit(1)

# ================== لاگ ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.FileHandler("bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("STT-Bot")

BANNER = "🎙 ربات تبدیل صدا به متن فارسی (Google Speech API) — آماده به کار"

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ================== وضعیت صف پردازش ==================
queue_lock = asyncio.Lock()     # پردازش ترتیبی: هر لحظه فقط یک فایل
waiting_count = 0               # تعداد کاربران در صف
user_tokens = {}                # user_id → لیست توکن‌های فعال آن کاربر
cancelled_tokens = set()        # توکن‌هایی که کاربر لغو کرده است
start_time = time.time()


# =========================================================
#  توابع کمکی عمومی
# =========================================================
def fmt_seconds(sec: float) -> str:
    """تبدیل ثانیه به قالب MM:SS یا HH:MM:SS"""
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def safe_filename(name: str) -> str:
    """نام فایل را برای ذخیره‌سازی امن می‌کند"""
    name = os.path.splitext(os.path.basename(name or "voice"))[0][:60]
    return re.sub(r"[^\w\u0600-\u06FF\- ]", "_", name).strip() or "voice"


async def safe_edit(msg, text: str):
    """ویرایش امن پیام وضعیت — خطاهای لحظه‌ای (مثل «پیام بدون تغییر») نادیده گرفته می‌شود"""
    try:
        await msg.edit(text)
    except Exception:
        pass


# =========================================================
#  ارتباط با Google Speech API  (همان منطق برنامه اصلی)
# =========================================================
def send_to_google_api(audio_data: bytes, language: str = LANGUAGE) -> dict:
    """
    ارسال PCM خام (16kHz mono 16bit) به گوگل و دریافت متن.
    مثل برنامه اصلی: پاسخ چندخطی JSON است و آخرین result معتبر برمی‌گردد.
    + بهبود: تلاش مجدد خودکار برای خطاهای موقتی شبکه / سرور.
    """
    url = f"{GOOGLE_API_URL}?client=chromium&lang={language}&key={API_KEY}"
    headers = {"Content-Type": "audio/l16; rate=16000"}
    last_error = "خطای ناشناخته"

    for attempt in range(1, GOOGLE_RETRIES + 1):
        try:
            response = requests.post(url, headers=headers, data=audio_data, timeout=90)

            if response.status_code == 200:
                response_text = response.text.strip()
                if not response_text:
                    return {"success": True, "text": ""}

                # پاسخ چند خط JSON جدا شده با \n است — از آخر بررسی می‌کنیم
                for line in reversed(response_text.split("\n")):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        results = data.get("result")
                        if results:
                            alternatives = results[0].get("alternative")
                            if alternatives and "transcript" in alternatives[0]:
                                return {"success": True, "text": alternatives[0]["transcript"].strip()}
                    except (json.JSONDecodeError, AttributeError, KeyError, IndexError):
                        continue
                return {"success": True, "text": ""}

            if response.status_code in (429, 500, 502, 503):
                # خطای موقتی — بعد از مکث کوتاه دوباره تلاش می‌کنیم
                last_error = f"HTTP {response.status_code} (تلاش {attempt}/{GOOGLE_RETRIES})"
                log.warning(f"⚠️ {last_error} — در حال تلاش مجدد...")
                time.sleep(2 * attempt)
                continue

            return {"success": False, "error": f"HTTP {response.status_code} - {response.text[:150]}"}

        except requests.exceptions.Timeout:
            last_error = f"زمان درخواست به پایان رسید (تلاش {attempt}/{GOOGLE_RETRIES})"
            log.warning(f"⚠️ {last_error}")
            time.sleep(2 * attempt)
        except requests.exceptions.RequestException as e:
            last_error = f"خطای شبکه: {e}"
            log.warning(f"⚠️ {last_error}")
            time.sleep(2 * attempt)

    return {"success": False, "error": last_error}


# =========================================================
#  پردازش صدا با ffmpeg  (بدون بارگذاری کل فایل در رم!)
# =========================================================
def convert_to_wav16k(input_path: str, output_path: str) -> dict:
    """
    تبدیل هر فرمتی (mp3/m4a/ogg/mp4/mkv/...) به WAV استاندارد برای گوگل:
    مونو، ۱۶ کیلوهرتز، ۱۶ بیت — با ffmpeg به‌صورت استریم (رم مصرف نمی‌کند).
    در صورت NORMALIZE=1 صدای ضعیف تقویت می‌شود (معادل normalize در برنامه اصلی).
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", input_path,
           "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16"]
    if NORMALIZE:
        cmd += ["-af", "dynaudnorm=f=250:g=15:p=0.9"]
    cmd += [output_path]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg خطا داد: {proc.stderr[-300:]}")

    with wave.open(output_path, "rb") as w:
        rate = w.getframerate()
        duration = w.getnframes() / float(rate)
    return {"rate": rate, "duration": duration}


# =========================================================
#  هسته اصلی: پردازش یک فایل از ابتدا تا انتها
# =========================================================
async def process_file(event, src_path: str, display_name: str, token: str):
    chat_id = event.chat_id

    # ---------- ۱) تبدیل به WAV 16kHz ----------
    status = await event.reply("🎚 در حال آماده‌سازی صدا با ffmpeg...\n(برای فایل‌های بزرگ ممکن است چند دقیقه طول بکشد)")
    wav_path = os.path.join(tempfile.mkdtemp(prefix="stt_wav_"), "audio16k.wav")
    try:
        info = await asyncio.to_thread(convert_to_wav16k, src_path, wav_path)
    except Exception as e:
        log.error(f"❌ خطای تبدیل ffmpeg: {e}")
        await event.reply(f"❌ تبدیل صدا ناموفق بود:\n{str(e)[:300]}")
        return
    finally:
        try:
            await status.delete()
        except Exception:
            pass

    duration = info["duration"]
    total_chunks = max(1, math.ceil(duration / CHUNK_SECONDS))
    log.info(f"📦 {display_name}: طول {fmt_seconds(duration)} → {total_chunks} قطعه")
    await event.reply(
        f"📦 فایل آماده شد!\n"
        f"⏱ طول صدا: {fmt_seconds(duration)}\n"
        f"🧩 تعداد قطعات: {total_chunks} قطعه × {CHUNK_SECONDS} ثانیه\n"
        f"🚀 شروع تبدیل (خوردخورد مثل برنامه دسکتاپ)..."
    )

    # ---------- ۲) ارسال قطعه‌ها به گوگل، یکی‌یکی ----------
    texts = []           # لیست (زمان شروع قطعه، متن)
    ok = empty = err = 0
    t0 = time.time()
    last_edit = 0.0
    cancelled = False
    progress_msg = await event.reply("🧠 در حال تبدیل به متن...\n🧩 قطعه: 0", parse_mode=None)

    with wave.open(wav_path, "rb") as w:
        rate = w.getframerate()
        frames_per_chunk = rate * CHUNK_SECONDS
        idx = 0
        while True:
            # بررسی لغو توسط کاربر
            if token in cancelled_tokens:
                cancelled = True
                break

            frames = w.readframes(frames_per_chunk)   # خواندن ترتیبی از دیسک — رم ثابت می‌ماند
            if not frames:
                break

            offset_sec = idx * CHUNK_SECONDS
            result = await asyncio.to_thread(send_to_google_api, frames, LANGUAGE)

            if result["success"] and result["text"]:
                texts.append((offset_sec, result["text"]))
                ok += 1
            elif result["success"]:
                empty += 1   # سکوت یا کیفیت پایین
            else:
                err += 1
                log.warning(f"❌ قطعه {idx + 1}: {result['error']}")

            idx += 1

            # ---------- پیشرفت زنده (هر ~۲۰ ثانیه) ----------
            now = time.time()
            if now - last_edit >= 20 or idx == total_chunks:
                last_edit = now
                pct = idx * 100.0 / total_chunks
                elapsed = now - t0
                eta = (elapsed / idx) * (total_chunks - idx) if idx else 0
                await safe_edit(
                    progress_msg,
                    f"🧠 در حال تبدیل به متن...\n"
                    f"🧩 قطعه: {idx}/{total_chunks} ({pct:.0f}٪)\n"
                    f"⏱ پردازش‌شده: {fmt_seconds(offset_sec)} از {fmt_seconds(duration)}\n"
                    f"⌛️ تقریباً {fmt_seconds(eta)} مانده",
                )

    # ---------- ۳) ساخت نتیجه ----------
    try:
        await progress_msg.delete()
    except Exception:
        pass

    if cancelled:
        await event.reply("🚫 پردازش این فایل با دستور شما لغو شد.")
        log.info(f"🚫 لغو شد: {display_name}")
        return

    clean_text = " ".join(t for _, t in texts).strip()
    stats = (
        f"📊 آمار: ✅ موفق {ok} | 🔇 خالی {empty} | ❌ خطا {err}\n"
        f"⏱ طول صوت: {fmt_seconds(duration)} | ⏳ زمان پردازش: {fmt_seconds(time.time() - t0)}"
    )
    if err:
        stats += f"\n⚠️ {err} قطعه خطا داد؛ اگر متن ناقص است چند دقیقه دیگر دوباره بفرستید."
    log.info(f"🏁 پایان {display_name}: {stats.splitlines()[0]}")

    if not clean_text:
        await event.reply(
            "⚠️ هیچ متنی تشخیص داده نشد!\n"
            "احتمالاً صدا ضعیف است، بدون گفتار است، یا سرویس گوگل موقتاً پاسخ نمی‌دهد.\n" + stats
        )
        return

    if len(clean_text) <= MAX_TEXT_IN_CHAT:
        # متن کوتاه → مستقیم در چت
        await event.reply(f"📝 متن تشخیص داده‌شده:\n\n{clean_text}\n\n{stats}")
    else:
        # متن طولانی → فایل txt با تایم‌استمپ + پیش‌نمایش در چت
        lines = [f"[{fmt_seconds(off)}] {txt}" for off, txt in texts]
        file_text = "متن تبدیل‌شده از «%s»\n%s\n\n%s" % (
            display_name, "-" * 40, "\n".join(lines)
        )
        buf = io.BytesIO(file_text.encode("utf-8"))
        buf.name = f"{safe_filename(display_name)}.txt"
        preview = clean_text[:900].rsplit(" ", 1)[0] + " …"
        await event.reply(
            f"📄 متن خیلی طولانی است ({len(clean_text):,} کاراکتر) — فایل کامل با تایم‌استمپ ارسال شد.\n\n"
            f"🔍 پیش‌نمایش:\n{preview}\n\n{stats}"
        )
        await client.send_file(
            chat_id, buf, force_document=True,
            caption=f"📝 متن کامل «{display_name}»\n{stats}",
        )


# =========================================================
#  هندلرها
# =========================================================
@client.on(events.NewMessage(incoming=True, pattern=r"^/(start|help)$"))
async def start_handler(event):
    await event.reply(
        "🎙 **ربات تبدیل صوت و ویدیو به متن فارسی**\n\n"
        "فقط کافیست فایل بفرستید — بقیه‌اش با من:\n"
        "• 🔈 ویس (voice) و پیام‌های تصویری ویدیویی\n"
        "• 🎵 فایل صوتی: mp3 / m4a / wav / ogg / flac\n"
        "• 🎬 فایل ویدیویی: mp4 / mkv / avi — تا ۲ گیگابایت\n\n"
        "⚙️ موتور تشخیص: Google Speech (همان روش برنامه دسکتاپ)\n"
        f"🧩 پردازش به‌صورت قطعات {CHUNK_SECONDS} ثانیه‌ای انجام می‌شود\n"
        "📋 فایل‌ها به‌ترتیب صف پردازش می‌شوند\n\n"
        "دستورها:\n"
        "/status — وضعیت صف و آمار\n"
        "/cancel — لغو پردازش آخرین فایل شما"
    )


@client.on(events.NewMessage(incoming=True, pattern=r"^/status$"))
async def status_handler(event):
    busy = queue_lock.locked()
    await event.reply(
        f"🤖 **وضعیت ربات**\n\n"
        f"{'🟡 در حال پردازش یک فایل...' if busy else '🟢 آزاد — همین حالا فایل بفرستید'}\n"
        f"👥 در صف: {waiting_count} نفر\n"
        f"⏱ روشن از: {fmt_seconds(time.time() - start_time)} پیش\n"
        f"🧩 طول قطعات: {CHUNK_SECONDS}s | زبان: {LANGUAGE} | سقف: {MAX_FILE_MB}MB"
    )


@client.on(events.NewMessage(incoming=True, pattern=r"^/cancel$"))
async def cancel_handler(event):
    tokens = user_tokens.get(event.sender_id, [])
    if not tokens:
        await event.reply("شما فایل در صف یا در حال پردازشی ندارید.")
        return
    cancelled_tokens.update(tokens)
    await event.reply(f"🚫 درخواست لغو ثبت شد ({len(tokens)} فایل). اگر در صف باشد، پردازش نمی‌شود؛ اگر در حال پردازش است، چند ثانیه دیگر متوقف می‌شود.")


@client.on(events.NewMessage(incoming=True))
async def media_handler(event):
    """پذیرش هر نوع فایل صوتی / تصویری و ارسال به صف پردازش"""
    global waiting_count

    msg = event.message
    if not msg.media or (msg.text or "").startswith("/"):
        return

    file = msg.file
    if file is None:
        return
    mime = file.mime_type or ""
    if not (mime.startswith("audio") or mime.startswith("video")):
        return  # عکس، فایل متنی و... نادیده گرفته می‌شوند

    size_mb = (file.size or 0) / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        await event.reply(
            f"⚠️ حجم فایل {size_mb:.0f}MB است و بیشتر از سقف مجاز ({MAX_FILE_MB}MB)!"
            "\nفایل‌های تا ۲ گیگابایت را می‌توانم پردازش کنم."
        )
        return

    display_name = file.name or ("voice-message.ogg" if mime.startswith("audio") else "video.mp4")
    token = uuid.uuid4().hex
    user_tokens.setdefault(event.sender_id, []).append(token)

    # ---------- صف ----------
    need_queue = queue_lock.locked()
    if need_queue:
        waiting_count += 1
        await event.reply(
            f"🔢 فایل شما در صف قرار گرفت (نوبت {waiting_count}).\n"
            "⏳ به‌ترتیب پردازش می‌شود — پیام لغو: /cancel"
        )

    tmp_dir = tempfile.mkdtemp(prefix="stt_dl_")
    src_path = os.path.join(tmp_dir, f"src{file.ext or ''}")
    status = await event.reply("⏳ در حال دریافت فایل از تلگرام... 0%")

    try:
        # ---------- دانلود با پیشرفت زنده ----------
        dl_last = {"t": 0.0}

        def dl_progress(current, total):
            now = time.time()
            if now - dl_last["t"] >= 8 or current >= total:
                dl_last["t"] = now
                pct = current * 100.0 / max(1, total)
                mb = current / (1024 * 1024)
                asyncio.ensure_future(safe_edit(status, f"⏳ در حال دریافت فایل از تلگرام... {pct:.0f}% ({mb:.0f}MB)"))

        await msg.download_media(file=src_path, progress_callback=dl_progress)
        await safe_edit(status, "✅ دانلود کامل شد.")
        log.info(f"📥 دانلود شد: {display_name} ({size_mb:.0f}MB)")

        # ---------- پردازش ترتیبی ----------
        async with queue_lock:
            if need_queue:
                waiting_count = max(0, waiting_count - 1)
            if token not in cancelled_tokens:
                await process_file(event, src_path, display_name, token)
            else:
                await event.reply("🚫 این فایل قبل از شروع پردازش لغو شد.")

    except Exception as e:
        log.exception(f"❌ خطای کلی در پردازش {display_name}: {e}")
        await event.reply(f"❌ خطای غیرمنتظره در پردازش فایل:\n{str(e)[:300]}")
    finally:
        # پاکسازی فایل‌های موقت و آزادسازی نوبت
        try:
            await status.delete()
        except Exception:
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tokens_of_user = user_tokens.get(event.sender_id)
        if tokens_of_user and token in tokens_of_user:
            tokens_of_user.remove(token)
            if not tokens_of_user:
                user_tokens.pop(event.sender_id, None)


# =========================================================
#  سرور سلامت (Health Server) — برای Hugging Face Spaces
# =========================================================
HEALTH_PORT = int(os.getenv("HEALTH_PORT") or os.getenv("PORT") or "7860")   # HF: 7860 | Render: متغیر PORT | ۰ = خاموش
_health_started = False


def start_health_server():
    """
    HF Spaces انتظار دارد اپ روی پورت 7860 (HTTP) گوش بدهد؛
    یک وب‌سرور فوق‌سبک استاندارد پایتون روشن می‌کنیم تا:
      ۱) Space وضعیت «Running» بگیرد   ۲) پینگ ضدخواب (cron-job.org و...) جواب بگیرد
    """
    global _health_started
    if _health_started or HEALTH_PORT <= 0:
        return
    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"STT Telegram Bot is alive!")

            def log_message(self, *args):
                pass  # لاگ درخواست‌های پینگ را بی‌صدا رد کن

        server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _health_started = True
        log.info(f"🩺 سرور سلامت روی پورت {HEALTH_PORT} فعال شد (برای پینگ ضدخواب)")
    except Exception as e:
        log.warning(f"⚠️ سرور سلامت راه نیفتاد (روی کامپیوتر شخصی مهم نیست): {e}")


# =========================================================
#  اجرا
# =========================================================
async def main():
    # اول پورت سلامت باز شود تا Render/HF اسکن پورت را پاس کند و
    # cron-job.org حتی هنگام اختلال تلگرام هم پاسخ بگیرد (تابع idempotent است)
    start_health_server()
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    log.info(BANNER)
    print(f"✅ ربات @{me.username or '?'} آماده است — منتظر فایل‌ها...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    # اتصال مجدد خودکار در صورت قطعی (برای اجرای ۲۴/۷)
    # ⚠️ عمداً از «with client:» استفاده نمی‌کنیم: __enter__ در Telethon متد
    # start() را «بدون bot_token» صدا می‌زند؛ روی سرور بدون TTY (Render/Colab/
    # Docker) نشست تازه به پرامپت «Please enter your phone» می‌خورد و با
    # EOFError می‌میرد. main() خودش با bot_token لاگین می‌کند.
    while True:
        try:
            client.loop.run_until_complete(main())
            break  # خروج تمیز (مثل Ctrl+C)
        except KeyboardInterrupt:
            log.info("👋 ربات با دستور کاربر متوقف شد")
            break
        except Exception as e:
            log.error(f"⚠️ اتصال قطع شد: {e} — ۳۰ ثانیه دیگر دوباره وصل می‌شوم...")
            time.sleep(30)
