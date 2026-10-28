#!/bin/bash
set -e

# اسکریپت نصب و راه‌اندازی اولیه ربات روی سرور لینوکس

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "🔧 در حال آماده‌سازی محیط اجرای ربات 3X-UI"
echo "=========================================="

# 1. بررسی نصب بودن پایتون 3 و venv
if ! command -v python3 &> /dev/null; then
    echo "❌ پایتون ۳ نصب نیست. در حال نصب پایتون..."
    sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
fi

# 2. ساخت محیط مجازی
if [ ! -d "venv" ]; then
    echo "📦 ساخت محیط مجازی ایزوله (venv)..."
    python3 -m venv venv
fi

# 3. ارتقای pip و نصب وابستگی‌ها
echo "📥 در حال نصب پکیج‌های پایتون درون venv..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 4. ایجاد فایل .env در صورت نبود
if [ ! -f ".env" ]; then
    echo "📄 ایجاد فایل تنظیمات .env از روی نمونه..."
    cp .env.example .env
fi

chmod +x run.sh

echo ""
echo "=========================================="
echo "✅ راه‌اندازی با موفقیت انجام شد!"
echo "جهت اجرای ربات دستور زیر را بزنید:"
echo "  ./run.sh"
echo "=========================================="
