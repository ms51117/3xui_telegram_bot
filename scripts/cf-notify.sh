#!/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

VLESS_DOMAIN_FILE="/etc/x-ui/vless_domain.txt"

# صبر برای برقراری تانل کلودفلر
sleep 8

# استخراج آخرین دامنه تانل VLESS
DOMAIN=$(journalctl -u cloudflared-quick.service --no-pager -n 200 | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1)
HOST=$(echo "$DOMAIN" | sed 's|https://||')

if [ -n "$HOST" ]; then
    mkdir -p /etc/x-ui
    echo "$HOST" > "$VLESS_DOMAIN_FILE"
    echo "Saved VLESS domain: $HOST to $VLESS_DOMAIN_FILE"
fi

exit 0
