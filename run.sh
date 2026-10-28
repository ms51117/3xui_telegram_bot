#!/bin/bash
# اسکریپت اجرای ربات با محیط مجازی venv

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    echo "⚠️ محیط مجازی پیدا نشد. در حال ساخت venv..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

echo "🚀 در حال اجرای ربات مدیریت اشتراک 3X-UI..."
exec ./venv/bin/python bot.py
