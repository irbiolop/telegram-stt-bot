#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🧩 gh_agent.py — پل ربات به GitHub
====================================
دو وظیفه:
  ۱) اجرای «ایجنت جزوه» روی GitHub Actions:
     ربات برای هر درخواست یک شاخهٔ موقت می‌سازد، متن رونویسی را روی آن می‌گذارد،
     رویداد jozve-job را dispatch می‌کند؛ ورک‌فلو OpenCode را بالا می‌آورد،
     جزوه را می‌سازد و output.md را روی همان شاخه کامیت می‌کند؛ ربات نتیجه را
     می‌خواند و شاخه را پاک می‌کند. (بالا آوردن + ساخت + پایین آوردن = هر بار یک ماشین)
  ۲) ذخیرهٔ پایدار فهرست کاربران بن‌شده در یک secret gist
     (دیسک Render بعد از هر دیپلوی خالی می‌شود؛ gist می‌ماند.)

متغیرهای محیطی:
  GH_TOKEN           ← PAT با اسکوپ repo (+gist برای بن)
  GH_REPO            ← «owner/name» (پیش‌فرض: irbiolop/telegram-stt-bot)
  GH_AGENT_EVENT     ← نام رویداد dispatch (پیش‌فرض: jozve-job)
  GH_API_BASE        ← برای تست با سرور محلی (پیش‌فرض: api.github.com)
"""

import os
import json
import base64
import time

import requests

GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GH_REPO = os.getenv("GH_REPO", "irbiolop/telegram-stt-bot")
GH_AGENT_EVENT = os.getenv("GH_AGENT_EVENT", "jozve-job")
API = os.getenv("GH_API_BASE", "https://api.github.com").rstrip("/")

JOB_DIR = "agent-jobs"          # پوشهٔ فایل‌های هر job روی شاخهٔ موقت
INPUT_NAME = "input.txt"
OUTPUT_NAME = "output.md"
ERROR_NAME = "error.txt"


class GhError(Exception):
    """خطای ارتباط با GitHub — پیام برای نمایش به کاربر در بر دارد."""


def available() -> bool:
    """کلید GitHub تنظیم شده است؟"""
    return bool(GH_TOKEN)


def _headers():
    return {"Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "telegram-stt-bot"}


def _req(method: str, path: str, *, ok=(200, 201, 204), timeout=60,
         tries=3, allow_404=False, **kw):
    """درخواست HTTP به API گیت‌هاب با تلاش مجدد؛ 404 در صورت نیاز قابل تحمل است."""
    last = "خطای ناشناخته"
    for i in range(max(1, tries)):
        try:
            r = requests.request(method, API + path, headers=_headers(),
                                 timeout=timeout, **kw)
            if r.status_code in ok:
                return r
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code in (502, 503, 504) and i < tries - 1:
                last = f"HTTP {r.status_code}"
                time.sleep(2 * (i + 1))
                continue
            raise GhError(f"GitHub {method} {path}: HTTP {r.status_code} — {r.text[:180]}")
        except requests.RequestException as e:
            last = str(e)[:180]
            if i >= tries - 1:
                break
            time.sleep(2 * (i + 1))
    raise GhError(f"GitHub {method} {path}: {last}")


# =========================================================
#  شاخه و فایل
# =========================================================
_default_branch_cache = {"name": ""}


def default_branch() -> str:
    if _default_branch_cache["name"]:
        return _default_branch_cache["name"]
    r = _req("GET", f"/repos/{GH_REPO}", allow_404=True)
    name = (r.json() or {}).get("default_branch") or "main" if r is not None else "main"
    _default_branch_cache["name"] = name
    return name


def create_branch(name: str) -> str:
    """شاخهٔ موقت از سرِ شاخهٔ پیش‌فرض — اگر از قبل باشد همان استفاده می‌شود."""
    base = _req("GET", f"/repos/{GH_REPO}/git/ref/heads/{default_branch()}")
    sha = (base.json().get("object") or {}).get("sha")
    if not sha:
        raise GhError("شاخهٔ اصلی مخزن پیدا نشد")
    try:
        r = _req("POST", f"/repos/{GH_REPO}/git/refs", allow_404=True,
                 json={"ref": f"refs/heads/{name}", "sha": sha})
    except GhError as e:
        if "422" not in str(e) and "409" not in str(e):
            raise
        r = None   # شاخه از قبل وجود دارد (تلاش مجدد) — با GET تأیید می‌شود
    if r is None:
        chk = _req("GET", f"/repos/{GH_REPO}/git/ref/heads/{name}", allow_404=True)
        if chk is None:
            raise GhError("ساخت شاخهٔ موقت روی گیت‌هاب ممکن نشد")
    return sha


def delete_branch(name: str):
    """پاک کردن شاخهٔ موقت — نبودنش خطا نیست."""
    try:
        _req("DELETE", f"/repos/{GH_REPO}/git/refs/heads/{name}", allow_404=True)
    except GhError:
        pass


def put_file(branch: str, path: str, text: str, message: str = "update") -> str:
    """ساخت/به‌روزرسانی یک فایل متنی روی شاخه؛ sha کامیت جدید برمی‌گردد."""
    r = _req("GET", f"/repos/{GH_REPO}/contents/{path}", allow_404=True,
             params={"ref": branch})
    sha = None
    if r is not None:
        sha = (r.json() or {}).get("sha")
    body = {"message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode(),
            "branch": branch}
    if sha:
        body["sha"] = sha
    r2 = _req("PUT", f"/repos/{GH_REPO}/contents/{path}", json=body, timeout=120)
    return ((r2.json() or {}).get("commit") or {}).get("sha", "")


def get_file(branch: str, path: str):
    """محتوای متنی فایل از شاخه؛ نبودن ← None (404 عادی است).
    پارامتر t برای شکستن کشِ مرزی گیت‌هاب — بعد از push، کش ممکن است
    چند ثانیه 404 قدیمی بدهد؛ ربات هر ۲۰ ثانیه دوباره چک می‌کند."""
    r = _req("GET", f"/repos/{GH_REPO}/contents/{path}", allow_404=True,
             params={"ref": branch, "t": str(int(time.time() * 1000))})
    if r is None:
        return None
    d = r.json() or {}
    enc = d.get("encoding")
    if enc == "base64":
        return base64.b64decode(d.get("content") or "").decode("utf-8", "replace")
    # فایل بزرگ → encoding ندارند و download_url می‌دهند
    url = d.get("download_url")
    if url:
        rr = requests.get(url, timeout=120)
        if rr.status_code == 200:
            return rr.text
    return None


# =========================================================
#  ایجنت جزوه (GitHub Actions)
# =========================================================
def dispatch(event_type: str, payload: dict):
    _req("POST", f"/repos/{GH_REPO}/dispatches",
         json={"event_type": event_type, "client_payload": payload}, ok=(200, 201, 204))


def start_agent_job(text: str, model: str, job_id: str, branch: str) -> str:
    """شاخهٔ موقت + متن رونویسی + dispatch — همهٔ گام‌های آغاز یک job."""
    create_branch(branch)
    put_file(branch, f"{JOB_DIR}/{job_id}/{INPUT_NAME}", text,
             message=f"jozve job {job_id}: input")
    dispatch(GH_AGENT_EVENT, {"job_id": job_id, "model": model, "branch": branch})
    return branch


def get_agent_output(job_id: str, branch: str):
    """output.md یا error.txt ایجنت — هیچ‌کدام نبود ← None"""
    out = get_file(branch, f"{JOB_DIR}/{job_id}/{OUTPUT_NAME}")
    if out:
        return {"ok": True, "text": out}
    err = get_file(branch, f"{JOB_DIR}/{job_id}/{ERROR_NAME}")
    if err:
        return {"ok": False, "error": err[:800]}
    return None


def cleanup_agent_job(job_id: str, branch: str):
    delete_branch(branch)


def poll_agent_output(job_id: str, branch: str, timeout_s=18 * 60, tick=20,
                      on_tick=None):
    """تا آماده شدن خروجی ایجنت صبر می‌کند؛ نتیجه/None (تایم‌اوت) برمی‌گرداند.
    on_tick(elapsed) برای پیام‌های میانهٔ ربات."""
    t0 = time.time()
    fail_streak = 0
    while time.time() - t0 < timeout_s:
        if on_tick:
            try:
                on_tick(time.time() - t0)
            except Exception:
                pass
        try:
            res = get_agent_output(job_id, branch)
            fail_streak = 0
            if res is not None:
                return res
        except GhError as e:
            fail_streak += 1
            if fail_streak >= 6:
                raise
        time.sleep(tick)
    return None


# =========================================================
#  gist — ذخیرهٔ پایدار بن‌شده‌ها
# =========================================================
BAN_FILENAME = "banned.json"


def gist_read(gid: str):
    """محتوای فایل banned.json داخل gist؛ نبودن/خطا ← None"""
    try:
        r = _req("GET", f"/gists/{gid}", allow_404=True)
    except GhError:
        return None
    if r is None:
        return None
    files = (r.json() or {}).get("files") or {}
    f = files.get(BAN_FILENAME) or {}
    if f.get("truncated"):
        url = f.get("raw_url")
        if not url:
            return None
        rr = requests.get(url, timeout=60)
        return rr.text if rr.status_code == 200 else None
    return f.get("content")


def gist_write(gid: str, content: str) -> bool:
    """به‌روزرسانی gist موجود — False یعنی gist دیگر در دسترس نیست."""
    try:
        _req("PATCH", f"/gists/{gid}",
             json={"files": {BAN_FILENAME: {"content": content}}})
        return True
    except GhError:
        return False


def gist_create(content: str):
    """ساخت secret gist جدید ← شناسه یا None"""
    try:
        r = _req("POST", "/gists",
                 json={"files": {BAN_FILENAME: {"content": content}},
                       "description": "telegram-stt-bot — banned users",
                       "public": False})
        return (r.json() or {}).get("id")
    except GhError:
        return None


def gist_delete(gid: str):
    try:
        _req("DELETE", f"/gists/{gid}", allow_404=True)
    except GhError:
        pass
