import subprocess
import re
import os
import io
import qrcode
import logging
from config import CLOUDFLARE_SERVICE, CONFIG_PORT, CONFIG_PATH, DB_PATH, PANEL_BASE_PATH

logger = logging.getLogger(__name__)

db_dir = os.path.dirname(os.path.abspath(DB_PATH))
DOMAIN_CACHE_FILE = os.path.join(db_dir, "last_known_domain.txt") if db_dir else "last_known_domain.txt"
PANEL_CACHE_FILE = os.path.join(db_dir, "last_known_panel_url.txt") if db_dir else "last_known_panel_url.txt"

# Direct shared file paths from host
VLESS_DOMAIN_FILE = "/etc/x-ui/vless_domain.txt"
PANEL_URL_FILE = "/etc/x-ui/panel_url.txt"

def _search_domain_in_log_files(hint: str = None) -> str:
    """Scans system log files (/var/log/syslog, daemon.log, etc.) for trycloudflare domains."""
    log_candidates = [
        "/var/log/syslog",
        "/var/log/daemon.log",
        "/var/log/messages"
    ]
    for p in log_candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(0, os.SEEK_END)
                    pos = max(0, f.tell() - 300000)
                    f.seek(pos)
                    lines = f.readlines()
                    for line in reversed(lines):
                        if hint and hint not in line:
                            continue
                        matches = re.findall(r'https://([a-z0-9-]+\.trycloudflare\.com)', line)
                        if matches:
                            return matches[-1]
            except Exception:
                continue
    return ""

def get_current_domain() -> str:
    """
    Extracts the latest trycloudflare domain for VLESS tunnel.
    1. First checks shared file /etc/x-ui/vless_domain.txt
    2. Then tries multiple journalctl directories
    3. Then searches syslog / daemon.log
    4. Then checks local cache file
    5. Falls back to environment variable or localhost
    """
    # 1. Check direct shared file
    if os.path.exists(VLESS_DOMAIN_FILE):
        try:
            with open(VLESS_DOMAIN_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip().replace("https://", "").replace("http://", "").rstrip("/")
                if content and "trycloudflare.com" in content:
                    save_custom_domain(content)
                    return content
        except Exception as e:
            logger.debug(f"Could not read {VLESS_DOMAIN_FILE}: {e}")

    # 2. Try extracting from journalctl on Linux (testing container & host journal dirs)
    if os.name != 'nt':
        candidate_cmds = [
            f"journalctl -u {CLOUDFLARE_SERVICE} --no-pager -n 200",
            f"journalctl -D /var/log/journal -u {CLOUDFLARE_SERVICE} --no-pager -n 200",
            f"journalctl -D /run/log/journal -u {CLOUDFLARE_SERVICE} --no-pager -n 200",
            f"journalctl -m -u {CLOUDFLARE_SERVICE} --no-pager -n 200",
            "journalctl --no-pager -n 300"
        ]
        for cmd in candidate_cmds:
            try:
                res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
                matches = re.findall(r'https://([a-z0-9-]+\.trycloudflare\.com)', res)
                if matches:
                    domain = matches[-1].strip()
                    save_custom_domain(domain)
                    return domain
            except Exception:
                continue

        # 3. Search raw log files
        from_logs = _search_domain_in_log_files(hint="cloudflared-quick") or _search_domain_in_log_files()
        if from_logs:
            save_custom_domain(from_logs)
            return from_logs

    # 4. Try reading from cache file
    if os.path.exists(DOMAIN_CACHE_FILE):
        try:
            with open(DOMAIN_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = f.read().strip()
                if cached and cached != "localhost" and "trycloudflare.com" in cached:
                    return cached
        except Exception:
            pass

    # 5. Default fallback
    return os.getenv("DEFAULT_DOMAIN", "localhost")

def save_custom_domain(domain: str):
    """Allows manual setting or caching of the domain"""
    clean = domain.replace("https://", "").replace("http://", "").strip().rstrip("/")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(DOMAIN_CACHE_FILE)), exist_ok=True)
        with open(DOMAIN_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(clean)
    except Exception as e:
        logger.warning(f"Failed to cache domain: {e}")
        
    try:
        if os.path.exists("/etc/x-ui"):
            with open(VLESS_DOMAIN_FILE, "w", encoding="utf-8") as f:
                f.write(clean)
    except Exception:
        pass
    return clean

def get_current_panel_url() -> str:
    """
    Extracts the latest trycloudflare domain for the 3X-UI Admin Panel.
    1. Checks shared file /etc/x-ui/panel_url.txt
    2. Tries journalctl for cloudflared-panel.service
    3. Searches syslog for cloudflared-panel
    4. Checks local cache file
    """
    # 1. Check direct shared file
    if os.path.exists(PANEL_URL_FILE):
        try:
            with open(PANEL_URL_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content and "trycloudflare.com" in content:
                    save_custom_panel_url(content)
                    return content
        except Exception as e:
            logger.debug(f"Could not read {PANEL_URL_FILE}: {e}")

    # 2. Try extracting from journalctl for cloudflared-panel
    if os.name != 'nt':
        candidate_cmds = [
            "journalctl -u cloudflared-panel.service --no-pager -n 200",
            "journalctl -D /var/log/journal -u cloudflared-panel.service --no-pager -n 200",
            "journalctl -D /run/log/journal -u cloudflared-panel.service --no-pager -n 200"
        ]
        for cmd in candidate_cmds:
            try:
                res = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
                matches = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com(/[A-Za-z0-9]+/)?', res)
                raw_urls = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', res)
                if raw_urls:
                    base_url = raw_urls[-1].strip().rstrip("/")
                    full_url = f"{base_url}{PANEL_BASE_PATH}"
                    save_custom_panel_url(full_url)
                    return full_url
            except Exception:
                continue

        # 3. Search raw log files
        panel_domain = _search_domain_in_log_files(hint="cloudflared-panel")
        if panel_domain:
            full_url = f"https://{panel_domain}{PANEL_BASE_PATH}"
            save_custom_panel_url(full_url)
            return full_url

    # 4. Check cached panel url
    if os.path.exists(PANEL_CACHE_FILE):
        try:
            with open(PANEL_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = f.read().strip()
                if cached and "trycloudflare.com" in cached:
                    return cached
        except Exception:
            pass

    return f"https://127.0.0.1:2053{PANEL_BASE_PATH}"

def save_custom_panel_url(url: str):
    clean = url.strip()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(PANEL_CACHE_FILE)), exist_ok=True)
        with open(PANEL_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(clean)
    except Exception:
        pass
        
    try:
        if os.path.exists("/etc/x-ui"):
            with open(PANEL_URL_FILE, "w", encoding="utf-8") as f:
                f.write(clean)
    except Exception:
        pass
    return clean

def build_vless_config(uuid: str, host: str, remark: str = "Client") -> str:
    """
    Generates exact VLESS link according to user's specification:
    vless://$UUID@$HOST:$PORT?path=%2Fvless&security=tls&encryption=none&host=$HOST&fp=chrome&type=ws&sni=$HOST#Tunnel-$REMARK
    Preserves @ in username so Telegram identity is clearly visible.
    """
    clean_remark = re.sub(r'[\s]+', '-', str(remark).strip()).replace("#", "")
    if not clean_remark:
        clean_remark = "Client"
    encoded_path = "%2F" + CONFIG_PATH.lstrip("/")
    
    config = (
        f"vless://{uuid}@{host}:{CONFIG_PORT}"
        f"?path={encoded_path}&security=tls&encryption=none&host={host}&fp=chrome&type=ws&sni={host}#Tunnel-{clean_remark}"
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
