# 🐳 داکرفایل — برای اجرای دائمی روی Hugging Face Spaces یا هر سرور Docker
#
# ساخت و اجرا:
#   docker build -t stt-telegram-bot .
#   docker run -d --name stt-bot \
#     -e API_ID=123456 \
#     -e API_HASH=xxxx \
#     -e BOT_TOKEN=xxxx:yyyy \
#     -v stt_session:/app/session \
#     stt-telegram-bot

FROM python:3.11-slim

# ffmpeg برای تبدیل صدا + اصول بهداشت کانتینر
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# --- اختیاری: مرورگر هدلس برای اسکرپینگ/اتوماسیون ---
# اگر خواستید علاوه بر ربات، مرورگر هدلس هم اجرا کنید یکی از دو خط زیر را از کامنت خارج کنید:
# (سelenium با کرومیوم سبک‌تر است، Playwright مدرن‌تر و پایدارتر)
# RUN apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver && rm -rf /var/lib/apt/lists/*
# RUN pip install --no-cache-dir playwright && python -m playwright install --with-deps chromium

# HF Spaces کانتینر را با کاربر UID=1000 اجرا می‌کند — فایل‌ها باید مال او باشند تا بات بتواند بنویسد
RUN useradd -m -u 1000 user

WORKDIR /app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user bot.py .

# نشست ربات در این پوشه ذخیره می‌شود (با volume مount کنید تا بعد از ری‌استارت دوباره لاگین نشود)
ENV SESSION_NAME=/app/session/stt_bot_session
RUN mkdir -p /app/session && chown -R user:user /app
USER user

CMD ["python", "bot.py"]
