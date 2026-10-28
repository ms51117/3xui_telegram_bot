FROM python:3.11-slim

# نصب وابستگی‌های سیستمی (systemd جهت خواندن لاگ‌های تانل، curl و منطقه زمانی تهران)
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# تنظیم منطقه زمانی ایران
ENV TZ=Asia/Tehran
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# کپی و نصب وابستگی‌های پایتون
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# کپی سورس برنامه
COPY config.py database.py xui_api.py cloudflare.py bot.py ./

# پوشه مخصوص ذخیره دائمی دیتابیس
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/bot_data.db

# اجرای ربات با لاگ بی‌درنگ
CMD ["python", "-u", "bot.py"]
