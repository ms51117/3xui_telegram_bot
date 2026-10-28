#!/bin/bash
set -e

echo "=== شروع به‌روزرسانی اسکریپت‌های سرور و ربات ==="

# 1. کپی اسکریپت‌های تانل به مسیر سیستمی
cp scripts/cf-notify.sh /usr/local/bin/cf-notify.sh
chmod +x /usr/local/bin/cf-notify.sh

cp scripts/panel-notify.sh /usr/local/bin/panel-notify.sh
chmod +x /usr/local/bin/panel-notify.sh

# 2. ری‌استارت سرویس‌های تانل جهت استخراج فوری دامنه‌های جدید
echo "در حال اجرای سرویس‌های تانل..."
systemctl restart cloudflared-panel.service || true
systemctl restart cloudflared-quick.service || true

# 3. بازسازی و راه‌اندازی کانتینر داکر
echo "در حال بیلد و اجرای کانتینر داکر ربات..."
docker compose down || true
docker compose up -d --build

echo "=== عملیات با موفقیت انجام شد! ==="
