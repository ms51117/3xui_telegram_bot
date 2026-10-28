#!/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

BOT_TOKEN="8653162681:AAFh5Zo9TlLZfRN_-bgBT5j7yzHeEpON1g8"
ADMIN_ID="87384626"
PANEL_PATH="/h4B2swmMPtUzP9SJqA/"
PANEL_URL_FILE="/etc/x-ui/panel_url.txt"

# صبر برای برقراری تانل پنل
sleep 8

BASE_URL=$(journalctl -u cloudflared-panel.service --no-pager -n 200 | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1)

if [ -n "$BASE_URL" ]; then
    PANEL_URL="${BASE_URL}${PANEL_PATH}"
    mkdir -p /etc/x-ui
    echo "$PANEL_URL" > "$PANEL_URL_FILE"
    echo "Saved Panel URL: $PANEL_URL to $PANEL_URL_FILE"

    # ارسال پیام اطلاع‌رسانی مستقیم به ادمین در تلگرام با توکن جدید
    curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
        -d chat_id="$ADMIN_ID" \
        --data-urlencode text="🔧 **آدرس جدید ورود به پنل 3X-UI:**

🌐 $PANEL_URL

👤 نام‌کاربری: lf73WSAPtH
🔑 رمز‌عبور: 1y7F4Xwj2AB7QUmk12" > /dev/null
fi

exit 0
