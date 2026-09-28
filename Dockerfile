# 🐳 داکرفایل — برای اجرای دائمی روی Render / Hugging Face Spaces / هر سرور Docker
#
# ساخت و اجرا:
#   docker build -t stt-telegram-bot .
#   docker run -d --name stt-bot \
#     -e API_ID=123456 \
#     -e API_HASH=xxxx \
#     -e BOT_TOKEN=xxxx:yyyy \
#     -e GEMINI_API_KEY=xxxx \
#     -v stt_session:/app/session \
#     stt-telegram-bot

FROM python:3.11-slim

# ffmpeg برای تبدیل صدا + کتابخانه‌های Pango/HarfBuzz برای WeasyPrint (ساخت PDF)
# + فونت فارسی وزیرمتن برای رندر درست جزوه در PDF + DejaVu برای نمادها
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ffmpeg curl ca-certificates \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
        fonts-dejavu-core \
    && mkdir -p /usr/share/fonts/truetype/vazirmatn \
    && (curl -fsSL "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Regular.ttf" -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf \
        || curl -fsSL "https://github.com/rastikerdar/vazirmatn/raw/v33.003/fonts/ttf/Vazirmatn-Regular.ttf" -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf) \
    && (curl -fsSL "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Bold.ttf" -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf \
        || curl -fsSL "https://github.com/rastikerdar/vazirmatn/raw/v33.003/fonts/ttf/Vazirmatn-Bold.ttf" -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf) \
    && (curl -fsSL "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Medium.ttf" -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Medium.ttf \
        || curl -fsSL "https://github.com/rastikerdar/vazirmatn/raw/v33.003/fonts/ttf/Vazirmatn-Medium.ttf" -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Medium.ttf) \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# --- اختیاری: مرورگر هدلس برای اسکرپینگ/اتوماسیون ---
# اگر خواستید علاوه بر ربات، مرورگر هدلس هم اجرا کنید یکی از دو خط زیر را از کامنت خارج کنید:
# RUN apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver && rm -rf /var/lib/apt/lists/*
# RUN pip install --no-cache-dir playwright && python -m playwright install --with-deps chromium

# HF Spaces کانتینر را با کاربر UID=1000 اجرا می‌کند — فایل‌ها باید مال او باشند تا بات بتواند بنویسد
RUN useradd -m -u 1000 user

WORKDIR /app

COPY --chown=user:user requirements.txt .
RUN pip install -q --no-cache-dir -r requirements.txt

COPY --chown=user:user bot.py jozve.py ./

# نشست ربات در این پوشه ذخیره می‌شود (با volume مount کنید تا بعد از ری‌استارت دوباره لاگین نشود)
ENV SESSION_NAME=/app/session/stt_bot_session
RUN mkdir -p /app/session && chown -R user:user /app
USER user

CMD ["python", "bot.py"]
