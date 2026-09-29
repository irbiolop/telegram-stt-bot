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
#   DEEPSEEK_API_KEY ← اختیاری؛ API رسمی DeepSeek (پولی اما بسیار ارزان، بدون سهمیهٔ روزانه) —
#     در صورت تنظیم اولویت دارد و در خطا خودکار به مدل‌های رایگان OpenRouter برمی‌گردد
JOZVE_AUTO = os.getenv("JOZVE_AUTO", "0") == "1"   # بعد از هر تبدیل «خودکار» جزوه ساخته شود؟ (پیش‌فرض: فقط با دکمه)
JOZVE_ON = {}                                       # chat_id → True/False (با دستور /jozve)
ADMIN_ID = int(os.getenv("ADMIN_ID", "7433357700"))  # گزارش ورود/استفادهٔ کاربران به این آیدی تلگرام

MAX_TEXT_IN_CHAT = 3500  # بالاتر از این مقدار، متن به‌صورت فایل txt فرستاده می‌شود

# --- تنظیم هر تبدیل: انتخاب زبان + حذف قطعات (گام‌به‌گام با دکمه) ---
# زبان‌های قابل انتخاب هنگام ارسال فایل (کد گوگل ← نام نمایشی)
LANGUAGES = {
    "fa": ("fa-IR", "🇮🇷 فارسی"),
    "en": ("en-US", "🇬🇧 انگلیسی"),
    "ar": ("ar-SA", "🇸🇦 عربی"),
    "tr": ("tr-TR", "🇹🇷 ترکی"),
    "ru": ("ru-RU", "🇷🇺 روسی"),
    "fr": ("fr-FR", "🇫🇷 فرانسوی"),
}
PENDING_SETUP = {}   # token → وضعیت تنظیم هر فایل (زبان، حذفیات، مراحل)
user_pending = {}    # user_id → {"token", "stage"} — مسیریابی ورودی متنی دقیقه‌ها
SETUP_TTL = 2 * 3600  # عمر تنظیم نیمه‌کاره: ۲ ساعت

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


def register_jz_pending(text: str, title: str, chat_id: int, user_id: int,
                        custom_prompt: str = "") -> str:
    """متن آمادهٔ جزوه را در حافظه نگه می‌دارد و توکن دکمه را برمی‌گرداند (عمر: ۴۸ ساعت)"""
    now = time.time()
    for t in [t for t, v in PENDING_JZ.items() if now - v["ts"] > 48 * 3600]:
        PENDING_JZ.pop(t, None)
    for uid, up in list(user_pending.items()):   # انتظار پرامپتِ توکن‌های منقضی پاک شود
        if up.get("stage") == "jz_prompt" and up.get("token") not in PENDING_JZ:
            user_pending.pop(uid, None)
    token = uuid.uuid4().hex[:12]
    PENDING_JZ[token] = {"text": text, "title": title, "chat_id": chat_id,
                         "user_id": user_id, "ts": now, "busy": False,
                         "custom_prompt": (custom_prompt or "").strip()}
    return token


def jz_buttons(token: str):
    return [[Button.inline("📚 تبدیل به جزوه", f"jz:{token}".encode())],
            [Button.inline("✍️ جزوه با پرامپت دلخواه", f"jzp:{token}".encode())]]


# =========================================================
#  تنظیم تبدیل: پارس دقیقه‌های حذفی + محاسبهٔ بازه‌های باقی‌مانده
# =========================================================
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _hms_to_sec(tok: str):
    """«M» (دقیقه) یا «M:SS» یا «H:MM:SS» → ثانیه"""
    tok = (tok or "").strip()
    if not tok:
        return None
    parts = tok.split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 1:
        return nums[0] * 60          # فقط دقیقه
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]   # دقیقه:ثانیه
    return nums[0] * 3600 + nums[1] * 60 + nums[2]  # ساعت:دقیقه:ثانیه


def parse_skip_ranges(raw: str):
    """ورودی متنی کاربر برای دقیقه‌های حذفی → (بازه‌ها، پیام خطا).
    نمونه‌های قابل قبول:
      3-7   |   ۳ تا ۷   |   2:30 تا 4:10   |   3-7، 12-15   |   از 10 تا 12:30 و 15 تا 16
    """
    txt = (raw or "").translate(_FA_DIGITS).replace("\u200c", " ").strip()
    if not txt:
        return None, "متن خالی است."
    # حذف کلمه‌های اضافی رایج (دقیقه، بخش، قسمت، از، لطفا)
    txt = re.sub(r"(?:دقیقه|مینوت|بخش|قسمت|لطفا)", " ", txt)
    txt = re.sub(r"\bاز\b", " ", txt)
    txt = txt.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ")
    parts = [p.strip() for p in re.split(r"[،,;؛\n+]+|\s+و\s+", txt) if p.strip()]
    if not parts:
        return None, "بازه‌ای پیدا نشد."
    ranges = []
    for part in parts:
        m = re.match(
            r"^(\d{1,2}(?::\d{1,2}){0,2})\s*(?:تا|[-–—~ـ])\s*(\d{1,2}(?::\d{1,2}){0,2})$",
            part,
        )
        if not m:
            return None, f"قسمت «{part[:30]}» قابل قبول نیست."
        a, b = _hms_to_sec(m.group(1)), _hms_to_sec(m.group(2))
        if a is None or b is None or b <= a:
            return None, f"قسمت «{part[:30]}» درست نیست؛ پایان بازه باید بعد از شروع آن باشد."
        if b - a < 5:
            return None, "هر بازهٔ حذفی باید دست‌کم ۵ ثانیه باشد."
        ranges.append((a, min(b, 24 * 3600)))
    if len(ranges) > 30:
        return None, "بیش از ۳۰ بازه در یک فایل مجاز نیست."
    return ranges, ""


def merge_ranges(rs):
    """بازه‌های هم‌پوشان را ادغام و مرتب می‌کند."""
    rs = sorted((max(0.0, float(a)), float(b)) for a, b in (rs or []) if b > a)
    out = []
    for a, b in rs:
        if out and a <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def compute_keep_segments(duration: float, skip_ranges):
    """با احتساب بازه‌های حذفی، بازه‌های باقی‌ماندهٔ صدا را برمی‌گرداند."""
    keeps, cur = [], 0.0
    for a, b in merge_ranges(skip_ranges or []):
        if a > cur:
            keeps.append((cur, min(a, duration)))
        cur = max(cur, b)
        if cur >= duration:
            break
    if cur < duration:
        keeps.append((cur, duration))
    return [(a, b) for a, b in keeps if b - a >= 1.0]


# =========================================================
#  وضعیت تنظیم نیمه‌کاره (زبان/حذفیات) + دکمه‌هایش
# =========================================================
def _prune_setups():
    now = time.time()
    expired = [t for t, v in PENDING_SETUP.items() if now - v.get("ts", 0) > SETUP_TTL]
    for t in expired:
        PENDING_SETUP.pop(t, None)
    for uid, up in list(user_pending.items()):
        if up.get("token") in expired:
            user_pending.pop(uid, None)


def get_setup(token: str):
    _prune_setups()
    return PENDING_SETUP.get(token)


def lang_buttons(token: str):
    codes = list(LANGUAGES)
    rows = []
    for i in range(0, len(codes), 3):
        rows.append([Button.inline(LANGUAGES[c][1], f"lang:{token}:{c}".encode())
                     for c in codes[i:i + 3]])
    return rows


def skip_q_buttons(token: str):
    return [[Button.inline("✂️ حذف قطعات", f"skipq:{token}".encode()),
             Button.inline("▶️ بدون حذف، ادامه", f"noskip:{token}".encode())],
            [Button.inline("✖️ انصراف", f"cx:{token}".encode())]]


def skip_input_buttons(token: str):
    return [[Button.inline("↩️ بازگشت", f"backq:{token}".encode())]]


def confirm_buttons(token: str):
    return [[Button.inline("✅ تأیید و شروع تبدیل", f"go:{token}".encode()),
             Button.inline("✖️ انصراف", f"cx:{token}".encode())]]


def confirm_text(st: dict) -> str:
    ranges = st.get("skip_ranges") or []
    skip_txt = ("، ".join(f"{fmt_seconds(a)} تا {fmt_seconds(b)}" for a, b in ranges)
                if ranges else "ندارد")
    return (
        "خلاصهٔ تنظیمات تبدیل:\n"
        f"• فایل: {str(st.get('display_name'))[:60]} ({st.get('size_mb', 0):.0f} مگابایت)\n"
        f"• زبان گفتار: {st.get('lang_name', '—')}\n"
        f"• بخش‌های حذفی: {skip_txt}\n\n"
        "با زدن «تأیید و شروع تبدیل»، دریافت و تبدیل آغاز می‌شود."
    )


# =========================================================
#  ساخت و ارسال جزوه (هم برای دکمه، هم حالت خودکار/کپشن)
# =========================================================
async def run_jozve(chat_id: int, text: str, display_name: str, user_id: int = 0,
                    custom_prompt: str = ""):
    """جزوه را از متن آماده می‌سازد و فایل‌ها را می‌فرستد — خارج از قفل صف اجرا می‌شود.
    custom_prompt: دستور ویژهٔ کاربر (از دکمهٔ «جزوه با پرامپت دلخواه»)"""
    if not jozve_available():
        await client.send_message(
            chat_id,
            "فعلاً امکان ساخت جزوه فراهم نیست؛ کلید OPENROUTER_API_KEY (یا DEEPSEEK_API_KEY) روی سرور تنظیم نشده است.",
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
        res = await asyncio.to_thread(jozve.produce_jozve, text, display_name, out_dir, _prog,
                                      custom_prompt)

        if not res.get("ok"):
            if res.get("quota"):
                await client.send_message(
                    chat_id,
                    "سهمیهٔ رایگان جزوه‌سازی برای امروز به پایان رسیده است "
                    "(سهمیهٔ رایگان OpenRouter روی همهٔ مدل‌های رایگان مشترک است).\n"
                    "نیمه‌شب به‌وقت جهانی (UTC) دوباره برقرار می‌شود؛ متن محفوظ است — فردا دوباره دکمه را بفشارید.",
                    parse_mode=None,
                    buttons=jz_buttons(register_jz_pending(text, display_name, chat_id, user_id, custom_prompt)),
                )
                admin_log(f"⚠️ جزوه ناموفق (سهمیهٔ روزانه) — «{display_name[:40]}» | user={user_id}")
            else:
                await client.send_message(
                    chat_id,
                    f"ساخت جزوه در این نوبت انجام نشد:\n{str(res.get('error'))[:200]}\n\n"
                    "متن محفوظ است — دکمه را دوباره بفشارید؛ اگر باز ساخته نشد، فایل را دوباره ارسال کنید.",
                    parse_mode=None,
                    buttons=jz_buttons(register_jz_pending(text, display_name, chat_id, user_id, custom_prompt)),
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
            f"📚 جزوه ارسال شد{' (پرامپت دلخواه)' if (custom_prompt or '').strip() else ''}: «{str(res.get('title'))[:60]}»\n"
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
async def process_file(event, src_path: str, display_name: str, token: str, wants_jozve: bool = False,
                       lang_code: str = "fa-IR", skip_ranges=None, label: str = ""):
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
    keeps = compute_keep_segments(duration, skip_ranges)
    if not keeps:
        await event.reply(
            "همهٔ فایل داخل بازه‌های حذفی قرار گرفت و بخشی برای تبدیل نماند.\n"
            "فایل را دوباره ارسال کنید و بازه‌های حذفی را درست‌تر وارد کنید.",
            parse_mode=None,
        )
        return
    total_chunks = sum(max(1, math.ceil((b - a) / CHUNK_SECONDS)) for a, b in keeps)
    skipped_sec = max(0.0, duration - sum(b - a for a, b in keeps))
    lang_name = next((n for c, (cc, n) in LANGUAGES.items() if cc == lang_code), lang_code)
    log.info(f"📦 {display_name}: طول {fmt_seconds(duration)} → {total_chunks} قطعه "
             f"(زبان {lang_code}، حذفی {fmt_seconds(skipped_sec)})")
    await event.reply(
        f"صدا آماده شد؛ مدت آن {fmt_seconds(duration)} است.\n"
        f"زبان تشخیص گفتار: {lang_name}\n"
        + (f"بخش‌های حذفی: {fmt_seconds(skipped_sec)} از صدا کنار گذاشته می‌شود.\n" if skipped_sec > 0.5 else "")
        + f"تبدیل در {total_chunks} قطعهٔ {CHUNK_SECONDS} ثانیه‌ای انجام می‌شود...",
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
        for seg_a, seg_b in keeps:
            if token in cancelled_tokens:
                cancelled = True
                break
            try:
                w.setpos(int(seg_a * rate))   # پرش به شروع بازهٔ سالم
            except Exception:
                pass
            seg_remaining = int((seg_b - seg_a) * rate)
            seg_off = seg_a
            while seg_remaining > 0:
                if token in cancelled_tokens:
                    cancelled = True
                    break
                n = min(frames_per_chunk, seg_remaining)
                frames = w.readframes(n)   # خواندن ترتیبی از دیسک — رم ثابت می‌ماند
                if not frames:
                    break

                offset_sec = seg_off
                result = await asyncio.to_thread(send_to_google_api, frames, lang_code)

                if result["success"] and result["text"]:
                    texts.append((offset_sec, result["text"]))
                    ok += 1
                elif result["success"]:
                    empty += 1   # سکوت یا کیفیت پایین
                else:
                    err += 1
                    log.warning(f"❌ قطعه {idx + 1}: {result['error']}")

                seg_off += len(frames) / (2.0 * rate)   # ۱۶بیت = ۲ بایت در نمونه
                seg_remaining -= n
                idx += 1

                # ---------- پیشرفت زنده (هر ~۲۰ ثانیه) ----------
                now = time.time()
                if now - last_edit >= 20 or idx == total_chunks:
                    last_edit = now
                    pct = idx * 100.0 / max(1, total_chunks)
                    elapsed = now - t0
                    eta = (elapsed / idx) * (total_chunks - idx) if idx else 0
                    await safe_edit(
                        progress_msg,
                        f"در حال تبدیل... {idx}/{total_chunks} ({pct:.0f}٪)\n"
                        f"{fmt_seconds(offset_sec)} از {fmt_seconds(duration)} — تقریباً {fmt_seconds(eta)} باقی مانده",
                    )
        if token in cancelled_tokens:
            cancelled = True

    # فایل WAV موقت دیگر لازم نیست
    try:
        shutil.rmtree(os.path.dirname(wav_path), ignore_errors=True)
    except Exception:
        pass

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
        + (f" • {fmt_seconds(skipped_sec)} حذف‌شده" if skipped_sec > 0.5 else "")
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
        "پس از ارسال فایل، سه مرحلهٔ کوتاه را طی می‌کنیم:\n"
        "۱) زبان گفتار فایل را انتخاب می‌کنید (فارسی، انگلیسی، عربی و…)\n"
        "۲) در صورت نیاز، دقیقه‌های حذفی را مشخص می‌کنید تا از تبدیل کنار گذاشته شوند\n"
        "۳) خلاصهٔ تنظیمات را تأیید می‌کنید و تبدیل آغاز می‌شود\n\n"
        "پس از هر تبدیل، زیر متن دو دکمه نمایش داده می‌شود:\n"
        "• «تبدیل به جزوه» — همان متن به جزوه‌ای مرتب و تیتربندی‌شده همراه جدول، نمودار و نقشهٔ ذهنی (PDF) تبدیل می‌شود.\n"
        "• «جزوه با پرامپت دلخواه» — دستور خودتان را می‌نویسید (مثلاً «فقط نکته‌های امتحانی را استخراج کن») و جزوه طبق همان دستور ساخته می‌شود.\n\n"
        "/status — وضعیت ربات\n"
        "/cancel — لغو تنظیم یا پردازش\n"
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
        f"قطعات {CHUNK_SECONDS} ثانیه‌ای • زبان: انتخابی هنگام ارسال فایل (پیش‌فرض فارسی) • سقف {MAX_FILE_MB} مگابایت"
    )


@client.on(events.NewMessage(incoming=True, pattern=r"^/cancel$"))
async def cancel_handler(event):
    # تنظیم نیمه‌کاره (انتخاب زبان/حذفیات) هم لغو شود
    cancelled_setups = 0
    up = user_pending.pop(event.sender_id, None)
    if up and up.get("token") in PENDING_SETUP:
        PENDING_SETUP.pop(up["token"], None)
        cancelled_setups += 1
    for t in [t for t, s in PENDING_SETUP.items() if s.get("user_id") == event.sender_id]:
        PENDING_SETUP.pop(t, None)
        cancelled_setups += 1

    tokens = user_tokens.get(event.sender_id, [])
    if not tokens and not cancelled_setups:
        await event.reply("فایلی در صف یا در حال پردازش ندارید.")
        return
    if tokens:
        cancelled_tokens.update(tokens)
    parts = []
    if cancelled_setups:
        parts.append(f"{cancelled_setups} تنظیم نیمه‌کاره باطل شد")
    if tokens:
        parts.append(f"درخواست لغو برای {len(tokens)} فایل ثبت شد (اگر در صف باشد پردازش نمی‌شود و اگر در حال پردازش باشد چند ثانیه بعد متوقف می‌شود)")
    await event.reply("🚫 " + "؛ ".join(parts) + ".")


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


@client.on(events.CallbackQuery(pattern=r"^jzp:"))
async def jz_prompt_start_handler(event):
    """دکمهٔ «جزوه با پرامپت دلخواه» — انتظار برای دستور کاربر"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    data = PENDING_JZ.get(token)
    if not data:
        await event.answer(
            "این متن دیگر در حافظه موجود نیست؛ فایل را دوباره ارسال و دکمه را بفشارید.",
            alert=True,
        )
        return
    if data.get("busy"):
        await event.answer("جزوه در حال ساخت است؛ لطفاً شکیبا باشید.", alert=True)
        return
    user_pending[event.sender_id] = {"token": token, "stage": "jz_prompt"}
    await event.answer()
    await event.edit(
        "دستور دلخواه خود را در یک پیام بنویسید و بفرستید.\n\n"
        "چند نمونه:\n"
        "• «فقط نکته‌های امتحانی و فرمول‌ها را استخراج کن»\n"
        "• «جزوه را به‌صورت پرسش و پاسخ بچین»\n"
        "• «خلاصه‌ای برای مرور شب امتحان بساز»\n\n"
        "دستور شما به قوانین اصلی جزوه (فارسی، پوشش کامل، جدول و نمودار) افزوده می‌شود؛ "
        "پس از ثبت، خلاصهٔ آن را برای تأیید نشان می‌دهم.",
        parse_mode=None,
        buttons=[[Button.inline("✖️ انصراف", f"jzx:{token}".encode())]],
    )


@client.on(events.CallbackQuery(pattern=r"^jzx:"))
async def jz_prompt_cancel_handler(event):
    """انصراف از ساخت جزوهٔ دلخواه"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    up = user_pending.get(event.sender_id)
    if up and up.get("token") == token:
        user_pending.pop(event.sender_id, None)
    data = PENDING_JZ.get(token)
    if data:
        data.pop("custom_prompt", None)
    await event.answer("لغو شد.")
    await event.edit(
        "درخواست جزوهٔ دلخواه لغو شد؛ دکمه‌های زیر متن ترنسکریپت همچنان فعال‌اند.",
        parse_mode=None, buttons=Button.clear(),
    )


@client.on(events.CallbackQuery(pattern=r"^jzg:"))
async def jz_prompt_go_handler(event):
    """تأیید پرامپت دلخواه — آغاز ساخت جزوه با دستور کاربر"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    data = PENDING_JZ.get(token)
    if not data:
        await event.answer(
            "این متن دیگر در حافظه موجود نیست؛ فایل را دوباره ارسال کنید.", alert=True)
        return
    if data.get("busy"):
        await event.answer("جزوه در حال ساخت است؛ لطفاً شکیبا باشید.", alert=True)
        return
    prompt = (data.get("custom_prompt") or "").strip()
    if not prompt:
        await event.answer("اول دستور دلخواه را بفرستید.", alert=True)
        return
    up = user_pending.get(event.sender_id)
    if up and up.get("token") == token:
        user_pending.pop(event.sender_id, None)
    data["busy"] = True
    try:
        await event.answer("در حال ساخت جزوه با دستور شما؛ چند دقیقه زمان می‌برد.")
    except Exception:
        pass
    _label = await user_label(event)
    admin_log(f"📚 درخواست جزوه (پرامپت دلخواه) — «{data['title'][:40]}» | {_label}\n"
              f"پرامپت: {prompt[:150]}")
    try:
        await run_jozve(data["chat_id"], data["text"], data["title"],
                        data["user_id"], custom_prompt=prompt)
    finally:
        data["busy"] = False


@client.on(events.NewMessage(incoming=True))
async def media_handler(event):
    """پذیرش فایل صوتی/تصویری → آغاز تنظیم گام‌به‌گام (زبان ← حذفیات ← تأیید)"""
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

    # اگر تنظیم نیمه‌کارهٔ قبلی دارد، باطل شود (تنظیم جدید جایگزین می‌شود)
    _prune_setups()
    old = user_pending.pop(event.sender_id, None)
    if old:
        PENDING_SETUP.pop(old.get("token"), None)

    token = uuid.uuid4().hex[:12]
    _label = await user_label(event)
    PENDING_SETUP[token] = {
        "chat_id": event.chat_id, "msg_id": msg.id, "user_id": event.sender_id,
        "display_name": display_name, "size_mb": size_mb,
        "wants_jozve": wants_jozve, "ts": time.time(), "label": _label,
        "lang_code": "", "lang_name": "", "skip_ranges": None, "stage": "lang",
    }
    user_pending[event.sender_id] = {"token": token, "stage": "lang"}
    admin_log(f"📥 فایل: «{display_name[:50]}» ({size_mb:.0f}MB) | از: {_label}")

    await event.reply(
        "فایل شما ثبت شد؛ دو گام کوتاه تا شروع تبدیل مانده است.\n\n"
        "گام ۱ — زبان گفتار فایل چیست؟\n"
        "(بعد از انتخاب زبان، در صورت نیاز دقیقه‌های حذفی را مشخص می‌کنید و با تأیید نهایی، تبدیل آغاز می‌شود.)",
        parse_mode=None,
        buttons=lang_buttons(token),
    )


async def _run_pipeline(st: dict, token: str):
    """پس از تأیید نهایی: دریافت فایل → صف → تبدیل (همان مسیر قبلی media_handler)"""
    global waiting_count
    chat_id = st["chat_id"]
    display_name = st["display_name"]

    try:
        msg = await client.get_messages(chat_id, ids=st["msg_id"])
    except Exception:
        msg = None
    if not msg or not getattr(msg, "media", None):
        await client.send_message(
            chat_id,
            "پیام فایل پیدا نشد (احتمالاً حذف شده است)؛ لطفاً فایل را دوباره ارسال کنید.",
            parse_mode=None,
        )
        return

    file = msg.file
    size_mb = ((file.size or 0) / (1024 * 1024)) if file else float(st.get("size_mb") or 0)
    if file is None or size_mb > MAX_FILE_MB:
        await client.send_message(
            chat_id,
            "این فایل دیگر قابل دریافت نیست یا حجمش بیش از سقف مجاز است؛ فایل را دوباره ارسال کنید.",
            parse_mode=None,
        )
        return

    _label = st.get("label") or f"id={st.get('user_id')}"
    skip_txt = f"{len(st.get('skip_ranges') or [])} بازه" if st.get("skip_ranges") else "ندارد"
    admin_log(f"▶️ تبدیل آغاز شد: «{display_name[:50]}» | زبان: {st.get('lang_name')} | "
              f"حذفیات: {skip_txt} | {_label}")

    # ---------- صف ----------
    need_queue = queue_lock.locked()
    if need_queue:
        waiting_count += 1
        await client.send_message(
            chat_id,
            f"فایل شما در صف قرار گرفت (نوبت {waiting_count}).\n"
            "به‌ترتیب پردازش می‌شود — لغو: /cancel",
            parse_mode=None,
        )

    tmp_dir = tempfile.mkdtemp(prefix="stt_dl_")
    src_path = os.path.join(tmp_dir, f"src{file.ext or ''}")
    status = await client.send_message(chat_id, "در حال دریافت فایل... 0٪", parse_mode=None)

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
        user_tokens.setdefault(st["user_id"], []).append(token)
        async with queue_lock:
            if need_queue:
                waiting_count = max(0, waiting_count - 1)
            if token not in cancelled_tokens:
                await process_file(
                    msg, src_path, display_name, token,
                    wants_jozve=bool(st.get("wants_jozve")),
                    lang_code=st.get("lang_code") or "fa-IR",
                    skip_ranges=st.get("skip_ranges"),
                    label=_label,
                )
            else:
                await client.send_message(chat_id, "این فایل قبل از شروع پردازش لغو شد.", parse_mode=None)

    except Exception as e:
        log.exception(f"❌ خطای کلی در پردازش {display_name}: {e}")
        await client.send_message(chat_id, f"خطای غیرمنتظره در پردازش فایل:\n{str(e)[:300]}", parse_mode=None)
        admin_log(f"⚠️ خطای پردازش «{display_name[:40]}»\n{str(e)[:200]}\nکاربر: {_label}")
    finally:
        # پاکسازی فایل‌های موقت و آزادسازی نوبت
        cancelled_tokens.discard(token)
        try:
            await status.delete()
        except Exception:
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tokens_of_user = user_tokens.get(st["user_id"])
        if tokens_of_user and token in tokens_of_user:
            tokens_of_user.remove(token)
            if not tokens_of_user:
                user_tokens.pop(st["user_id"], None)


# ---------- کال‌بک‌های تنظیم گام‌به‌گام ----------

@client.on(events.CallbackQuery(pattern=r"^lang:"))
async def lang_pick_handler(event):
    """گام ۱: انتخاب زبان گفتار فایل"""
    try:
        _, token, code = (event.data or b"").decode("utf-8", "ignore").split(":", 2)
    except ValueError:
        await event.answer("دادهٔ دکمه نامعتبر است.", alert=True)
        return
    st = get_setup(token)
    if not st:
        await event.answer("این درخواست منقضی شده است؛ فایل را دوباره ارسال کنید.", alert=True)
        return
    if code not in LANGUAGES:
        await event.answer("زبان نامعتبر است.", alert=True)
        return
    cc, name = LANGUAGES[code]
    st["lang_code"], st["lang_name"], st["stage"] = cc, name, "skip_q"
    user_pending[st["user_id"]] = {"token": token, "stage": "skip_q"}
    await event.answer(f"زبان: {name}")
    await event.edit(
        f"زبان گفتار: {name}\n\n"
        "گام ۲ — آیا می‌خواهید بخشی از فایل از تبدیل حذف شود؟\n"
        "با «حذف قطعات» می‌توانید دقیقه‌های مشخصی (مثلاً تیتراژ، آگهی یا مقدمه) را کنار بگذارید.",
        parse_mode=None,
        buttons=skip_q_buttons(token),
    )


@client.on(events.CallbackQuery(pattern=r"^skipq:"))
async def skip_input_start_handler(event):
    """گام ۲ (الف): کاربر می‌خواهد قطعاتی را حذف کند — منتظر دقیقه‌ها می‌مانیم"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    st = get_setup(token)
    if not st:
        await event.answer("این درخواست منقضی شده است؛ فایل را دوباره ارسال کنید.", alert=True)
        return
    st["stage"] = "skip_input"
    user_pending[st["user_id"]] = {"token": token, "stage": "skip_input"}
    await event.answer("دقیقه‌ها را بنویسید.")
    await event.edit(
        "دقیقهٔ بخش‌هایی که باید حذف شود را بنویسید و بفرستید.\n\n"
        "قالب‌های قابل قبول:\n"
        "• 3-7\n"
        "• ۳ تا ۷\n"
        "• 2:30 تا 4:10\n"
        "• 3-7، 12-15  (چند بازه با ویرگول یا «و»)\n\n"
        "پس از ثبت، خلاصهٔ تنظیمات را برای تأیید نهایی نشان می‌دهم.",
        parse_mode=None,
        buttons=skip_input_buttons(token),
    )


@client.on(events.CallbackQuery(pattern=r"^backq:"))
async def skip_back_handler(event):
    """بازگشت از ورودی دقیقه‌ها به پرسش حذف قطعات"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    st = get_setup(token)
    if not st:
        await event.answer("این درخواست منقضی شده است؛ فایل را دوباره ارسال کنید.", alert=True)
        return
    st["stage"] = "skip_q"
    user_pending[st["user_id"]] = {"token": token, "stage": "skip_q"}
    await event.answer()
    await event.edit(
        f"زبان گفتار: {st.get('lang_name', '—')}\n\n"
        "گام ۲ — آیا می‌خواهید بخشی از فایل از تبدیل حذف شود؟",
        parse_mode=None,
        buttons=skip_q_buttons(token),
    )


@client.on(events.CallbackQuery(pattern=r"^noskip:"))
async def noskip_handler(event):
    """گام ۲ (ب): بدون حذف — نمایش خلاصهٔ تأیید"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    st = get_setup(token)
    if not st:
        await event.answer("این درخواست منقضی شده است؛ فایل را دوباره ارسال کنید.", alert=True)
        return
    st["skip_ranges"] = None
    user_pending.pop(st["user_id"], None)
    await event.answer()
    await event.edit(confirm_text(st), parse_mode=None, buttons=confirm_buttons(token))


@client.on(events.CallbackQuery(pattern=r"^go:"))
async def go_handler(event):
    """تأیید نهایی — آغاز دریافت و تبدیل"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    st = get_setup(token)
    if not st:
        await event.answer("این درخواست منقضی شده است؛ فایل را دوباره ارسال کنید.", alert=True)
        return
    if not st.get("lang_code"):
        await event.answer("اول زبان گفتار را انتخاب کنید.", alert=True)
        return
    PENDING_SETUP.pop(token, None)
    up = user_pending.get(st["user_id"])
    if up and up.get("token") == token:
        user_pending.pop(st["user_id"], None)
    await event.answer("در حال آماده‌سازی...")
    await event.edit("تنظیمات تأیید شد ✅ — پیگیری پردازش در پیام‌های بعدی...",
                     parse_mode=None, buttons=Button.clear())
    await _run_pipeline(st, token)


@client.on(events.CallbackQuery(pattern=r"^cx:"))
async def cancel_setup_handler(event):
    """انصراف از تنظیم این فایل"""
    token = (event.data or b"").decode("utf-8", "ignore").split(":", 1)[-1]
    st = get_setup(token)
    if st:
        PENDING_SETUP.pop(token, None)
        up = user_pending.get(st.get("user_id"))
        if up and up.get("token") == token:
            user_pending.pop(st["user_id"], None)
    await event.answer("لغو شد.")
    await event.edit(
        "این درخواست لغو شد؛ فایل را دوباره ارسال کنید تا از ابتدا تنظیم شود.",
        parse_mode=None, buttons=Button.clear(),
    )


@client.on(events.NewMessage(incoming=True))
async def skip_text_handler(event):
    """دریافت متنی دقیقه‌های حذفی (تنها در چت خصوصی و فقط وقتی کاربر در مرحلهٔ ورودِ دقیقه‌هاست)"""
    if not event.is_private:
        return
    raw = (event.message.message or "").strip()
    if not raw or raw.startswith("/"):
        return
    if event.message.media:
        return
    up = user_pending.get(event.sender_id)
    if not up or up.get("stage") != "skip_input":
        return
    token = up["token"]
    st = get_setup(token)
    if not st:
        user_pending.pop(event.sender_id, None)
        return

    ranges, err = parse_skip_ranges(raw)
    if not ranges:
        await event.reply(
            f"{err}\n\nیک بار دیگر با این قالب بفرستید:\n"
            "• 3-7\n• ۲:۳۰ تا ۴:۱۰\n• 3-7، 12-15",
            parse_mode=None,
        )
        return

    st["skip_ranges"] = merge_ranges(ranges)
    st["stage"] = "confirm"
    user_pending.pop(event.sender_id, None)
    await event.reply(confirm_text(st), parse_mode=None, buttons=confirm_buttons(token))


@client.on(events.NewMessage(incoming=True))
async def jz_prompt_text_handler(event):
    """دریافت پرامپت دلخواه جزوه (چت خصوصی، فقط وقتی کاربر در انتظار پرامپت است)"""
    if not event.is_private:
        return
    raw = (event.message.message or "").strip()
    if not raw or raw.startswith("/") or event.message.media:
        return
    up = user_pending.get(event.sender_id)
    if not up or up.get("stage") != "jz_prompt":
        return
    token = up["token"]
    data = PENDING_JZ.get(token)
    if not data:
        user_pending.pop(event.sender_id, None)
        return
    if len(raw) > 500:
        await event.reply(
            "دستور شما طولانی است (حداکثر ۵۰۰ حرف)؛ لطفاً کوتاه‌تر بفرستید.",
            parse_mode=None,
        )
        return
    data["custom_prompt"] = raw
    user_pending.pop(event.sender_id, None)
    shown = raw if len(raw) <= 300 else raw[:300] + "…"
    await event.reply(
        f"دستور شما ثبت شد:\n\n«{shown}»\n\n"
        "با زدن «تأیید و ساخت جزوه»، جزوه بر اساس همین دستور ساخته و ارسال می‌شود.",
        parse_mode=None,
        buttons=[[Button.inline("✅ تأیید و ساخت جزوه", f"jzg:{token}".encode()),
                  Button.inline("✖️ انصراف", f"jzx:{token}".encode())]],
    )


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
