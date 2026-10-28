import subprocess
import re
import os
import io
import qrcode
import logging
from config import CLOUDFLARE_SERVICE, CONFIG_PORT, CONFIG_PATH, DB_PATH

logger = logging.getLogger(__name__)

db_dir = os.path.dirname(os.path.abspath(DB_PATH))
DOMAIN_CACHE_FILE = os.path.join(db_dir, "last_known_domain.txt") if db_dir else "last_known_domain.txt"

def get_current_domain() -> str:
    """
    Extracts the latest trycloudflare domain from systemd logs on Linux.
    Falls back to a saved cache file or environment variable if not on Linux or if command fails.
    """
    # 1. Try extracting from journalctl on Linux
    if os.name != 'nt':
        try:
            cmd = f"journalctl -u {CLOUDFLARE_SERVICE} --no-pager -n 200"
            res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
            matches = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', res)
            if matches:
                domain = matches[-1].replace("https://", "").strip()
                # Cache the domain
                with open(DOMAIN_CACHE_FILE, "w", encoding="utf-8") as f:
                    f.write(domain)
                return domain
        except Exception as e:
            logger.warning(f"Could not read domain from journalctl: {e}")

    # 2. Try reading from cache file
    if os.path.exists(DOMAIN_CACHE_FILE):
        try:
            with open(DOMAIN_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = f.read().strip()
                if cached:
                    return cached
        except Exception:
            pass

    # 3. Default fallback
    return os.getenv("DEFAULT_DOMAIN", "localhost")

def save_custom_domain(domain: str):
    """Allows manual setting of the domain by admin"""
    clean = domain.replace("https://", "").replace("http://", "").strip().rstrip("/")
    with open(DOMAIN_CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(clean)
    return clean

def build_vless_config(uuid: str, host: str, remark: str = "Client") -> str:
    """
    Generates exact VLESS link according to user's specification:
    vless://$UUID@$HOST:$PORT?path=%2Fvless&security=tls&encryption=none&host=$HOST&fp=chrome&type=ws&sni=$HOST#Tunnel-$EMAIL
    """
    # Clean remark for URL fragment
    safe_remark = re.sub(r'[\s]+', '-', remark.strip())
    encoded_path = "%2F" + CONFIG_PATH.lstrip("/")
    
    config = (
        f"vless://{uuid}@{host}:{CONFIG_PORT}"
        f"?path={encoded_path}&security=tls&encryption=none&host={host}&fp=chrome&type=ws&sni={host}#Tunnel-{safe_remark}"
    )
    return config

def generate_qr_code(text: str) -> io.BytesIO:
    """Generates a QR code image buffer in PNG format ready to send in Telegram"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    bio = io.BytesIO()
    bio.name = 'qr.png'
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio
