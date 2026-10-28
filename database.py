import sqlite3
import datetime
import os
import re
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)

def get_connection():
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            name TEXT,
            username TEXT,
            telegram_id INTEGER,
            uuid TEXT,
            sub_id TEXT,
            inbound_id INTEGER DEFAULT 2,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tg_id ON users(telegram_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_username ON users(username)')
    conn.commit()
    conn.close()
    logger.info("Local database initialized successfully.")

def normalize_username(username: str) -> str:
    if not username:
        return ""
    u = username.strip()
    if u.startswith("@"):
        u = u[1:]
    return u.lower()

def add_or_update_user(email: str, name: str = None, username: str = None, 
                       telegram_id: int = None, uuid: str = None, 
                       sub_id: str = None, inbound_id: int = 2, is_active: int = 1):
    clean_username = normalize_username(username)
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    existing = cursor.fetchone()
    
    if existing:
        # Keep existing values if new ones are None
        updated_name = name if name is not None else existing['name']
        updated_username = clean_username if username is not None else existing['username']
        updated_tg_id = telegram_id if telegram_id is not None else existing['telegram_id']
        updated_uuid = uuid if uuid is not None else existing['uuid']
        updated_sub_id = sub_id if sub_id is not None else existing['sub_id']
        updated_active = is_active if is_active is not None else existing['is_active']
        
        cursor.execute('''
            UPDATE users 
            SET name = ?, username = ?, telegram_id = ?, uuid = ?, sub_id = ?, inbound_id = ?, is_active = ?
            WHERE email = ?
        ''', (updated_name, updated_username, updated_tg_id, updated_uuid, updated_sub_id, inbound_id, updated_active, email))
    else:
        cursor.execute('''
            INSERT INTO users (email, name, username, telegram_id, uuid, sub_id, inbound_id, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (email, name or email, clean_username, telegram_id, uuid, sub_id, inbound_id, is_active))
        
    conn.commit()
    conn.close()

def get_user_by_email(email: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_telegram_id(telegram_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_username(username: str):
    clean = normalize_username(username)
    if not clean:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(username) = ?", (clean,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def bind_telegram_id_by_username(username: str, telegram_id: int):
    clean = normalize_username(username)
    if not clean:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET telegram_id = ? WHERE LOWER(username) = ?", (telegram_id, clean))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def bind_telegram_id_by_email(email: str, telegram_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET telegram_id = ? WHERE email = ?", (telegram_id, email))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def update_user_profile(email: str, name: str = None, username: str = None, telegram_id: int = None, notes: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    updates = []
    params = []
    
    if name is not None:
        updates.append("name = ?")
        params.append(name.strip())
    if username is not None:
        clean_username = normalize_username(username)
        updates.append("username = ?")
        params.append(clean_username)
    if telegram_id is not None:
        updates.append("telegram_id = ?")
        params.append(telegram_id)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)
        
    if not updates:
        conn.close()
        return False
        
    params.append(email)
    sql = f"UPDATE users SET {', '.join(updates)} WHERE email = ?"
    cursor.execute(sql, tuple(params))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def list_all_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def search_users(query: str):
    clean = query.strip().lstrip("@").lower()
    conn = get_connection()
    cursor = conn.cursor()
    pattern = f"%{clean}%"
    cursor.execute('''
        SELECT * FROM users 
        WHERE LOWER(name) LIKE ? 
           OR LOWER(username) LIKE ? 
           OR LOWER(email) LIKE ? 
           OR CAST(telegram_id AS TEXT) LIKE ?
        ORDER BY created_at DESC
    ''', (pattern, pattern, pattern, pattern))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_user(email: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE email = ?", (email,))
    conn.commit()
    conn.close()

def sync_existing_clients(clients_list, inbound_id: int = 2):
    """
    Safely imports clients from 3X-UI without deleting or modifying them.
    If client email is numeric (Telegram ID), binds telegram_id automatically.
    """
    for client in clients_list:
        email = str(client.get("email", "")).strip()
        if not email:
            continue
        uuid = client.get("id") or client.get("uuid")
        sub_id = client.get("subId")
        is_enable = 1 if client.get("enable", True) else 0
        
        # Check if email is a numeric Telegram ID
        tg_id = None
        if email.isdigit():
            tg_id = int(email)
            default_name = f"کاربر {email}"
        else:
            default_name = email
            
        existing = get_user_by_email(email)
        if not existing:
            add_or_update_user(
                email=email,
                name=default_name,
                username=None,
                telegram_id=tg_id,
                uuid=uuid,
                sub_id=sub_id,
                inbound_id=inbound_id,
                is_active=is_enable
            )
        else:
            # Update UUID or sub_id if missing or changed
            add_or_update_user(
                email=email,
                uuid=uuid,
                sub_id=sub_id,
                is_active=is_enable
            )

def get_client_remark(user_info) -> str:
    """
    Constructs a clear identifying remark for the VLESS config.
    Priority:
    1. If name is customized and username exists: Name-@username-id
    2. If only username exists: @username-id
    3. If only custom name exists: Name-id
    4. Fallback: id or email
    """
    if isinstance(user_info, str):
        u = get_user_by_email(user_info)
        if not u:
            return user_info
    elif isinstance(user_info, dict):
        u = user_info
    else:
        return "Client"

    username = u.get("username")
    name = u.get("name")
    tg_id = u.get("telegram_id")
    email = u.get("email", "")

    clean_u = f"@{username.lstrip('@').strip()}" if username else ""
    clean_name = re.sub(r'[\s]+', '-', str(name).strip()) if (name and not str(name).startswith("کاربر ") and name != email) else ""
    clean_tg_id = str(tg_id).strip() if tg_id else (email if str(email).isdigit() else "")

    parts = []
    if clean_name:
        parts.append(clean_name)
    if clean_u:
        parts.append(clean_u)
    if clean_tg_id:
        parts.append(clean_tg_id)

    if parts:
        return "-".join(parts)

    return email or "Client"

