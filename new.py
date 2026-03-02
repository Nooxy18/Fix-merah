#!/usr/bin/env python3
# col.py (final) - Elegant UI + per-user sender support + broadcast
# Header: 🤖 ||BOT BYPASS RED NUMBER||☠️
# IMPORTANT: Configure .env (RECIPIENT_EMAIL, TELEGRAM_BOT_TOKEN, OWNER_IDS, etc.)

import os
import re
import json
import time
import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# -------------------------
# Config & Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

# OWNER_IDS can be comma separated
OWNER_IDS = {int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

COUNTDOWN_SECONDS = int(os.getenv("COUNTDOWN_SECONDS", "15"))
MAX_JEBOL = int(os.getenv("MAX_JEBOL", "15"))
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "").strip()  # target recipient (you must control this)

EMAIL_SENDER = os.getenv("GMAIL_SENDER")        # optional global fallback sender
EMAIL_PASS = os.getenv("GMAIL_APP_PASSWORD")

UPGRADE_IMAGE = os.getenv("UPGRADE_IMAGE", "https://files.catbox.moe/sdgn0r.jpg")

APP = None  # Application instance

# -------------------------
# Styling helpers (English)
# -------------------------
HEADER_BOX = "╭────────────────────────────────────╮\n│ 🤖 ||BOT BYPASS RED NUMBER||☠️ │\n╰────────────────────────────────────╯"
DIV = "────────────────────────────────────────────────"

def build_start_text(first_name: str, user_id: int, premium: bool, premium_info: str) -> str:
    lines = []
    lines.append(HEADER_BOX)
    lines.append(f"🔥 Welcome, *{first_name}*")
    lines.append(DIV)
    if premium:
        lines.append("💎 *Status:* Premium User")
        lines.append(f"⏳ *Valid until:* `{premium_info}`")
    else:
        lines.append("🧩 *Status:* Free User")
        lines.append("🚀 *Upgrade to Premium to unlock exclusive features!*")
    lines.append(DIV)
    lines.append("*Available Commands:*")
    lines.append("• `/send <number>` — Send report")
    lines.append("• `/mylistsenders` — Show your private senders")
    lines.append("• `/listsenders` — Show global senders (owner)")
    lines.append("• `/status` — Show your account status")
    lines.append(DIV)
    lines.append("*Owner/Admin Commands:*")
    lines.append("• `/addsender email:password`")
    lines.append("• `/remsender email`")
    lines.append("• `/fixsender email`")
    lines.append("• `/addpremium <user_id> [days]`")
    lines.append("• `/listpremium` | `/removepremium <user_id>`")
    lines.append("• `/broadcast <message>` (owner only)")
    lines.append("• `/pbroadcast <message>` (premium-only broadcast, owner only)")
    lines.append(DIV)
    lines.append(f"👤 *User ID:* `{user_id}`")
    return "\n".join(lines)

def build_senders_list_text_global(db: dict) -> str:
    lines = [HEADER_BOX, "📧 *Global Senders (owner)*", DIV]
    senders = db.get("global", {}).get("senders", [])
    if not senders:
        lines.append("_No global senders configured._")
        return "\n".join(lines)
    for idx, s in enumerate(senders, start=1):
        st = s.get("status", "ACTIVE")
        used = s.get("used", 0)
        emoji = "✅" if st == "ACTIVE" else ("🚫" if st == "LIMIT" else ("🔒" if st == "BAD_AUTH" else "⚠️"))
        lines.append(f"{idx}. `{s.get('email')}` — {emoji} *{st}* (`{used}/{MAX_JEBOL}`)")
    lines.append(DIV)
    lines.append("_Note: Global senders marked LIMIT will be skipped until reset with_ `/fixsender`.")
    return "\n".join(lines)

def build_senders_list_text_user(user_senders: list) -> str:
    lines = ["📧 *Your Private Senders*", DIV]
    if not user_senders:
        lines.append("_You have no private senders. Add one with_ `/myaddsender email:password`")
        return "\n".join(lines)
    for idx, s in enumerate(user_senders, start=1):
        st = s.get("status", "ACTIVE")
        used = s.get("used", 0)
        emoji = "✅" if st == "ACTIVE" else ("🚫" if st == "LIMIT" else ("🔒" if st == "BAD_AUTH" else "⚠️"))
        lines.append(f"{idx}. `{s.get('email')}` — {emoji} *{st}* (`{used}/{MAX_JEBOL}`)")
    lines.append(DIV)
    lines.append("_Private senders are used before global senders for your `/send` requests._")
    return "\n".join(lines)

def build_status_text(uid: int, premium_expiry) -> str:
    lines = [HEADER_BOX, "📌 *Account Status*", DIV]
    if premium_expiry and premium_expiry > datetime.now(timezone.utc):
        remain = premium_expiry.strftime("%d %b %Y %H:%M UTC")
        lines.append(f"💎 *Premium:* Active")
        lines.append(f"⏳ *Valid until:* `{remain}`")
    else:
        lines.append("🧩 *Premium:* Not Active")
    lines.append(DIV)
    lines.append(f"👤 *User ID:* `{uid}`")
    return "\n".join(lines)

# -------------------------
# JSON helpers & DB layout
# -------------------------
DATA_DIR = DATA_DIR
SENDERS_DB_PATH = DATA_DIR / "senders.json"
PREMIUM_DB_PATH = DATA_DIR / "premium.json"

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception(f"Failed to read JSON {path}")
        return {}

def save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)

def ensure_senders_db():
    db = load_json(SENDERS_DB_PATH)
    changed = False
    if "global" not in db:
        db["global"] = {"senders": [], "pointer": 0}
        changed = True
    if "users" not in db:
        db["users"] = {}
        changed = True
    if changed:
        save_json(SENDERS_DB_PATH, db)

def load_senders_db() -> dict:
    ensure_senders_db()
    return load_json(SENDERS_DB_PATH)

def save_senders_db(db: dict) -> None:
    save_json(SENDERS_DB_PATH, db)

# -------------------------
# Sender helpers (global + per-user)
# -------------------------
def add_global_sender(email_addr: str, password: str) -> bool:
    db = load_senders_db()
    g = db["global"]["senders"]
    if any(s.get("email") == email_addr for s in g): return False
    g.append({"email": email_addr, "password": password, "status": "ACTIVE", "used": 0})
    db["global"]["senders"] = g
    save_senders_db(db)
    return True

def remove_global_sender(email_addr: str) -> bool:
    db = load_senders_db()
    g = db["global"]["senders"]
    new = [s for s in g if s.get("email") != email_addr]
    if len(new) == len(g): return False
    db["global"]["senders"] = new
    db["global"]["pointer"] = 0
    save_senders_db(db)
    return True

def add_user_sender(user_id: int, email_addr: str, password: str) -> bool:
    db = load_senders_db()
    users = db.setdefault("users", {})
    ukey = str(user_id)
    if ukey not in users:
        users[ukey] = {"senders": [], "pointer": 0}
    s_list = users[ukey]["senders"]
    if any(s.get("email") == email_addr for s in s_list): return False
    s_list.append({"email": email_addr, "password": password, "status": "ACTIVE", "used": 0})
    users[ukey]["senders"] = s_list
    db["users"] = users
    save_senders_db(db)
    return True

def remove_user_sender(user_id: int, email_addr: str) -> bool:
    db = load_senders_db()
    users = db.get("users", {})
    ukey = str(user_id)
    if ukey not in users: return False
    s_list = users[ukey]["senders"]
    new = [s for s in s_list if s.get("email") != email_addr]
    if len(new) == len(s_list): return False
    users[ukey]["senders"] = new
    users[ukey]["pointer"] = 0
    db["users"] = users
    save_senders_db(db)
    return True

def reset_user_sender(user_id: int, email_addr: str) -> bool:
    db = load_senders_db()
    users = db.get("users", {})
    ukey = str(user_id)
    if ukey not in users: return False
    for s in users[ukey]["senders"]:
        if s.get("email") == email_addr:
            s["status"] = "ACTIVE"
            s["used"] = 0
            save_senders_db(db)
            return True
    return False

def reset_global_sender(email_addr: str) -> bool:
    db = load_senders_db()
    for s in db["global"]["senders"]:
        if s.get("email") == email_addr:
            s["status"] = "ACTIVE"
            s["used"] = 0
            save_senders_db(db)
            return True
    return False

def mark_sender_status_global(email_addr: str, status: str) -> None:
    db = load_senders_db()
    for s in db["global"]["senders"]:
        if s.get("email") == email_addr:
            s["status"] = status
            save_senders_db(db)
            return

def mark_sender_status_user(user_id: int, email_addr: str, status: str) -> None:
    db = load_senders_db()
    users = db.get("users", {})
    ukey = str(user_id)
    if ukey not in users: return
    for s in users[ukey]["senders"]:
        if s.get("email") == email_addr:
            s["status"] = status
            save_senders_db(db)
            return

def increment_sender_used_global(email_addr: str) -> int:
    db = load_senders_db()
    for s in db["global"]["senders"]:
        if s.get("email") == email_addr:
            s["used"] = int(s.get("used", 0)) + 1
            used = s["used"]
            if used >= MAX_JEBOL:
                s["status"] = "LIMIT"
            save_senders_db(db)
            return used
    return -1

def increment_sender_used_user(user_id: int, email_addr: str) -> int:
    db = load_senders_db()
    users = db.get("users", {})
    ukey = str(user_id)
    if ukey not in users: return -1
    for s in users[ukey]["senders"]:
        if s.get("email") == email_addr:
            s["used"] = int(s.get("used", 0)) + 1
            used = s["used"]
            if used >= MAX_JEBOL:
                s["status"] = "LIMIT"
            save_senders_db(db)
            return used
    return -1

def find_next_active_sender_for_user(user_id: int):
    """
    Return (sender_entry, 'user'/'global', index) or (None, None, -1)
    Preference:
      1) user's own senders (rotated)
      2) global senders (rotated)
    """
    db = load_senders_db()
    users = db.get("users", {})
    ukey = str(user_id)
    # 1) user senders
    if ukey in users:
        user_block = users[ukey]
        s_list = user_block.get("senders", [])
        n = len(s_list)
        if n:
            ptr = int(user_block.get("pointer", 0)) % n
            for offset in range(n):
                idx = (ptr + offset) % n
                s = s_list[idx]
                if s.get("status", "ACTIVE") == "ACTIVE":
                    users[ukey]["pointer"] = (idx + 1) % n
                    db["users"] = users
                    save_senders_db(db)
                    return s, "user", idx
    # 2) global senders
    g = db.get("global", {"senders": [], "pointer": 0})
    g_list = g.get("senders", [])
    n = len(g_list)
    if n:
        ptr = int(g.get("pointer", 0)) % n
        for offset in range(n):
            idx = (ptr + offset) % n
            s = g_list[idx]
            if s.get("status", "ACTIVE") == "ACTIVE":
                db["global"]["pointer"] = (idx + 1) % n
                save_senders_db(db)
                return s, "global", idx
    return None, None, -1

# -------------------------
# SMTP send (sync)
# -------------------------
import ssl

def send_email_smtp(sender: str, password: str, recipient: str, subject: str, body: str):
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = subject

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
        server.quit()

        logging.info(f"SMTP send OK from {sender} -> {recipient}")
        return True, ""

    except smtplib.SMTPAuthenticationError as e:
        logging.error(f"SMTP auth error {sender}: {e}")
        return False, "BAD_AUTH"

    except Exception as e:
        logging.exception(f"SMTP ERROR {sender}: {e}")
        return False, "OTHER"

# -------------------------
# Owner notify helper
# -------------------------
async def notify_owners(text: str):
    global APP
    if not APP:
        logging.warning("APP not initialized; cannot notify owners")
        return
    for oid in OWNER_IDS:
        try:
            await APP.bot.send_message(chat_id=oid, text=text)
        except Exception:
            logging.exception(f"Failed to notify owner {oid}")

# -------------------------
# Countdown UI (style 2)
# -------------------------
async def countdown_ui(chat_id: int, bot, seconds: int = COUNTDOWN_SECONDS):
    try:
        msg = await bot.send_message(chat_id=chat_id, text=f"🔥 Bypass in progress... {seconds}s remaining")
    except Exception:
        return
    for rem in range(seconds, 0, -1):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"🔥 Bypass in progress... {rem}s remaining")
        except Exception:
            pass
        await asyncio.sleep(1)
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="✅ Bypass completed.")
    except Exception:
        pass

# -------------------------
# Premium helpers
# -------------------------
def load_premium_db(): return load_json(PREMIUM_DB_PATH)
def save_premium_db(d): save_json(PREMIUM_DB_PATH, d)

def add_premium(uid: int, days: int = 30) -> datetime:
    db = load_premium_db()
    expiry = datetime.now(timezone.utc) + timedelta(days=days)
    db[str(uid)] = expiry.isoformat()
    save_premium_db(db)
    return expiry

def remove_premium(uid: int) -> bool:
    db = load_premium_db()
    if str(uid) in db:
        del db[str(uid)]
        save_premium_db(db)
        return True
    return False

def get_premium_expiry(uid: int):
    db = load_premium_db()
    iso = db.get(str(uid))
    if not iso: return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None

# -------------------------
# Report body template (neutral)
# -------------------------
EMAIL_SUBJECT = "Question about Android application"

def create_report_body(number: str) -> str:
    return (
        f"Здравствуйте, служба поддержки!\n"
        f"Меня зовут [Repzsx], мой номер телефона {number}. "
        "Я не могу войти в свой аккаунт приложения, потому что постоянно получаю сообщение «Не могу войти», "
        "хотя я использую официальное приложение. Пожалуйста, проверьте и исправьте это как можно скорее, "
        "чтобы мой номер можно было повторно активировать. Спасибо."
    )

# -------------------------
# Core send flow (per-user then global rotation)
# -------------------------
async def send_flow(chat_id: int, bot, number: str, user_id: int):
    if not RECIPIENT_EMAIL:
        await bot.send_message(chat_id=chat_id, text="❌ RECIPIENT_EMAIL not configured in .env")
        return False, "no_recipient"

    attempted = False
    db = load_senders_db()
    total_possible = 0
    total_possible += len(db.get("global", {}).get("senders", []))
    total_possible += sum(len(v.get("senders", [])) for v in db.get("users", {}).values())
    total_possible = max(total_possible, 1)

    for _ in range(total_possible):
        s_entry, scope, idx = find_next_active_sender_for_user(user_id)
        if not s_entry:
            break
        attempted = True
        sender_email = s_entry.get("email")
        sender_pass = s_entry.get("password", "")

        subject = EMAIL_SUBJECT
        body = create_report_body(number)

        try:
            ok, status_hint = await asyncio.get_running_loop().run_in_executor(None, send_email_smtp,
                                                                               sender_email, sender_pass, RECIPIENT_EMAIL, subject, body)
        except Exception as e:
            logging.exception(f"Executor send error for {sender_email}: {e}")
            ok, status_hint = False, "OTHER"

        if not ok:
            # mark on correct scope and notify owner
            if scope == "user":
                mark_sender_status_user(user_id, sender_email, "BAD")
            else:
                mark_sender_status_global(sender_email, "BAD")
            if status_hint == "LIMIT":
                if scope == "user":
                    mark_sender_status_user(user_id, sender_email, "LIMIT")
                else:
                    mark_sender_status_global(sender_email, "LIMIT")
                asyncio.create_task(notify_owners(f"⚠️ Sender {sender_email} marked LIMIT."))
            if status_hint == "BAD_AUTH":
                if scope == "user":
                    mark_sender_status_user(user_id, sender_email, "BAD_AUTH")
                else:
                    mark_sender_status_global(sender_email, "BAD_AUTH")
                asyncio.create_task(notify_owners(f"⚠️ Sender {sender_email} marked BAD_AUTH (auth failed)."))
            continue

        # success -> countdown then increment usage in proper scope
        await countdown_ui(chat_id, bot, COUNTDOWN_SECONDS)
        if scope == "user":
            used = increment_sender_used_user(user_id, sender_email)
        else:
            used = increment_sender_used_global(sender_email)
        await bot.send_message(chat_id=chat_id, text=f"✅ SUCCESS SEND FROM `{sender_email}` (`{used}/{MAX_JEBOL}`)", parse_mode="Markdown")
        # notify owner if reached limit
        if used >= MAX_JEBOL:
            asyncio.create_task(notify_owners(f"⚠️ Sender {sender_email} reached limit {used}/{MAX_JEBOL} and marked LIMIT."))
        return True, f"sent_via_{sender_email}"

    if not attempted:
        return False, "no_active_senders"
    return False, "all_senders_failed_or_limit"

# -------------------------
# Commands
# -------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    first = user.first_name if user and user.first_name else "User"
    uid = user.id if user else 0
    expiry = get_premium_expiry(uid)
    premium = expiry and expiry > datetime.now(timezone.utc)
    premium_info = expiry.strftime("%d %b %Y %H:%M UTC") if premium else ""
    text = build_start_text(first, uid, premium, premium_info)
    buttons = []
    if premium:
        buttons = [[InlineKeyboardButton("📞 Contact Admin", url=f"tg://user?id={next(iter(OWNER_IDS),0)}"),
                    InlineKeyboardButton("💡 Check Premium", callback_data="check_premium")]]
        await update.message.reply_markdown(text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
    else:
        buttons = [[InlineKeyboardButton("💎 Upgrade Premium", callback_data="upgrade_premium")],
                   [InlineKeyboardButton("📞 Contact Admin", url=f"tg://user?id={next(iter(OWNER_IDS),0)}"),
                    InlineKeyboardButton("💡 Check Premium", callback_data="check_premium")]]
        if UPGRADE_IMAGE:
            try:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=UPGRADE_IMAGE, caption="🚀 Upgrade to Premium now and unlock all exclusive features!")
            except Exception:
                logging.warning("Failed to send upgrade image.")
        await update.message.reply_markdown(text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

# premium decorator
def premium_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        uid = update.effective_user.id
        if uid in OWNER_IDS:
            return await func(update, context, *a, **kw)
        expiry = get_premium_expiry(uid)
        now = datetime.now(timezone.utc)
        if not expiry or expiry <= now:
            await update.message.reply_text("🔒 *Access Denied!* This feature is for *Premium users* only. Use /start to upgrade.", parse_mode="Markdown")
            return
        return await func(update, context, *a, **kw)
    return wrapper

@premium_required
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Usage: /send <number>
    if not context.args:
        return await update.message.reply_text("Usage: /send <number>\nExample: /send +628123456789")
    raw = context.args[0]
    number = re.sub(r"\D", "", raw)
    if not number:
        return await update.message.reply_text("Invalid number.")
    number_display = f"+{number}"
    await update.message.reply_text(f"⚙️ Processing send for `{number_display}` ...", parse_mode="Markdown")
    ok, status = await send_flow(update.effective_chat.id, context.bot, number_display, update.effective_user.id)
    if not ok:
        if status in ("no_senders", "no_active_senders", "all_senders_failed_or_limit"):
            await update.message.reply_text("❌ All senders unavailable or limited. Please configure your private sender with `/myaddsender` or ask owner to add global senders.")
        else:
            await update.message.reply_text(f"⚠️ Finished with status: {status}")

async def listsenders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_senders_db()
    text = build_senders_list_text_global(db)
    await update.message.reply_markdown(text)

async def mylistsenders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db = load_senders_db()
    users = db.get("users", {})
    ukey = str(uid)
    user_block = users.get(ukey, {"senders": []})
    text = build_senders_list_text_user(user_block.get("senders", []))
    await update.message.reply_markdown(text)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    expiry = get_premium_expiry(uid)
    text = build_status_text(uid, expiry)
    await update.message.reply_markdown(text)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "check_premium":
        uid = update.effective_user.id
        expiry = get_premium_expiry(uid)
        if expiry and expiry > datetime.now(timezone.utc):
            await q.edit_message_text(f"💎 Premium active until `{expiry.strftime('%d %b %Y %H:%M UTC')}`", parse_mode="Markdown")
        else:
            await q.edit_message_text("🧩 You are not premium. Use /start to see upgrade options.", parse_mode="Markdown")
    elif q.data == "upgrade_premium":
        caption = (
            "💎 *UPGRADE PREMIUM*\n"
            "🇮🇩 10.000 = 1 Week\n"
            "🇮🇩 15.000 = Lifetime\n"
            "🇬🇧 1$ = Lifetime\n\nContact Admin to complete payment."
        )
        try:
            await context.bot.send_message(chat_id=q.message.chat_id, text=caption, parse_mode="Markdown")
        except:
            await q.edit_message_text("Please contact admin to upgrade.")

# -------------------------
# User-level sender commands
# -------------------------
async def myaddsender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args or ":" not in context.args[0]:
        return await update.message.reply_text("Usage: /myaddsender email:password")
    email_addr, password = context.args[0].split(":", 1)
    ok = add_user_sender(uid, email_addr.strip(), password.strip())
    if ok:
        await update.message.reply_text(f"✅ Private sender `{email_addr}` added for your account.", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ That sender already exists in your private list.")

async def myremsender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("Usage: /myremsender email")
    email_addr = context.args[0].strip()
    ok = remove_user_sender(uid, email_addr)
    if ok:
        await update.message.reply_text("🗑️ Private sender removed.")
    else:
        await update.message.reply_text("⚠️ Private sender not found.")

# -------------------------
# Admin/global sender commands (owner only)
# -------------------------
async def addsender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_IDS:
        return await update.message.reply_text("❌ Only owner can use this.")
    if not context.args or ":" not in context.args[0]:
        return await update.message.reply_text("Usage: /addsender email:password")
    email_addr, password = context.args[0].split(":", 1)
    ok = add_global_sender(email_addr.strip(), password.strip())
    if ok:
        await update.message.reply_text(f"✅ Global sender `{email_addr}` added (ACTIVE).", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Global sender already exists.")

async def remsender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_IDS:
        return await update.message.reply_text("❌ Only owner.")
    if not context.args:
        return await update.message.reply_text("Usage: /remsender email")
    ok = remove_global_sender(context.args[0].strip())
    if ok:
        await update.message.reply_text("🗑️ Global sender removed.")
    else:
        await update.message.reply_text("⚠️ Global sender not found.")

async def fixsender_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_IDS:
        return await update.message.reply_text("❌ Only owner.")
    if not context.args:
        return await update.message.reply_text("Usage: /fixsender email")
    email = context.args[0].strip()
    ok_global = reset_global_sender(email)
    db = load_senders_db()
    users = db.get("users", {})
    ok_user_any = False
    for uid, ub in users.items():
        for s in ub.get("senders", []):
            if s.get("email") == email:
                reset_user_sender(int(uid), email)
                ok_user_any = True
    if ok_global or ok_user_any:
        await update.message.reply_text("✅ Sender reset to ACTIVE (used=0).")
    else:
        await update.message.reply_text("⚠️ Sender not found.")

# -------------------------
# Premium admin commands (owner)
# -------------------------
async def addpremium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_IDS:
        return await update.message.reply_text("❌ Only owner.")
    if not context.args:
        return await update.message.reply_text("Usage: /addpremium <user_id> [days]")
    try:
        uid = int(context.args[0])
    except:
        return await update.message.reply_text("User ID must be numeric.")
    days = int(context.args[1]) if len(context.args) > 1 else 30
    expiry = add_premium(uid, days=days)
    await update.message.reply_text(f"✅ Premium activated for `{uid}` until `{expiry.strftime('%d %b %Y %H:%M UTC')}`", parse_mode="Markdown")

async def listpremium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_IDS:
        return await update.message.reply_text("❌ Only owner.")
    db = load_premium_db()
    if not db:
        return await update.message.reply_text("_No premium users._", parse_mode="Markdown")
    lines = [f"• `{uid}` — `{iso}`" for uid, iso in db.items()]
    await update.message.reply_markdown("📋 *Premium Users:*\n" + "\n".join(lines))

async def removepremium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in OWNER_IDS:
        return await update.message.reply_text("❌ Only owner.")
    if not context.args:
        return await update.message.reply_text("Usage: /removepremium <user_id>")
    uid = int(context.args[0])
    ok = remove_premium(uid)
    if ok:
        await update.message.reply_text("✅ Premium removed.")
    else:
        await update.message.reply_text("⚠️ No premium record found.")

# -------------------------
# Broadcast commands (owner only)
# -------------------------
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in OWNER_IDS:
        return await update.message.reply_text("❌ You do not have permission to use this command.")
    if not context.args:
        return await update.message.reply_text("📢 Usage: /broadcast <message>")
    message = " ".join(context.args)
    await update.message.reply_text("📤 Sending broadcast to all known users...")
    # Broadcast targets: all users who appear in premium DB OR senders DB users keys
    sent = 0
    # Try premium DB users
    pdb = load_premium_db()
    for uid in pdb.keys():
        try:
            await context.bot.send_message(chat_id=int(uid), text=f"📢 <b>Announcement:</b>\n\n{message}", parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.25)
        except Exception as e:
            logging.warning(f"Failed broadcast to premium user {uid}: {e}")
    # Also try users in senders DB
    sdb = load_senders_db().get("users", {})
    for uid in sdb.keys():
        try:
            await context.bot.send_message(chat_id=int(uid), text=f"📢 <b>Announcement:</b>\n\n{message}", parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.25)
        except Exception:
            pass
    await update.message.reply_text(f"✅ Broadcast sent (attempts): {sent}")

async def pbroadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in OWNER_IDS:
        return await update.message.reply_text("❌ You do not have permission to use this command.")
    if not context.args:
        return await update.message.reply_text("💎 Usage: /pbroadcast <message>")
    message = " ".join(context.args)
    await update.message.reply_text("📤 Sending broadcast to Premium users...")
    sent = 0
    db = load_premium_db()
    for uid, iso in db.items():
        try:
            exp_dt = datetime.fromisoformat(iso)
            if exp_dt > datetime.now(timezone.utc):
                await context.bot.send_message(chat_id=int(uid), text=f"💎 <b>Premium Announcement:</b>\n\n{message}", parse_mode=ParseMode.HTML)
                sent += 1
                await asyncio.sleep(0.25)
        except Exception as e:
            logging.warning(f"Failed pbroadcast to {uid}: {e}")
    await update.message.reply_text(f"✅ Premium broadcast sent (attempts): {sent}")

# -------------------------
# Main
# -------------------------
def main():
    global APP
    APP = Application.builder().token(BOT_TOKEN).build()

    # public / user
    APP.add_handler(CommandHandler("start", start_command))
    APP.add_handler(CommandHandler("send", send_command))
    APP.add_handler(CommandHandler("listsenders", listsenders_command))
    APP.add_handler(CommandHandler("mylistsenders", mylistsenders_command))
    APP.add_handler(CommandHandler("status", status_command))

    # user-level sender management
    APP.add_handler(CommandHandler("myaddsender", myaddsender_command))
    APP.add_handler(CommandHandler("myremsender", myremsender_command))

    # owner / admin (global)
    APP.add_handler(CommandHandler("addsender", addsender_command))
    APP.add_handler(CommandHandler("remsender", remsender_command))
    APP.add_handler(CommandHandler("fixsender", fixsender_command))

    # premium admin
    APP.add_handler(CommandHandler("addpremium", addpremium_command))
    APP.add_handler(CommandHandler("listpremium", listpremium_command))
    APP.add_handler(CommandHandler("removepremium", removepremium_command))

    # broadcast
    APP.add_handler(CommandHandler("broadcast", broadcast_command))
    APP.add_handler(CommandHandler("pbroadcast", pbroadcast_command))

    # buttons
    APP.add_handler(CallbackQueryHandler(button_callback))

    logging.info("🚀 Bot ||BOT BYPASS RED NUMBER|| is running (final).")
    APP.run_polling()

if __name__ == "__main__":
    main()
