import logging
import asyncio
import datetime
import time
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

from config import (
    BOT_TOKEN, ADMIN_ID, INBOUND_ID
)
import database as db
from xui_api import XUIClient
import cloudflare

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

xui = XUIClient()

# Conversation states for adding a user
(
    ADD_NAME,
    ADD_USERNAME,
    ADD_TRAFFIC,
    ADD_DAYS,
    ADD_CONFIRM
) = range(5)

# Conversation states for adjusting user
(
    ADJUST_TRAFFIC,
    ADJUST_DAYS,
    BROADCAST_MSG,
    SET_DOMAIN,
    SEARCH_QUERY
) = range(5, 10)

# ----------------- Helper Functions -----------------

def format_bytes(bytes_val: int) -> str:
    if not bytes_val or bytes_val <= 0:
        return "0 MB"
    gb = bytes_val / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.2f} GB"
    mb = bytes_val / (1024 ** 2)
    return f"{mb:.1f} MB"

def format_expiry(expiry_ms: int) -> str:
    if not expiry_ms or expiry_ms <= 0:
        return "♾ نامحدود"
    now_ms = time.time() * 1000
    diff_ms = expiry_ms - now_ms
    if diff_ms <= 0:
        return "❌ منقضی شده"
    days = int(diff_ms / (1000 * 86400))
    hours = int((diff_ms % (1000 * 86400)) / (1000 * 3600))
    if days > 0:
        return f"{days} روز و {hours} ساعت دیگر"
    return f"{hours} ساعت دیگر"

def make_progress_bar(used: int, total: int, length: int = 10) -> str:
    if not total or total <= 0:
        return "♾ نامحدود"
    pct = min(1.0, max(0.0, used / total))
    filled = int(round(pct * length))
    empty = length - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {int(pct * 100)}%"

async def safe_reply_text(message, text: str, reply_markup=None):
    try:
        return await message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Markdown reply failed ({e}), falling back to plain text.")
        clean_text = text.replace("*", "").replace("`", "").replace("_", "")
        return await message.reply_text(clean_text, reply_markup=reply_markup)

async def safe_edit_message_text(query, text: str, reply_markup=None):
    try:
        return await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Markdown edit failed ({e}), falling back to plain text.")
        clean_text = text.replace("*", "").replace("`", "").replace("_", "")
        return await query.edit_message_text(clean_text, reply_markup=reply_markup)

# ----------------- Start & Routing -----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    user = update.effective_user
    chat_id = update.effective_chat.id if update.effective_chat else None
    
    logger.info(f"===> [USER INTERACTION] User ID: {user.id}, Username: @{user.username}, Name: {user.first_name}")

    # Check if admin
    if user.id == ADMIN_ID:
        logger.info(f"User {user.id} recognized as ADMIN. Showing Admin Dashboard.")
        await show_admin_dashboard(update, context)
        return

    # Regular user flow
    # 0. Check for deep-link activation parameter (e.g. /start sub_email)
    args = context.args
    if args and len(args) > 0:
        param = args[0].strip()
        if param.startswith("sub_"):
            email_param = param.replace("sub_", "").strip()
            if db.bind_telegram_id_by_email(email_param, user.id):
                logger.info(f"Auto-bound user {email_param} to Telegram ID {user.id} via deep-link.")

    # 1. Check if user is already linked by telegram_id
    db_user = db.get_user_by_telegram_id(user.id)
    
    # 2. If not found by telegram_id, try matching by @username
    if not db_user and user.username:
        if db.bind_telegram_id_by_username(user.username, user.id):
            db_user = db.get_user_by_telegram_id(user.id)
            logger.info(f"Auto-bound user @{user.username} to Telegram ID {user.id}")

    # 3. If found, display their customer portal
    if db_user:
        await show_user_panel(update, context, db_user)
    else:
        # User not recognized
        text = (
            f"👋 سلام **{user.first_name}** عزیز!\n\n"
            "به ربات مدیریت سرویس خوش آمدید.\n"
            "⚠️ در حال حاضر هیچ اشتراک فعالی به حساب کاربری شما متصل نیست.\n\n"
            f"🔹 **شناسه عددی شما:** `{user.id}`\n"
            f"🔹 **نام‌کاربری شما:** `@{user.username or 'ندارد'}`\n\n"
            "اگر اشتراک تهیه کرده‌اید، اطلاعات بالا را برای پشتیبانی ارسال فرمایید."
        )
        keyboard = [
            [InlineKeyboardButton("💬 ارتباط با پشتیبانی", url=f"tg://user?id={ADMIN_ID}")]
        ]
        if update.effective_message:
            await safe_reply_text(update.effective_message, text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_general_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    user = update.effective_user
    logger.info(f"===> [GENERAL MESSAGE] Received text '{update.effective_message.text}' from User ID: {user.id} (@{user.username})")
    await start(update, context)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"===> [BOT ERROR] Exception occurred: {context.error}", exc_info=context.error)

# ----------------- User Panel -----------------

async def show_user_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_user: dict):
    email = db_user['email']
    client = xui.get_client_by_email(email)
    
    name = db_user.get('name') or email
    
    if client:
        up = client.get("up", 0)
        down = client.get("down", 0)
        used = up + down
        total = client.get("total", 0)
        expiry_ms = client.get("expiryTime", 0)
        is_enable = client.get("enable", True)

        status_text = "🟢 فعال" if is_enable else "🔴 غیرفعال / پایان حجم"
        remaining_bytes = max(0, total - used) if total > 0 else 0
        rem_str = format_bytes(remaining_bytes) if total > 0 else "♾ نامحدود"
        
        text = (
            f"👤 **پنل کاربری: {name}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"▫️ **وضعیت سرویس:** {status_text}\n"
            f"▫️ **مصرف کل:** {format_bytes(used)} از {format_bytes(total) if total > 0 else 'نامحدود'}\n"
            f"▫️ **حجم باقیمانده:** {rem_str}\n"
            f"▫️ **میزان مصرف:** {make_progress_bar(used, total)}\n"
            f"▫️ **اعتبار زمانی:** {format_expiry(expiry_ms)}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
    else:
        text = (
            f"👤 **پنل کاربری: {name}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            "⚠️ وضعیت سرویس در حال بررسی می‌باشد.\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

    keyboard = [
        [InlineKeyboardButton("🔑 دریافت کانفیگ و QR کد", callback_data=f"user_get_config_{email}")],
        [InlineKeyboardButton("🔄 بروزرسانی وضعیت", callback_data=f"user_refresh_{email}")],
        [
            InlineKeyboardButton("📚 راهنمای اتصال", callback_data="user_guide"),
            InlineKeyboardButton("💬 پشتیبانی", url=f"tg://user?id={ADMIN_ID}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await safe_edit_message_text(update.callback_query, text, reply_markup=reply_markup)
    elif update.effective_message:
        await safe_reply_text(update.effective_message, text, reply_markup=reply_markup)

async def user_get_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    email = query.data.replace("user_get_config_", "")
    
    db_user = db.get_user_by_email(email)
    if not db_user:
        await query.message.reply_text("❌ حساب کاربری یافت نشد.")
        return

    client = xui.get_client_by_email(email)
    uuid_val = (client.get("uuid") or db_user.get("uuid")) if client else db_user.get("uuid")
    
    if not uuid_val:
        await query.message.reply_text("❌ کانفیگ برای این کاربر آماده نیست. با پشتیبانی تماس بگیرید.")
        return

    host = cloudflare.get_current_domain()
    config_url = cloudflare.build_vless_config(uuid_val, host, db_user.get("name") or email)
    qr_bio = cloudflare.generate_qr_code(config_url)

    caption = (
        f"🔑 **کانفیگ اختصاصی شما:**\n\n"
        f"```\n{config_url}\n```\n\n"
        "💡 جهت اتصال، متن بالا را کپی کرده یا کد QR را در نرم‌افزار (v2rayNG / Streisand / v2rayN) اسکن کنید."
    )
    await query.message.reply_photo(photo=qr_bio, caption=caption, parse_mode="Markdown")

async def user_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("اطلاعات بروزرسانی شد.")
    email = query.data.replace("user_refresh_", "")
    db_user = db.get_user_by_email(email)
    if db_user:
        await show_user_panel(update, context, db_user)

async def user_guide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    guide_text = (
        "📚 **راهنمای جامع اتصال به سرویس:**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📱 **اندروید (Android):**\n"
        "1. برنامه **v2rayNG** را نصب کنید.\n"
        "2. دکمه «🔑 دریافت کانفیگ» را بزنید و متن کانفیگ را کپی کنید.\n"
        "3. وارد برنامه v2rayNG شوید، علامت **+** را بزنید و گزینه **Import config from Clipboard** را انتخاب کنید.\n"
        "4. دکمه اتصال در پایین را بزنید.\n\n"
        "🍏 **آیفون و آیپد (iOS):**\n"
        "1. یکی از برنامه‌های **Streisand** یا **FoXray** یا **V2Box** را از App Store نصب کنید.\n"
        "2. با زدن دکمه «🔑 دریافت کانفیگ»، کد QR را در برنامه اسکن فرمایید.\n\n"
        "💻 **ویندوز (Windows):**\n"
        "1. نرم‌افزار **v2rayN** را باز کنید.\n"
        "2. پس از کپی کردن کانفیگ، کلیدهای `Ctrl + V` را بزنید تا کانفیگ اضافه شود.\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "در صورت بروز هرگونه سوال یا مشکل با پشتیبانی در ارتباط باشید."
    )
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="user_back_to_panel")]]
    await query.message.reply_text(guide_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def user_back_to_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    db_user = db.get_user_by_telegram_id(user.id)
    if db_user:
        await show_user_panel(update, context, db_user)

# ----------------- Admin Dashboard -----------------

async def show_admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Fetch status & user counts
    users = db.list_all_users()
    domain = cloudflare.get_current_domain()
    
    text = (
        "👑 **پنل مدیریت پیشرفته 3X-UI**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **دامنه تانل کلودفلر:**\n`{domain}`\n\n"
        f"👥 **تعداد کاربران ثبت‌شده:** `{len(users)} کاربر`\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "برای مدیریت کامل، یکی از گزینه‌های زیر را انتخاب کنید:"
    )

    keyboard = [
        [
            InlineKeyboardButton("➕ افزودن کاربر جدید", callback_data="admin_add_user"),
            InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_list_users_0")
        ],
        [
            InlineKeyboardButton("🔍 جستجوی کاربر", callback_data="admin_search_user"),
            InlineKeyboardButton("🌐 مدیریت تانل کلودفلر", callback_data="admin_cf_menu")
        ],
        [
            InlineKeyboardButton("📊 وضعیت سرور", callback_data="admin_server_status"),
            InlineKeyboardButton("📢 پیام همگانی", callback_data="admin_broadcast")
        ],
        [
            InlineKeyboardButton("🔄 همگام‌سازی با 3X-UI", callback_data="admin_sync_xui")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await safe_edit_message_text(update.callback_query, text, reply_markup=reply_markup)
    elif update.effective_message:
        await safe_reply_text(update.effective_message, text, reply_markup=reply_markup)

# ----------------- Admin: User List & Management -----------------

PAGE_SIZE = 6

async def admin_list_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    page = int(query.data.split("_")[-1])
    users = db.list_all_users()
    total_users = len(users)
    
    if not users:
        text = "👥 هیچ کاربری در دیتابیس ثبت نشده است.\nاز دکمه «همگام‌سازی» یا «افزودن کاربر» استفاده کنید."
        keyboard = [
            [InlineKeyboardButton("➕ افزودن کاربر", callback_data="admin_add_user")],
            [InlineKeyboardButton("🔄 همگام‌سازی با پنل", callback_data="admin_sync_xui")],
            [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="admin_home")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_users)
    page_users = users[start_idx:end_idx]

    text = f"👥 **لیست کاربران (صفحه {page + 1} از {(total_users - 1) // PAGE_SIZE + 1})**\nکل کاربران: {total_users}\n\nجهت مشاهده و مدیریت روی نام هر کاربر کلیک کنید:"
    
    keyboard = []
    for u in page_users:
        name = u.get("name") or u["email"]
        status_icon = "🟢" if u.get("is_active", 1) else "🔴"
        uname_display = f" (@{u['username']})" if u.get('username') else ""
        btn_text = f"{status_icon} {name}{uname_display}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_view_user_{u['email']}")])

    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"admin_list_users_{page - 1}"))
    if end_idx < total_users:
        nav_buttons.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"admin_list_users_{page + 1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_home")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_view_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    email = query.data.replace("admin_view_user_", "")
    db_user = db.get_user_by_email(email)
    
    if not db_user:
        await query.edit_message_text("❌ کاربر مورد نظر در دیتابیس یافت نشد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_list_users_0")]]))
        return

    client = xui.get_client_by_email(email)
    
    name = db_user.get("name") or email
    uname = f"@{db_user['username']}" if db_user.get("username") else "ثبت نشده"
    tg_id_str = str(db_user.get("telegram_id")) if db_user.get("telegram_id") else "متصل نشده"
    
    if client:
        up = client.get("up", 0)
        down = client.get("down", 0)
        used = up + down
        total = client.get("total", 0)
        expiry_ms = client.get("expiryTime", 0)
        is_enable = client.get("enable", True)
        
        status_icon = "🟢 فعال" if is_enable else "🔴 غیرفعال"
        rem_bytes = max(0, total - used) if total > 0 else 0
        rem_str = format_bytes(rem_bytes) if total > 0 else "♾ نامحدود"
        
        text = (
            f"👤 **مدیریت کاربر: {name}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📧 **ایمیل در پنل:** `{email}`\n"
            f"👤 **نام‌کاربری تلگرام:** {uname}\n"
            f"🆔 **آیدی عددی تلگرام:** `{tg_id_str}`\n"
            f"🟢 **وضعیت:** {status_icon}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 **حجم کل:** {format_bytes(total) if total > 0 else '♾ نامحدود'}\n"
            f"📥 **مصرف شده:** {format_bytes(used)}\n"
            f"🔋 **باقیمانده:** {rem_str}\n"
            f"📈 **درصد مصرف:** {make_progress_bar(used, total)}\n"
            f"⏳ **اعتبار زمانی:** {format_expiry(expiry_ms)}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
    else:
        text = (
            f"👤 **کاربر: {name}**\n"
            f"📧 **ایمیل:** `{email}`\n"
            f"👤 **نام‌کاربری تلگرام:** {uname}\n"
            f"🆔 **آیدی عددی تلگرام:** `{tg_id_str}`\n\n"
            "⚠️ این کاربر در دیتابیس محلی ثبت است اما در اینباند 3X-UI یافت نشد."
        )

    toggle_text = "⏸ غیرفعال کردن" if (client and client.get("enable", True)) else "▶️ فعال کردن"
    toggle_val = "0" if (client and client.get("enable", True)) else "1"

    keyboard = [
        [
            InlineKeyboardButton("📤 ارسال کانفیگ به تلگرامش", callback_data=f"admin_send_user_{email}"),
            InlineKeyboardButton("🔑 دریافت لینک و QR", callback_data=f"admin_show_config_{email}")
        ],
        [
            InlineKeyboardButton("➕ تمدید حجم (+GB)", callback_data=f"admin_adj_traffic_{email}"),
            InlineKeyboardButton("⏳ تمدید روز (+روز)", callback_data=f"admin_adj_days_{email}")
        ],
        [
            InlineKeyboardButton("🔁 ریست مصرف ترافیک", callback_data=f"admin_reset_traffic_{email}"),
            InlineKeyboardButton(toggle_text, callback_data=f"admin_toggle_{email}_{toggle_val}")
        ],
        [
            InlineKeyboardButton("🗑 حذف کامل کاربر", callback_data=f"admin_del_confirm_{email}")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="admin_list_users_0")
        ]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_show_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    email = query.data.replace("admin_show_config_", "")
    
    db_user = db.get_user_by_email(email)
    client = xui.get_client_by_email(email)
    uuid_val = (client.get("uuid") or db_user.get("uuid")) if client else db_user.get("uuid")
    
    if not uuid_val:
        await query.message.reply_text("❌ شناسه UUID برای این کاربر موجود نیست.")
        return

    host = cloudflare.get_current_domain()
    config_url = cloudflare.build_vless_config(uuid_val, host, db_user.get("name") or email)
    qr_bio = cloudflare.generate_qr_code(config_url)

    caption = (
        f"🔑 **کانفیگ کاربر {db_user.get('name') or email}:**\n\n"
        f"```\n{config_url}\n```"
    )
    await query.message.reply_photo(photo=qr_bio, caption=caption, parse_mode="Markdown")

async def admin_send_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    email = query.data.replace("admin_send_user_", "")
    
    db_user = db.get_user_by_email(email)
    if not db_user or not db_user.get("telegram_id"):
        await query.answer("❌ آیدی عددی تلگرام کاربر ثبت نشده است (هنوز ربات را استارت نکرده).", show_alert=True)
        return

    client = xui.get_client_by_email(email)
    uuid_val = (client.get("uuid") or db_user.get("uuid")) if client else db_user.get("uuid")
    
    if not uuid_val:
        await query.answer("❌ شناسه UUID موجود نیست.", show_alert=True)
        return

    host = cloudflare.get_current_domain()
    config_url = cloudflare.build_vless_config(uuid_val, host, db_user.get("name") or email)
    qr_bio = cloudflare.generate_qr_code(config_url)

    target_chat_id = db_user["telegram_id"]
    try:
        user_msg = (
            f"🌐 **کانفیگ اشتراک شما به‌روزرسانی شد.**\n\n"
            f"```\n{config_url}\n```\n\n"
            "💡 متن بالا را کپی کرده یا کد QR را اسکن کنید."
        )
        await context.bot.send_photo(chat_id=target_chat_id, photo=qr_bio, caption=user_msg, parse_mode="Markdown")
        await query.answer("✅ کانفیگ با موفقیت به پیوی تلگرام کاربر ارسال شد.", show_alert=True)
    except Exception as e:
        logger.error(f"Failed to send config to {target_chat_id}: {e}")
        await query.answer(f"❌ خطا در ارسال به تلگرام کاربر: {e}", show_alert=True)

async def admin_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    email = parts[2]
    enable_flag = bool(int(parts[3]))
    
    success = xui.adjust_client(email, enable=enable_flag)
    if success:
        await query.answer(f"وضعیت به {'فعال' if enable_flag else 'غیرفعال'} تغییر یافت.")
    else:
        await query.answer("خطا در تغییر وضعیت در پنل.", show_alert=True)
        
    await admin_view_user_callback(update, context)

async def admin_reset_traffic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    email = query.data.replace("admin_reset_traffic_", "")
    success = xui.reset_client_traffic(email)
    if success:
        await query.answer("✅ حجم مصرفی کاربر صفر شد و کاربر مجدداً فعال گردید.", show_alert=True)
    else:
        await query.answer("❌ خطا در ریست حجم مصرفی.", show_alert=True)
    await admin_view_user_callback(update, context)

async def admin_del_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    email = query.data.replace("admin_del_confirm_", "")
    
    text = (
        f"⚠️ **آیا مطمئن هستید که می‌خواهید کاربر زیر را حذف کنید؟**\n\n"
        f"📧 ایمیل: `{email}`\n\n"
        "این عمل غیرقابل بازگشت است و کاربر از پنل 3X-UI نیز حذف خواهد شد."
    )
    keyboard = [
        [
            InlineKeyboardButton("❌ بله، حذف شود", callback_data=f"admin_do_del_{email}"),
            InlineKeyboardButton("🔙 انصراف", callback_data=f"admin_view_user_{email}")
        ]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_do_del_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    email = query.data.replace("admin_do_del_", "")
    
    # 1. Delete from 3X-UI
    xui.delete_client(email)
    # 2. Delete from local DB
    db.delete_user(email)
    
    await query.answer("✅ کاربر با موفقیت حذف شد.", show_alert=True)
    query.data = "admin_list_users_0"
    await admin_list_users_callback(update, context)

# ----------------- Admin: Search User -----------------

async def admin_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🔍 **جستجوی کاربر**\n\n"
        "لطفاً نام، آیدی عددی، ایمیل یا نام‌کاربری تلگرام (@username) کاربر را ارسال کنید:"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data="admin_home")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return SEARCH_QUERY

async def admin_search_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text.strip()
    matched = db.search_users(query_text)
    
    if not matched:
        text = f"❌ هیچ کاربری با مشخصات `{query_text}` یافت نشد."
        keyboard = [
            [InlineKeyboardButton("🔍 جستجوی مجدد", callback_data="admin_search_user")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_home")]
        ]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ConversationHandler.END

    text = f"🔍 **نتایج جستجو برای `{query_text}` ({len(matched)} مورد):**\nجهت مشاهده و مدیریت روی نام کاربر کلیک کنید:"
    keyboard = []
    for u in matched[:15]:
        name = u.get("name") or u["email"]
        status_icon = "🟢" if u.get("is_active", 1) else "🔴"
        uname_display = f" (@{u['username']})" if u.get('username') else ""
        keyboard.append([InlineKeyboardButton(f"{status_icon} {name}{uname_display}", callback_data=f"admin_view_user_{u['email']}")])

    keyboard.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_home")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ConversationHandler.END

# ----------------- Background Jobs -----------------

async def cloudflare_domain_watcher_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodically checks if Cloudflare tunnel domain changed and notifies admin"""
    current_domain = cloudflare.get_current_domain()
    last_domain = context.bot_data.get("last_domain")
    
    if last_domain is None:
        context.bot_data["last_domain"] = current_domain
        return
        
    if current_domain and current_domain != last_domain and current_domain != "localhost":
        context.bot_data["last_domain"] = current_domain
        logger.info(f"Detected Cloudflare domain change: {last_domain} -> {current_domain}")
        
        notice_text = (
            "⚠️ **توجه: دامنه تانل کلودفلر تغییر کرد!**\n\n"
            f"▫️ **دامنه قبلی:** `{last_domain}`\n"
            f"▫️ **دامنه جدید:** `{current_domain}`\n\n"
            "جهت ارسال خودکار کانفیگ به‌روز شده به تمامی کاربران فعال، دکمه زیر را لمس کنید:"
        )
        keyboard = [
            [InlineKeyboardButton("📢 ارسال خودکار کانفیگ جدید به همه", callback_data="admin_cf_broadcast_all")],
            [InlineKeyboardButton("🌐 مدیریت تانل", callback_data="admin_cf_menu")]
        ]
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=notice_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin of domain change: {e}")

# ----------------- Admin: Add User Wizard -----------------

async def add_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "➕ **افزودن کاربر جدید** (مرحله ۱ از ۴)\n\n"
        "لطفاً **نام کاربر** را وارد کنید:\n"
        "(مثال: `علی رضایی`)"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel_conv")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADD_NAME

async def add_user_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data["new_name"] = name
    
    text = (
        f"✅ نام: **{name}**\n\n"
        "➕ (مرحله ۲ از ۴)\n"
        "لطفاً **آیدی کاربری تلگرام (@username)** یا **آیدی عددی** کاربر را بفرستید:\n\n"
        "🔹 مثال: `@ms511` یا `87384626`\n"
        "🔹 اگر در حال حاضر آیدی ندارید، عدد `0` بفرستید."
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel_conv")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADD_USERNAME

async def add_user_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_input = update.message.text.strip()
    tg_username = None
    tg_id = None
    
    if raw_input != "0":
        if raw_input.isdigit():
            tg_id = int(raw_input)
        else:
            tg_username = db.normalize_username(raw_input)

    context.user_data["new_username"] = tg_username
    context.user_data["new_tg_id"] = tg_id
    
    text = (
        "➕ (مرحله ۳ از ۴)\n"
        "لطفاً **میزان حجم مجاز (به گیگابایت)** را ارسال کنید:\n\n"
        "🔹 مثال: `30` (برای ۳۰ گیگابایت)\n"
        "🔹 عدد `0` برای نامحدود"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel_conv")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADD_TRAFFIC

async def add_user_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_val = update.message.text.strip()
    try:
        traffic_gb = float(text_val)
    except ValueError:
        await update.message.reply_text("❌ لطفاً یک عدد معتبر برای حجم به گیگابایت وارد کنید (مثلاً 30 یا 0):")
        return ADD_TRAFFIC

    context.user_data["new_traffic"] = traffic_gb
    
    text = (
        "➕ (مرحله ۴ از ۴)\n"
        "لطفاً **مدت زمان اعتبار (به روز)** را ارسال کنید:\n\n"
        "🔹 مثال: `30` (برای ۳۰ روز)\n"
        "🔹 عدد `0` برای نامحدود"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel_conv")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADD_DAYS

async def add_user_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_val = update.message.text.strip()
    try:
        days = int(text_val)
    except ValueError:
        await update.message.reply_text("❌ لطفاً یک عدد معتبر برای روز وارد کنید (مثلاً 30 یا 0):")
        return ADD_DAYS

    context.user_data["new_days"] = days
    name = context.user_data["new_name"]
    username = context.user_data["new_username"]
    tg_id = context.user_data["new_tg_id"]
    traffic = context.user_data["new_traffic"]

    # Generate a unique email identifier in 3X-UI
    if username:
        base_email = username
    elif tg_id:
        base_email = str(tg_id)
    else:
        # Use slug of name or timestamp
        base_email = f"user_{int(time.time())}"
        
    email = base_email
    # Check uniqueness
    existing = xui.get_client_by_email(email)
    if existing:
        email = f"{base_email}_{int(time.time()) % 1000}"

    context.user_data["new_email"] = email

    confirm_text = (
        "📋 **پیش‌نمایش و تأیید ساخت کاربر:**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 **نام:** {name}\n"
        f"🆔 **نام کاربری تلگرام:** @{username if username else 'ندارد'}\n"
        f"🔢 **آیدی عددی:** `{tg_id if tg_id else 'ندارد'}`\n"
        f"📧 **ایمیل در پنل:** `{email}`\n"
        f"📊 **حجم:** {traffic} GB ({'نامحدود' if traffic == 0 else ''})\n"
        f"⏳ **مدت اعتبار:** {days} روز ({'نامحدود' if days == 0 else ''})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        "آیا این کاربر در پنل ساخته شود؟"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ تأیید و ساخت", callback_data="admin_add_confirm_yes"),
            InlineKeyboardButton("❌ انصراف", callback_data="admin_cancel_conv")
        ]
    ]
    await update.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADD_CONFIRM

async def add_user_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    name = context.user_data["new_name"]
    username = context.user_data["new_username"]
    tg_id = context.user_data["new_tg_id"]
    traffic = context.user_data["new_traffic"]
    days = context.user_data["new_days"]
    email = context.user_data["new_email"]

    # 1. Call 3X-UI API
    client_res = xui.add_client(
        email=email,
        total_gb=traffic,
        expiry_days=days,
        tg_id=tg_id,
        comment=name,
        inbound_id=INBOUND_ID
    )

    if not client_res:
        await query.edit_message_text(
            "❌ خطا در ساخت کلاینت در پنل 3X-UI.\nلطفاً از روشن بودن پنل و درستی نام کاربری و رمز اطمینان حاصل کنید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="admin_home")]])
        )
        return ConversationHandler.END

    uuid_val = client_res["uuid"]
    sub_id = client_res["subId"]

    # 2. Save in local database
    db.add_or_update_user(
        email=email,
        name=name,
        username=username,
        telegram_id=tg_id,
        uuid=uuid_val,
        sub_id=sub_id,
        inbound_id=INBOUND_ID
    )

    # 3. Generate VLESS config
    host = cloudflare.get_current_domain()
    config_url = cloudflare.build_vless_config(uuid_val, host, name)
    qr_bio = cloudflare.generate_qr_code(config_url)

    success_text = (
        f"🎉 **کاربر با موفقیت ساخته شد!**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 **نام:** {name}\n"
        f"📧 **ایمیل:** `{email}`\n"
        f"👤 **آیدی تلگرام:** @{username or 'ندارد'}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔑 **کانفیگ تولید شده:**\n"
        f"```\n{config_url}\n```\n"
    )

    # If numeric ID is available, automatically send to the user's telegram!
    if tg_id:
        try:
            user_msg = (
                f"🎉 سلام **{name}** عزیز!\n"
                "سرویس شما فعال شد. کانفیگ اختصاصی شما:\n\n"
                f"```\n{config_url}\n```\n\n"
                "💡 متن بالا را کپی کرده یا کد QR را در اپلیکیشن v2rayNG اسکن فرمایید."
            )
            # Re-generate QR for user message
            user_qr = cloudflare.generate_qr_code(config_url)
            await context.bot.send_photo(chat_id=tg_id, photo=user_qr, caption=user_msg, parse_mode="Markdown")
            success_text += "\n✅ کانفیگ به صورت خودکار به پیوی تلگرام کاربر ارسال شد!"
        except Exception as e:
            logger.warning(f"Could not auto-send config to user {tg_id}: {e}")
            success_text += "\n⚠️ کاربر هنوز ربات را استارت نزده، بنابراین لینک برایش ارسال نشد."

    keyboard = [
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_list_users_0")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="admin_home")]
    ]
    await query.message.reply_photo(photo=qr_bio, caption=success_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ عملیات لغو شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منو", callback_data="admin_home")]]))
    return ConversationHandler.END

# ----------------- Admin: Adjust Traffic & Days -----------------

async def admin_adj_traffic_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    email = query.data.replace("admin_adj_traffic_", "")
    context.user_data["adj_email"] = email
    
    text = (
        f"➕ **افزودن حجم برای کاربر:** `{email}`\n\n"
        "لطفاً مقدار حجم اضافه شونده را به **گیگابایت** وارد کنید:\n"
        "🔹 مثال: `10` (برای ۱۰ گیگ افزایش)\n"
        "🔹 برای کاهش حجم می‌توانید عدد منفی بگذارید: `-5`"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data=f"admin_view_user_{email}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADJUST_TRAFFIC

async def admin_adj_traffic_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val_text = update.message.text.strip()
    email = context.user_data.get("adj_email")
    try:
        gb = float(val_text)
    except ValueError:
        await update.message.reply_text("❌ عدد نامعتبر است. لطفاً عدد ارسال کنید:")
        return ADJUST_TRAFFIC

    success = xui.adjust_client(email, add_gb=gb)
    if success:
        await update.message.reply_text(
            f"✅ مقدار {gb} گیگابایت با موفقیت به ترافیک کاربر اضافه شد.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 مشاهده کاربر", callback_data=f"admin_view_user_{email}")]])
        )
    else:
        await update.message.reply_text(
            "❌ خطا در افزایش حجم کاربر در پنل.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin_view_user_{email}")]])
        )
    return ConversationHandler.END

async def admin_adj_days_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    email = query.data.replace("admin_adj_days_", "")
    context.user_data["adj_email"] = email
    
    text = (
        f"⏳ **تمدید روز برای کاربر:** `{email}`\n\n"
        "لطفاً تعداد روز اضافه شونده را وارد کنید:\n"
        "🔹 مثال: `30` (برای ۳۰ روز تمدید)\n"
        "🔹 برای کاهش روز: عدد منفی مثل `-5`"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data=f"admin_view_user_{email}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADJUST_DAYS

async def admin_adj_days_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val_text = update.message.text.strip()
    email = context.user_data.get("adj_email")
    try:
        days = int(val_text)
    except ValueError:
        await update.message.reply_text("❌ لطفاً یک عدد صحیح برای روز وارد کنید:")
        return ADJUST_DAYS

    success = xui.adjust_client(email, add_days=days)
    if success:
        await update.message.reply_text(
            f"✅ مدت {days} روز با موفقیت به انقضای کاربر اضافه شد.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 مشاهده کاربر", callback_data=f"admin_view_user_{email}")]])
        )
    else:
        await update.message.reply_text(
            "❌ خطا در تمدید روزهای کاربر در پنل.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=f"admin_view_user_{email}")]])
        )
    return ConversationHandler.END

# ----------------- Admin: Cloudflare Menu & Broadcast Configs -----------------

async def admin_cf_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    domain = cloudflare.get_current_domain()
    text = (
        "🌐 **مدیریت دامنه کلودفلر (TryCloudflare)**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔗 **دامنه فعال فعلی:**\n`{domain}`\n\n"
        "می‌توانید دامنه را به صورت زنده بازخوانی کنید، آن را دستی وارد کنید، "
        "یا با یک کلیک کانفیگ جدید را برای **تمامی کاربران فعال** در تلگرام بفرستید."
    )
    keyboard = [
        [InlineKeyboardButton("🔄 بازخوانی خودکار دامنه از لاگ", callback_data="admin_cf_refresh")],
        [InlineKeyboardButton("✍️ ثبت دستی دامنه", callback_data="admin_cf_manual")],
        [InlineKeyboardButton("📢 ارسال کانفیگ جدید به تمام کاربران", callback_data="admin_cf_broadcast_all")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_home")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_cf_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    domain = cloudflare.get_current_domain()
    await query.answer(f"دامنه بازخوانی شد: {domain}", show_alert=True)
    await admin_cf_menu_callback(update, context)

async def admin_cf_manual_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "✍️ لطفاً آدرس دامنه جدید را ارسال کنید:\n"
        "مثال: `xyz-test.trycloudflare.com`"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data="admin_cf_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return SET_DOMAIN

async def admin_cf_manual_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    domain = update.message.text.strip()
    saved = cloudflare.save_custom_domain(domain)
    await update.message.reply_text(
        f"✅ دامنه جدید با موفقیت ذخیره شد:\n`{saved}`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 بازگشت به مدیریت دامنه", callback_data="admin_cf_menu")]])
    )
    return ConversationHandler.END

async def admin_cf_broadcast_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("در حال ارسال کانفیگ به کاربران...")
    
    host = cloudflare.get_current_domain()
    users = db.list_all_users()
    sent_count = 0
    fail_count = 0

    for u in users:
        tg_id = u.get("telegram_id")
        if not tg_id:
            continue
        uuid_val = u.get("uuid")
        if not uuid_val:
            continue
            
        config_url = cloudflare.build_vless_config(uuid_val, host, u.get("name") or u["email"])
        try:
            msg = (
                "🌐 **آدرس سرور به‌روزرسانی شد.**\n\n"
                f"```\n{config_url}\n```"
            )
            await context.bot.send_message(chat_id=tg_id, text=msg, parse_mode="Markdown")
            sent_count += 1
            await asyncio.sleep(0.05) # Rate limit protection
        except Exception as e:
            logger.warning(f"Broadcast config to {tg_id} failed: {e}")
            fail_count += 1

    await query.edit_message_text(
        f"✅ **عملیات ارسال کانفیگ پایان یافت.**\n\n"
        f"▫️ موفق: `{sent_count}` کاربر\n"
        f"▫️ ناموفق (استارت نزده یا بلاک): `{fail_count}` کاربر",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به مدیریت دامنه", callback_data="admin_cf_menu")]])
    )

# ----------------- Admin: Server Status -----------------

async def admin_server_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    status = xui.get_server_status()
    if status:
        cpu = status.get("cpu", 0)
        mem = status.get("mem", {})
        mem_curr = format_bytes(mem.get("current", 0))
        mem_total = format_bytes(mem.get("total", 0))
        disk = status.get("disk", {})
        disk_curr = format_bytes(disk.get("current", 0))
        disk_total = format_bytes(disk.get("total", 0))
        xray_state = status.get("xray", {}).get("state", "ناشناخته")
        uptime = int(status.get("uptime", 0) / 3600)
        
        text = (
            "📊 **وضعیت سرور و پنل 3X-UI**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚡️ **هسته Xray:** {xray_state}\n"
            f"⏳ **آپ‌تایم:** {uptime} ساعت\n"
            f"💻 **مصرف پردازنده (CPU):** {cpu:.1f}%\n"
            f"🧠 **مصرف رم (RAM):** {mem_curr} از {mem_total}\n"
            f"💾 **مصرف دیسک:** {disk_curr} از {disk_total}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
    else:
        text = "❌ امکان دریافت اطلاعات وضعیت سرور از API پنل وجود ندارد."

    keyboard = [
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin_server_status")],
        [InlineKeyboardButton("🔙 بازگشت به منو", callback_data="admin_home")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ----------------- Admin: Broadcast Message -----------------

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "📢 **ارسال پیام همگانی به کاربران**\n\n"
        "لطفاً متن پیامی که می‌خواهید برای همه کاربران ارسال شود را بفرستید:"
    )
    keyboard = [[InlineKeyboardButton("❌ انصراف", callback_data="admin_home")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BROADCAST_MSG

async def admin_broadcast_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    broadcast_text = update.message.text
    users = db.list_all_users()
    sent = 0
    failed = 0
    
    for u in users:
        tg_id = u.get("telegram_id")
        if tg_id:
            try:
                await context.bot.send_message(chat_id=tg_id, text=broadcast_text)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1

    await update.message.reply_text(
        f"📢 پیام ارسال شد.\n▫️ ارسال موفق: {sent}\n▫️ ناموفق: {failed}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="admin_home")]])
    )
    return ConversationHandler.END

# ----------------- Admin: Sync with 3X-UI -----------------

async def admin_sync_xui_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("در حال دریافت کلاینت‌ها از پنل...")
    
    clients = xui.get_all_clients(INBOUND_ID)
    if not clients:
        await query.edit_message_text(
            "❌ هیچ کلاینتی از اینباند دریافت نشد یا ارتباط با پنل قطع است.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_home")]])
        )
        return

    db.sync_existing_clients(clients, INBOUND_ID)
    users = db.list_all_users()
    
    text = (
        f"✅ **همگام‌سازی با موفقیت انجام شد.**\n\n"
        f"▫️ تعداد کلاینت‌های خوانده شده از پنل: `{len(clients)}`\n"
        f"▫️ مجموع کاربران ثبت‌شده در ربات: `{len(users)}`\n\n"
        "کاربران قبلی شما با حفظ ایمیل و کانفیگ‌ها به ربات اضافه شدند و هیچ قطعی نخواهند داشت."
    )
    keyboard = [
        [InlineKeyboardButton("👥 مشاهده لیست کاربران", callback_data="admin_list_users_0")],
        [InlineKeyboardButton("🔙 منوی اصلی", callback_data="admin_home")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ----------------- Navigation Callbacks -----------------

async def admin_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_admin_dashboard(update, context)

# ----------------- Main App Setup -----------------

def main():
    # Initialize DB
    db.init_db()

    # Pre-sync existing clients on startup
    try:
        existing_clients = xui.get_all_clients(INBOUND_ID)
        if existing_clients:
            db.sync_existing_clients(existing_clients, INBOUND_ID)
            logger.info(f"Initial sync complete: {len(existing_clients)} clients loaded.")
    except Exception as e:
        logger.warning(f"Initial 3X-UI sync failed on startup: {e}")

    # Build Telegram Bot application
    app = Application.builder().token(BOT_TOKEN).build()

    # 1. Add User Conversation
    add_user_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_user_start, pattern="^admin_add_user$")],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_user_name)],
            ADD_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_user_username)],
            ADD_TRAFFIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_user_traffic)],
            ADD_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_user_days)],
            ADD_CONFIRM: [CallbackQueryHandler(add_user_confirm, pattern="^admin_add_confirm_yes$")]
        },
        fallbacks=[CallbackQueryHandler(cancel_conversation, pattern="^admin_cancel_conv$")],
        per_message=False
    )
    app.add_handler(add_user_conv)

    # 2. Adjust Traffic Conversation
    adj_traffic_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_adj_traffic_start, pattern=r"^admin_adj_traffic_")],
        states={
            ADJUST_TRAFFIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adj_traffic_finish)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conversation, pattern="^admin_cancel_conv$")],
        per_message=False
    )
    app.add_handler(adj_traffic_conv)

    # 3. Adjust Days Conversation
    adj_days_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_adj_days_start, pattern=r"^admin_adj_days_")],
        states={
            ADJUST_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adj_days_finish)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conversation, pattern="^admin_cancel_conv$")],
        per_message=False
    )
    app.add_handler(adj_days_conv)

    # 4. Manual Domain Conversation
    set_domain_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_cf_manual_start, pattern="^admin_cf_manual$")],
        states={
            SET_DOMAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cf_manual_finish)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conversation, pattern="^admin_cf_menu$")],
        per_message=False
    )
    app.add_handler(set_domain_conv)

    # 5. Broadcast Message Conversation
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={
            BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_finish)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conversation, pattern="^admin_home$")],
        per_message=False
    )
    app.add_handler(broadcast_conv)

    # 6. Search User Conversation
    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_search_start, pattern="^admin_search_user$")],
        states={
            SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_finish)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conversation, pattern="^admin_home$")],
        per_message=False
    )
    app.add_handler(search_conv)

    # Error handler
    app.add_error_handler(error_handler)

    # Standard Commands & Callbacks
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_message))
    
    # User callbacks
    app.add_handler(CallbackQueryHandler(user_get_config_callback, pattern=r"^user_get_config_"))
    app.add_handler(CallbackQueryHandler(user_refresh_callback, pattern=r"^user_refresh_"))
    app.add_handler(CallbackQueryHandler(user_guide_callback, pattern="^user_guide$"))
    app.add_handler(CallbackQueryHandler(user_back_to_panel_callback, pattern="^user_back_to_panel$"))

    # Admin callbacks
    app.add_handler(CallbackQueryHandler(admin_home_callback, pattern="^admin_home$"))
    app.add_handler(CallbackQueryHandler(admin_list_users_callback, pattern=r"^admin_list_users_"))
    app.add_handler(CallbackQueryHandler(admin_view_user_callback, pattern=r"^admin_view_user_"))
    app.add_handler(CallbackQueryHandler(admin_show_config_callback, pattern=r"^admin_show_config_"))
    app.add_handler(CallbackQueryHandler(admin_send_user_callback, pattern=r"^admin_send_user_"))
    app.add_handler(CallbackQueryHandler(admin_toggle_callback, pattern=r"^admin_toggle_"))
    app.add_handler(CallbackQueryHandler(admin_reset_traffic_callback, pattern=r"^admin_reset_traffic_"))
    app.add_handler(CallbackQueryHandler(admin_del_confirm_callback, pattern=r"^admin_del_confirm_"))
    app.add_handler(CallbackQueryHandler(admin_do_del_callback, pattern=r"^admin_do_del_"))
    app.add_handler(CallbackQueryHandler(admin_cf_menu_callback, pattern="^admin_cf_menu$"))
    app.add_handler(CallbackQueryHandler(admin_cf_refresh_callback, pattern="^admin_cf_refresh$"))
    app.add_handler(CallbackQueryHandler(admin_cf_broadcast_all_callback, pattern="^admin_cf_broadcast_all$"))
    app.add_handler(CallbackQueryHandler(admin_server_status_callback, pattern="^admin_server_status$"))
    app.add_handler(CallbackQueryHandler(admin_sync_xui_callback, pattern="^admin_sync_xui$"))

    # Schedule periodic Cloudflare tunnel domain watcher (checks every 60 seconds)
    if app.job_queue:
        app.job_queue.run_repeating(cloudflare_domain_watcher_job, interval=60, first=15)
        logger.info("Cloudflare tunnel watcher job scheduled successfully.")

    logger.info("Bot starting polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
