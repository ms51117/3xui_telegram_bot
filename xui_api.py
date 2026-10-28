import requests
import json
import sqlite3
import os
import re
import uuid as uuid_pkg
import time
import logging
from typing import Optional, Dict, Any, List

from config import (
    PANEL_URL, PANEL_BASE_PATH, PANEL_USERNAME, PANEL_PASSWORD,
    PANEL_API_TOKEN, INBOUND_ID, XUI_DB_PATH
)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

class XUIClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.base_url = PANEL_URL.rstrip('/')
        self.base_path = PANEL_BASE_PATH
        self.api_token = PANEL_API_TOKEN.strip() if PANEL_API_TOKEN else ""
        self.is_logged_in = False
        self.last_login_time = 0

        # Set standard browser headers to prevent 403 from 3X-UI WAF / origin checks
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": self.base_url,
            "Referer": f"{self.api_prefix}"
        })

        # If Bearer token is provided, set it immediately
        if self.api_token:
            self.session.headers["Authorization"] = f"Bearer {self.api_token}"
            self.is_logged_in = True
            logger.info("Using Bearer API Token for 3X-UI authentication.")

    @property
    def api_prefix(self) -> str:
        # Construct full URL prefix like: https://127.0.0.1:2053/h4B2swmMPtUzP9SJqA/
        return f"{self.base_url}{self.base_path}"

    def login(self) -> bool:
        # If API token is provided, no login needed
        if self.api_token:
            return True

        payload = {
            "username": PANEL_USERNAME,
            "password": PANEL_PASSWORD,
            "twoFactorCode": ""
        }

        # Try establishing session cookies and extracting CSRF token from HTML
        try:
            init_resp = self.session.get(self.api_prefix, timeout=5)
            if init_resp.status_code == 200:
                match = re.search(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', init_resp.text)
                if match:
                    self.session.headers["X-CSRF-Token"] = match.group(1)
        except Exception:
            pass

        # Try with full base path first, then without if needed
        candidate_urls = [
            f"{self.api_prefix}login",
            f"{self.base_url}/login"
        ]

        for login_url in candidate_urls:
            try:
                # 1. Try JSON payload
                resp = self.session.post(login_url, json=payload, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        self._on_login_success()
                        return True
                    else:
                        logger.error(f"Login failed at {login_url}: {data.get('msg')}")
                elif resp.status_code in (400, 403):
                    # 2. Try form data payload
                    form_resp = self.session.post(login_url, data=payload, timeout=10)
                    if form_resp.status_code == 200 and form_resp.json().get("success"):
                        self._on_login_success()
                        return True
                    logger.warning(f"Login attempt at {login_url} returned {resp.status_code}")
            except Exception as e:
                logger.warning(f"Login attempt error at {login_url}: {e}")

        self.is_logged_in = False
        return False

    def _on_login_success(self):
        self.is_logged_in = True
        self.last_login_time = time.time()
        logger.info("Successfully logged in to 3X-UI panel API.")
        
        # Fetch CSRF token for subsequent requests
        try:
            csrf_url = f"{self.api_prefix}csrf-token"
            r = self.session.get(csrf_url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("success") and data.get("obj"):
                    self.session.headers["X-CSRF-Token"] = data.get("obj")
        except Exception:
            pass

    def ensure_logged_in(self) -> bool:
        if self.api_token:
            return True
        if not self.is_logged_in or (time.time() - self.last_login_time > 1200):
            return self.login()
        return True

    def get_inbound(self, inbound_id: int = INBOUND_ID) -> Optional[Dict[str, Any]]:
        """Fetches complete inbound details including clients list and stats"""
        if not self.ensure_logged_in():
            # Fallback to local SQLite if API is unavailable
            return self._get_inbound_from_sqlite(inbound_id)

        url = f"{self.api_prefix}panel/api/inbounds/get/{inbound_id}"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    obj = data.get("obj")
                    if obj:
                        # Ensure settings is a dict
                        if isinstance(obj.get("settings"), str):
                            try:
                                obj["settings"] = json.loads(obj["settings"])
                            except Exception:
                                pass
                        return obj
        except Exception as e:
            logger.warning(f"Error calling get_inbound API: {e}. Falling back to SQLite.")

        return self._get_inbound_from_sqlite(inbound_id)

    def _get_inbound_from_sqlite(self, inbound_id: int) -> Optional[Dict[str, Any]]:
        """Fallback method to read inbound directly from x-ui.db on the Linux host"""
        if not os.path.exists(XUI_DB_PATH):
            return None
        try:
            conn = sqlite3.connect(XUI_DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM inbounds WHERE id = ?", (inbound_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return None
            
            inbound = dict(row)
            # Parse settings JSON
            if "settings" in inbound and isinstance(inbound["settings"], str):
                try:
                    inbound["settings"] = json.loads(inbound["settings"])
                except Exception:
                    pass

            # Also fetch clientStats if available in client_traffics table
            cursor.execute("SELECT * FROM client_traffics WHERE inbound_id = ?", (inbound_id,))
            client_traffics = [dict(r) for r in cursor.fetchall()]
            inbound["clientStats"] = client_traffics

            conn.close()
            return inbound
        except Exception as e:
            logger.error(f"Error reading SQLite inbound: {e}")
            return None

    def get_all_clients(self, inbound_id: int = INBOUND_ID) -> List[Dict[str, Any]]:
        """
        Returns merged list of clients with their stats:
        email, uuid, enable, expiryTime, total (bytes), up, down, etc.
        """
        inbound = self.get_inbound(inbound_id)
        if not inbound:
            return []

        settings = inbound.get("settings", {})
        clients = settings.get("clients", []) if isinstance(settings, dict) else []
        stats = {s.get("email"): s for s in inbound.get("clientStats", []) if s.get("email")}

        merged_clients = []
        for c in clients:
            email = c.get("email")
            stat = stats.get(email, {})
            merged = {
                "email": email,
                "id": c.get("id"),
                "uuid": c.get("id"),
                "enable": c.get("enable", True),
                "expiryTime": stat.get("expiryTime") or c.get("expiryTime", 0),
                "total": stat.get("total") or c.get("totalGB", 0) or 0,
                "up": stat.get("up", 0),
                "down": stat.get("down", 0),
                "subId": c.get("subId", ""),
                "tgId": c.get("tgId", ""),
                "comment": c.get("comment", "")
            }
            merged_clients.append(merged)
        return merged_clients

    def get_client_by_email(self, email: str, inbound_id: int = INBOUND_ID) -> Optional[Dict[str, Any]]:
        clients = self.get_all_clients(inbound_id)
        for c in clients:
            if c.get("email") == email:
                return c
        return None

    def add_client(self, email: str, total_gb: float = 0, expiry_days: int = 0,
                   tg_id: Optional[int] = None, comment: str = "",
                   inbound_id: int = INBOUND_ID) -> Optional[Dict[str, Any]]:
        """
        Creates a new client in 3X-UI.
        total_gb: 0 for unlimited, or e.g. 30
        expiry_days: 0 for unlimited, or e.g. 30
        """
        client_uuid = str(uuid_pkg.uuid4())
        client_sub_id = str(uuid_pkg.uuid4()).replace("-", "")[:16]
        
        # Calculate expiry timestamp in milliseconds
        expiry_ms = 0
        if expiry_days > 0:
            expiry_ms = int((time.time() + (expiry_days * 86400)) * 1000)

        # Calculate bytes for totalGB
        total_bytes = 0
        if total_gb > 0:
            total_bytes = int(total_gb * 1024 * 1024 * 1024)

        if not self.ensure_logged_in():
            logger.error("Cannot add client: failed to log in to 3X-UI.")
            return None

        # 1. Try modern 3X-UI Clients API: POST /panel/api/clients/add
        url = f"{self.api_prefix}panel/api/clients/add"
        payload = {
            "client": {
                "id": client_uuid,
                "email": email,
                "enable": True,
                "expiryTime": expiry_ms,
                "totalGB": total_bytes,
                "tgId": tg_id or "",
                "subId": client_sub_id,
                "comment": comment
            },
            "inboundIds": [inbound_id]
        }

        try:
            resp = self.session.post(url, json=payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("success"):
                logger.info(f"Successfully added client {email} via Clients API.")
                return {
                    "uuid": client_uuid,
                    "email": email,
                    "subId": client_sub_id,
                    "total": total_bytes,
                    "expiryTime": expiry_ms
                }
        except Exception as e:
            logger.warning(f"Clients API add failed: {e}. Trying inbounds addClient API.")

        # 2. Fallback to Inbounds API: POST /panel/api/inbounds/addClient
        inbound_url = f"{self.api_prefix}panel/api/inbounds/addClient"
        inbound_payload = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": client_uuid,
                    "email": email,
                    "enable": True,
                    "expiryTime": expiry_ms,
                    "totalGB": total_bytes,
                    "tgId": tg_id or "",
                    "subId": client_sub_id,
                    "comment": comment
                }]
            })
        }
        try:
            resp = self.session.post(inbound_url, json=inbound_payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("success"):
                logger.info(f"Successfully added client {email} via Inbounds addClient API.")
                return {
                    "uuid": client_uuid,
                    "email": email,
                    "subId": client_sub_id,
                    "total": total_bytes,
                    "expiryTime": expiry_ms
                }
            else:
                logger.error(f"Inbounds addClient failed: {resp.text}")
        except Exception as e:
            logger.error(f"Exception adding client via inbounds addClient: {e}")

        return None

    def delete_client(self, email: str, client_uuid: str = None, inbound_id: int = INBOUND_ID) -> bool:
        """Deletes a client from 3X-UI"""
        if not self.ensure_logged_in():
            return False

        # 1. Try Clients API: POST /panel/api/clients/del/{email}
        url = f"{self.api_prefix}panel/api/clients/del/{email}"
        try:
            resp = self.session.post(url, timeout=10)
            if resp.status_code == 200 and resp.json().get("success"):
                logger.info(f"Client {email} deleted via Clients API.")
                return True
        except Exception as e:
            logger.warning(f"Clients API delete failed: {e}")

        # 2. Try Inbound API: POST /panel/api/inbounds/{id}/delClient/{uuid}
        if client_uuid:
            inbound_del_url = f"{self.api_prefix}panel/api/inbounds/{inbound_id}/delClient/{client_uuid}"
            try:
                resp = self.session.post(inbound_del_url, timeout=10)
                if resp.status_code == 200 and resp.json().get("success"):
                    logger.info(f"Client {email} deleted via Inbounds delClient API.")
                    return True
            except Exception as e:
                logger.error(f"Inbounds delClient failed: {e}")

        return False

    def reset_client_traffic(self, email: str, client_uuid: str = None, inbound_id: int = INBOUND_ID) -> bool:
        """Zeroes upload and download counters for a client and re-enables them"""
        if not self.ensure_logged_in():
            return False

        # Try Clients API: POST /panel/api/clients/resetTraffic/{email}
        url = f"{self.api_prefix}panel/api/clients/resetTraffic/{email}"
        try:
            resp = self.session.post(url, timeout=10)
            if resp.status_code == 200 and resp.json().get("success"):
                return True
        except Exception:
            pass

        # Try Inbounds API: POST /panel/api/inbounds/resetClientTraffic/{email}
        inbound_url = f"{self.api_prefix}panel/api/inbounds/resetClientTraffic/{email}"
        try:
            resp = self.session.post(inbound_url, timeout=10)
            if resp.status_code == 200 and resp.json().get("success"):
                return True
        except Exception as e:
            logger.error(f"Reset client traffic failed: {e}")

        return False

    def adjust_client(self, email: str, add_gb: float = 0, add_days: int = 0,
                      enable: Optional[bool] = None, client_uuid: str = None,
                      inbound_id: int = INBOUND_ID) -> bool:
        """
        Adjusts quota or expiry days.
        """
        if not self.ensure_logged_in():
            return False

        # 1. Try bulkAdjust if adding days or bytes
        if add_gb != 0 or add_days != 0:
            url = f"{self.api_prefix}panel/api/clients/bulkAdjust"
            payload = {
                "emails": [email],
                "addDays": add_days,
                "addBytes": int(add_gb * 1024 * 1024 * 1024)
            }
            try:
                resp = self.session.post(url, json=payload, timeout=10)
                if resp.status_code == 200 and resp.json().get("success"):
                    logger.info(f"Adjusted client {email} successfully via bulkAdjust.")
                    return True
            except Exception as e:
                logger.warning(f"bulkAdjust failed: {e}")

        # 2. Otherwise fetch current client, modify, and update
        client = self.get_client_by_email(email, inbound_id)
        if not client:
            return False

        current_total = client.get("total", 0)
        current_expiry = client.get("expiryTime", 0)
        new_total = current_total + int(add_gb * 1024 * 1024 * 1024) if add_gb != 0 else current_total
        
        new_expiry = current_expiry
        if add_days != 0:
            base_time = max(time.time(), (current_expiry / 1000.0) if current_expiry > 0 else time.time())
            new_expiry = int((base_time + (add_days * 86400)) * 1000)

        new_enable = enable if enable is not None else client.get("enable", True)

        # Update via update endpoint
        update_url = f"{self.api_prefix}panel/api/inbounds/updateClient/{client.get('uuid') or client_uuid}"
        update_payload = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": client.get("uuid") or client_uuid,
                    "email": email,
                    "enable": new_enable,
                    "expiryTime": new_expiry,
                    "totalGB": new_total,
                    "tgId": client.get("tgId", ""),
                    "subId": client.get("subId", ""),
                    "comment": client.get("comment", "")
                }]
            })
        }
        try:
            resp = self.session.post(update_url, json=update_payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("success"):
                return True
        except Exception as e:
            logger.error(f"updateClient failed: {e}")

        return False

    def get_server_status(self) -> Optional[Dict[str, Any]]:
        """Returns CPU, RAM, Disk, and system status"""
        if not self.ensure_logged_in():
            return None
        url = f"{self.api_prefix}panel/api/server/status"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200 and resp.json().get("success"):
                return resp.json().get("obj")
        except Exception as e:
            logger.error(f"Error fetching server status: {e}")
        return None
