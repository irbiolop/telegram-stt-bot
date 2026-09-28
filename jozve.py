#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📚 ماژول جزوه‌ساز هوش مصنوعی (jozve.py)
=========================================================
متن خام ترنسکریپت → ویراستاری و ساختاردهی توسط LLM طبق «زبان مارکاپ جزوه»
→ رندر به HTML فارسی راست‌چین زیبا → تبدیل به PDF با WeasyPrint (اگر نصب بود)

زبان مارکاپ جزوه (JM):
  #  عنوان کل جزوه        ##  فصل        ###  زیرتیتر
  متن پاراگراف | - لیست | 1. لیست مرتب | > نقل‌قول | ==هایلایت== **بولد** *ایتالیک* `کد`
  :::note/tip/warning/important/example/summary عنوان  ...  :::     ← کادرهای رنگی
  | جدول | GFM |   ```mermaid  نقشه ذهنی/فلوچارت   ```chart  نمودار میله‌ای

راهبرد ضد اهمال‌کاری:
  ۱) برنامه‌ریز (Planner) فقط عنوان/شرح فصل‌ها را می‌سازد.
  ۲) مرز فصل‌ها به‌صورت «قطعی» روی خود متن (مرز جمله) بریده می‌شود؛ نه با قضاوت مدل.
  ۳) هر فراخوانی فقط یک بازهٔ مشخص را می‌نویسد و ابتدا/انتهای بازه با نقل‌قول
     کلمه‌به‌کلمه به مدل قفل می‌شود ⇒ هیچ بخشی از متن نمی‌تواند جا بیفتد.
  ۴) خروجی ناقص (finish=length) با «ادامه بده» تا ۲ بار تکمیل می‌شود.

کلیدها از محیط:
  OPENROUTER_API_KEY ← رایگان از openrouter.ai/keys  (تیر رایگان: ۵۰ درخواست در روز)
  JOZVE_MODELS       ← اختیاری؛ لیست مدل‌های جدا با کاما
"""

import os
import re
import json
import time
import math
import html
import base64
import logging
import datetime

import requests

log = logging.getLogger("Jozve")

# =========================================================
#  تنظیمات
# =========================================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_REFERER = os.getenv("OPENROUTER_REFERER", "https://github.com/irbiolop/telegram-stt-bot")

# زنجیرهٔ مدل‌های رایگان — اولی اصلی، بقیه پشتیبان (اگر یکی شلوغ/خطا داد می‌پریم بعدی)
DEFAULT_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",      # تست‌شده — اصلی (reasoning بلند دارد — سنتینل الزامی)
    "nvidia/nemotron-3-ultra-550b-a55b:free",  # پشتیبان ۱
    "qwen/qwen3.8-27b:free",                    # پشتیبان ۲ (گاهی شلوغ)
    "google/gemma-4-31b-it:free",               # پشتیبان ۳ (گاهی شلوغ)
]
MODELS = [m.strip() for m in os.getenv("JOZVE_MODELS", "").split(",") if m.strip()] or DEFAULT_MODELS

SECTION_WORDS = int(os.getenv("JOZVE_SECTION_WORDS", "1300"))  # طول تقریبی هر بازه (کلمه)
DEBUG_DIR = os.getenv("JOZVE_DEBUG_DIR", "")                  # اگر ست شود، پاسخ خام مدل ذخیره می‌شود


def _debug_dump(name: str, text: str):
    if DEBUG_DIR:
        try:
            with open(os.path.join(DEBUG_DIR, name), "w", encoding="utf-8") as f:
                f.write(text or "")
        except Exception:
            pass
MAX_SECTIONS = int(os.getenv("JOZVE_MAX_SECTIONS", "10"))      # سقف تعداد فصل‌ها
REQUEST_GAP = float(os.getenv("JOZVE_GAP", "3.0"))             # فاصله بین درخواست‌ها (سقف ۲۰/دقیقه)
SHORT_WORDS = int(os.getenv("JOZVE_SHORT_WORDS", "600"))       # زیر این تعداد کلمه، تک‌فصلی

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def has_provider() -> bool:
    """آیا کلید هوش مصنوعی تنظیم شده است؟"""
    return bool(OPENROUTER_API_KEY)


def fa_num(n) -> str:
    return str(n).translate(_FA_DIGITS)


# =========================================================
#  هستهٔ ارتباط با OpenRouter (با fallback بین مدل‌ها)
# =========================================================
def _chat(messages, max_tokens=6000, temperature=0.2, timeout=150) -> dict:
    """یک درخواست چت به OpenRouter؛ در خطا/۴۲۹ بین مدل‌های رایگان جابه‌جا می‌شود."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER,
        "X-Title": "Telegram STT Jozve Bot",
    }
    errors = []
    for _round in range(2):  # دو دور کامل روی لیست مدل‌ها
        for model in MODELS:
            try:
                t0 = time.time()
                log.info(f"🤖 درخواست به {model} ...")
                r = requests.post(
                    OPENROUTER_URL, headers=headers, timeout=timeout,
                    json={"model": model, "messages": messages,
                          "max_tokens": max_tokens, "temperature": temperature,
                          "frequency_penalty": 0.3, "presence_penalty": 0.2},
                )
                if r.status_code == 200:
                    d = r.json()
                    ch = (d.get("choices") or [{}])[0]
                    msg = ch.get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, list):  # بعضی پروایدرها آرایهٔ segment می‌دهند
                        content = "".join(seg.get("text", "") for seg in content
                                          if isinstance(seg, dict))
                    if content and content.strip():
                        log.info(f"✅ {model} پاسخ داد در {time.time()-t0:.0f}s "
                                 f"(finish={ch.get('finish_reason')})")
                        return {"ok": True, "text": content.strip(), "model": model,
                                "finish": ch.get("finish_reason") or ""}
                    errors.append(f"{model}: پاسخ خالی")
                    log.warning(f"⚠️ {model}: پاسخ خالی")
                else:
                    try:
                        em = ((r.json().get("error") or {}).get("message") or "")[:110]
                    except Exception:
                        em = r.text[:110]
                    errors.append(f"{model}: HTTP {r.status_code} {em}")
                    if r.status_code == 401:  # کلید غلط → ادامه بی‌فایده است
                        return {"ok": False, "error": "کلید OpenRouter نامعتبر است (HTTP 401)"}
            except requests.exceptions.Timeout:
                errors.append(f"{model}: timeout")
                log.warning(f"⚠️ {model}: timeout بعد از {time.time()-t0:.0f}s")
            except Exception as e:
                errors.append(f"{model}: {str(e)[:110]}")
                log.warning(f"⚠️ {model}: {str(e)[:110]}")
            time.sleep(1.5)
        time.sleep(2.0)
    return {"ok": False, "error": " | ".join(errors[:6])}


# =========================================================
#  پرامپت‌ها — قوانین سخت‌گیرانه ضد اهمال‌کاری
# =========================================================
MARKUP_SPEC = r"""
【راهنمای زبان مارکاپ جزوه — فقط با این دستورات خروجی بده】
## تیتر فصل            (هر بخش با یک «##» شروع می‌شود)
### تیتر فرعی          (زیرمبحث‌ها)
متن پاراگراف ساده
- آیتم فهرست نقطه‌ای
1. آیتم فهرست مرتب
> نقل‌قول مستقیم گوینده    (جمله‌های کلیدی و تاثیرگذار، داخل این کادر)
==متن هایلایت‌شده==       (تعریف‌ها و جمله‌های طلایی)
**بولد**   *ایتالیک*   `اصطلاح فنی`

کادر رنگی نکته:            (نوع‌ها: note=نکته، tip=توصیه کاربردی، warning=هشدار،
                             important=نکته کلیدی، example=مثال، summary=جمع‌بندی)
:::note عنوان کادر
متن کادر
:::

جدول (فرمت استاندارد):
| ستون ۱ | ستون ۲ |
| --- | --- |
| مقدار | مقدار |

نقشه ذهنی / نمودار (Mermaid) — برای مباحث سلسله‌مراتبی یا چندشاخه:
```mermaid
mindmap
  root((مبحث اصلی))
    شاخه ۱
      زیرشاخه
    شاخه ۲
```

نمودار میله‌ای — برای اعداد قابل مقایسه:
```chart
عنوان: عنوان نمودار
آیتم: برچسب | 42
آیتم: برچسب | 17
```

⚠️ هیچ ایموجی در خروجی استفاده نکن. قبل از هر دستور و بعد از آن یک خط خالی بگذار.
"""

SYSTEM_EDITOR = (
    "تو «ویراستار ارشد جزوه‌های درسی» هستی؛ متن خام سخنرانی/کلاس را به جزوهٔ رسمی، "
    "زیبا و کامل تبدیل می‌کنی.\n\n"
    "قانون ۱ — پوشش صددرصدی (مهم‌ترین قانون): ترنسکریپت «خلاصه» نمی‌شود؛ فقط ویرایش و "
    "ساختاردهی می‌شود. هر جمله، تعریف، مثال، عدد، نام، مقایسه، تکرار تأکیدی، حاشیه‌گویی "
    "و حتی نکتهٔ به‌ظاهر فرعیِ داخل بازهٔ تو باید در جزوه بیاید. حجم جزوهٔ هر بازه باید "
    "تقریباً هم‌حجم یا بیشتر از همان بازهٔ خام باشد.\n\n"
    "قانون ۲ — ممنوعیت اهمال‌کاری: عبارت‌های «و غیره»، «الخ»، «به همین ترتیب»، «...»، "
    "«مطابق قبل» و هر شکل دیگرِ جای‌خالی‌گذاری ممنوع است. اگر چیزی در متن هست و در جزوه "
    "نیست، کار تو ناقص و مردود است.\n\n"
    "قانون ۳ — منع اختراع: هیچ اطلاعات، مثال یا عددی که در متن نیست نساز. تیترزنی و "
    "سازماندهی آزاد است؛ افزودن محتوای جدید ممنوع.\n\n"
    "قانون ۴ — ویرایش مجاز و لازم: حذف کلمات پرکننده (خب، امم، یعنیِ تکراری)، روان‌سازی "
    "جمله، رسمی‌سازی محاوره («می‌شه» → «می‌شود»)، یکدست‌سازی اصطلاحات فنی، شکستن "
    "جملات خیلی طولانی.\n\n"
    "قانون ۵ — آیتم‌های تصویری: هرجا در بازهٔ تو مناسب بود (محتوایی، نه تزئینی) از این‌ها "
    "استفاده کن: کادر ::: برای تأکیدها، جدول برای مقایسه‌ها، ```chart برای اعداد، "
    "```mermaid (mindmap یا flowchart) برای مباحث درختی/چندشاخه، > برای جمله‌های کلیدی "
    "گوینده، ==هایلایت== برای تعریف‌ها.\n\n"
    "قانون ۶ — فقط مارکاپ: خروجی تو فقط خطوط مارکاپ است؛ بدون مقدمه و توضیح اضافه، بدون "
    "بلوک ``` که کل خروجی را بپوشاند، بدون ایموجی.\n\n"
    "قانون ۷ — زبان: همهٔ خروجی باید فارسی باشد. اگر متن مخاطب فارسی است، عناوین و محتوا "
    "هرگز به چینی، انگلیسی یا زبان دیگری نوشته نشوند (اصطلاحات تخصصی لاتین اشکال ندارد).\n\n" + MARKUP_SPEC
)

PLANNER_SYSTEM = (
    "تو طراح ساختار جزوهٔ درسی هستی. به ترنسکریپت خام نگاه کن و فصل‌بندی منطقی پیشنهاد بده.\n"
    "خروجی فقط و فقط JSON معتبر است (بدون هیچ متن اضافه و بدون بلوک کد)، به شکل:\n"
    '{"title": "عنوان کلی جزوه", "sections": [{"title": "عنوان فصل", '
    '"brief": "یک جمله: این فصل چه بخشی از بحث را پوشش می‌دهد"}]}\n'
    "قواعد: دقیقاً {k} فصل. فصل‌ها به ترتیب زمانی متن باشند و روی هم رفته کل متن از اول تا "
    "آخر را پوشش بدهند. عناوین فارسی روان و توصیفی باشند (نه «بخش اول» و «بخش دوم»).\n"
    "⚠️ همهٔ خروجی باید فارسی باشد — هیچ کلمهٔ چینی، انگلیسی یا روسی در عناوین نیاید."
)


# =========================================================
#  برنامه‌ریز: عنوان + فصل‌بندی
# =========================================================
def _extract_plan_json(txt: str):
    """استخراج مقاوم JSON از پاسخ مدل — با تعمیر کامای فارسی، کامای انتهایی و برش‌ها"""
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m:
        return None
    raw = m.group(0)
    attempts = [
        raw,
        re.sub(r"،\s*(?=[\"{\[\d])", ", ", raw),          # کامای فارسی بین فیلدها
        re.sub(r",\s*([}\]])", r"\1", raw),               # کامای انتهایی اضافه
        re.sub(r"،\s*(?=[\"{\[\d])", ", ", re.sub(r",\s*([}\]])", r"\1", raw)),
    ]
    for a in attempts:
        try:
            return json.loads(a)
        except Exception:
            continue
    # آخرین راه: برداشت تک‌تک اشیای فصل‌ها حتی اگر کل JSON ناقص باشد
    objs = re.findall(r'\{\s*"title"\s*:\s*"([^"]+)"\s*,?\s*(?:"brief"\s*:\s*"([^"]*)")?\s*\}', raw)
    if objs:
        return {"title": None,
                "sections": [{"title": t, "brief": b or ""} for t, b in objs]}
    return None


def _is_bad_lang(s: str) -> bool:
    """تشخیص عنوان غیرفارسی (چینی/روسی/...) برای فیلتر پاسخ‌های مدل"""
    return bool(re.search(r"[\u4e00-\u9fff\u0400-\u04ff\u3040-\u30ff]", s or ""))


def _plan_sections(text: str, display_name: str) -> dict:
    words = len(text.split())
    if words <= SHORT_WORDS:
        return {"title": display_name, "sections": [{"title": "کل مبحث", "brief": "پوشش کامل متن"}]}
    k = max(1, min(MAX_SECTIONS, math.ceil(words / SECTION_WORDS)))
    if k == 1:
        return {"title": display_name, "sections": [{"title": "کل مبحث", "brief": "پوشش کامل متن"}]}

    r = _chat(
        [{"role": "system", "content": PLANNER_SYSTEM.replace("{k}", str(k))},
         {"role": "user", "content":
          f"ترنسکریپت خام:\n\n{text[:60000]}\n\n"
          "⛔ تحویل: تحلیل را حداکثر ۲ خط کن، بعد فقط JSON را بین دو خط "
          "«===شروع===» و «===پایان===» بنویس."}],
        max_tokens=4000, temperature=0.1,
    )
    if r.get("ok"):
        via_m = _extract_between_markers(r["text"])
        clean = via_m if via_m else _strip_thinking(r["text"])
        plan = _extract_last_json(clean) or _extract_plan_json(clean)
        if plan:
            try:
                secs = plan.get("sections") or []
                clean_secs = []
                for s in secs:
                    if not (isinstance(s, dict) and s.get("title")):
                        continue
                    st = str(s["title"])[:80]
                    if _is_bad_lang(st):           # عنوان چینی/روسی → فیلتر
                        continue
                    clean_secs.append({"title": st,
                                       "brief": str(s.get("brief", ""))[:300]})
                if len(clean_secs) >= 2:
                    i0 = len(clean_secs)
                    while i0 < k:
                        clean_secs.append({"title": f"فصل {fa_num(i0+1)}", "brief": ""})
                        i0 += 1
                    title = plan.get("title") or display_name
                    if _is_bad_lang(str(title)):
                        title = display_name
                    return {"title": str(title)[:120], "sections": clean_secs[:k]}
            except Exception as e:
                log.warning(f"⚠️ تحلیل JSON برنامه‌ریز ناموفق: {e}")
    log.warning("⚠️ برنامه‌ریز پاسخ معتبر نداد — فصل‌بندی پیش‌فرض استفاده می‌شود")
    titles = [f"فصل {fa_num(i+1)}" for i in range(k)]
    return {"title": display_name,
            "sections": [{"title": t, "brief": ""} for t in titles]}


# =========================================================
#  برش قطعی متن به بازه‌های فصل‌ها (مرز جمله‌ای — ضد جاافتادن)
# =========================================================
def _snap_boundary(words, b: int, window: int = 120) -> int:
    """مرز را به نزدیک‌ترین پایان جمله (رو به جلو) قفل می‌کند."""
    for j in range(b, min(b + window, len(words))):
        w = words[j]
        if w and w[-1] in ".!؟?…:":
            return j + 1
    return b


def _split_ranges(text: str, k: int):
    """متن را به k بازهٔ پیوسته می‌برد؛ مرزها روی پایان جمله snap می‌شوند."""
    words = text.split()
    n = len(words)
    if k <= 1:
        return [(0, n)]
    per = n / k
    bounds = [0]
    for i in range(1, k):
        b = _snap_boundary(words, int(i * per))
        if b <= bounds[-1] + 40 or b >= n - 20:
            b = min(int(i * per), n - 1)
        bounds.append(b)
    bounds.append(n)
    ranges = [(bounds[i], bounds[i + 1]) for i in range(k)]
    # حذف بازه‌های تهی احتمالی
    out = [(a, b) for a, b in ranges if b > a]
    return out if out else [(0, n)]


# =========================================================
#  نوشتن یک فصل (با مکانیزم «ادامه بده» در صورت قطع شدن)
# =========================================================
# =========================================================
#  پاکسازی خروجی مدل (حذف reasoning تزریق‌شده به content)
# =========================================================
def _has_persian(s: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", s or ""))


def _strip_thinking(text: str) -> str:
    """بعضی مدل‌های رایگان زنجیره فکرشان را اول content می‌ریزند — تا اولین
    تیترِ فارسی واقعی (#/##/###) را حذف می‌کنیم."""
    if not text:
        return text
    lines = text.split("\n")
    head = "\n".join(lines[:4]).lower()
    triggers = ("here's a thinking", "here is a thinking", "thinking process",
                "let me", "i need to", "user provides", "analyze the",
                "first,", "1. **analyze")
    suspect = any(t in head for t in triggers) or (
        len(text) > 400 and not _has_persian(text[:300]) and _has_persian(text[300:]))
    if not suspect:
        return text
    for idx, ln in enumerate(lines):
        if re.match(r"^\s*#{1,4}\s+\S", ln) and _has_persian(ln):
            return "\n".join(lines[idx:]).strip()
    return text


def _extract_last_json(txt: str):
    """آخرین شیء JSON کامل حاوی sections را از متن پیدا می‌کند (reasoning ممکن است
    JSON قلابی قبل از جواب اصلی داشته باشد)"""
    start = txt.rfind("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for j in range(start, len(txt)):
            c = txt[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        cand = txt[start:j + 1]
                        try:
                            d = json.loads(cand)
                            if isinstance(d, dict) and d.get("sections"):
                                return d
                        except Exception:
                            pass
                        break
        start = txt.rfind("{", 0, start)
    return None


MARKUP_END = "===پایان==="


def _extract_between_markers(t: str):
    """بلندترین جفت ===شروع===…===پایان=== را برمی‌دارد (reasoning جفت‌های قلابی
    با placeholder می‌نویسد؛ بلوک واقعی همیشه بلندترین است)"""
    best = None
    s = t.find("===شروع===")
    while s != -1:
        e = t.find(MARKUP_END, s + len("===شروع==="))
        if e == -1:
            break
        seg = t[s + len("===شروع==="):e].strip()
        if best is None or len(seg) > len(best):
            best = seg
        s = t.find("===شروع===", e + len(MARKUP_END))
    return best or None


def _clean_fragment(t: str) -> str:
    """استخراج جواب نهایی: سنتینل → حذف reasoning → حذف بلوک ``` و مقدمه‌ها"""
    t = t.strip()
    via_marker = _extract_between_markers(t)
    if via_marker:
        t = via_marker
    t = _strip_thinking(t)
    m = re.match(r"^```[a-zA-Z]*\s*\n([\s\S]*?)\n?```$", t)
    if m:
        t = m.group(1).strip()
    # اگر مدل قبل از اولین تیتر، توضیح اضافه نوشته، آن توضیح را حذف کن
    lines = t.split("\n")
    for idx, ln in enumerate(lines):
        if ln.strip().startswith("#"):
            if idx and idx <= 4:  # فقط مقدمهٔ کوتاه را ببر
                lines = lines[idx:]
            break
    return "\n".join(lines).strip()


def _write_section(words, i, k, sec: dict, a: int, b: int, used: set) -> str:
    range_text = " ".join(words[a:b])
    prev_note = (" ".join(words[max(0, a - 12):a]) or "—") if a > 0 else "آغاز متن"
    next_note = (" ".join(words[b:b + 12]) or "—") if b < len(words) else "پایان متن"
    user_msg = (
        f"شما فصل {fa_num(i)} از {fa_num(k)} جزوه را می‌نویسید با عنوان «{sec['title']}».\n"
        f"{('توضیح فصل: ' + sec['brief']) if sec.get('brief') else ''}\n\n"
        f"متن کامل بازهٔ تو (ویراستاری فقط روی همین):\n"
        f"⟦بازه⟧\n{range_text}\n⟦پایان بازه⟧\n\n"
        f"زمینهٔ صرفاً اطلاعاتی — این‌ها را بازنویسی نکن:\n"
        f"- پایان فصل قبل: «…{prev_note}»\n"
        f"- شروع فصل بعد: «{next_note}…»\n\n"
        f"ساختار الزامی خروجی:\n"
        f"- دقیقاً با «## {sec['title']}» شروع کن و فقط همین یک تیتر سطح ## باشد؛ زیربخش‌ها با ###.\n"
        f"- الزامی: دست‌کم یکی از جدول / کادر ::: / ```chart داخل فصل بیاید (به‌تناسب محتوا). "
        f"اگر مبحث درختی/چندشاخه است یک ```mermaid هم بزن.\n"
        f"- پوشش ۱۰۰٪ بازه، کلمه‌به‌کلمه؛ هیچ نکته‌ای جا نیفتد. ولی حجم کل فصل حدوداً هم‌حجم بازه باشد "
        f"(بازنویسی متنِ کامل را چند بار تکرار نکن؛ تکرار تأکیدی گوینده فقط یک بار بیاید).\n"
        f"- ویرایش مجاز: حذف پرکننده‌ها، رسمی‌سازی، روان‌سازی. اختراع ممنوع.\n"
        f"- همهٔ خروجی فارسی؛ فقط مارکاپ؛ بدون ایموجی.\n\n"
        f"⛔ قالب تحویل: خروجی نهایی را دقیقاً بین دو خط «===شروع===» و «===پایان===» بگذار. "
        f"تحلیل را حداکثر ۲-۳ خط کن (یا ننویس) و سریع ===شروع=== را بگذار؛ بودجه توکن محدود است. "
        f"داخل دو علامت فقط مارکاپ جزوه باشد."
    )
    msgs = [{"role": "system", "content": SYSTEM_EDITOR},
            {"role": "user", "content": user_msg}]
    r = _chat(msgs, max_tokens=6000, temperature=0.2)
    if not r.get("ok"):
        raise RuntimeError(f"فصل {i}: {r.get('error')}")
    used.add(r["model"])
    raw = r.get("text") or ""
    _debug_dump(f"sec{i}_call0.txt", raw)
    frag = _clean_fragment(raw)

    # ضد انحطاط/قطع: ادامه تا وقتی سنتینل «===شروع===» نیامده یا حجم کم است (حداکثر ۳ بار)
    range_words = max(1, b - a)
    cap = int(range_words * 3.2)
    cont = 0
    raw = r.get("text") or ""
    while (cont < 3 and ("===شروع===" not in raw or len(frag.split()) < cap)):
        if "===شروع===" not in raw or len(frag.split()) < 60:
            # شروع تازه: reasoning چرک را از زمینه حذف کن تا بودجه برای محتوا بماند
            msgs = msgs[:2] + [
                {"role": "assistant", "content": "تحلیل کامل شد."},
                {"role": "user", "content":
                 "عالی. حالا بدون هیچ تحلیل و توضیحی، فقط این را بفرست: خط «===شروع===»، "
                 "سپس مارکاپ کامل فصل (با جدول/کادر/نمودار/نقشه ذهنی)، سپس خط «===پایان===». "
                 "هیچ کلمه‌ای خارج از این دو علامت ننویس."},
            ]
        else:
            msgs.append({"role": "assistant", "content": raw})
            msgs.append({"role": "user", "content":
                         "خروجی‌ات قبل از ===پایان=== قطع شده. دقیقاً از همان نقطهٔ قطع ادامه بده؛ "
                         "هیچ چیز را تکرار نکن، قبلی‌ها را بازنویسی نکن. اگر بازه تمام شده، فقط بنویس: "
                         "===پایان==="})
        r2 = _chat(msgs, max_tokens=8000, temperature=0.2)
        if not r2.get("ok"):
            log.warning(f"⚠️ ادامهٔ فصل {i} ناموفق: {r2.get('error')}")
            break
        used.add(r2["model"])
        raw = r2.get("text") or ""
        _debug_dump(f"sec{i}_call{cont+1}.txt", raw)
        add = _clean_fragment(raw)
        if not add:
            break
        add = add.replace(MARKUP_END, "").strip()
        if add and not frag.endswith(add[-60:][:60]):
            frag = add if len(add.split()) > len(frag.split()) else frag + "\n" + add
        r = r2
        cont += 1

    if len(frag.split()) < 40:
        raise RuntimeError(f"فصل {i}: مدل پاسخ قابل استفاده نداد (پس از {cont} تلاش)")

    if not frag.startswith("##"):
        frag = f"## {sec['title']}\n\n" + frag
    return frag


# =========================================================
#  رندر مارکاپ → HTML
# =========================================================
_CALLOUTS = {
    "note": ("💡", "#eff6ff", "#2563eb"),
    "tip": ("✅", "#ecfdf5", "#059669"),
    "warning": ("⚠️", "#fffbeb", "#d97706"),
    "important": ("🔥", "#fef2f2", "#dc2626"),
    "example": ("📝", "#f8fafc", "#475569"),
    "summary": ("📌", "#faf5ff", "#9333ea"),
}


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"==(.+?)==", r"<mark>\1</mark>", s)
    return s


def _mermaid_html(src: str, mode: str) -> str:
    if mode == "js":
        return (f'<div class="diagram"><pre class="mermaid">'
                f'{html.escape(src)}</pre></div>')
    # حالت PDF: تبدیل به تصویر با سرویس mermaid.ink
    try:
        state = json.dumps({"code": src, "mermaid": {"theme": "default"}})
        b64 = base64.urlsafe_b64encode(state.encode()).decode()
        url = f"https://mermaid.ink/img/{b64}?type=png&width=900"
        rr = requests.get(url, timeout=40,
                          headers={"User-Agent": "Mozilla/5.0 jozve-bot"})
        ct = rr.headers.get("Content-Type", "")
        if rr.status_code == 200 and "image" in ct and len(rr.content) > 500:
            uri = "data:image/png;base64," + base64.b64encode(rr.content).decode()
            return f'<div class="diagram"><img src="{uri}" alt="نمودار ذهنی"/></div>'
    except Exception as e:
        log.warning(f"⚠️ mermaid.ink ناموفق: {e}")
    return (f'<div class="diagram"><pre class="mfail">'
            f'{html.escape(src)}</pre></div>')


def _chart_html(src: str) -> str:
    title = ""
    items = []
    for ln in src.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r"^(?:عنوان|title)\s*[:：]\s*(.+)", ln)
        if m:
            title = m.group(1).strip()
            continue
        m = re.match(r"^(?:آیتم|بار|مورد|item|bar)\s*[:：]\s*([^|]+)\|\s*([\d.,]+)", ln)
        if m:
            try:
                items.append((m.group(1).strip(), float(m.group(2).replace(",", ""))))
            except ValueError:
                pass
    if not items:
        return f'<pre class="code">{html.escape(src)}</pre>'
    mx = max(v for _, v in items) or 1
    rows = []
    for idx, (label, val) in enumerate(items):
        pct = max(2, round(val * 100.0 / mx, 1))
        rows.append(
            f'<div class="crow"><div class="clabel">{_inline(label)}</div>'
            f'<div class="cbarwrap"><div class="cbar c{idx % 5}" style="width:{pct}%"></div>'
            f'<span class="cval">{fa_num(("%g" % val))}</span></div></div>')
    t = f'<div class="chart-title">{_inline(title)}</div>' if title else ""
    return f'<div class="chart">{t}{"".join(rows)}</div>'


def _table_html(raw_rows) -> str:
    if not raw_rows:
        return ""
    head = _split_row(raw_rows[0])
    if len(raw_rows) > 2 and re.match(r"^[\s|:\-]+$", raw_rows[1]):
        body_raw = raw_rows[2:]
    else:
        body_raw = raw_rows[1:]
    th = "".join(f"<th>{_inline(c)}</th>" for c in head)
    trs = ""
    for r in body_raw:
        cells = _split_row(r)
        if not cells:
            continue
        trs += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>"
    return f'<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def _split_row(line: str):
    s = line.strip().strip("|")
    return [c.strip() for c in s.split("|")] if s else []


def render_html(markup: str, *, title: str, source: str, mode: str = "js") -> str:
    """مارکاپ کامل جزوه → صفحهٔ HTML مستقل (mode=js تعاملی برای کاربر، img مخصوص PDF)."""
    out, toc = [], []
    h2n = 0
    emoji_ok = (mode == "js")
    lines = markup.split("\n")
    i, para = 0, []

    def flush_para():
        nonlocal para
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para = []

    while i < len(lines):
        s = lines[i].strip()
        if not s:
            flush_para(); i += 1; continue

        # ---- بلوک‌های ``` ----
        if s.startswith("```"):
            flush_para()
            lang = s[3:].strip().lower()
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            src = "\n".join(buf)
            if lang == "mermaid":
                out.append(_mermaid_html(src, mode))
            elif lang == "chart":
                out.append(_chart_html(src))
            else:
                out.append(f'<pre class="code">{html.escape(src)}</pre>')
            continue

        # ---- کادر رنگی ::: ----
        m = re.match(r"^:::\s*([a-zA-Z]+)\s*(.*)$", s)
        if m:
            flush_para()
            ctype = m.group(1).lower()
            ctitle = m.group(2).strip()
            icon, bg, border = _CALLOUTS.get(ctype, _CALLOUTS["note"])
            inner = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(":::"):
                ln = lines[i].strip()
                if ln:
                    inner.append(ln)
                i += 1
            i += 1
            body = "".join(f"<p>{_inline(x)}</p>" for x in inner)
            ttl = _inline(ctitle) if ctitle else _inline(ctype)
            ic = icon if emoji_ok else "●"
            out.append(f'<div class="co" style="background:{bg};border-color:{border}">'
                       f'<div class="cot" style="color:{border}"><span class="coi">{ic}</span> {ttl}</div>'
                       f'{body}</div>')
            continue

        # ---- تیترها ----
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush_para()
            level, txt = len(m.group(1)), m.group(2).strip()
            if level == 1:
                i += 1  # عنوان اصلی در کاور صفحه می‌نشیند
                continue
            if level == 2:
                h2n += 1
                toc.append(f'<a href="#sec-{h2n}">{_inline(txt)}</a>')
                out.append(f'<h2 id="sec-{h2n}"><span class="chnum">{fa_num(h2n)}</span>'
                           f'{_inline(txt)}</h2>')
            else:
                out.append(f"<h3>{_inline(txt)}</h3>")
            i += 1
            continue

        # ---- جدول ----
        if "|" in s and i + 1 < len(lines) and re.match(r"^[\s|:\-]+$", lines[i + 1]) \
                and "-" in lines[i + 1]:
            flush_para()
            rows = []
            while i < len(lines) and "|" in lines[i]:
                rows.append(lines[i].strip())
                i += 1
            out.append(_table_html(rows))
            continue

        # ---- لیست‌ها ----
        if re.match(r"^[-*+]\s+", s):
            flush_para()
            items = []
            while i < len(lines) and re.match(r"^[-*+]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        if re.match(r"^\d+[.)]\s+", s):
            flush_para()
            items = []
            while i < len(lines) and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+[.)]\s+", "", lines[i].strip()))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue

        # ---- نقل قول ----
        if s.startswith(">"):
            flush_para()
            qs = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                qs.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + _inline(" ".join(qs)) + "</blockquote>")
            continue

        # ---- خط جداکننده ----
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush_para()
            out.append("<hr/>")
            i += 1
            continue

        para.append(s)
        i += 1

    flush_para()
    body = "\n".join(out)
    toc_html = ""
    if toc:
        lis = "".join(f"<li>{a}</li>" for a in toc)
        toc_html = (f'<details class="toc" open><summary>فهرست مطالب</summary>'
                    f'<ol>{lis}</ol></details>')
    date_str = datetime.date.today().strftime("%Y-%m-%d")

    css = _CSS().replace("__FONTFACE__", _font_face_css())
    cover_emoji = "📚 " if emoji_ok else ""
    mermaid_js = ""
    if mode == "js":
        mermaid_js = ('<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js">'
                      '</script><script>mermaid.initialize({startOnLoad:true,theme:"neutral"});</script>')

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<header class="cover">
<div class="ct">{cover_emoji}{html.escape(title)}</div>
<div class="cm">منبع: {html.escape(source)} &nbsp;•&nbsp; {date_str} &nbsp;•&nbsp; ویراستاری و ساختاردهی با هوش مصنوعی</div>
</header>
{toc_html}
<article>
{body}
</article>
<footer class="foot">این جزوه با ویراستاری کامل متن خام (بدون حذف محتوا) و کمک هوش مصنوعی تهیه شده است.</footer>
</div>
{mermaid_js}
</body>
</html>"""


def _font_face_css() -> str:
    """فونت وزیرمتن را به‌صورت data-URI داخل CSS می‌گذارد تا فایل HTML مستقل باشد."""
    try:
        reg = open(os.path.join(_FONT_DIR, "Vazirmatn-Regular.ttf"), "rb").read()
        bold = open(os.path.join(_FONT_DIR, "Vazirmatn-Bold.ttf"), "rb").read()
        r64 = base64.b64encode(reg).decode()
        b64 = base64.b64encode(bold).decode()
        return ("@font-face{font-family:'Vazirmatn';font-weight:400;"
                f"src:url(data:font/ttf;base64,{r64}) format('truetype');}}\n"
                "@font-face{font-family:'Vazirmatn';font-weight:700;"
                f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}\n")
    except Exception:
        return ""


def _CSS() -> str:
    return """__FONTFACE__
*{box-sizing:border-box}
body{font-family:'Vazirmatn',Tahoma,Arial,sans-serif;background:#f1f5f9;margin:0;color:#1e293b;
     font-size:15.5px;line-height:2}
.wrap{max-width:880px;margin:0 auto;padding:28px 18px 60px}
.cover{background:linear-gradient(135deg,#1e3a8a 0%,#4f46e5 60%,#7c3aed 100%);color:#fff;
       border-radius:18px;padding:38px 34px;box-shadow:0 10px 30px rgba(79,70,229,.25);margin-bottom:26px}
.cover .ct{font-size:26px;font-weight:700;line-height:1.7}
.cover .cm{margin-top:14px;font-size:12.5px;opacity:.85}
.toc{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:14px 22px;margin-bottom:26px}
.toc summary{font-weight:700;color:#4338ca;cursor:pointer;font-size:15px}
.toc ol{margin:10px 0 4px;padding-right:20px}
.toc li{margin:4px 0}
.toc a{color:#4338ca;text-decoration:none}
.toc a:hover{text-decoration:underline}
article{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:34px 38px}
h2{font-size:21px;color:#0f172a;margin:2.1em 0 .9em;background:#f8fafc;border-right:6px solid #6366f1;
   border-radius:10px;padding:12px 16px;line-height:1.8}
h2 .chnum{display:inline-block;background:#6366f1;color:#fff;border-radius:8px;min-width:34px;
          text-align:center;margin-left:10px;padding:1px 8px;font-size:16px}
h3{font-size:17px;color:#4338ca;margin:1.6em 0 .6em}
p{margin:.7em 0;text-align:justify}
ul,ol{margin:.6em 1.6em .9em 0;padding:0}
li{margin:.3em 0}
mark{background:#fef08a;color:#713f12;border-radius:4px;padding:0 5px}
code{background:#eef2ff;color:#3730a3;border-radius:5px;padding:1px 6px;font-size:.92em;
     font-family:inherit}
blockquote{background:#fffbeb;border-right:5px solid #f59e0b;border-radius:10px;
           margin:1em 0;padding:12px 18px;color:#78350f}
table{border-collapse:collapse;width:100%;margin:1.1em 0;font-size:14.5px}
th{background:#4f46e5;color:#fff;padding:9px 12px;text-align:right}
th:first-child{border-radius:0 8px 0 0}
th:last-child{border-radius:8px 0 0 0}
td{border:1px solid #e2e8f0;padding:8px 12px}
tr:nth-child(even) td{background:#f8fafc}
.co{border:1px solid transparent;border-right-width:5px;border-radius:12px;padding:12px 18px;margin:1.1em 0}
.co p{margin:.35em 0}
.cot{font-weight:700;font-size:14.5px}
.chart{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:16px 20px;margin:1.2em 0}
.chart-title{font-weight:700;color:#334155;margin-bottom:10px;text-align:center}
.crow{display:flex;align-items:center;margin:7px 0}
.clabel{width:26%;font-size:13.5px;color:#475569}
.cbarwrap{flex:1;background:#e2e8f0;border-radius:6px;position:relative;height:22px;margin-right:8px}
.cbar{height:100%;border-radius:6px;min-width:8px}
.c0{background:linear-gradient(90deg,#6366f1,#8b5cf6)}
.c1{background:linear-gradient(90deg,#0ea5e9,#6366f1)}
.c2{background:linear-gradient(90deg,#10b981,#0ea5e9)}
.c3{background:linear-gradient(90deg,#f59e0b,#f97316)}
.c4{background:linear-gradient(90deg,#ef4444,#f59e0b)}
.cval{position:absolute;left:8px;top:0;line-height:22px;font-size:12.5px;color:#334155;font-weight:700}
.diagram{background:#fff;border:1px dashed #c7d2fe;border-radius:12px;padding:14px;margin:1.2em 0;
         overflow-x:auto;text-align:center}
.diagram img{max-width:100%;height:auto}
pre.mermaid,pre.mfail{background:transparent;font-family:inherit;margin:0;white-space:pre-wrap}
pre.mfail{background:#f8fafc;border-radius:8px;padding:12px;color:#475569;text-align:right;font-size:13.5px}
pre.code{background:#0f172a;color:#e2e8f0;border-radius:10px;padding:14px 18px;overflow-x:auto;
         direction:ltr;text-align:left;font-size:13.5px}
hr{border:none;border-top:1px dashed #cbd5e1;margin:2em 0}
.foot{color:#94a3b8;font-size:12px;text-align:center;margin-top:26px}
@media print{
 @page{size:A4;margin:19mm 16mm;
       @bottom-center{content:"صفحه " counter(page) " از " counter(pages);
                      font-family:'Vazirmatn',Tahoma;font-size:9pt;color:#94a3b8}}
 body{background:#fff;font-size:11.5pt}
 .wrap{max-width:100%;padding:0}
 article{border:none;padding:0}
 h2{page-break-before:always}
 h2:first-of-type{page-break-before:avoid}
 .co,.chart,.diagram,table,blockquote{page-break-inside:avoid}
 .toc,.foot{display:none}
}"""


# =========================================================
#  تولید نهایی جزوه + PDF
# =========================================================
def produce_jozve(text: str, display_name: str, out_dir: str, progress=None) -> dict:
    try:
        return _produce(text, display_name, out_dir, progress)
    except Exception as e:
        log.exception(f"❌ خطای جزوه‌سازی: {e}")
        return {"ok": False, "error": str(e)[:300]}


def _produce(text: str, display_name: str, out_dir: str, progress) -> dict:
    def _p(info: dict):
        if progress:
            try:
                progress(info)
            except Exception:
                pass

    if not has_provider():
        return {"ok": False, "error": "کلید OPENROUTER_API_KEY تنظیم نشده است"}
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    if len(words) < 30:
        return {"ok": False, "error": "متن برای جزوه‌سازی خیلی کوتاه است"}

    _p({"stage": "plan"})
    plan = _plan_sections(text, display_name)
    k = len(plan["sections"])
    ranges = _split_ranges(text, k)
    if len(ranges) < k:  # احتیاط
        k = len(ranges)
        plan["sections"] = plan["sections"][:k]

    used = set()
    parts = []
    for i in range(k):
        sec = plan["sections"][i]
        a, b = ranges[i]
        _p({"stage": "section", "section": i + 1, "total": k, "title": sec["title"]})
        parts.append(_write_section(words, i + 1, k, sec, a, b, used))
        time.sleep(REQUEST_GAP)

    markup = f"# {plan['title']}\n\n" + "\n\n".join(parts)
    words_out = len(markup.split())
    provider = "OpenRouter · " + ", ".join(
        sorted({m.split("/")[-1].replace(":free", "") for m in used}))[:70]

    _p({"stage": "render"})
    base = re.sub(r"[^\w\u0600-\u06FF\- ]", "_", display_name)[:50].strip() or "jozve"
    markup_path = os.path.join(out_dir, base + ".md")
    with open(markup_path, "w", encoding="utf-8") as f:
        f.write(markup)
    html_path = os.path.join(out_dir, base + ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(markup, title=plan["title"], source=display_name, mode="js"))

    _p({"stage": "pdf"})
    pdf_path = None
    try:
        from weasyprint import HTML as _WHTML  # noqa
        pdf_str = render_html(markup, title=plan["title"], source=display_name, mode="img")
        pdf_path = os.path.join(out_dir, base + ".pdf")
        _WHTML(string=pdf_str).write_pdf(pdf_path)
        if os.path.getsize(pdf_path) < 2000:
            pdf_path = None
    except Exception as e:
        log.warning(f"⚠️ ساخت PDF ناموفق (فقط HTML ارسال می‌شود): {e}")
        pdf_path = None

    _p({"stage": "done"})
    return {"ok": True, "title": plan["title"], "provider": provider,
            "html": html_path, "pdf": pdf_path, "sections": k,
            "words_in": len(words), "words_out": words_out}


def render_progress(info: dict) -> str:
    """پیام وضعیت فارسی برای نمایش در چت (از bot.py صدا زده می‌شود)."""
    st = info.get("stage")
    if st == "plan":
        return "🧭 مرحله ۱ از ۳: تحلیل متن و طراحی فصل‌بندی جزوه..."
    if st == "section":
        s, t = info.get("section", 0), info.get("total", 1)
        bar = "█" * s + "░" * (t - s)
        return (f"✍️ مرحله ۲ از ۳: نوشتن جزوه [{bar}]\n"
                f"📖 فصل {fa_num(s)} از {fa_num(t)}: {info.get('title', '')}")
    if st == "render":
        return "🎨 مرحله ۳ از ۳: چیدمان، جدول‌ها و نقشه‌های ذهنی..."
    if st == "pdf":
        return "📄 ساخت فایل PDF (اگر نشد، همان HTML ارسال می‌شود)..."
    return "⏳ در حال کار روی جزوه..."
