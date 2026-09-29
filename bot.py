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
from telethon import TelegramClient, events, Button

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

# --- جزوه‌ساز هوش مصنوعی (اختیاری) ---
# کلیدها را jozve.py از محیط می‌خواند:
#   OPENROUTER_API_KEY ← کلید اصلی (رایگان از openrouter.ai/keys — هر کلید: ۵۰ درخواست در روز)
#   OPENROUTER_API_KEY_BACKUP ← کلید پشتیبان؛ با تمام‌شدن سهمیهٔ اصلی، خودکار به آن سوییچ می‌شود
JOZVE_AUTO = os.getenv("JOZVE_AUTO", "0") == "1"   # بعد از هر تبدیل «خودکار» جزوه ساخته شود؟ (پیش‌فرض: فقط با دکمه)
JOZVE_ON = {}                                       # chat_id → True/False (با دستور /jozve)
ADMIN_ID = int(os.getenv("ADMIN_ID", "7433357700"))  # گزارش ورود/استفادهٔ کاربران به این آیدی تلگرام

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

# ================== ماژول جزوه‌ساز (اختیاری — فایل jozve.py) ==================
try:
    import jozve
except Exception as _je:
    jozve = None
    log.warning(f"⚠️ ماژول jozve بارگذاری نشد؛ جزوه‌سازی غیرفعال می‌ماند: {_je}")

BANNER = "🎙 ربات تبدیل صدا به متن فارسی (Google Speech API) — آماده به کار"

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ================== وضعیت صف پردازش ==================
queue_lock = asyncio.Lock()     # پردازش ترتیبی: هر لحظه فقط یک فایل
waiting_count = 0               # تعداد کاربران در صف
user_tokens = {}                # user_id → لیست توکن‌های فعال آن کاربر
cancelled_tokens = set()        # توکن‌هایی که کاربر لغو کرده است
start_time = time.time()

# --- حافظهٔ متن‌های آمادهٔ جزوه (برای دکمهٔ «تبدیل به جزوه») و کاربران ---
USERS_FILE = "bot_users.json"   # فهرست کاربران (برای گزارش ادمین) — کنار session ذخیره می‌شود
PENDING_JZ = {}                 # token → {"text","title","chat_id","user_id","ts","busy"}


def _load_users() -> set:
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return {int(x) for x in json.load(f)}
    except Exception:
        return set()


known_users = _load_users()


def _save_users():
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(known_users), f)
    except Exception:
        pass


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


def jozve_available() -> bool:
    """آیا جزوه‌سازی آماده کار است؟ (ماژول + حداقل یک کلید هوش مصنوعی)"""
    try:
        return jozve is not None and jozve.has_provider()
    except Exception:
        return False


# =========================================================
#  گزارش به ادمین + دکمهٔ جزوه
# =========================================================
async def notify_admin(text: str):
    """گزارش رویدادها به آیدی ادمین.
    (تلگرام اجازه نمی‌دهد ربات پیام اول را بفرستد؛ ادمین باید یک بار به ربات Start داده باشد)"""
    try:
        await client.send_message(ADMIN_ID, text, parse_mode=None)
    except Exception as e:
        log.warning(f"گزارش به ادمین نرفت: {e}")


def admin_log(text: str):
    """ارسال غیرمسدودکنندهٔ گزارش به ادمین"""
    try:
        asyncio.ensure_future(notify_admin(text))
    except Exception:
        pass


async def user_label(event) -> str:
    """نام و آیدی کاربر برای گزارش ادمین"""
    try:
        u = await event.get_sender()
        name = " ".join(x for x in [getattr(u, "first_name", ""), getattr(u, "last_name", "")] if x).strip()
        un = getattr(u, "username", "") or ""
        return f"{name or '?'}" + (f" (@{un})" if un else "") + f" | id={event.sender_id}"
    except Exception:
        return f"id={getattr(event, 'sender_id', '?')}"


def register_jz_pending(text: str, title: str, chat_id: int, user_id: int) -> str:
    """متن آمادهٔ جزوه را در حافظه نگه می‌دارد و توکن دکمه را برمی‌گرداند (عمر: ۴۸ ساعت)"""
    now = time.time()
    for t in [t for t, v in PENDING_JZ.items() if now - v["ts"] > 48 * 3600]:
        PENDING_JZ.pop(t, None)
    token = uuid.uuid4().hex[:12]
    PENDING_JZ[token] = {"text": text, "title": title, "chat_id": chat_id,
                         "user_id": user_id, "ts": now, "busy": False}
    return token


def jz_buttons(token: str):
    return [Button.inline("📚 تبدیل به جزوه", f"jz:{token}".encode())]


# =========================================================
#  ساخت و ارسال جزوه (هم برای دکمه، هم حالت خودکار/کپشن)
# =========================================================
async def run_jozve(chat_id: int, text: str, display_name: str, user_id: int = 0):
    """جزوه را از متن آماده می‌سازد و فایل‌ها را می‌فرستد — خارج از قفل صف اجرا می‌شود."""
    if not jozve_available():
        await client.send_message(
            chat_id,
            "فعلاً امکان ساخت جزوه فراهم نیست؛ کلید OPENROUTER_API_KEY روی سرور تنظیم نشده است.",
            parse_mode=None,
        )
        return

    status = await client.send_message(
        chat_id,
        "ساخت جزوه آغاز شد؛ بسته به طول متن ۲ تا ۱۰ دقیقه زمان می‌برد و همین پیام به‌روزرسانی می‌شود.",
        parse_mode=None,
    )
    loop = asyncio.get_running_loop()

    def _prog(info: dict):
        """پل پیشرفت: از ترِد پردازش به پیام تلگرام (thread-safe)"""
        try:
            txt = jozve.render_progress(info)
        except Exception:
            return
        asyncio.run_coroutine_threadsafe(safe_edit(status, txt), loop)

    out_dir = tempfile.mkdtemp(prefix="jozve_")
    t0 = time.time()
    try:
        res = await asyncio.to_thread(jozve.produce_jozve, text, display_name, out_dir, _prog)

        if not res.get("ok"):
            if res.get("quota"):
                await client.send_message(
                    chat_id,
                    "سهمیهٔ رایگان جزوه‌سازی برای امروز به پایان رسیده است "
                    "(سهمیهٔ رایگان OpenRouter روی همهٔ مدل‌های رایگان مشترک است).\n"
                    "نیمه‌شب به‌وقت جهانی (UTC) دوباره برقرار می‌شود؛ متن محفوظ است — فردا دوباره دکمه را بفشارید.",
                    parse_mode=None,
                    buttons=jz_buttons(register_jz_pending(text, display_name, chat_id, user_id)),
                )
                admin_log(f"⚠️ جزوه ناموفق (سهمیهٔ روزانه) — «{display_name[:40]}» | user={user_id}")
            else:
                await client.send_message(
                    chat_id,
                    f"ساخت جزوه در این نوبت انجام نشد:\n{str(res.get('error'))[:200]}\n\n"
                    "متن محفوظ است — دکمه را دوباره بفشارید؛ اگر باز ساخته نشد، فایل را دوباره ارسال کنید.",
                    parse_mode=None,
                    buttons=jz_buttons(register_jz_pending(text, display_name, chat_id, user_id)),
                )
                admin_log(f"⚠️ جزوه ناموفق — «{display_name[:40]}» | user={user_id}\n{str(res.get('error'))[:200]}")
            return

        note = res.get("partial")
        cap = f"جزوه «{res['title']}»"
        if note:
            cap += f"\n({note})"
        if res.get("pdf") and os.path.exists(res["pdf"]):
            await client.send_file(chat_id, res["pdf"], force_document=True,
                                   caption=cap + " — PDF")
        await client.send_file(chat_id, res["html"], force_document=True,
                               caption=cap + " — در هر مرورگری قابل بازکردن است")
        await safe_edit(status, "جزوه آماده و ارسال شد ✅")
        admin_log(
            f"📚 جزوه ارسال شد: «{str(res.get('title'))[:60]}»\n"
            f"{res.get('sections', '?')} فصل | {res.get('words_in', 0)} به {res.get('words_out', 0)} کلمه | "
            f"موتور: {res.get('provider', '?')} | زمان: {fmt_seconds(time.time() - t0)}\n"
            f"user={user_id}"
        )
        log.info(f"📚 جزوه ارسال شد: {res['title']}")
    except Exception as e:
        log.exception(f"❌ خطای جزوه: {e}")
        try:
            await client.send_message(
                chat_id,
                f"ساخت جزوه با خطا متوقف شد:\n{str(e)[:250]}\n\nمتن محفوظ است — دکمه را دوباره بفشارید.",
                parse_mode=None,
                buttons=jz_buttons(register_jz_pending(text, display_name, chat_id, user_id)),
            )
        except Exception:
            pass
        admin_log(f"⚠️ خطای جزوه | user={user_id}\n{str(e)[:200]}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


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
async def process_file(event, src_path: str, display_name: str, token: str, wants_jozve: bool = False):
    chat_id = event.chat_id

    # ---------- ۱) تبدیل به WAV 16kHz ----------
    status = await event.reply("در حال آماده‌سازی صدا...\n(برای فایل‌های حجیم چند دقیقه زمان می‌برد)", parse_mode=None)
    wav_path = os.path.join(tempfile.mkdtemp(prefix="stt_wav_"), "audio16k.wav")
    try:
        info = await asyncio.to_thread(convert_to_wav16k, src_path, wav_path)
    except Exception as e:
        log.error(f"❌ خطای تبدیل ffmpeg: {e}")
        await event.reply(f"تبدیل صدا ناموفق بود:\n{str(e)[:300]}", parse_mode=None)
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
        f"صدا آماده شد؛ مدت آن {fmt_seconds(duration)} است.\n"
        f"تبدیل در {total_chunks} قطعهٔ {CHUNK_SECONDS} ثانیه‌ای انجام می‌شود...",
        parse_mode=None,
    )

    # ---------- ۲) ارسال قطعه‌ها به گوگل، یکی‌یکی ----------
    texts = []           # لیست (زمان شروع قطعه، متن)
    ok = empty = err = 0
    t0 = time.time()
    last_edit = 0.0
    cancelled = False
    progress_msg = await event.reply(
        f"تبدیل آغاز شد...\nقطعه: 0 از {total_chunks}", parse_mode=None
    )

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
                    f"در حال تبدیل... {idx}/{total_chunks} ({pct:.0f}٪)\n"
                    f"{fmt_seconds(offset_sec)} از {fmt_seconds(duration)} — تقریباً {fmt_seconds(eta)} باقی مانده",
                )

    # ---------- ۳) ساخت نتیجه ----------
    try:
        await progress_msg.delete()
    except Exception:
        pass

    if cancelled:
        await event.reply("پردازش این فایل به درخواست شما لغو شد.", parse_mode=None)
        log.info(f"🚫 لغو شد: {display_name}")
        admin_log(f"🚫 لغو شد: «{display_name[:50]}» | user={event.sender_id}")
        return

    clean_text = " ".join(t for _, t in texts).strip()
    stats = (
        f"طول صدا {fmt_seconds(duration)} • زمان تبدیل {fmt_seconds(time.time() - t0)} • "
        f"{ok} قطعهٔ موفق" + (f" • {err} قطعه خطا داد" if err else "")
    )
    log.info(f"🏁 پایان {display_name}: {stats}")
    _label = await user_label(event)

    if not clean_text:
        await event.reply(
            "متنی تشخیص داده نشد!\n"
            "به احتمال زیاد صدای فایل ضعیف است یا گفتاری در آن وجود ندارد — چند دقیقه دیگر دوباره ارسال کنید.\n"
            + stats,
            parse_mode=None,
        )
        admin_log(f"⚠️ تبدیل بدون نتیجه: «{display_name[:50]}»\n{stats}\nکاربر: {_label}")
        return

    # دکمهٔ «تبدیل به جزوه» — متن ۴۸ ساعت در حافظه می‌ماند تا با یک لمس جزوه شود
    jz_token = register_jz_pending(clean_text, display_name, chat_id, event.sender_id)
    kb = jz_buttons(jz_token)

    if len(clean_text) <= MAX_TEXT_IN_CHAT:
        # متن کوتاه → مستقیم در چت
        await event.reply(
            f"{clean_text}\n\n————————————\n{stats}",
            parse_mode=None, buttons=kb,
        )
    else:
        # متن طولانی → فایل txt + پیش‌نمایش در چت
        file_text = f"متن «{display_name}»\n\n{clean_text}"
        buf = io.BytesIO(file_text.encode("utf-8"))
        buf.name = f"{safe_filename(display_name)}.txt"
        preview = clean_text[:900].rsplit(" ", 1)[0] + " …"
        await event.reply(
            f"متن بسیار طولانی است ({len(clean_text):,} حرف)؛ فایل کامل آن ارسال شد.\n\n"
            f"بخش آغازین متن:\n{preview}\n\n{stats}",
            parse_mode=None, buttons=kb,
        )
        await client.send_file(
            chat_id, buf, force_document=True,
            caption=f"متن «{display_name}»",
        )

    admin_log(f"✅ تبدیل شد: «{display_name[:50]}»\n{stats}\nکاربر: {_label}")

    # ---------- ۴) جزوه — فقط وقتی خودِ کاربر خواسته باشد (کپشن «جزوه» یا /jozve on) ----------
    auto = JOZVE_ON.get(chat_id, JOZVE_AUTO)
    if wants_jozve or auto:
        asyncio.create_task(run_jozve(chat_id, clean_text, display_name, event.sender_id))


# =========================================================
#  هندلرها
# =========================================================
@client.on(events.NewMessage(incoming=True, pattern=r"^/(start|help)$"))
async def start_handler(event):
    await event.reply(
        "سلام 👋\n"
        "فایل صوتی یا ویدیویی خود را ارسال کنید تا متن آن استخراج شود.\n\n"
        "• ویس و پیام تصویری\n"
        "• فایل صوتی: mp3 ، m4a ، wav ، ogg ، flac\n"
        "• ویدیو: mp4 ، mkv ، avi — تا ۲ گیگابایت\n\n"
        "پس از هر تبدیل، زیر متن دکمهٔ «تبدیل به جزوه» نمایش داده می‌شود؛ با زدن آن، همان متن به "
        "جزوه‌ای مرتب و تیتربندی‌شده همراه جدول و نمودار (PDF) تبدیل می‌شود.\n\n"
        "/status — وضعیت ربات\n"
        "/cancel — لغو پردازش\n"
        "/jozve — ساخت خودکار جزوه پس از هر تبدیل (روشن/خاموش)"
    )
    is_new = event.sender_id not in known_users
    known_users.add(event.sender_id)
    _save_users()
    _label = await user_label(event)
    admin_log(
        f"👤 {'کاربر جدید' if is_new else 'ورود'}: {_label}\n"
        f"تعداد کاربران ثبت‌شده: {len(known_users)}"
    )


@client.on(events.NewMessage(incoming=True, pattern=r"^/status$"))
async def status_handler(event):
    busy = queue_lock.locked()
    st = ("در حال حاضر مشغول پردازش یک فایل هستم؛ با ارسال فایل، در صف قرار می‌گیرید." if busy
          else "آماده دریافت فایل هستم — همین حالا ارسال کنید.")
    await event.reply(
        f"{st}\n"
        f"در صف: {waiting_count} نفر\n"
        f"مدت فعالیت: {fmt_seconds(time.time() - start_time)}\n"
        f"قطعات {CHUNK_SECONDS} ثانیه‌ای • زبان {LANGUAGE} • سقف {MAX_FILE_MB} مگابایت"
    )


@client.on(events.NewMessage(incoming=True, pattern=r"^/cancel$"))
async def cancel_handler(event):
    tokens = user_tokens.get(event.sender_id, [])
    if not tokens:
        await event.reply("فایلی در صف یا در حال پردازش ندارید.")
        return
    cancelled_tokens.update(tokens)
    await event.reply(f"🚫 درخواست لغو ثبت شد ({len(tokens)} فایل). اگر فایل در صف باشد، پردازش نمی‌شود و اگر در حال پردازش باشد، چند ثانیه بعد متوقف می‌شود.")


@client.on(events.NewMessage(incoming=True, pattern=r"^/jozve(\s+(on|off|روشن|خاموش))?\s*$"))
async def jozve_handler(event):
    """روشن/خاموش کردن «جزوهٔ خودکار» بعد از هر تبدیل (بدون نیاز به دکمه)"""
    if not jozve_available():
        await event.reply(
            "فعلاً امکان ساخت جزوه فراهم نیست.\n"
            "روی سرور باید متغیر محیطی OPENROUTER_API_KEY تنظیم شود "
            "(کلید رایگان از openrouter.ai/keys قابل دریافت است)."
        )
        return
    arg = (event.pattern_match.group(1) or "").strip().lower()
    current = JOZVE_ON.get(event.chat_id, JOZVE_AUTO)
    if arg in ("on", "روشن"):
        state = True
    elif arg in ("off", "خاموش"):
        state = False
    else:
        state = not current
    JOZVE_ON[event.chat_id] = state
    await event.reply(
        ("ساخت خودکار جزوه فعال شد: پس از هر تبدیل، جزوه نیز ساخته و ارسال می‌شود.\n"
         "یادآوری: سهمیهٔ رایگان روزانه محدود است و هر جزوه چند درخواست مصرف می‌کند.")
        if state else
        "غیرفعال شد — جزوه تنها زمانی ساخته می‌شود که زیر متن، دکمهٔ «تبدیل به جزوه» را بفشارید."
    )
    _label = await user_label(event)
    admin_log(f"⚙️ /jozve {'روشن' if state else 'خاموش'} | {_label}")


@client.on(events.CallbackQuery(pattern=r"^jz:"))
async def jz_button_handler(event):
    """دکمهٔ «تبدیل به جزوه» زیر متن ترنسکریپت"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    data = PENDING_JZ.get(token)
    if not data:
        await event.answer(
            "این متن دیگر در حافظه موجود نیست (ربات دوباره راه‌اندازی شده است). فایل را دوباره ارسال و دکمه را بفشارید.",
            alert=True,
        )
        return
    if data.get("busy"):
        await event.answer("جزوه در حال ساخت است؛ لطفاً شکیبا باشید.", alert=True)
        return
    data["busy"] = True
    try:
        await event.answer("در حال انجام است؛ چند دقیقه زمان می‌برد.")
    except Exception:
        pass
    _label = await user_label(event)
    admin_log(f"📚 درخواست جزوه (دکمه) — «{data['title'][:40]}» | {_label}")
    try:
        await run_jozve(data["chat_id"], data["text"], data["title"], data["user_id"])
    finally:
        data["busy"] = False


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
            f"حجم فایل {size_mb:.0f} مگابایت است؛ بیش از سقف تعیین‌شده ({MAX_FILE_MB} مگابایت) قابل دریافت نیست.",
            parse_mode=None,
        )
        return

    display_name = file.name or ("voice-message.ogg" if mime.startswith("audio") else "video.mp4")
    wants_jozve = "جزوه" in (msg.message or "")   # کپشن «جزوه» = جزوه‌سازی خودکارِ همین فایل
    token = uuid.uuid4().hex
    user_tokens.setdefault(event.sender_id, []).append(token)
    _label = await user_label(event)
    admin_log(f"📥 فایل: «{display_name[:50]}» ({size_mb:.0f}MB) | از: {_label}")

    # ---------- صف ----------
    need_queue = queue_lock.locked()
    if need_queue:
        waiting_count += 1
        await event.reply(
            f"فایل شما در صف قرار گرفت (نوبت {waiting_count}).\n"
            "به‌ترتیب پردازش می‌شود — لغو: /cancel",
            parse_mode=None,
        )

    tmp_dir = tempfile.mkdtemp(prefix="stt_dl_")
    src_path = os.path.join(tmp_dir, f"src{file.ext or ''}")
    status = await event.reply("در حال دریافت فایل... 0٪", parse_mode=None)

    try:
        # ---------- دانلود با پیشرفت زنده ----------
        dl_last = {"t": 0.0}

        def dl_progress(current, total):
            now = time.time()
            if now - dl_last["t"] >= 8 or current >= total:
                dl_last["t"] = now
                pct = current * 100.0 / max(1, total)
                mb = current / (1024 * 1024)
                asyncio.ensure_future(safe_edit(status, f"در حال دریافت فایل... {pct:.0f}٪ ({mb:.0f}MB)"))

        await msg.download_media(file=src_path, progress_callback=dl_progress)
        await safe_edit(status, "فایل دریافت شد ✅")
        log.info(f"📥 دانلود شد: {display_name} ({size_mb:.0f}MB)")

        # ---------- پردازش ترتیبی ----------
        async with queue_lock:
            if need_queue:
                waiting_count = max(0, waiting_count - 1)
            if token not in cancelled_tokens:
                await process_file(event, src_path, display_name, token, wants_jozve)
            else:
                await event.reply("این فایل قبل از شروع پردازش لغو شد.", parse_mode=None)

    except Exception as e:
        log.exception(f"❌ خطای کلی در پردازش {display_name}: {e}")
        await event.reply(f"خطای غیرمنتظره در پردازش فایل:\n{str(e)[:300]}", parse_mode=None)
        admin_log(f"⚠️ خطای پردازش «{display_name[:40]}»\n{str(e)[:200]}\nکاربر: {_label}")
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
