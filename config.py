import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Settings
BOT_TOKEN = os.getenv("BOT_TOKEN", "8653162681:AAGJPWnnLiPnZMkI0N_cOAB7cG1a6IULaY0")
ADMIN_ID = int(os.getenv("ADMIN_ID", "87384626"))

# 3X-UI Panel Settings
# If bot is running on the same Linux server, http://127.0.0.1:2053 is recommended.
# If connecting from remote or tunnel, set PANEL_URL to the external domain.
PANEL_URL = os.getenv("PANEL_URL", "http://127.0.0.1:2053").rstrip("/")
PANEL_BASE_PATH = os.getenv("PANEL_BASE_PATH", "/h4B2swmMPtUzP9SJqA/").strip("/")
if PANEL_BASE_PATH and not PANEL_BASE_PATH.startswith("/"):
    PANEL_BASE_PATH = "/" + PANEL_BASE_PATH
if PANEL_BASE_PATH and not PANEL_BASE_PATH.endswith("/"):
    PANEL_BASE_PATH = PANEL_BASE_PATH + "/"

PANEL_USERNAME = os.getenv("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "admin")

# Target Inbound for VPN configs
INBOUND_ID = int(os.getenv("INBOUND_ID", "2"))
CONFIG_PORT = os.getenv("CONFIG_PORT", "443")
CONFIG_PATH = os.getenv("CONFIG_PATH", "/vless")

# Cloudflare Tunnel service name on Linux
CLOUDFLARE_SERVICE = os.getenv("CLOUDFLARE_SERVICE", "cloudflared-quick.service")

# Database & Storage
DB_PATH = os.getenv("DB_PATH", "bot_data.db")
XUI_DB_PATH = os.getenv("XUI_DB_PATH", "/etc/x-ui/x-ui.db")
