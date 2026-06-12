# ================================================================================
# SIGNATURE DIGITAL — BOT
# ================================================================================
# SUMMARY:
#   Added !veo extendall to extend all licenses at once by a given duration.
#   Added !veo reduceall to reduce all licenses at once. If any license goes
#   past today after reducing, it gets auto-revoked automatically.
#
# DATE/TIME (SL) : 13-06-2026
# ────────────────────────────────────────────────────────────────────────────────
# [ADDED] !sgd add/revoke/extend/check/list/resetdevice/setcookies/getcookies
# [ADDED] SGD_SERVER_URL + SGD_ADMIN_TOKEN env vars
# [ADDED] _handle_sgd_command() — SGD Access license manager
# [ADDED] !sgd section in !admin help panel
# ================================================================================

import requests
import re
import random
import imaplib
import email as _email_lib
import json
import os
import time
import logging
import traceback
import threading
import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request
import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.background import BackgroundScheduler

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
# WaSenderAPI — set WASENDER_API_KEY in Railway environment variables
WASENDER_API_KEY  = os.environ.get("WASENDER_API_KEY", "YOUR_WASENDER_API_KEY")
ADMIN_NUMBER      = "94778315058"
ADMIN_NUMBER_2    = "94765813386"
ADMIN_NUMBER_3    = "94763824563"
ADMIN_NUMBERS     = [ADMIN_NUMBER, ADMIN_NUMBER_2, ADMIN_NUMBER_3]
GOOGLE_SHEET_ID   = "1ML6Qc3jsmNrYqL3qnYO-NZnff4mawuxxW04jUX5JF8s"

# ── VEO License Manager ────────────────────────────────────────────────────────
FLOW_SERVER_URL   = os.environ.get("FLOW_SERVER_URL",   "https://flow-80.up.railway.app")
FLOW_ADMIN_SECRET = os.environ.get("FLOW_ADMIN_SECRET", "Rukaiy@123")

# ── SGD Access License Manager ─────────────────────────────────────────────────
SGD_SERVER_URL    = os.environ.get("SGD_SERVER_URL",    "https://sgd-app.up.railway.app")
SGD_ADMIN_TOKEN   = os.environ.get("SGD_ADMIN_TOKEN",   "changeme-admin-token")

SHEET_NAME        = "subscriptions"
SUBSCRIPTION_DAYS    = 7
ULTRA_EXPIRY_HOURS   = 168  # 7 days exactly

# ── ChatGPT OTP ────────────────────────────────────────────────────────────────
CHATGPT_GMAIL_ADDRESS  = "ahmedfawser@gmail.com"
CHATGPT_GMAIL_APP_PASS = "hcxz mmke utsi achy"
PERPLEXITY_GMAIL_ADDRESS  = "ahmedfawser@gmail.com"
PERPLEXITY_GMAIL_APP_PASS = "hcxz mmke utsi achy"

# Greeting storage — replaces old creds_store for greeting text
_greeting_store      = {"greeting_text": "", "greeting_number_map": {}}
_greeting_store_lock = threading.Lock()

# Ultra creds storage — single slot (username/password/extra)
_creds_slot1      = {"username": "", "password": "", "extra": "", "file_content": "", "cookie_version": 1}
_creds_slot1_lock = threading.Lock()

# Ultra creds delivery log — phone -> ISO timestamp of last "creating account" reply
_creds_sent_phones      = {}  # phone -> cookie version sent
_creds_sent_phones_lock = threading.Lock()

# Dedicated waiting list — phones who got "creating account" message, waiting for cookie

# Permanent expiry message sent log — phone+software -> True, never sends again
_expiry_msg_sent      = set()
_expiry_msg_sent_lock = threading.Lock()

def extract_duration_from_subscription(sub_name):
    """
    Extract duration from subscription string.
    Looks for patterns like: 2W (2 weeks), 2m (2 months), 3D (3 days)
    Returns: (duration_value, duration_unit) or (None, None) if not found
    
    Examples:
    - "Ultra 1950 2W" → (2, "W") = 14 days
    - "Adobe 1950 2m" → (2, "m") = 2 months
    - "Standard 1950 3D" → (3, "D") = 3 days
    """
    if not sub_name:
        return None, None
    
    import re
    # Match pattern: number followed by M, m, W, w, D, or d
    match = re.search(r'(\d+)\s*([MmWwDd])', str(sub_name).strip())
    
    if match:
        duration_value = int(match.group(1))
        duration_unit = match.group(2).upper()  # Normalize to uppercase
        return duration_value, duration_unit
    
    return None, None


def calculate_expiry_date_from_duration(purchase_date, duration_value, duration_unit):
    """
    Calculate expiry date based on duration value and unit.
    
    Args:
        purchase_date: datetime object of purchase date
        duration_value: integer (e.g., 2, 3, 14)
        duration_unit: string - "W" (weeks), "M" (months), or "D" (days)
    
    Returns:
        datetime object of expiry date, or None if invalid
    """
    if not purchase_date or not duration_value or not duration_unit:
        return None
    
    try:
        if duration_unit == "W":  # Weeks
            return purchase_date + timedelta(weeks=duration_value)
        elif duration_unit == "D":  # Days
            return purchase_date + timedelta(days=duration_value)
        elif duration_unit == "M":  # Months
            return add_n_months(purchase_date, duration_value)
        else:
            return None
    except Exception:
        return None

# Tracks when creds were last sent to each customer { phone: datetime }


# Key: normalize_phone + "_" + slot_username — value: True
import re as _re_gmail
def parse_ultra_notify_time(sub_name):
    """
    Extract notification time from subscription string.
    Handles format: ULTRA [TIME] [PRICE] [DURATION]
    
    Examples:
    - "ULTRA 1339 1950 2W" → (13, 39) — send notification at 13:39 (1:39 PM)
    - "ULTRA 1940" → (19, 40) — send notification at 19:40 (7:40 PM)
    - "ULTRA 1950 2W" → None — no time specified, use default 23:59
    
    Returns: (hour, minute) tuple or None if not found
    """
    if not sub_name:
        return None
    
    import re as _re_pt
    stripped = _re_pt.sub(r'^(ULTRA|ULT[A-Z]*)\s*', '', sub_name.strip().upper()).strip()
    
    # Find all 4-digit numbers and check if they're valid times
    numbers = _re_pt.findall(r'\b(\d{4})\b', stripped)
    
    for t in numbers:
        h, mn = int(t[:2]), int(t[2:])
        # Check if it's a valid time (0-23 hours, 0-59 minutes)
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return h, mn
    
    return None
COOLDOWN_SECONDS  = 300


CACHE_SECONDS     = 300
SL_TZ             = ZoneInfo("Asia/Colombo")   # UTC+5:30 — used everywhere for dates

# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME PERSISTENCE — auto-save/load to Railway volume at /data/memory.json
# Mount your Railway volume at /data — bot saves every 60s + on key events
# ═══════════════════════════════════════════════════════════════════════════════
DATA_FILE         = "/data/memory.json"
VEO_LICENSE_FILE  = "/data/veo_licenses.json"   # shared with Flow server
DT_FMT            = "%Y-%m-%dT%H:%M:%S"

# ── VEO License store (in-memory, persisted to VEO_LICENSE_FILE) ───────────────
# Structure: { "key": {"expires": "YYYY-MM-DD", "plan": "ultra"} }
# Devices:   { "key": "deviceId" }
# Exempt:    ["key", ...]   — device-lock exempt keys
_veo_licenses      = {}   # key -> {expires, plan}
_veo_devices       = {}   # key -> deviceId
_veo_exempt        = []   # list of keys exempt from device lock
_veo_lock          = threading.Lock()
_veo_file_lock     = threading.Lock()

# Message dedup — prevents same webhook firing twice
_processed_msg_ids     = set()
_processed_msg_lock    = threading.Lock()
_processed_msg_maxsize = 500

# Tracks which NUMBER_MENU slots were manually set via !setdesc
_custom_menu_slots = set()

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(funcName)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def sl_now():
    """Current datetime in Sri Lanka time."""
    return datetime.now(SL_TZ)

def sl_today():
    """Today's date in Sri Lanka time."""
    return sl_now().date()

# ═══════════════════════════════════════════════════════════════════════════════
# BOT STOP FLAG — set by !botstop to cancel all running background jobs
# ═══════════════════════════════════════════════════════════════════════════════
_bot_stop_event = threading.Event()

def bot_is_stopped():
    return _bot_stop_event.is_set()

def stop_bot():
    _bot_stop_event.set()
    logger.info("=== BOT STOP TRIGGERED — all background jobs will halt ===")

def resume_bot():
    _bot_stop_event.clear()

# ═══════════════════════════════════════════════════════════════════════════════
# BLOCKED NUMBERS — entirely silenced, no creds, no messages
# ═══════════════════════════════════════════════════════════════════════════════
_blocked_numbers      = set()
_blocked_numbers_lock = threading.Lock()

def is_blocked(phone):
    with _blocked_numbers_lock:
        return normalize_phone(phone) in _blocked_numbers

def block_number(phone):
    with _blocked_numbers_lock:
        _blocked_numbers.add(normalize_phone(phone))

def unblock_number(phone):
    with _blocked_numbers_lock:
        _blocked_numbers.discard(normalize_phone(phone))
    logger.info("=== BOT RESUMED ===")

# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE CREDENTIALS
# ═══════════════════════════════════════════════════════════════════════════════
def get_google_credentials():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        try:
            info = json.loads(creds_json)
            logger.info("Google credentials loaded from env var")
            return Credentials.from_service_account_info(info, scopes=scopes)
        except Exception as _exc6:
            logger.error("Failed to parse GOOGLE_CREDENTIALS: " + str(_exc6))
            raise
    creds_file = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    if os.path.exists(creds_file):
        logger.info("Google credentials loaded from file: " + creds_file)
        return Credentials.from_service_account_file(creds_file, scopes=scopes)
    raise FileNotFoundError("No Google credentials. Set GOOGLE_CREDENTIALS env var on Railway.")

# ═══════════════════════════════════════════════════════════════════════════════
# ULTRA CREDENTIALS — 3 slots, round-robin 1→2→3→1→2→3...
# Each slot: username, password, extra, enabled
# MEMORY ONLY — clears on redeploy (no file/volume needed)
# Set via !setcreds1/2/3 commands after each deploy
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# EXPIRY SENT TRACKING — memory only, resets on redeploy
# In-memory dict: { "chat_id_SOFTWARE": datetime_sent }
# 24h block prevents double-sending within same deploy
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory 24h block: { dedup_key: datetime_sent }
_expiry_sent_memory = {}
_expiry_memory_lock = threading.Lock()
EXPIRY_MEMORY_HOURS = 24

def expiry_already_sent_memory(dedup_key):
    with _expiry_memory_lock:
        if dedup_key not in _expiry_sent_memory:
            return False
        stored = _expiry_sent_memory[dedup_key]
        # Make timezone-aware if naive (e.g. restored from old memimport)
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=SL_TZ)
            _expiry_sent_memory[dedup_key] = stored
        elapsed = (sl_now() - stored).total_seconds()
        if elapsed < EXPIRY_MEMORY_HOURS * 3600:
            logger.info("Memory block: already sent " + dedup_key + " (" + str(int(elapsed/3600)) + "h ago)")
            return True
        return False

def mark_expiry_sent_memory(dedup_key):
    with _expiry_memory_lock:
        _expiry_sent_memory[dedup_key] = sl_now()

def expiry_already_sent(dedup_key):
    return expiry_already_sent_memory(dedup_key)

def mark_expiry_sent(dedup_key):
    mark_expiry_sent_memory(dedup_key)


# ═══════════════════════════════════════════════════════════════════════════════
# GREETING SENT TRACKING — in-memory 24h block
# ═══════════════════════════════════════════════════════════════════════════════
_greeting_sent_memory = {}
_greeting_memory_lock = threading.Lock()

def greeting_sent_recently(chat_id):
    """True if greeting was sent to this chat_id in the last 48h (memory only)."""
    with _greeting_memory_lock:
        if chat_id not in _greeting_sent_memory:
            return False
        stored = _greeting_sent_memory[chat_id]
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=SL_TZ)
            _greeting_sent_memory[chat_id] = stored
        elapsed = (sl_now() - stored).total_seconds()
        return elapsed < 12 * 3600

def mark_greeting_sent(chat_id):
    with _greeting_memory_lock:
        _greeting_sent_memory[chat_id] = sl_now()

# ═══════════════════════════════════════════════════════════════════════════════
# ULTRA ACTIVATED — code word bypass
# Bot reads the actual WhatsApp chat history (last 50 messages) to check if
# admin sent "Ultra Activated" within the last 48 hours. No memory, no file —
# survives redeployments because it reads directly from WhatsApp.
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# WASENDERAPI — send helpers
# Docs: https://wasenderapi.com/api-docs/messages/send-text-message
# Auth: Bearer token in Authorization header
# Phone format: E.164 e.g. +94778315058
# Webhook set in WaSenderAPI dashboard (no auto-register needed)
# ═══════════════════════════════════════════════════════════════════════════════
WASENDER_BASE_URL = "https://www.wasenderapi.com/api"


def _wasender_headers():
    return {
        "Authorization": "Bearer " + WASENDER_API_KEY,
        "Content-Type":  "application/json",
    }

def send_message(chat_id, message):
    """Send text message. Returns response dict on success, None on failure."""
    try:
        # Typing delay simulation — skip for admin numbers to keep commands instant
        if chat_id.lstrip("+") not in ADMIN_NUMBERS:
            _char_count  = len(message) if message else 0
            _type_delay  = min(1.5 + (_char_count / 120), 5.0)
            _type_delay += random.uniform(0.3, 1.2)
            time.sleep(_type_delay)
        r = requests.post(
            WASENDER_BASE_URL + "/send-message",
            headers=_wasender_headers(),
            json={"to": chat_id, "text": message},
            timeout=15
        )
        logger.info("sendMessage → " + chat_id + " | " + str(r.status_code))
        if r.status_code not in (200, 201):
            logger.error("sendMessage failed: " + str(r.status_code) + " " + r.text[:200])
            if r.status_code == 422:
                raise ValueError("INVALID_NUMBER:" + chat_id)
            return None
        return r.json()
    except ValueError:
        raise
    except Exception as _exc9:
        logger.error("sendMessage error to " + chat_id + ": " + str(_exc9))
        return None


def send_message_with_retry(chat_id, message, retries=3, delay=15):
    """Send message with up to 3 retries. Notifies admin on all-fail."""
    last_error = "unknown"
    for attempt in range(1, retries + 1):
        try:
            result = send_message(chat_id, message)
            if result:
                return result
            last_error = "API returned failure (non-200)"
        except ValueError as ve:
            last_error = str(ve)
            break  # invalid number — stop immediately
        except Exception as ex:
            last_error = str(ex)
        if attempt < retries:
            wait = delay + random.uniform(2, 5)
            logger.warning("sendMessage attempt " + str(attempt) + " failed for " + chat_id + " — retrying in " + str(round(wait, 1)) + "s")
            time.sleep(wait)
    logger.error("sendMessage FAILED all " + str(retries) + " attempts for " + chat_id)
    _notify_admins_delivery_failed(chat_id, message, last_error)
    return None


def send_message_until_sent(chat_id, message, on_success=None, on_failure=None):
    """
    Retries in a background thread — max 3 attempts.
    Delays: 5s, 10s between retries.
    On final failure: notifies all admins with reason and calls on_failure().
    """
    MAX_ATTEMPTS = 3
    RETRY_DELAYS = [15, 30]

    def _worker():
        last_error = "unknown"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = send_message(chat_id, message)
                if result:
                    logger.info("✅ Message delivered on attempt " + str(attempt) + " to " + chat_id)
                    if on_success:
                        try: on_success()
                        except Exception as _exc10: logger.error("on_success error: " + str(_exc10))
                    return
                last_error = "API returned failure (non-200)"
            except ValueError as ve:
                last_error = str(ve)
                logger.error("Stopping — invalid number: " + chat_id)
                break
            except Exception as ex:
                last_error = str(ex)

            if attempt < MAX_ATTEMPTS:
                wait = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)] + random.uniform(1, 4)
                logger.warning("⚠️ Message failed attempt " + str(attempt) + "/" + str(MAX_ATTEMPTS) +
                               " for " + chat_id + " — retrying in " + str(round(wait, 1)) + "s")
                time.sleep(wait)

        # All attempts exhausted
        logger.error("❌ Message FAILED all " + str(MAX_ATTEMPTS) + " attempts to " + chat_id)
        _notify_admins_delivery_failed(chat_id, message, last_error)
        if on_failure:
            try: on_failure()
            except Exception as ex: logger.error("on_failure error: " + str(ex))

    threading.Thread(target=_worker, daemon=True).start()


def _notify_admins_delivery_failed(chat_id, message, reason):
    logger.error("Delivery failed for " + chat_id + " — " + reason)

def _notify_admin_failure(context, error_str):
    logger.error("Bot error [" + str(context) + "]: " + str(error_str)[:300])

def send_file_text(chat_id, content, filename, caption="", is_admin_test=False):
    """Upload .txt file via multipart/form-data so WhatsApp receives it as proper text file."""
    _wait_msg = "I'm setting up your account. Please wait 🙏"
    try:
        _file_bytes = content.encode("utf-8")
        # multipart/form-data upload — WaSenderAPI stores file with correct .txt extension
        upload_r = requests.post(
            WASENDER_BASE_URL + "/upload",
            headers={
                "Authorization": "Bearer " + os.environ.get("WASENDER_API_KEY", ""),
            },
            files={
                "file": (filename, _file_bytes, "text/plain"),
            },
            timeout=30
        )
        if upload_r.status_code not in (200, 201):
            logger.error("send_file_text upload failed: " + str(upload_r.status_code) + " " + upload_r.text[:300])
            if not is_admin_test:
                send_message(chat_id, _wait_msg)
            else:
                send_message(chat_id, "❌ Upload failed: " + str(upload_r.status_code) + " " + upload_r.text[:200])
            return False

        upload_data = upload_r.json()
        media_url = (
            upload_data.get("publicUrl") or
            upload_data.get("url") or
            upload_data.get("mediaUrl") or
            upload_data.get("fileUrl") or
            (upload_data.get("data") or {}).get("url") or
            (upload_data.get("data") or {}).get("publicUrl") or
            (upload_data.get("data") or {}).get("mediaUrl")
        )
        if not media_url:
            logger.error("send_file_text: no URL in upload response: " + str(upload_data)[:300])
            if not is_admin_test:
                send_message(chat_id, _wait_msg)
            else:
                send_message(chat_id, "❌ No URL in upload response: " + str(upload_data)[:200])
            return False

        logger.info("send_file_text: uploaded to " + media_url)

        send_r = requests.post(
            WASENDER_BASE_URL + "/send-message",
            headers=_wasender_headers(),
            json={
                "to":          chat_id,
                "documentUrl": media_url,
                "fileName":    filename,
            },
            timeout=20
        )
        if send_r.status_code in (200, 201):
            logger.info("send_file_text (document) → " + chat_id + " | " + filename)
            return True
        else:
            logger.error("send_file_text send failed: " + str(send_r.status_code) + " " + send_r.text[:300])
            if not is_admin_test:
                send_message(chat_id, _wait_msg)
            else:
                send_message(chat_id, "❌ Send failed: " + str(send_r.status_code) + " " + send_r.text[:200])
            return False
    except Exception as _exc13:
        logger.error("send_file_text error: " + str(_exc13))
        if not is_admin_test:
            send_message(chat_id, _wait_msg)
        else:
            send_message(chat_id, "❌ Error: " + str(_exc13)[:200])
        return False
        return False


# ═══════════════════════════════════════════════════════════════════════════════
_cooldowns      = {}
_cooldown_lock  = threading.Lock()

def is_on_cooldown(chat_id):
    if chat_id.lstrip("+") in ADMIN_NUMBERS:
        return False
    with _cooldown_lock:
        if chat_id not in _cooldowns:
            return False
        ts, secs = _cooldowns[chat_id]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=SL_TZ)
            _cooldowns[chat_id] = (ts, secs)
        elapsed  = (sl_now() - ts).total_seconds()
        if elapsed < secs:
            logger.info("Cooldown: " + chat_id + " (" + str(int(secs - elapsed)) + "s left)")
            return True
        return False

def set_cooldown(chat_id, seconds=COOLDOWN_SECONDS):
    with _cooldown_lock:
        _cooldowns[chat_id] = (sl_now(), seconds)
    logger.info("Cooldown set: " + chat_id + " (" + str(seconds) + "s)")

# ═══════════════════════════════════════════════════════════════════════════════
# SOFTWARE DESCRIPTION COOLDOWN — 72h per chat per slot
# Prevents re-sending the same software description to the same customer
# Key: (chat_id, slot_num)  Value: datetime sent
# ═══════════════════════════════════════════════════════════════════════════════
DESC_COOLDOWN_HOURS = 12
_desc_cooldown      = {}   # { (chat_id, slot_num): datetime }
_desc_cooldown_lock = threading.Lock()

def desc_on_cooldown(chat_id, slot_num):
    key = (chat_id, slot_num)
    with _desc_cooldown_lock:
        if key not in _desc_cooldown:
            return False
        _dt = _desc_cooldown[key]
        if _dt.tzinfo is None:
            _dt = _dt.replace(tzinfo=SL_TZ)
            _desc_cooldown[key] = _dt
        elapsed = (sl_now() - _dt).total_seconds()
        if elapsed < DESC_COOLDOWN_HOURS * 3600:
            logger.info("Desc cooldown: " + chat_id + " slot=" + str(slot_num) +
                        " (" + str(int((DESC_COOLDOWN_HOURS * 3600 - elapsed) / 3600)) + "h left)")
            return True
        return False

def set_desc_cooldown(chat_id, slot_num):
    key = (chat_id, slot_num)
    with _desc_cooldown_lock:
        _desc_cooldown[key] = sl_now()
    logger.info("Desc cooldown set: " + chat_id + " slot=" + str(slot_num))




# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENT CREDS SENT — tracks which slot usernames were sent to each customer
# Survives deployments via volume. Resets per-slot when new creds are set.
# Structure: { "+94XXXXXXXXX": ["username1", "username2"] }

# ═══════════════════════════════════════════════════════════════════════════════
_save_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════════════════════
# VEO LICENSE PERSISTENCE — /data/veo_licenses.json (shared with Flow server)
# ═══════════════════════════════════════════════════════════════════════════════

def _veo_save():
    """Atomically write veo_licenses.json to /data volume."""
    try:
        with _veo_file_lock:
            os.makedirs("/data", exist_ok=True)
            with _veo_lock:
                payload = {
                    "licenses": dict(_veo_licenses),
                    "devices":  dict(_veo_devices),
                    "exempt":   list(_veo_exempt),
                }
            tmp = VEO_LICENSE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, VEO_LICENSE_FILE)
        logger.info("VEO licenses saved to " + VEO_LICENSE_FILE)
    except Exception as _e:
        logger.error("veo_save error: " + str(_e))

def _veo_load():
    """Load veo_licenses.json from /data on startup."""
    global _veo_licenses, _veo_devices, _veo_exempt
    try:
        if not os.path.exists(VEO_LICENSE_FILE):
            logger.info("No VEO license file found — starting fresh")
            return
        with open(VEO_LICENSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _veo_lock:
            _veo_licenses = data.get("licenses", {})
            _veo_devices  = data.get("devices",  {})
            _veo_exempt   = data.get("exempt",   [])
        logger.info("VEO licenses loaded: " + str(len(_veo_licenses)) + " keys")
    except Exception as _e:
        logger.error("veo_load error: " + str(_e))

def _veo_today():
    from datetime import date
    return date.today()

def build_snapshot():
    """Build the full memory snapshot dict (same format as memexport)."""
    with _cooldown_lock:
        cooldowns_serial = {
            k: [v[0].strftime(DT_FMT), v[1]]
            for k, v in _cooldowns.items()
        }
    with _desc_cooldown_lock:
        desc_cd_serial = {
            k[0] + "|" + str(k[1]): v.strftime(DT_FMT)
            for k, v in _desc_cooldown.items()
        }
    with _expiry_memory_lock:
        expiry_serial = {
            k: v.strftime(DT_FMT)
            for k, v in _expiry_sent_memory.items()
        }
    with _greeting_memory_lock:
        greeting_serial = {
            k: v.strftime(DT_FMT)
            for k, v in _greeting_sent_memory.items()
        }
    with _blocked_numbers_lock:
        blocked_serial = list(_blocked_numbers)
    custom_descs = {}
    for n in _custom_menu_slots:
        msg = NUMBER_MENU.get(n)
        if msg is not None:
            custom_descs[str(n)] = msg
    with _greeting_store_lock:
        greeting_store_serial = dict(_greeting_store)
    with _creds_slot1_lock:
        creds_slot1_serial = dict(_creds_slot1)
    with _creds_sent_phones_lock:
        creds_reply_log_serial = dict(_creds_sent_phones)
    with _expiry_msg_sent_lock:
        expiry_msg_sent_serial = list(_expiry_msg_sent)
    return {
        "_v":                  1,
        "blocked_numbers":     blocked_serial,
        "greeting_store":      greeting_store_serial,
        "creds_slot1":         creds_slot1_serial,
        "creds_reply_log":     creds_reply_log_serial,
        "expiry_msg_sent":     expiry_msg_sent_serial,
        "number_menu":         custom_descs,
        "cooldowns":           cooldowns_serial,
        "desc_cooldown":       desc_cd_serial,
        "expiry_sent":         expiry_serial,
        "greeting_sent":       greeting_serial,
    }

def save_memory():
    """Save all memory to volume file. Silent on failure."""
    try:
        with _save_lock:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            snapshot = build_snapshot()
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA_FILE)  # atomic write
        logger.info("Memory saved to " + DATA_FILE)
    except Exception as _exc14:
        logger.error("save_memory error: " + str(_exc14))
        _notify_admin_failure("save_memory", str(_exc14))

def restore_snapshot(snapshot):
    """Restore all memory stores from a snapshot dict. Shared by load_memory and !memimport."""
    restored = []
    if "number_menu" in snapshot:
        for k, v in snapshot["number_menu"].items():
            try:
                slot_n = int(k)
                NUMBER_MENU[slot_n] = v
                _custom_menu_slots.add(slot_n)
            except:
                pass
        restored.append("number_menu")
    if "cooldowns" in snapshot:
        with _cooldown_lock:
            _cooldowns.clear()
            for k, v in snapshot["cooldowns"].items():
                try:
                    _cooldowns[k] = (datetime.strptime(v[0], DT_FMT).replace(tzinfo=SL_TZ), v[1])
                except:
                    pass
        restored.append("cooldowns")
    if "desc_cooldown" in snapshot:
        with _desc_cooldown_lock:
            _desc_cooldown.clear()
            for k, v in snapshot["desc_cooldown"].items():
                try:
                    chat, slot = k.rsplit("|", 1)
                    _desc_cooldown[(chat, int(slot))] = datetime.strptime(v, DT_FMT).replace(tzinfo=SL_TZ)
                except:
                    pass
        restored.append("desc_cooldown")
    if "expiry_sent" in snapshot:
        with _expiry_memory_lock:
            _expiry_sent_memory.clear()
            for k, v in snapshot["expiry_sent"].items():
                try:
                    dt = datetime.strptime(v, DT_FMT)
                    _expiry_sent_memory[k] = dt.replace(tzinfo=SL_TZ)
                except:
                    pass
        restored.append("expiry_sent")
    if "greeting_sent" in snapshot:
        with _greeting_memory_lock:
            _greeting_sent_memory.clear()
            for k, v in snapshot["greeting_sent"].items():
                try:
                    dt = datetime.strptime(v, DT_FMT)
                    _greeting_sent_memory[k] = dt.replace(tzinfo=SL_TZ)
                except:
                    pass
        restored.append("greeting_sent")
    if "greeting_store" in snapshot:
        with _greeting_store_lock:
            _greeting_store.update(snapshot["greeting_store"])
        restored.append("greeting_store")
    if "creds_slot1" in snapshot:
        with _creds_slot1_lock:
            _creds_slot1.update(snapshot["creds_slot1"])
        restored.append("creds_slot1")
    if "creds_reply_log" in snapshot:
        with _creds_sent_phones_lock:
            _creds_sent_phones.update(snapshot["creds_reply_log"] if isinstance(snapshot["creds_reply_log"], dict) else {p: 0 for p in snapshot["creds_reply_log"]})
        restored.append("creds_reply_log")
    if "expiry_msg_sent" in snapshot:
        with _expiry_msg_sent_lock:
            _expiry_msg_sent.update(snapshot["expiry_msg_sent"])
        restored.append("expiry_msg_sent")
    return restored

def load_memory():
    """Load memory from volume file on startup."""
    try:
        if not os.path.exists(DATA_FILE):
            logger.info("No memory file found at " + DATA_FILE + " — starting fresh")
            return
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        restored = restore_snapshot(snapshot)
        logger.info("Memory loaded from " + DATA_FILE + " — restored: " + ", ".join(restored))
    except Exception as _exc15:
        logger.error("load_memory error: " + str(_exc15))

def _auto_save_loop():
    """Background thread: saves memory every 60 seconds."""
    while True:
        time.sleep(60)
        save_memory()

# ═══════════════════════════════════════════════════════════════════════════════
# SHEET CACHE — refreshes every 5 minutes
# ═══════════════════════════════════════════════════════════════════════════════
_sheet_cache = {"rows": None, "updated": None}
_sheet_lock  = threading.Lock()

def get_sheet_rows(force=False):
    with _sheet_lock:
        now = sl_now()
        if not force and _sheet_cache["rows"] is not None and _sheet_cache["updated"] is not None:
            age = (now - _sheet_cache["updated"]).total_seconds()
            if age < CACHE_SECONDS:
                logger.info("Sheet cache hit (age: " + str(int(age)) + "s)")
                return _sheet_cache["rows"]
        creds  = get_google_credentials()
        client = gspread.authorize(creds)
        ss     = client.open_by_key(GOOGLE_SHEET_ID)
        try:
            sheet = ss.worksheet(SHEET_NAME)
        except Exception:
            sheet = ss.get_worksheet(0)
        rows = sheet.get_all_values()
        _sheet_cache["rows"]    = rows
        _sheet_cache["updated"] = now
        logger.info("Sheet refreshed: " + str(len(rows)) + " rows")
        return rows

def invalidate_sheet_cache():
    with _sheet_lock:
        _sheet_cache["rows"]    = None
        _sheet_cache["updated"] = None

# ═══════════════════════════════════════════════════════════════════════════════
# DATE HELPERS — all dates use Sri Lanka time
# ═══════════════════════════════════════════════════════════════════════════════
def parse_date(date_str):
    formats = [
        "%m/%d/%Y", "%m/%d/%y",
        "%Y-%m-%d",
        "%d/%m/%Y", "%d-%m-%Y",
        "%d-%B-%y", "%d-%B-%Y",
        "%d-%b-%y", "%d-%b-%Y",
        "%d %B %Y", "%d %b %Y", "%d %b %y"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except:
            continue
    pass  # silently skip unparseable dates
    return None

def normalize_phone(phone):
    """Strip formatting, keep full international number. Returns digits only."""
    p = str(phone).replace("+","").replace(" ","").replace("-","").replace("@c.us","").replace("@s.whatsapp.net","").strip()
    if p.startswith("94") and len(p) == 11:
        p = p[2:]
    return p

def to_chat_id(phone):
    """Any phone → WaSenderAPI E.164 format e.g. +94778315058 or +966542852072"""
    p = str(phone).replace("+","").replace(" ","").replace("-","").replace("@c.us","").replace("@s.whatsapp.net","").strip()
    if len(p) == 9 and p.startswith("7"):
        p = "94" + p
    elif len(p) == 10 and p.startswith("07"):
        p = "94" + p[1:]
    return "+" + p

# ═══════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION CHECKER (Ultra)
# ═══════════════════════════════════════════════════════════════════════════════
def check_subscription(phone_number):
    try:
        rows  = get_sheet_rows()
        local = normalize_phone(phone_number)
        today = sl_today()
        results = []
        for i, row in enumerate(rows):
            if i == 0 or len(row) < 3:
                continue
            if normalize_phone(str(row[1])) != local:
                continue
            row_sub = str(row[2]).strip()
            if not row_sub.upper().startswith("ULT"):
                continue
            purchase_date = parse_date(str(row[0]).strip())
            if not purchase_date:
                continue
            # Column D flag — if EXPIRED written here, treat as inactive regardless of date
            col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
            if col_d == "EXPIRED":
                logger.info("check_subscription: row has EXPIRED flag — treating as inactive")
                _dur_v, _dur_u = extract_duration_from_subscription(row_sub)
                if _dur_v and _dur_u:
                    _exp_dt = calculate_expiry_date_from_duration(purchase_date, _dur_v, _dur_u)
                    _exp_str = _exp_dt.strftime("%B %d, %Y") if _exp_dt else (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).strftime("%B %d, %Y")
                else:
                    _exp_str = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).strftime("%B %d, %Y")
                results.append({
                    "purchase_date": purchase_date.strftime("%B %d, %Y"),
                    "expiry_date":   _exp_str,
                    "active":        False,
                    "purchase_dt":   purchase_date,
                })
                continue
            # Extract duration — supports 2W, 3M, 14D etc.
            _dur_v, _dur_u = extract_duration_from_subscription(row_sub)
            if _dur_v and _dur_u:
                _exp_dt  = calculate_expiry_date_from_duration(purchase_date, _dur_v, _dur_u)
                expiry_date = _exp_dt if _exp_dt else purchase_date + timedelta(days=SUBSCRIPTION_DAYS)
            else:
                expiry_date = purchase_date + timedelta(days=SUBSCRIPTION_DAYS)
            notify_time  = parse_ultra_notify_time(row_sub)
            exp_h        = notify_time[0] if notify_time else 23
            exp_m        = notify_time[1] if notify_time else 59
            expiry_dt_full = expiry_date.replace(hour=exp_h, minute=exp_m, second=59, tzinfo=SL_TZ)
            expiry_str   = expiry_date.strftime("%B %d, %Y") + " " + expiry_dt_full.strftime("%I:%M %p")
            results.append({
                "purchase_date": purchase_date.strftime("%B %d, %Y"),
                "expiry_date":   expiry_str,
                "active":        sl_now() < expiry_dt_full,
                "purchase_dt":   purchase_date,
            })
        logger.info("check_subscription: " + local + " → " + str(len(results)) + " ULT rows")
        # If multiple rows, only keep latest active — prevents old row showing wrong expiry
        if len(results) > 1:
            active = [r for r in results if r["active"]]
            if active:
                results = [sorted(active, key=lambda r: r["purchase_dt"])[-1]]
            else:
                results = [sorted(results, key=lambda r: r["purchase_dt"])[-1]]
        return results
    except Exception as _exc16:
        logger.error("check_subscription error: " + str(_exc16))
        logger.error(traceback.format_exc())
        _notify_admin_failure("check_subscription", str(_exc16))
        return None

def has_renewed(phone, software_key, original_purchase_date, rows=None):
    """True if a newer purchase of the same software exists."""
    try:
        if rows is None:
            rows = get_sheet_rows()
        local = normalize_phone(phone)
        for i, row in enumerate(rows):
            if i == 0 or len(row) < 3:
                continue
            if normalize_phone(str(row[1])) != local:
                continue
            row_sub = str(row[2]).strip()
            if software_key == "ULT":
                if not row_sub.upper().startswith("ULT"):
                    continue
            else:
                if get_general_software_key(row_sub) != software_key:
                    continue
            row_purchase = parse_date(str(row[0]).strip())
            if row_purchase and row_purchase.date() > original_purchase_date.date():
                logger.info("Renewal found: " + local + " | " + software_key)
                return True
        return False
    except Exception as _exc17:
        logger.error("has_renewed error: " + str(_exc17))
        return False

# ═══════════════════════════════════════════════════════════════════════════════
# CHAT HISTORY — in-memory cache (WaSenderAPI has no getChatHistory endpoint)
# Messages are recorded as they arrive via webhook and when bot sends them.
# Structure: { chat_id: [ {"text": str, "timestamp": datetime, "type": str} ] }
# Max 200 entries per chat, auto-pruned to 100 when full.
# ═══════════════════════════════════════════════════════════════════════════════
_chat_history_cache: dict = {}
_chat_history_lock  = threading.Lock()
CHAT_HISTORY_MAX    = 200
CHAT_HISTORY_TRIM   = 100

def _record_chat_message(chat_id, text, msg_type="incoming"):
    """Record a message (incoming or outgoing) into the in-memory cache."""
    if not chat_id or not text:
        return
    entry = {"text": str(text), "timestamp": sl_now(), "type": msg_type}
    with _chat_history_lock:
        if chat_id not in _chat_history_cache:
            _chat_history_cache[chat_id] = []
        hist = _chat_history_cache[chat_id]
        hist.append(entry)
        if len(hist) > CHAT_HISTORY_MAX:
            _chat_history_cache[chat_id] = hist[-CHAT_HISTORY_TRIM:]

def get_chat_history_messages(chat_id, count=100):
    """
    Returns list of dicts: { "text": str, "timestamp": datetime (SL time), "type": str }
    Sourced from in-memory cache recorded during this deployment.
    """
    with _chat_history_lock:
        hist = _chat_history_cache.get(chat_id, [])
        return list(hist[-count:])  # newest last

def get_chat_history_texts(chat_id, count=100):
    """Simple text-only list."""
    return [m["text"] for m in get_chat_history_messages(chat_id, count)]

def ultra_expired_already_sent_history(chat_id):
    _expiry_phrases = (
        "ultra subscription has expired",
        "veo 3 ultra",
        "subscription has expired",
        "account expired",
        "ultra expired",
        "expired",
        "your subscription expired",
        "plan expired",
        "account has expired",
    )
    for text in get_chat_history_texts(chat_id, 100):
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in _expiry_phrases):
            logger.info("Ultra expired already in history: " + chat_id)
            return True
    return False

def greeting_already_sent_history(chat_id):
    """True if the greeting message was already sent in this chat's history."""
    msgs = get_chat_history_messages(chat_id, 50)
    # If no history at all — definitely not sent
    if not msgs:
        return False
    # Match phrases that uniquely identify the greeting (default or custom)
    # Default greeting contains the Sinhala intro + the AI Tools list + trustpilot URL
    for text in (m.get("text","") for m in msgs):
        if ("ඔයාට ඕන Software එකේ නම එවන්න" in text and "🔮 AI Tools" in text) or \
           "trustpilot.com/review/signaturedigital.net" in text:
            return True
    return False

# Tracks when admin last MANUALLY replied to a customer (not bot-sent messages)
# { normalized_phone: datetime }
_admin_replied_at      = {}
_admin_replied_at_lock = threading.Lock()

def _record_admin_reply(phone):
    with _admin_replied_at_lock:
        _admin_replied_at[normalize_phone(phone)] = sl_now()

def _admin_messaged_within(chat_id, seconds):
    """True if admin manually replied to this customer within the last N seconds."""
    phone = normalize_phone(chat_id.lstrip("+"))
    with _admin_replied_at_lock:
        ts = _admin_replied_at.get(phone)
    if ts is None:
        return False
    return (sl_now() - ts).total_seconds() < seconds

# ═══════════════════════════════════════════════════════════════════════════════
# SKIP KEYWORDS & SOFTWARE KEY MAPPER
# ═══════════════════════════════════════════════════════════════════════════════
EXPIRY_SKIP_KEYWORDS = [
    "1 YEAR", "1 YR", "1YR", "1YEAR",
    "LINKEDIN", "PHOTOSHOP", " PS ", "ACROBAT",
    "CLAUDE", "DEC 24", "EDU EMAIL"
]

def should_skip_sub(sub_name):
    upper = sub_name.upper().strip()
    if "@" in sub_name:
        return True
    for kw in EXPIRY_SKIP_KEYWORDS:
        if kw in upper:
            return True
    return False

import re as _re_sub

def get_sub_duration_days(sub_name):
    """
    Returns the number of days for a subscription based on its name.
    - NW pattern (2W, 3W) → N * 7 days
    - ND pattern (14D, 21D) → N days
    - NM pattern (2M, 3M) → N months converted to days (approx 30 days/month)
    - CANVA / DUOLINGO / LIGHTROOM / OFFICE / 365 → 365 days (1 year)
    - everything else → 7 days (default)
    """
    import re as _re_dur
    upper = sub_name.upper().strip()
    # 1-year plans
    if any(kw in upper for kw in ["CANVA", "DUOLINGO", "LIGHTROOM", "OFFICE", "365"]):
        return 365
    # Weeks: NW
    mw = _re_dur.search(r'(\d+)\s*W\b', upper)
    if mw:
        return int(mw.group(1)) * 7
    # Days: ND
    md = _re_dur.search(r'(\d+)\s*D\b', upper)
    if md:
        return int(md.group(1))
    # Months: NM
    mm = _re_dur.search(r'(\d+)\s*M\b', upper)
    if mm:
        return int(mm.group(1)) * 30
    return SUBSCRIPTION_DAYS  # default 7 days

def get_sub_months(sub_name):
    """Legacy — returns months. Used for backward compat."""
    upper = sub_name.upper().strip()
    if any(kw in upper for kw in ["CANVA", "DUOLINGO", "LIGHTROOM", "OFFICE", "365"]):
        return 12
    m = _re_sub.search(r'(\d+)\s*M\b', upper)
    if m:
        return int(m.group(1))
    return 1

def add_n_months(dt, n):
    """Add n months to a datetime. Clamps to last day of month."""
    month = dt.month + n
    year  = dt.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))

def get_general_software_key(sub_name):
    upper = sub_name.upper().strip()
    if should_skip_sub(sub_name):
        return None
    if upper.startswith("PVT") or upper.startswith("PRIVATE"):      return "ULTRA_PRIVATE"
    if upper.startswith("ULT"):                                      return "ULTRA"
    if upper.startswith("CHAT") or upper.startswith("GPT") or upper == "CHAT" or upper == "CHATGPT":  return "CHATGPT"
    if upper.startswith("CAP"):                                   return "CAPCUT"
    if upper.startswith("VEO"):                                   return "VEO"
    if upper.startswith("ADOBE"):                                 return "ADOBE"
    if upper.startswith("ENV"):                                   return "ENVATO"
    if upper.startswith("GEM"):                                   return "GEMINI"
    if upper.startswith("FRPK") or upper.startswith("FREEP"):    return "FREEPIK"
    if upper.startswith("GAMMA"):                                 return "GAMMA"
    if upper.startswith("PERP") or upper.startswith("PPLY"):     return "PERPLEXITY"
    if upper.startswith("QUILL"):                                 return "QUILLBOT"
    if upper.startswith("KAHOOT"):                                return "KAHOOT"
    if upper.startswith("GRAM") or upper.startswith("GRMR"):     return "GRAMMARLY"
    if upper.startswith("GRO") or upper.startswith("GROCK") or upper.startswith("SUPERGROK") or "GROK" in upper: return "GROK"
    if "CANVA" in upper:                                          return "CANVA"
    if "DUOLINGO" in upper:                                       return "DUOLINGO"
    if "LIGHTROOM" in upper:                                      return "LIGHTROOM"
    if "OFFICE" in upper or "365" in upper:                    return "OFFICE"
    # Strip trailing NM pattern and retry (e.g. CAP3M→CAP, GPT3M→GPT, CHAT 6M→CHAT)
    stripped = _re_sub.sub(r'\s*\d+\s*M\b', '', upper).strip()
    if stripped and stripped != upper:
        return get_general_software_key(stripped)
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# ULTRA EXPIRED DESCRIPTION — appended to all Ultra expiry messages
# ═══════════════════════════════════════════════════════════════════════════════
ULTRA_EXPIRED_DESC = (
    "🔥 *VEO 3 Ultra - Shared Account දවස් 7ක් 1950ක් පමණයි!*\n\n"
    "🔥 *UNLIMITED AI CREDITS*\n"
    "(Credits ඉවර වුනොත්, අලුත් Account එකක් දෙනවා)\n\n"
    "✅ *7 Days Subscription & Warranty*\n"
    "▫️Team Account | Unlimited AI Credits - රු. 1950/-\n"
    "▫️Private Account | 25,000 AI Credits - රු. 3350/-\n\n"
    "✅ *14 Days Subscription & Warranty*\n"
    "▫️Team Account | Unlimited AI Credits - රු. 3750/-\n"
    "▫️Private Account | 25,000 AI Credits - රු. 5750/-\n\n"
    "🤝 *Account ඒක suspend වුනොත් අලුත් Account එකක් Replace කරලා දෙනවා.*\n\n"
    "✅ *No Failed Generation Issue*\n"
    "✅ *All models working with 4K Outputs*\n"
    "✅ *Flow AI & Whisk AI*\n"
    "✅ *Latest Gemini 3 Ultra*\n"
    "🗓️ *7 Days Warranty for account & credits*\n\n"
    "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
    "8007 327 854\n"
    "Ahmed Bin Fawzer\n"
    "Commercial Bank\n"
    "Katugastota Branch"
)

# ═══════════════════════════════════════════════════════════════════════════════
# RENEWAL MESSAGES
# ═══════════════════════════════════════════════════════════════════════════════
RENEWAL_MESSAGES = {
    "CHATGPT": (
        "ChatGPT $20 Plus Unlimited මාසයක් රු. 1750ක් පමණයි!\n\n"
        "✅ Unlimited Access to GPT-4!\n"
        "💬 Unlimited Messaging Quota!\n"
        "⬆️ Unlimited File Uploads & Analysis\n"
        "🖼️ Unlimited Image Generation\n"
        "🎥 Unlimited Video Generation (SORA AI)\n\n"
        "✔️ GPT-4 Team Shared Plans\n\n"
        "> 1 Month - 1750 LKR\n"
        "> 3 Months - 4750 LKR\n"
        "> 6 Months - 8750 LKR\n\n"
        "✔️ GPT-5.2 Private Plan - Own Email\n"
        "(ඔයාගේ Email එකටම Activate වෙනවා)\n\n"
        "> 1 Month - 3750 LKR (6250 LKR)\n\n"
        "✔️ GPT-5.2 Private Plan - Private Account\n"
        "(අපි වෙනම Private Account එකක් දෙනවා)\n\n"
        "> 1 Month - 2750 LKR (6250 LKR)\n\n"
        "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "CAPCUT": (
        "✅ 𝗖𝗔𝗣𝗖𝗨𝗧 𝗣𝗥𝗢 - මාසයක් රු. 𝟭𝟳𝟱𝟬ක් පමණයි!\n\n"
        "• ✅ *PRO Features Fully Unlocked*\n"
        "• 🔑 *Private Account | 100% Privacy*\n"
        "• 📲 *Supports Android/iPhone/Mac/Windows*\n"
        "• 📺 *No Advertisements or Watermarks*\n"
        "• 🏝️ *10k+ Templates, 80+ Pro Tools, 4K Exports*\n"
        "• 🎙️ *Unlimited Text to Audio & 350+ Voice Filters*\n"
        "• ☁️ *2 TB Team Shared Cloud Storage*\n\n"
        "*1 Device Access - 30 Days Plan*\n"
        "> *1 Month - රු. 1750*\n"
        "> *3 Months - රු. 4750*\n"
        "> *6 Months - රු. 9450*\n\n"
        "*2 Device Access - 30 Days Plan*\n"
        "> *1 Month - රු. 1950*\n"
        "> *3 Months - රු. 5750*\n"
        "> *6 Months - රු. 10,750*\n\n"
        "Payment කරලා receipt එක එවන්න. විනාඩි 30ක් ඇතුලත PRO එක හදලා දෙන්නම්.\n\n"
        "8007 327 854\nM F Ahamed\nKatugastota Branch\nCommerical Bank"
    ),
    "GEMINI": (
        "*GEMINI 3 PRO මාසයක් - රු. 1750 ක් පමණයි!*\n\n"
        "🔥 *Powerful Gemini 3 Pro*\n"
        "♾️ *Unlimited Messaging Quota*\n"
        "♾️ *Unlimited Image Generations*\n"
        "♾️ *Unlimited File Uploads & Analysis*\n"
        "⬆️ *50x higher Deep Thinking Capacity*\n"
        "🍌 *Unlimited NanoBanana Pro*\n"
        "📦 *2 TB Google Drive Storage*\n"
        "🌐 *NoteBook LM*\n\n"
        "💌 *Activated on your own email*\n\n"
        "*Private Plans*\n\n"
        "> 1 Month - 1750 LKR\n"
        "> 3 Months - 4750 LKR\n"
        "> 6 Months - 7750 LKR\n\n"
        "✅ Payment කරලා receipt සහ ඔයාගේ email එක එවන්න. දැන්ම Activate කරලා දෙන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "ENVATO": (
        "*✅ Envato Elements - Stock Downloader Subscription*\n\n"
        "⬇️ *100 Downloads Per Day*\n"
        "🔥 *3,000 Total Downloads*\n"
        "📅 *30 Days Subscription*\n"
        "✔️ *2450 LKR*\n\n"
        "💯 *Any type of file from Envato Elements can be downloaded with official license.*\n\n"
        "> *OTHER PLANS*\n\n"
        "50 Daily - 1950 LKR\n"
        "23 Daily - 1550 LKR\n\n"
        "✅ Payment කරලා receipt සහ ඔයාගේ email එක එවන්න. දැන්ම Activate කරලා දෙන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "FREEPIK": (
        "*✅ Freepik - Stock Downloader Subscription*\n\n"
        "⬇️ *30 Downloads Per Day*\n"
        "🔥 *900 Total Downloads*\n"
        "📅 *30 Days Subscription*\n\n"
        "*2450 LKR*\n\n"
        "> Other plans\n\n"
        "10 Downloads Daily - 1750 LKR\n\n"
        "💯 *Any type of file from Freepik can be downloaded with official license.*\n\n"
        "✅ Payment කරලා receipt සහ ඔයාගේ email එක එවන්න. දැන්ම Activate කරලා දෙන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "PERPLEXITY": (
        "*Perplexity AI $20 Pro එක රු. 1750ක් පමණයි!*\n\n"
        "🔍 *Advanced Search*\n"
        "📊 *Data-Driven Insights*\n"
        "💡 *AI-Powered Answers*\n"
        "🌐 *Real-Time Information*\n"
        "🚀 *Fast and Accurate Responses*\n"
        "🧠 *Cutting-Edge Technology*\n\n"
        "✔️ *Perplexity Pro - Team Shared Plans*\n\n"
        "> 1 Month - 1750 LKR\n"
        "> 3 Months - 3750 LKR\n"
        "> 6 Months - 4750 LKR\n\n"
        "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "VEO": (
        "*✅ VEO 3 Pro මාසයක් - රු. 2450 ක් පමණයි!*\n\n"
        "• ✅ *Official $19.99 Google AI Pro*\n"
        "• ✨ *1,000 AI Credits*\n"
        "• ⬆️ *Flow Pro, Whisk Pro and other AI Tools*\n"
        "• 🌟 *Gemini 3.1 Advanced Pro*\n"
        "♾️ *Unlimited AI Image Generation - NanoBanana AI*\n"
        "• 📖 *NoteBook LM Pro*\n"
        "• 🍎 *Anti-Gravity Pro*\n\n"
        "Payment කරලා receipt එක එවන්න. විනාඩි 30 ඇතුලත PRO එක හදලා දෙන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "GAMMA": (
        "✅ *Gamma AI $25 Premium | Team Shared - රු. 2750ක් පමණයි*\n\n"
        "🌟 *4,000 Monthly AI Credits (Shared)*\n"
        "✨ *Create up to 60 cards per prompt*\n"
        "👑 *Premium AI image models*\n"
        "⭐ *Custom branding & fonts*\n"
        "📊 *Detailed analytics & advanced sharing*\n\n"
        "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "ADOBE": (
        "*✅ Adobe Creative Cloud මාසයක් රු. 1950ක් පමණයි!*\n\n"
        "✨ *All Adobe Cloud Apps*\n"
        "🔥 *4,000 AI Credits*\n"
        "🖼️ *Adobe Standard Stock Unlimited*\n"
        "🍌 *Nano Banana Included*\n"
        "🎨 *Generative Fill & AI-Powered Features*\n"
        "💻 *2 Device Access*\n"
        "✅ *Official Activation*\n\n"
        "🗓️ *Available Plans*\n\n"
        "> 1 Month - 1950 LKR\n"
        "> 2 Months - 3750 LKR\n"
        "> 3 Months - 4250 LKR\n"
        "> 6 Months - 7250 LKR\n\n"
        "Payment කරලා receipt එක එවන්න. Activation එක දැන්ම එවන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "GRAMMARLY": (
        "✅ *Grammarly $20 Premium | Team Shared - මාසයක් රු. 1750ක් පමණයි!*\n\n"
        "✨ 1,000 AI Prompt Credits (shared)\n"
        "🔁 AI Humanizer\n"
        "🔍 AI/Plagiarism Detector\n"
        "📃 Advanced Grammar & Punctuation\n"
        "⏫ Clarity Enhancements\n"
        "🕵️ Tone Detection\n"
        "👁️ Word Choice Suggestions\n"
        "✔️ Formality & Fluency Fixes\n\n"
        "*Team Shared Plans*\n\n"
        "> 1 Month - 1750 LKR\n"
        "> 3 Months - 4750 LKR\n"
        "> 6 Months - 7750 LKR\n\n"
        "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "QUILLBOT": (
        "*Quillbot $20 Premium එක රු.1750 ක් පමණයි!*\n\n"
        "📝 *Instant Paraphrasing*\n"
        "🔍 *Grammar Check & Enhancement*\n"
        "📑 *Summarization Tools*\n"
        "🌐 *Multilingual Support*\n"
        "⚡ *Fast & Efficient Writing*\n"
        "💡 *AI-Powered Writing Assistant*\n\n"
        "*Team Shared Plans*\n\n"
        "> 1 Month - 1750 LKR\n"
        "> 3 Months - 4750 LKR\n"
        "> 6 Months - 7750 LKR\n\n"
        "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "KAHOOT": (
        "Your Kahoot subscription has expired. Please contact admin to renew.\n\n"
        "8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
    ),
    "CLAUDE": (
        "⚠️ *Claude Pro - 3 Months Plan - 1750 LKR* is currently out of stock due to massive demand.\n\n"
        "We will let you know once the promotional offers are back! 🙏"
    ),
    "ULTRA_EXPIRED": (
        "🔥 *VEO 3 Ultra - Shared Account දවස් 7ක් 2250ක් පමණයි!*\n\n"
        "🔥 *UNLIMITED AI CREDITS*\n"
        "(Credits ඉවර වුනොත්, අලුත් Account එකක් දෙනවා)\n\n"
        "🤝 *Account ඒක suspend වුනොත් හෝ credits ටික ඉවර වුනොත්, අලුත් Account එකක් Replace කරලා දෙනවා.*\n\n"
        "❤️ *Shared Account - Rs. 2250*\n"
        "✅ *No Failed Generation Issue*\n"
        "✅ *All models working with 4K Outputs*\n"
        "✅ *Flow AI & Whisk AI*\n"
        "✅ *Latest Gemini 3 Ultra*\n"
        "🗓️ *7 Days Warranty for account & credits*\n\n"
        "⚠️ *දෙන්වීමයි*\n"
        "> Feb 10 Security Update එකට පස්සේ, VEO 3 Ultra 1 month හෝ private account එකක් කොහෙවත් වගකීම එක්ක ගන්න බැහැ. ඕක කොහොමත් දවසකින් suspend වෙනවා. බොරුවට Ultra Plan විකුණන scammersට අහුවෙලා සල්ලි නැති කරගන්න එපා.\n\n"
        "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
        "8007 327 854\n"
        "Ahmed Bin Fawzer\n"
        "Commercial Bank\n"
        "Katugastota Branch"
    ),
    "GROK": (
        "🤖 *Grok SuperGrok - Renew Now!*\n\n"
        "✅ SuperGrok Unlimited Access\n"
        "✅ Grok 3 & Grok 3 Mini\n"
        "✅ Unlimited Messaging\n"
        "✅ Image Generation\n"
        "✅ Deep Search & Think Mode\n\n"
        "Payment කරලා receipt එක එවන්න. Logins ටික දැන්ම එවන්නම්.\n\n"
        "8007 327 854\n"
        "Ahmed Bin Fawzer\n"
        "Commercial Bank\n"
        "Katugastota Branch"
    ),
}

# ═══════════════════════════════════════════════════════════════════════════════
# GREETING
# ═══════════════════════════════════════════════════════════════════════════════
def _send_greeting_direct(chat_id, message):
    """Send greeting bypassing typing delay — greeting has its own fixed 5s delay."""
    try:
        r = requests.post(
            WASENDER_BASE_URL + "/send-message",
            headers=_wasender_headers(),
            json={"to": chat_id, "text": message},
            timeout=15
        )
        logger.info("sendGreeting → " + chat_id + " | " + str(r.status_code))
    except Exception as _exc_g:
        logger.error("sendGreeting error: " + str(_exc_g))

def send_greeting(chat_id):
    if greeting_sent_recently(chat_id) or greeting_already_sent_history(chat_id):
        logger.info("Greeting already sent — skipping: " + chat_id)
        return
    # Mark IMMEDIATELY before sleeping so concurrent messages don't trigger double-send
    mark_greeting_sent(chat_id)
    time.sleep(random.uniform(15, 30))  # Typing delay for greeting
    with _greeting_store_lock:
        custom_greeting = _greeting_store.get("greeting_text", "")
        if custom_greeting:
            _send_greeting_direct(chat_id, custom_greeting)
        else:
            _send_greeting_direct(chat_id,
            "ඔයාට ඕන Software එකේ නම එවන්න. විස්තර ටික දෙන්ම එවන්නම් 👇\n"
            "🔮 AI Tools\n\n"
            "1️⃣ ChatGPT Plus\n"
            "2️⃣ CapCut Pro\n"
            "3️⃣ VEO 3 Ultra\n"
            "4️⃣ Grok Supergrok\n"
            "5️⃣ Gemini Pro\n"
            "6️⃣ Stealth Writer\n"
            "7️⃣ Envato Elements\n"
            "8️⃣ Adobe Creative Cloud Pro\n"
            "9️⃣ Canva Pro\n"
            "🔟 Perplexity Pro\n"
            "1️⃣1️⃣ Office 365 Pro\n"
            "1️⃣2️⃣ Gamma AI Pro\n"
            "1️⃣3️⃣ Lightroom Pro\n"
            "1️⃣4️⃣ Jenni AI Pro\n"
            "1️⃣5️⃣ SciSpace AI Pro\n"
            "1️⃣6️⃣ Motion Array Pro\n"
            "1️⃣7️⃣ FreePik Premium\n"
            "1️⃣8️⃣ QuillBot Premium\n"
            "1️⃣9️⃣ Grammarly Premium\n"
            "2️⃣0️⃣ Duolingo Super\n"
            "2️⃣1️⃣ Kahoot Pro\n"
            "2️⃣2️⃣ Windows 10/11 Pro Key\n\n"
            "✅ Verified Customer Reviews ටික මෙතැනින් බලන්න පුළුවන්: https://www.trustpilot.com/review/signaturedigital.net"
        )

# ═══════════════════════════════════════════════════════════════════════════════
# EXPIRY NOTIFICATION
# ═══════════════════════════════════════════════════════════════════════════════
# Maps software_key → NUMBER_MENU slot for sending the description as second message
SOFTWARE_KEY_TO_SLOT = {
    "CHATGPT":       1,
    "GEMINI":        2,
    "ULTRA":         3,
    "ULTRA_EXPIRED": 3,
    "GROK":          4,
    "CLAUDE":        5,
    "GAMMA":         8,
    "PERPLEXITY":    9,
    "CAPCUT":        11,
    "ENVATO":        14,
    "ADOBE":         15,
    "FREEPIK":       17,
    "QUILLBOT":      18,
    "GRAMMARLY":     19,
    "KAHOOT":        23,
    # VEO intentionally excluded
}

# Reverse map: slot number → software key (for active-sub check on intent)
_SLOT_TO_SOFTWARE_KEY = {v: k for k, v in SOFTWARE_KEY_TO_SLOT.items() if k not in ("ULTRA", "ULTRA_EXPIRED")}
# VEO slot 3 is shared with ULTRA — VEO is separate, handle via "VEO" key
_SLOT_TO_SOFTWARE_KEY[3] = "ULTRA"  # slot 3 = Ultra/VEO Ultra

# Human-readable display names for the "please wait" message
_SOFTWARE_DISPLAY_NAMES = {
    "CHATGPT":    "ChatGPT",
    "GEMINI":     "Gemini Pro",
    "CAPCUT":     "CapCut Pro",
    "ENVATO":     "Envato Elements",
    "ADOBE":      "Adobe Creative Cloud",
    "FREEPIK":    "FreePik",
    "GAMMA":      "Gamma AI",
    "PERPLEXITY": "Perplexity AI",
    "QUILLBOT":   "QuillBot",
    "GRAMMARLY":  "Grammarly",
    "KAHOOT":     "Kahoot",
    "VEO":        "VEO Pro",
    "CANVA":      "Canva Pro",
    "LIGHTROOM":  "Lightroom",
    "DUOLINGO":   "Duolingo",
    "GROK":       "Grok",
}

def _get_all_active_software_keys(phone):
    """
    Returns a set of software keys (e.g. {"ULTRA", "CHATGPT"}) for which
    this phone has an active, non-expired, non-EXPIRED-flagged row in the sheet.
    Used to prevent description spam to existing subscribers.
    """
    active_keys = set()
    try:
        rows  = get_sheet_rows()
        local = normalize_phone(phone)
        today = sl_today()
        for i, row in enumerate(rows):
            if i == 0 or len(row) < 3:
                continue
            if normalize_phone(str(row[1])) != local:
                continue
            col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
            if col_d == "EXPIRED":
                continue
            software_key = get_general_software_key(str(row[2]).strip())
            if not software_key:
                continue
            purchase_date = parse_date(str(row[0]).strip())
            if not purchase_date:
                continue
            dur_val, dur_unit = extract_duration_from_subscription(str(row[2]).strip())
            if dur_val and dur_unit:
                expiry_dt  = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                expiry_day = expiry_dt.date() if expiry_dt else add_n_months(purchase_date, get_sub_months(str(row[2]).strip())).date()
            else:
                expiry_day = add_n_months(purchase_date, get_sub_months(str(row[2]).strip())).date()
            if expiry_day >= today:
                active_keys.add(software_key)
    except Exception as _exc18:
        logger.error("_get_all_active_software_keys error: " + str(_exc18))
    return active_keys


def _send_active_subs_to_chat(customer_chat_id):
    """Send the customer a formatted list of all their active subscriptions.
    Called when admin types !check in a customer's chat."""
    try:
        phone = customer_chat_id.lstrip("+")
        rows  = get_sheet_rows()
        local = normalize_phone(phone)
        today = sl_today()
        active_subs = []

        for i, row in enumerate(rows):
            if i == 0 or len(row) < 3:
                continue
            if normalize_phone(str(row[1])) != local:
                continue
            col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
            if col_d == "EXPIRED":
                continue
            sub_name = str(row[2]).strip()
            if not sub_name:
                continue
            purchase_date = parse_date(str(row[0]).strip())
            if not purchase_date:
                continue

            dur_val, dur_unit = extract_duration_from_subscription(sub_name)
            if dur_val and dur_unit:
                _exp_dt    = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                expiry_day = _exp_dt.date() if _exp_dt else add_n_months(purchase_date, get_sub_months(sub_name)).date()
            else:
                expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()

            if expiry_day < today:
                continue

            days_left = (expiry_day - today).days
            software_key = get_general_software_key(sub_name)
            display_name = _SOFTWARE_DISPLAY_NAMES.get(software_key, software_key or sub_name)

            expiry_display = expiry_day.strftime("%d %b %Y")
            if software_key == "ULTRA":
                notify_time = parse_ultra_notify_time(sub_name)
                if notify_time:
                    expiry_display += " " + "{:02d}:{:02d}".format(*notify_time)

            active_subs.append({
                "display":   display_name,
                "purchase":  purchase_date.strftime("%d %b %Y"),
                "expiry":    expiry_display,
                "days_left": days_left,
            })

        if not active_subs:
            send_message(customer_chat_id, "❌ You don't have any active subscriptions at the moment.")
            return

        active_subs.sort(key=lambda s: s["days_left"])

        lines = ["📋 *Your Active Subscriptions*\n"]
        for s in active_subs:
            _days_str = str(s["days_left"]) + " day" + ("s" if s["days_left"] != 1 else "") + " left"
            if s["days_left"] == 0:
                _days_str = "expires today"
            lines.append(
                "✅ *" + s["display"] + "*\n"
                "   📅 " + s["purchase"] + " → " + s["expiry"] + "\n"
                "   ⏰ " + _days_str
            )
        lines.append("\n_Need help? Just message us anytime._")
        send_message(customer_chat_id, "\n\n".join(lines))
        logger.info("✅ !check: sent " + str(len(active_subs)) + " active subs to " + customer_chat_id)
    except Exception as _exc_chk:
        logger.error("_send_active_subs_to_chat error: " + str(_exc_chk))


def _has_active_subscription_for_slot(phone, slot):
    """Returns (True, display_name) if phone has an active subscription for the given slot, else (False, None)."""
    software_key = _SLOT_TO_SOFTWARE_KEY.get(slot) or ("ULTRA" if slot == 3 else None)
    if not software_key:
        return False, None
    active_keys = _get_all_active_software_keys(phone)
    if software_key in active_keys:
        display = _SOFTWARE_DISPLAY_NAMES.get(software_key, software_key)
        return True, display
    return False, None

_active_sub_support_sent = {}
_active_sub_support_lock = threading.Lock()

def _send_active_sub_support_msg(chat_id, display_name, phone):
    """Send a high-priority support message to a customer who already has an active subscription."""
    _key = chat_id + "_" + str(display_name)
    with _active_sub_support_lock:
        _last = _active_sub_support_sent.get(_key)
        if _last and (sl_now() - _last).total_seconds() < 3600:
            return  # Only once per hour
        _active_sub_support_sent[_key] = sl_now()
    msg = (
        "⚠️ You already have an active " + str(display_name) + " subscription!\n\n"
        "If you're having issues accessing your account, please wait. Admin will check on this and reply right shortly."
    )
    send_message(chat_id, msg)


def send_expiry_notification(chat_id, software_key, purchase_date_str, expiry_date_str):
    # Permanent one-time check — never send expiry message twice to same customer for same software
    _perm_key = chat_id.lstrip("+") + "_" + software_key + "_EXPIRED"
    with _expiry_msg_sent_lock:
        if _perm_key in _expiry_msg_sent:
            logger.info("Expiry already sent permanently — skipping: " + _perm_key)
            return False
        _expiry_msg_sent.add(_perm_key)
    save_memory()

    dedup_key = chat_id + "_" + software_key
    if expiry_already_sent(dedup_key):
        logger.info("Expiry already sent (24h block): " + dedup_key)
        return False
    # Also check actual chat history — catches cases after restart
    if _expiry_already_sent_in_chat(chat_id, software_key):
        logger.info("Expiry already in chat history — skipping: " + chat_id + " " + software_key)
        mark_expiry_sent(dedup_key)
        return False
    mark_expiry_sent(dedup_key)
    # Write EXPIRED flag to sheet column D
    _mark_row_expired_in_sheet(chat_id.lstrip("+"), purchase_date_str)
    msg = (
        "❌ Your *" + software_key + "* subscription has expired.\n\n"
        "📅 Purchased: " + purchase_date_str + "\n"
        "⏰ Expired on: " + expiry_date_str
    )
    slot = SOFTWARE_KEY_TO_SLOT.get(software_key)
    desc = NUMBER_MENU.get(slot) if slot else None

    def _after_send():
        if desc:
            send_message_until_sent(chat_id, desc)
        if software_key in ("CANVA", "DUOLINGO"):
            _trigger_email_report(chat_id, software_key)

    send_message_until_sent(chat_id, msg, on_success=_after_send)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# INTENT DETECTOR — detects ALL software keywords in a message
# Returns list of matched slot numbers (from NUMBER_MENU) for multi-software
# Also returns "ULTRA" as a special key for subscription lookup
# ═══════════════════════════════════════════════════════════════════════════════

# Each entry: (slot_number_or_special_key, [keywords...])
_INTENT_MAP = [
    (1,       ["CHATGPT", "CHAT GPT", "GPT-4", "GPT4", "GPT 4", "GPT", "CHAT", "CHTGPT", "CHETGPT", "CHATGP", "CHAGPT", "CHAPGPT"]),
    (2,       ["GEMINI PRO", "GOOGLE GEMINI PRO", "GEMINI ADVANCED", "ANTIGRAVITY PRO",
               "ANTIGRAVITY", "NOTEBOOK LM", "NOTEBOOKLM", "NOTEBOOK LLM", "GEMINI"]),
    (3,       ["ULTRA_EXPIRED", "GOOGLE AI ULTRA", "GEMINI ULTRA",
               "GOOGLE ULTRA", "ULTRA PLAN", "ULTRA"]),
    (4,       ["SUPERGROK", "SUPER GROK", "GROCK", "GROK", "GORK", "GRK AI", "GRK"]),
    (5,       ["CLAUDE AI", "CLAUDE PRO", "CLAUDE", "CLOUDE", "CLODE", "CLADUE"]),
    (6,       ["JENNI AI", "JENNI"]),
    (7,       ["SCISPACE", "SCI SPACE"]),
    (8,       ["GAMMA AI", "GAMMA"]),
    (9,       ["PERPLEXITY", "PERP"]),
    (10,      ["STEALTH WRITER", "STEALTH"]),
    (11,      ["CAPCUT", "CAP CUT", "CEPCUT", "CUPCAT", "CUPCUT", "CAPCUP", "CAPCT"]),
    (12,      ["CANVA"]),
    (13,      ["LIGHTROOM", "LIGHT ROOM", "LR PRO"]),
    (14,      ["ENVATO ELEMENTS", "ENVATO ELEMENT", "ENVATO ELMNTS", "ENVATO ELMNT",
               "ENVATO ELEMNT", "ENVATO ELMENTS", "ENVATO ELEMETS", "ENVATO"]),
    (15,      ["ADOBE CREATIVE CLOUD", "CREATIVE CLOUD", "CLOUD AI", "CLOUD", "ADOBE CC", "ADOBE"]),
    (17,      ["FREEPIK", "FREE PIK"]),
    (18,      ["QUILLBOT", "QUILL BOT", "QUILL"]),
    (19,      ["GRAMMARLY", "GRAMMER", "GRAMMERY"]),
    (21,      ["OFFICE 365", "OFFICE365", "MS OFFICE", "MICROSOFT OFFICE", "MICROSOFT 365", "MS365", "OFFICE"]),
    (22,      ["DUOLINGO"]),
    (23,      ["KAHOOT"]),
    ("VEO",   ["VEO 3 ULTRA", "VEO 3", "VEO3", "VEO", "GOOGLE AI PRO", "GOOGLE AI"]),
]

def get_intent_software_key(text):
    """Returns the FIRST matched software key (string) or None. Used for single-intent checks."""
    if not text:
        return None
    t = text.upper().strip()
    for key, keywords in _INTENT_MAP:
        if any(k in t for k in keywords):
            if key == 3:
                return "ULTRA"
            if key == "VEO":
                return "VEO"
            return str(key) if isinstance(key, int) else key
    return None

def get_all_intents(text):
    """
    Returns list of (slot_num_or_key, message) for ALL software mentioned.
    Used for multi-software replies. Deduplicates.
    """
    if not text:
        return []
    t = text.upper().strip()
    seen = set()
    results = []
    for key, keywords in _INTENT_MAP:
        if not any(k in t for k in keywords):
            continue
        # Map to NUMBER_MENU slot
        if key == "VEO":
            slot = 3
        elif key == 3:
            slot = 3
        else:
            slot = key
        if slot in seen:
            continue
        seen.add(slot)
        msg = NUMBER_MENU.get(slot)
        if msg:
            results.append((slot, msg))
    return results

# ═══════════════════════════════════════════════════════════════════════════════
# NUMBER MENU — customer sends just a number (1-23) to get software info
# Fill in None slots later when you have the description ready
# ═══════════════════════════════════════════════════════════════════════════════
# Maps greeting display number → canonical NUMBER_MENU slot
# e.g. if Grok is listed as #2 in greeting, _greeting_number_map[2] = 4
# Updated by !setgreeting command and persisted to volume
_greeting_number_map = {
    1: 1,   # ChatGPT
    2: 11,  # CapCut
    3: 3,   # VEO 3 Ultra
    4: 4,   # Grok
    5: 2,   # Gemini
    6: 10,  # Stealth Writer
    7: 14,  # Envato Elements
    8: 15,  # Adobe
    9: 12,  # Canva
    10: 9,  # Perplexity
    11: 21, # Office 365
    12: 8,  # Gamma
    13: 13, # Lightroom
    14: 6,  # Jenni
    15: 7,  # SciSpace
    16: 16, # Motion Array
    17: 17, # FreePik
    18: 18, # QuillBot
    19: 19, # Grammarly
    20: 22, # Duolingo
    21: 23, # Kahoot
    22: 24, # Windows
}  # greeting_pos → slot_num
_greeting_number_map_lock = threading.Lock()

# Maps software name keywords (lowercase) → canonical slot number
# Used to parse greeting text and detect which software is at each position
_SOFTWARE_NAME_TO_SLOT = {
    "chatgpt": 1, "chat gpt": 1, "gpt": 1,
    "gemini": 2, "gemini pro": 2,
    "ultra": 3, "google ai ultra": 3, "veo 3": 3, "veo3": 3,
    "grok": 4, "supergrok": 4,
    "claude": 5, "claude ai": 5,
    "jenni": 6, "jenni ai": 6,
    "scispace": 7, "sci space": 7,
    "gamma": 8, "gamma ai": 8,
    "perplexity": 9,
    "stealth": 10, "stealth writer": 10,
    "capcut": 11, "cap cut": 11,
    "canva": 12,
    "lightroom": 13,
    "envato": 14,
    "adobe": 15, "creative cloud": 15,
    "motion array": 16,
    "freepik": 17, "free pik": 17,
    "quillbot": 18, "quill": 18,
    "grammarly": 19, "grmrly": 19,
    "linkedin": 20,
    "office 365": 21, "office365": 21, "office": 21, "microsoft": 21,
    "microsoft 365": 21, "ms365": 21, "ms office": 21, "365": 21,
    "duolingo": 22,
    "kahoot": 23,
    "windows": 24, "windows key": 24, "windows 10": 24, "windows 11": 24,
    "win10": 24, "win11": 24, "win key": 24, "product key": 24,
    "windows pro": 24, "win": 24,
}

def parse_greeting_number_map(greeting_text):
    """Parse greeting text and return {display_number: slot_num} mapping.
    Handles emoji numbers like 1️⃣, 1️⃣1️⃣, 2️⃣0️⃣ etc.
    """
    import re
    mapping = {}
    lines = greeting_text.split("\n")

    # Replace emoji digits — do multi-digit combos first (e.g. 1️⃣1️⃣ → 11)
    EMOJI_DIGITS = [
        ('🔟', '10'),
        ('1️⃣', '1'), ('2️⃣', '2'), ('3️⃣', '3'), ('4️⃣', '4'), ('5️⃣', '5'),
        ('6️⃣', '6'), ('7️⃣', '7'), ('8️⃣', '8'), ('9️⃣', '9'), ('0️⃣', '0'),
    ]

    for line in lines:
        clean = line
        for emoji, digit in EMOJI_DIGITS:
            clean = clean.replace(emoji, digit)
        # Collapse adjacent digits that came from emoji (remove spaces between them)
        clean = re.sub(r'(\d)\s+(\d)', r'\1\2', clean).strip()

        # Match leading number
        m = re.match(r'^(\d{1,2})\b', clean)
        if not m:
            continue
        pos = int(m.group(1))
        if pos < 1 or pos > 30:
            continue

        line_lower = line.lower()
        matched_slot = None
        best_len = 0
        for name, slot in _SOFTWARE_NAME_TO_SLOT.items():
            if name in line_lower and len(name) > best_len:
                matched_slot = slot
                best_len = len(name)
        if matched_slot and pos not in mapping:
            mapping[pos] = matched_slot

    return mapping

def resolve_greeting_number(n):
    """Given a number customer typed, return the actual slot via greeting map (or n itself)."""
    with _greeting_number_map_lock:
        return _greeting_number_map.get(n, n)

NUMBER_MENU = {
    # ── AI Tools ──────────────────────────────────────────────────────────────
    1:  None,  # ChatGPT Plus
    2:  None,  # Gemini Pro
    3:  None,  # Google AI Ultra (Veo 3)
    4:  None,  # Grok Supergrok
    5:  None,  # Claude AI
    6:  None,  # Jenni AI
    7:  None,  # SciSpace
    8:  None,  # Gamma AI
    9:  None,  # Perplexity AI
    10: None,  # Stealth Writer Unlimited
    # ── Design & Creative Tools ───────────────────────────────────────────────
    11: None,  # CapCut Pro
    12: None,  # Canva Pro
    13: None,  # Lightroom Pro
    14: None,  # Envato Elements
    15: None,  # Adobe Creative Cloud
    16: None,  # Motion Array
    17: None,  # FreePik
    # ── Writing & Productivity ────────────────────────────────────────────────
    18: None,  # QuillBot Premium
    19: None,  # Grammarly Premium
    20: None,  # LinkedIn Premium
    21: None,  # Office 365
    22: None,  # Duolingo Super
    23: None,  # Kahoot
    # ── Utility Tools ─────────────────────────────────────────────────────────
    24: (
        "🪟 *Windows 10/11 Pro Key*\n\n"
        "✅ Genuine retail product key\n"
        "✅ One-time activation, lifetime validity\n"
        "✅ Works on Windows 10 Pro and Windows 11 Pro\n"
        "✅ Online + telephone activation supported\n"
        "✅ Tied permanently to your Microsoft account\n\n"
        "💰 Price: 1500 LKR\n\n"
        "📲 Easy setup — DM if interested!"
    ),
}

# Human-readable name for each slot number (used in admin commands + replies)
SOFTWARE_NAMES = {
    1:  "ChatGPT Plus",          2:  "Gemini Pro",              3:  "Google AI Ultra (Veo 3)",
    4:  "Grok Supergrok",        5:  "Claude AI",               6:  "Jenni AI",
    7:  "SciSpace",              8:  "Gamma AI",                9:  "Perplexity AI",
    10: "Stealth Writer",        11: "CapCut Pro",              12: "Canva Pro",
    13: "Lightroom Pro",         14: "Envato Elements",         15: "Adobe Creative Cloud",
    16: "Motion Array",          17: "FreePik",                 18: "QuillBot Premium",
    19: "Grammarly Premium",     20: "LinkedIn Premium",        21: "Office 365",
    22: "Duolingo Super",        23: "Kahoot",                  24: "Windows 10/11 Pro Key",
}

# Greeting position → display name (matches new greeting order)
GREETING_POSITION_NAMES = {
    1:  "ChatGPT Plus",
    2:  "CapCut Pro",
    3:  "VEO 3 Ultra",
    4:  "Grok Supergrok",
    5:  "Gemini Pro",
    6:  "Stealth Writer",
    7:  "Envato Elements",
    8:  "Adobe Creative Cloud Pro",
    9:  "Canva Pro",
    10: "Perplexity Pro",
    11: "Office 365 Pro",
    12: "Gamma AI Pro",
    13: "Lightroom Pro",
    14: "Jenni AI Pro",
    15: "SciSpace AI Pro",
    16: "Motion Array Pro",
    17: "FreePik Premium",
    18: "QuillBot Premium",
    19: "Grammarly Premium",
    20: "Duolingo Super",
    21: "Kahoot Pro",
    22: "Windows 10/11 Pro Key",
}

# RENEWAL_MESSAGES also sync'd into NUMBER_MENU via key — keep both in sync
# when admin updates a slot via !setdesc, we update NUMBER_MENU directly

def get_number_intent(text):
    """If message is just a number 1-24, return the resolved slot via greeting map. Otherwise None."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.isdigit():
        n = int(stripped)
        if 1 <= n <= 24:
            return resolve_greeting_number(n)
    return None

def get_multi_number_intents(text):
    """Parse messages like '1 & 2', '1,3,4,7', '1 7 8', '1/4/7', '1, 2, 3' → list of valid slot numbers."""
    if not text:
        return []
    import re as _re_num
    stripped = text.strip()
    # If it's purely a single number, let get_number_intent handle it
    if stripped.isdigit():
        return []
    # Check if message is purely numbers with separators (&, ,, space, /)
    if not _re_num.match(r'^[\d\s,&/]+$', stripped):
        return []
    nums = _re_num.findall(r'\d+', stripped)
    results = []
    seen = set()
    for n_str in nums:
        n = int(n_str)
        if 1 <= n <= 24:
            resolved = resolve_greeting_number(n)
            if resolved not in seen:
                seen.add(resolved)
                results.append(resolved)
    return results

# ═══════════════════════════════════════════════════════════════════════════════
# DAILY EXPIRY CHECK
# Uses SL time for date comparison. Sends to subscriptions expiring today (SL).
# Has 1-day grace so if bot was down at midnight it still catches them.
# send_expiry_notification() itself enforces the 24h no-repeat block.
# ═══════════════════════════════════════════════════════════════════════════════
def _expiry_already_sent_in_chat(chat_id, software_key):
    """
    Check in-memory chat history whether an expiry message was already sent
    to this customer for this software. Prevents re-sending after restarts.
    """
    for text in get_chat_history_texts(chat_id, 100):
        if "has expired" in text and (software_key in text or "ULTRA" in text.upper()):
            return True
    return False

def check_and_send_expiry_notifications():
    """Runs at midnight — sends expiry for all subs EXCEPT Ultra.
    Each message is sent with a random delay between sends
    to avoid triggering WhatsApp spam detection."""
    logger.info("=== Daily expiry check started (SL time: " + str(sl_now()) + ") ===")
    # Random startup delay 1–5 min so it never fires at exactly the same second
    _start_delay = random.uniform(60, 300)
    logger.info("Expiry check sleeping " + str(int(_start_delay)) + "s before starting")
    time.sleep(_start_delay)
    try:
        invalidate_sheet_cache()
        rows  = get_sheet_rows()
        today = sl_today()
        sent_count = 0

        for i, row in enumerate(rows):
            if bot_is_stopped():
                logger.info("Expiry check halted by !botstop")
                return
            if i == 0 or len(row) < 3:
                continue
            purchase_date = parse_date(str(row[0]).strip())
            phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
            sub_name      = str(row[2]).strip()
            software_key  = get_general_software_key(sub_name)
            if not software_key or not purchase_date:
                continue

            # Ultra handled separately via one-shot scheduler
            if software_key in ("ULTRA", "ULTRA_PRIVATE"):
                continue

            duration_value, duration_unit = extract_duration_from_subscription(sub_name)
            if duration_value and duration_unit:
                expiry_datetime = calculate_expiry_date_from_duration(purchase_date, duration_value, duration_unit)
                if expiry_datetime:
                    expiry_day = expiry_datetime.date()
                else:
                    logger.warning("Failed to calculate expiry for: " + sub_name)
                    continue
            else:
                expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()

            if expiry_day != today:
                continue

            target = to_chat_id(phone)
            if has_renewed(phone, software_key, purchase_date, rows):
                logger.info("Renewed — skip: " + target + " | " + software_key)
                continue

            sent = send_expiry_notification(
                target, software_key,
                purchase_date.strftime("%B %d, %Y"),
                expiry_day.strftime("%B %d, %Y")
            )
            if sent:
                sent_count += 1
                time.sleep(random.uniform(60, 180))

        logger.info("=== Daily expiry done | Sent: " + str(sent_count) + " ===")
    except Exception as _exc19:
        logger.error("check_and_send_expiry_notifications error: " + str(_exc19))
        logger.error(traceback.format_exc())
        _notify_admin_failure("expiry_notifications_scheduler", str(_exc19))

def _fire_ultra_expiry_entry(phone, sub_name, purchase_date_str, expiry_day_str, software_key):
    """
    Called once at the exact scheduled time for a single ULTRA row.
    Sends the expiry message, marks col D, notifies admin.
    """
    if bot_is_stopped():
        logger.info("_fire_ultra_expiry_entry: bot stopped, skipping " + phone)
        return
    try:
        target    = to_chat_id(phone)
        fmt_phone = _format_phone_for_sheet(phone)
        purchase_date = parse_date(purchase_date_str)
        expiry_day    = parse_date(expiry_day_str)
        expiry_day_d  = expiry_day.date() if expiry_day else None
        rows          = get_sheet_rows()

        if software_key == "ULTRA_PRIVATE":
            dedup_key = target + "_PVT_" + str(expiry_day_d)
            if not expiry_already_sent(dedup_key):
                mark_expiry_sent(dedup_key)
                _mark_row_expired_in_sheet(phone, purchase_date_str)
                pvt_msg   = "Your Ultra private plan warranty has expired. However you can use it until suspension."
                ultra_desc = NUMBER_MENU.get(3)
                def _send_pvt(_t=target, _m=pvt_msg, _d=ultra_desc):
                    send_message(_t, _m)
                    if _d:
                        send_message(_t, _d)
                threading.Thread(target=_send_pvt, daemon=True).start()
                logger.info("PVT expiry sent to " + fmt_phone)
            return

        if purchase_date and has_renewed(phone, "ULTRA", purchase_date, rows):
            logger.info("_fire_ultra_expiry_entry: " + phone + " has renewed, skipping")
            return

        sent = send_ultra_expiry_notification(
            target, sub_name, purchase_date_str,
            expiry_day_d.strftime("%B %d, %Y") if expiry_day_d else expiry_day_str
        )
        if sent:
            logger.info("Ultra expiry sent to " + fmt_phone)
        else:
            logger.error("Ultra expiry FAILED for " + fmt_phone + " — " + sub_name)
    except Exception as _exc20:
        logger.error("_fire_ultra_expiry_entry error (" + phone + "): " + str(_exc20))
        logger.error(traceback.format_exc())


_ultra_expiry_queue_lock = threading.Lock()
_ULTRA_EXPIRY_QUEUE_FILE = "/data/ultra_expiry_queue.json"

def _save_ultra_expiry_queue(entries):
    """Persist today's expiry queue to Railway volume."""
    try:
        with open(_ULTRA_EXPIRY_QUEUE_FILE, "w") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        logger.info("Ultra expiry queue saved: " + str(len(entries)) + " entries")
    except Exception as _exc21:
        logger.error("_save_ultra_expiry_queue error: " + str(_exc21))

def _load_ultra_expiry_queue():
    """Load today's expiry queue from Railway volume."""
    try:
        if not os.path.exists(_ULTRA_EXPIRY_QUEUE_FILE):
            return []
        with open(_ULTRA_EXPIRY_QUEUE_FILE) as f:
            return json.load(f)
    except Exception as _exc22:
        logger.error("_load_ultra_expiry_queue error: " + str(_exc22))
        return []

def _schedule_ultra_expiry_jobs(entries):
    """Schedule one-shot APScheduler jobs for each entry at its exact fire_time."""
    now = sl_now()
    scheduled = 0
    for entry in entries:
        if entry.get("fired"):
            continue
        fire_dt_str = entry.get("fire_time")
        if not fire_dt_str:
            continue
        try:
            from datetime import datetime as _dt
            fire_dt = _dt.strptime(fire_dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SL_TZ)
        except Exception:
            continue
        if fire_dt <= now:
            # Missed — fire immediately in a thread
            logger.info("Ultra expiry missed, firing immediately: " + entry["phone"])
            def _fire_now(e=entry):
                _fire_ultra_expiry_entry(e["phone"], e["sub_name"], e["purchase_date_str"],
                                         e["expiry_day_str"], e["software_key"])
                e["fired"] = True
                _save_ultra_expiry_queue(_load_ultra_expiry_queue())
            threading.Thread(target=_fire_now, daemon=True).start()
        else:
            job_id = "ultra_exp_" + entry["phone"] + "_" + fire_dt_str.replace(" ", "_")
            try:
                scheduler.add_job(
                    _fire_and_mark_ultra_entry,
                    trigger="date",
                    run_date=fire_dt,
                    args=[entry],
                    id=job_id,
                    replace_existing=True
                )
                scheduled += 1
                logger.info("Scheduled ultra expiry: " + entry["phone"] + " at " + fire_dt_str)
            except Exception as _exc23:
                logger.error("Failed to schedule ultra expiry job: " + str(_exc23))
    logger.info("Scheduled " + str(scheduled) + " ultra expiry jobs from queue")

def _fire_and_mark_ultra_entry(entry):
    """Wrapper called by APScheduler one-shot job."""
    _fire_ultra_expiry_entry(
        entry["phone"], entry["sub_name"],
        entry["purchase_date_str"], entry["expiry_day_str"],
        entry["software_key"]
    )
    entry["fired"] = True
    queue = _load_ultra_expiry_queue()
    for q in queue:
        if q["phone"] == entry["phone"] and q["sub_name"] == entry["sub_name"]:
            q["fired"] = True
    _save_ultra_expiry_queue(queue)

def load_todays_ultra_expiries():
    """
    Runs at midnight 00:00. Scans sheet ONCE for all ULTRA rows expiring today.
    Writes them to Railway volume. Schedules one-shot APScheduler jobs at exact notify times.
    No more per-minute API polling.
    """
    if bot_is_stopped():
        return
    today = sl_today()
    logger.info("=== Loading today's ULTRA expiries for " + str(today) + " ===")
    try:
        invalidate_sheet_cache()
        rows    = get_sheet_rows()
        entries = []

        for i, row in enumerate(rows):
            if i == 0 or len(row) < 3:
                continue
            purchase_date = parse_date(str(row[0]).strip())
            phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
            sub_name      = str(row[2]).strip()
            software_key  = get_general_software_key(sub_name)
            if software_key not in ("ULTRA", "ULTRA_PRIVATE") or not purchase_date:
                continue
            col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
            if col_d == "EXPIRED":
                continue

            dur_val, dur_unit = extract_duration_from_subscription(sub_name)
            if dur_val and dur_unit:
                exp_dt     = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                expiry_day = exp_dt.date() if exp_dt else (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
            else:
                expiry_day = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()

            if expiry_day != today:
                continue

            notify_time = parse_ultra_notify_time(sub_name)
            notify_hour = notify_time[0] if notify_time else 23
            notify_min  = notify_time[1] if notify_time else 59

            from datetime import datetime as _dt2
            fire_dt = _dt2(today.year, today.month, today.day, notify_hour, notify_min, 0).replace(tzinfo=SL_TZ)
            fire_dt_str = fire_dt.strftime("%Y-%m-%d %H:%M:%S")

            entries.append({
                "phone":            phone,
                "sub_name":         sub_name,
                "purchase_date_str": purchase_date.strftime("%B %d, %Y"),
                "expiry_day_str":   expiry_day.strftime("%B %d, %Y"),
                "software_key":     software_key,
                "fire_time":        fire_dt_str,
                "fired":            False,
            })

        _save_ultra_expiry_queue(entries)
        logger.info("Today's ULTRA expiry queue: " + str(len(entries)) + " entries")
        _schedule_ultra_expiry_jobs(entries)

        # Also notify admin of today's schedule
        if entries:
            msg = "📋 *Today's ULTRA Expiry Schedule — " + str(today) + "*\n\n"
            for e in entries:
                msg += "• " + _format_phone_for_sheet(e["phone"]) + " — " + e["sub_name"] + " @ " + e["fire_time"][-8:-3] + "\n"
            logger.info("Today's ULTRA expiry schedule loaded: " + str(len(entries)) + " entries")

    except Exception as _exc24:
        logger.error("load_todays_ultra_expiries error: " + str(_exc24))
        logger.error(traceback.format_exc())
        _notify_admin_failure("load_todays_ultra_expiries", str(_exc24))

def check_and_send_ultra_expiry():
    """Legacy shim — kept so existing scheduler ref doesn't break. Does nothing now."""
    pass

# ═══════════════════════════════════════════════════════════════════════════════
# DATE COMMAND HELPERS — for !listexpiry / !sendexpiry
# ═══════════════════════════════════════════════════════════════════════════════
import re as _re

def _parse_short_date(s):
    """
    Parse flexible date strings into a date object.
    Accepts: mar13, mar 13, march13, march 13, 13mar, 13 mar,
             2026-03-13, 03/13/2026, 03/13, 13/03 etc.
    Assumes current year if year not provided.
    """
    s = s.strip().lower()
    year = sl_today().year

    # Try standard formats with year first
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%B %d %Y", "%b %d %Y",
                "%d %B %Y", "%d %b %Y"]:
        try:
            return datetime.strptime(s, fmt).date()
        except:
            pass

    # Try formats without year — assume current year
    for fmt in ["%b%d", "%b %d", "%B%d", "%B %d", "%d%b", "%d %b", "%d%B", "%d %B",
                "%m/%d", "%d/%m"]:
        try:
            d = datetime.strptime(s, fmt)
            return d.replace(year=year).date()
        except:
            pass

    # Regex: mar13 / march13 / 13mar / 13march (with optional space)
    m = _re.match(r'^([a-z]+)\s*(\d{1,2})$', s)
    if m:
        try:
            return datetime.strptime(m.group(1)[:3] + " " + m.group(2) + " " + str(year), "%b %d %Y").date()
        except:
            pass
    m = _re.match(r'^(\d{1,2})\s*([a-z]+)$', s)
    if m:
        try:
            return datetime.strptime(m.group(2)[:3] + " " + m.group(1) + " " + str(year), "%b %d %Y").date()
        except:
            pass

    return None

def _get_expiries_for_date(target_date):
    """Return list of (phone_display, software_key, expiry_date, purchase_date, raw_phone) for a given date."""
    invalidate_sheet_cache()
    rows  = get_sheet_rows()
    found = []
    seen  = set()
    for i, row in enumerate(rows):
        if i == 0 or len(row) < 3:
            continue
        purchase_date = parse_date(str(row[0]).strip())
        phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
        sub_name      = str(row[2]).strip()
        software_key  = get_general_software_key(sub_name)
        if not software_key or not purchase_date:
            continue
        expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()
        if expiry_day != target_date:
            continue
        local = normalize_phone(phone)
        dedup = local + "_" + software_key
        if dedup in seen:
            continue
        seen.add(dedup)
        display = local[-9:] if len(local) >= 9 else local
        found.append((display, software_key, expiry_day, purchase_date, phone))
    return found

def _list_expiry_for_date(chat_id, target_date):
    try:
        found = _get_expiries_for_date(target_date)
        if not found:
            send_message(chat_id, "✅ No subscriptions expiring on *" + str(target_date) + "*")
            return
        msg = "📋 *Expiring on " + str(target_date) + ": " + str(len(found)) + "*\n\n"
        for ph, key, exp, purch, _ in found:
            dedup  = to_chat_id(ph) + "_" + key
            status = " ✅sent" if expiry_already_sent(dedup) else ""
            renewed = " 🔄renewed" if has_renewed(ph, key, purch) else ""
            msg   += "• " + ph + " — " + key + status + renewed + "\n"
        send_message(chat_id, msg)
    except Exception as _exc25:
        send_message(chat_id, "❌ Failed: " + str(_exc25))
        logger.error(traceback.format_exc())

def _get_ultra_expiries_for_date(target_date):
    """Return ULT* rows whose expiry date (from duration or default 7 days) == target_date."""
    invalidate_sheet_cache()
    rows  = get_sheet_rows()
    found = []
    seen  = set()
    for i, row in enumerate(rows):
        if i == 0 or len(row) < 3:
            continue
        purchase_date = parse_date(str(row[0]).strip())
        phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
        sub_name      = str(row[2]).strip()
        if not sub_name.upper().startswith("ULT"):
            continue
        if not purchase_date:
            continue
        # Use duration from sub name (2W, 3M, 14D etc.), fallback to 7 days
        dur_val, dur_unit = extract_duration_from_subscription(sub_name)
        if dur_val and dur_unit:
            exp_dt     = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
            expiry_day = exp_dt.date() if exp_dt else (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
        else:
            expiry_day = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
        if expiry_day != target_date:
            continue
        local = normalize_phone(phone)
        dedup = local + "_ULTRA7_" + str(expiry_day)
        if dedup in seen:
            continue
        seen.add(dedup)
        display = local[-9:] if len(local) >= 9 else local
        found.append((display, sub_name, expiry_day, purchase_date, phone))
    return found

def _trigger_email_report(chat_id, software_key):
    """Scan chat history for email and report to admins (Canva/Duolingo)."""
    def _run():
        try:
            import re
            EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
            messages = get_chat_history_messages(chat_id, 100)
            emails_found = set()
            for m in messages:
                found = EMAIL_RE.findall(m.get("text", ""))
                for e in found:
                    emails_found.add(e.lower())
            phone = chat_id.lstrip("+")
            if phone.startswith("94"):
                phone = phone[2:]
            if emails_found:
                msg = "📧 *" + software_key + " Expiry — Email found*\n"
                msg += "👤 Customer: " + phone + "\n"
                msg += "📩 " + "\n📩 ".join(sorted(emails_found))
            else:
                msg = "📧 *" + software_key + " Expiry — No email found*\n👤 Customer: " + phone
            logger.info("Email report for " + software_key + " customer " + phone + ": " + str(sorted(emails_found)))
        except Exception as _exc26:
            logger.error("_trigger_email_report error: " + str(_exc26))
    threading.Thread(target=_run, daemon=True).start()

def _mark_row_expired_in_sheet(phone_raw, purchase_date_str):
    """Write EXPIRED to column D of the matching row — survives restarts."""
    def _run():
        try:
            creds  = get_google_credentials()
            client = gspread.authorize(creds)
            ss     = client.open_by_key(GOOGLE_SHEET_ID)
            try:
                sheet = ss.worksheet(SHEET_NAME)
            except Exception:
                sheet = ss.get_worksheet(0)
            rows  = sheet.get_all_values()
            local = normalize_phone(phone_raw)
            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                if normalize_phone(str(row[1])) != local:
                    continue
                if not row[0].strip():
                    continue
                parsed = parse_date(str(row[0]).strip())
                if parsed and parsed.strftime("%B %d, %Y") == purchase_date_str:
                    sheet.update_cell(i + 1, 4, "EXPIRED")
                    invalidate_sheet_cache()
                    logger.info("Marked EXPIRED in sheet row " + str(i+1) + " for " + phone_raw)
                    return
        except Exception as _exc27:
            logger.error("_mark_row_expired_in_sheet error: " + str(_exc27))
    threading.Thread(target=_run, daemon=True).start()

def send_ultra_expiry_notification(chat_id, sub_name, purchase_date_str, expiry_date_str):
    # Use phone + date in ISO format as dedup key — matches showexpiry and scheduler
    phone     = normalize_phone(chat_id.lstrip("+"))
    try:
        from datetime import datetime as _dt
        expiry_date_obj = _dt.strptime(expiry_date_str, "%B %d, %Y").date()
    except Exception:
        expiry_date_obj = None
    dedup_key = phone + "_ULTRA7_" + str(expiry_date_obj) if expiry_date_obj else chat_id + "_ULTRA7_" + expiry_date_str
    if expiry_already_sent(dedup_key):
        logger.info("Ultra expiry already sent (24h block): " + dedup_key)
        return False
    # Also check actual chat history — catches cases after restart
    if _expiry_already_sent_in_chat(chat_id, "ULTRA"):
        logger.info("Ultra expiry already in chat history — skipping: " + chat_id)
        mark_expiry_sent(dedup_key)
        return False
    # Mark sent immediately so second run never double-sends
    mark_expiry_sent(dedup_key)
    # Write EXPIRED flag to sheet column D — survives restarts, overrides all cache
    _mark_row_expired_in_sheet(chat_id.lstrip("+"), purchase_date_str)

    # Check if purchased between Apr 3–Apr 8 → prepend extra 2 days message
    extra_prefix = ""
    try:
        from datetime import datetime as _dt2
        purch_obj = _dt2.strptime(purchase_date_str, "%B %d, %Y").date()
        apr3 = _dt2(2026, 4, 3).date()
        apr8 = _dt2(2026, 4, 8).date()
        if apr3 <= purch_obj <= apr8:
            extra_prefix = "ℹ️ Extra 2 days were added to your subscription due to the Google server issue on Apr 9. Your subscription is over now.\n\n"
    except Exception:
        pass

    send_message_until_sent(chat_id,
        extra_prefix +
        "❌ Your *ULTRA* subscription has expired.\n\n"
        "📅 Purchase date: " + purchase_date_str + "\n"
        "⏰ Expiry: " + expiry_date_str
    )
    _ultra_live_desc = NUMBER_MENU.get(3)
    if _ultra_live_desc:
        time.sleep(1)
        send_message(chat_id, _ultra_live_desc)
    return True

def _list_ultra_expiry_for_date(chat_id, target_date):
    try:
        found = _get_ultra_expiries_for_date(target_date)
        if not found:
            send_message(chat_id, "✅ No ULTRA subscriptions expiring on *" + str(target_date) + "*")
            return
        msg = "📋 *ULTRA Expiring on " + str(target_date) + ": " + str(len(found)) + "*\n\n"
        for ph, sub, exp, purch, raw_phone in found:
            # Use same dedup format as send_ultra_expiry_notification
            local_ph = normalize_phone(raw_phone)
            dedup    = local_ph + "_ULTRA7_" + str(exp)
            # Also check sheet col D
            sheet_expired = False
            try:
                sheet_rows = get_sheet_rows()
                for srow in sheet_rows:
                    if len(srow) < 3: continue
                    if normalize_phone(str(srow[1])) != local_ph: continue
                    if not str(srow[2]).strip().upper().startswith("ULT"): continue
                    if str(srow[3]).strip().upper() == "EXPIRED" if len(srow) > 3 else False:
                        sheet_expired = True
                        break
            except Exception:
                pass
            if expiry_already_sent(dedup) or sheet_expired:
                status = " ✅sent"
            else:
                status = ""
            msg += "• " + ph + " — " + sub + status + "\n"
        send_message(chat_id, msg)
    except Exception as _exc28:
        send_message(chat_id, "❌ Failed: " + str(_exc28))
        logger.error(traceback.format_exc())

def _send_ultra_expiry_for_date(chat_id, target_date):
    try:
        found = _get_ultra_expiries_for_date(target_date)
        if not found:
            send_message(chat_id, "✅ No ULTRA subscriptions expiring on *" + str(target_date) + "*")
            return
        count   = 0
        skipped = 0
        renewed_list = []
        for ph, sub, exp_day, purchase_date, raw_phone in found:
            if bot_is_stopped():
                send_message(chat_id, "🛑 Stopped by !botstop after " + str(count) + " sends.")
                return
            if has_renewed(raw_phone, "ULT", purchase_date):
                logger.info("ULTRA renewed — skip: " + raw_phone)
                renewed_list.append(ph)
                skipped += 1
                continue
            target = to_chat_id(raw_phone)
            sent   = send_ultra_expiry_notification(
                target,
                sub,
                purchase_date.strftime("%B %d, %Y"),
                exp_day.strftime("%B %d, %Y")
            )
            if sent:
                count += 1
                time.sleep(5)
            else:
                skipped += 1
        msg  = "✅ ULTRA expiry done for *" + str(target_date) + "*!\n"
        msg += "Sent: " + str(count) + "\n"
        if renewed_list:
            msg += "🔄 Already renewed (" + str(len(renewed_list)) + "): " + ", ".join(renewed_list) + "\n"
        if skipped - len(renewed_list) > 0:
            msg += "Skipped (already sent): " + str(skipped - len(renewed_list))
        send_message(chat_id, msg)
    except Exception as _exc29:
        send_message(chat_id, "❌ Failed: " + str(_exc29))
        logger.error(traceback.format_exc())


def _send_expiry_for_date(chat_id, target_date):
    try:
        found = _get_expiries_for_date(target_date)
        if not found:
            send_message(chat_id, "✅ No subscriptions expiring on *" + str(target_date) + "*")
            return
        count = 0
        skipped = 0
        for ph, key, exp_day, purchase_date, raw_phone in found:
            if bot_is_stopped():
                send_message(chat_id, "🛑 Stopped by !botstop after " + str(count) + " sends.")
                return
            if has_renewed(raw_phone, key, purchase_date):
                logger.info("Renewed — skip: " + raw_phone + " | " + key)
                skipped += 1
                continue
            target = to_chat_id(raw_phone)
            sent = send_expiry_notification(
                target, key,
                purchase_date.strftime("%B %d, %Y"),
                exp_day.strftime("%B %d, %Y")
            )
            if sent:
                count += 1
                time.sleep(5)
            else:
                skipped += 1
        msg = "✅ Done for *" + str(target_date) + "*!\n"
        msg += "Sent: " + str(count) + "\n"
        if skipped:
            msg += "Skipped (renewed or already sent): " + str(skipped)
        send_message(chat_id, msg)
    except Exception as _exc30:
        send_message(chat_id, "❌ Failed: " + str(_exc30))
        logger.error(traceback.format_exc())

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

# ── Credits finished / account suspended keywords ────────────────────────────

def handle_message(chat_id, text, sender_name):
    try:
        _handle_message_inner(chat_id, text, sender_name)
    except Exception as _exc_hm:
        logger.error("handle_message error: " + str(_exc_hm))
        logger.error(traceback.format_exc())

def _handle_message_inner(chat_id, text, sender_name):
    is_admin = chat_id.lstrip("+") in ADMIN_NUMBERS
    is_image = not text or text.strip() == ""
    phone    = chat_id.lstrip("+")
    if is_admin:
        logger.info("👨‍💼 ADMIN " + phone + " sent: " + repr(text))
    elif is_image:
        logger.info("📸 " + phone + " sent an image/receipt")
    else:
        logger.info("💬 " + phone + " sent: " + repr(text))

    # ══ Admin commands ═══════════════════════════════════════════════════════════════════════
    if is_admin and text and text.startswith("!"):
        handle_admin_command(chat_id, text)
        return

    # ── Admin test: send "creds" to receive the creds file for testing ───────────────────
    if is_admin and text and text.strip().lower() == "creds":
        with _creds_slot1_lock:
            _test_content = _creds_slot1.get("file_content", "").strip()
        if _test_content:
            logger.info("🧪 Admin test — sending creds to " + phone)
            send_message(chat_id, _test_content)
        else:
            send_message(chat_id, "❌ No creds set. Use !setcreds1 to set it.")
        return

    # ── Human read/think delay — simulates time before starting to type ──────────────────
    # Skipped for admins. Random 5–30 seconds per message.
    _read_delay = random.uniform(5, 30)
    logger.info("⏳ Read/think delay " + str(int(_read_delay)) + "s before processing reply for " + phone)
    time.sleep(_read_delay)

    # ══ Renewal button tap ════════════════════════════════════════════════════════════════
    if text and text.lower().startswith("renew_"):
        key = text.replace("renew_", "").upper()
        send_message(chat_id, RENEWAL_MESSAGES.get(key, "Please contact admin to renew."))
        return



    # ══ Text-only shortcuts: number menu + software-name intent ═══════════════════════
    # Images bypass these entirely and go straight to the subscription/creds flow.
    if not is_admin and not is_image:
        # If admin messaged this user within 5 mins — skip descriptions (each admin msg resets timer)
        admin_active = _admin_messaged_within(chat_id, 300)

        # Pure greeting — skip intent detection entirely
        _text_stripped = text.strip().lower() if text else ""
        _greeting_phrases = ("can i get more info", "more info", "tell me more", "what is this", "what do you sell", "yeah that", "yes that", "ok send", "okay send", "send info", "send details", "interested", "i need info")
        _is_pure_hi = _text_stripped in ("hi", "hello", "hey", "hii", "helo", "hola", "hai", "heyy", "helloo") or \
            any(kw in _text_stripped for kw in _greeting_phrases)


        # Availability query detection — time-based payment response
        _avail_keywords = (
            "ganne puluwandhe", "ganne puluwnda", "ganna puluwandhe", "ganna puluwnda",
            "ganne puluwanda", "ganna puluwanda", "ganne puluanda", "ganna puluanda",
            "ganne puluwand", "ganna puluwand",
            "dhen ganne", "dhenme ganne", "denna ganne", "dhen ganna", "dhenme ganna",
            "dhen oan", "dhen wan", "dhen one", "dhen wanne",
            "account ekak gnn", "account gnn", "gnn puluwandhe",
            "ganne oan", "ganne one", "ganne oana",
            "dhenme ganna puluwandhe", "dhenme ganne puluwandhe",
        )
        _sinhala_avail = ("දැන් ගන්න", "දෙන්න ගන්න", "ගන්න පුළුවන්", "දැන්ම ගන්න")
        if any(kw in _text_stripped for kw in _avail_keywords) or any(kw in (text or "") for kw in _sinhala_avail):
            logger.info("📍 " + phone + " asked about availability/payment → sending bank details")
            _sl_hour = sl_now().hour
            _bank = "\n\n8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
            if 12 <= _sl_hour < 24:
                _avail_msg = "ඔව් ගන්න පුළුවන්. Payment කරලා receipt එක එවන්න. දැන්ම Active කරලා දෙන්නම්." + _bank
            else:
                _avail_msg = "ඔව් ගන්න පුළුවන්. Payment කරලා missed call එකක් දෙන්න. දෙන්ම active කරලා දෙන්නම්." + _bank
            send_message(chat_id, _avail_msg)
            return

        # Price inquiry detection — only send sinhala message if NO software name detected
        _price_keywords = ("price", "prices", "how much", "gaane keeyedhe", "keeyakdhe", "mila ewanna", "mile tike", "keeyakdhe wenne", "what is price", "send price", "mila", "keeyada", "kiyada", "kiyadha")
        _is_price_ask = any(kw in _text_stripped for kw in _price_keywords)
        _has_software = bool(get_all_intents(text)) or bool(get_number_intent(text)) or bool(get_multi_number_intents(text))
        if _is_price_ask and not _has_software:
            send_greeting(chat_id)
            return
        # Multi-software intent — detect ALL mentioned software and reply for each
        _skip_menu = False
        intents = [] if _is_pure_hi else get_all_intents(text)
        if intents:
            _intent_names = [_SLOT_TO_SOFTWARE_KEY.get(s, "slot" + str(s)) for s, _ in intents]
            logger.info("📍 " + phone + " mentioned software: " + ", ".join(_intent_names) + " (" + str(len(intents)) + " in queue)")
            is_ultra_intent = get_intent_software_key(text) == "ULTRA"

            # Mark cooldowns immediately so STEP4 doesn't double-send while thread is sleeping
            for slot, _ in intents:
                set_desc_cooldown(chat_id, slot)

            def _send_desc_after_greeting(_chat_id=chat_id, _phone=phone, _intents=intents, _text=text, _admin_active=admin_active, _is_ultra_intent=is_ultra_intent):
                _greeted = False
                if not _admin_active and not _is_ultra_intent:
                    # Don't send greeting to Ultra subscribers — they're existing customers
                    if not greeting_already_sent_history(_chat_id) and not greeting_sent_recently(_chat_id):
                        logger.info("📤 Sending greeting to " + _phone + " first (after 15-30s delay)")
                        send_greeting(_chat_id)
                        _greeted = True
                    else:
                        logger.info("⏭️ Greeting already sent earlier — skipping")
                _sent_slots = set()
                if _greeted:
                    _gap = random.uniform(15, 25)
                    logger.info("⏳ Waiting " + str(int(_gap)) + "s after greeting before sending descriptions")
                    time.sleep(_gap)
                for i, (slot, msg) in enumerate(_intents):
                    _sw_name = _SLOT_TO_SOFTWARE_KEY.get(slot, "slot" + str(slot))
                    if slot in _sent_slots:
                        continue
                    _active, _display = _has_active_subscription_for_slot(_phone, slot)
                    if _active:
                        # For ULTRA — skip support message, check_subscription will deliver the cookie
                        if slot == 3:
                            logger.info("⏭️ " + _phone + " has active ULTRA — skipping support msg, creds flow will handle")
                            _sent_slots.add(slot)
                            continue
                        logger.info("⚠️ " + _phone + " ALREADY HAS active " + _display + " → sending support message instead of description")
                        _send_active_sub_support_msg(_chat_id, _display, _phone)
                        _sent_slots.add(slot)
                        continue
                    _remaining = len(_intents) - i - 1
                    if _remaining > 0:
                        logger.info("📤 Sending " + _sw_name + " description to " + _phone + " (" + str(_remaining) + " more queued after this)")
                    else:
                        logger.info("📤 Sending " + _sw_name + " description to " + _phone + " (last in queue)")
                    # Set cooldown BEFORE sending so STEP4 doesn't double-send
                    set_desc_cooldown(_chat_id, slot)
                    send_message(_chat_id, msg)
                    _sent_slots.add(slot)

            threading.Thread(target=_send_desc_after_greeting, daemon=True).start()
            if not is_ultra_intent:
                return
            _skip_menu = True
        else:
            _skip_menu = False

        # Number menu — customer sends just a number e.g. "10" for CapCut
        num = get_number_intent(text)
        if not _skip_menu and num is not None:
            # Slot 3 = Ultra → fall through to check_subscription for cookie delivery
            if num == 3:
                logger.info("📍 " + phone + " sent '3' (ULTRA) — routing to cookie flow")
                _skip_menu = True
            else:
                _sw_name_num = _SLOT_TO_SOFTWARE_KEY.get(num, "slot" + str(num))
                logger.info("📍 " + phone + " sent number '" + str(num) + "' (" + _sw_name_num + ")")
                _greeted_num = False
                if not admin_active:
                    if not greeting_already_sent_history(chat_id) and not greeting_sent_recently(chat_id):
                        logger.info("📤 Sending greeting to " + phone + " first")
                        send_greeting(chat_id)
                        _greeted_num = True
                if _greeted_num:
                    _gap = random.uniform(15, 25)
                    logger.info("⏳ Waiting " + str(int(_gap)) + "s after greeting before sending description")
                    time.sleep(_gap)
                _active_num, _display_num = _has_active_subscription_for_slot(phone, num)
                if _active_num:
                    logger.info("⚠️ " + phone + " ALREADY HAS active " + _display_num + " → sending support message")
                    _send_active_sub_support_msg(chat_id, _display_num, phone)
                else:
                    msg = NUMBER_MENU.get(num)
                    if msg:
                        logger.info("📤 Sending " + _sw_name_num + " description to " + phone)
                        send_message(chat_id, msg)
                        set_desc_cooldown(chat_id, num)
                return

        # Multi-number intent — e.g. "1 & 2", "1,3,4,7", "1 7 8"
        multi_nums = get_multi_number_intents(text)
        if not _skip_menu and multi_nums:
            _mn_names = [_SLOT_TO_SOFTWARE_KEY.get(n, "slot" + str(n)) for n in multi_nums]
            logger.info("📍 " + phone + " sent multiple numbers: " + ", ".join(str(n) for n in multi_nums) + " → " + ", ".join(_mn_names) + " (" + str(len(multi_nums)) + " in queue)")
            _greeted_mn = False
            if not admin_active:
                if not greeting_already_sent_history(chat_id) and not greeting_sent_recently(chat_id):
                    logger.info("📤 Sending greeting to " + phone + " first")
                    send_greeting(chat_id)
                    _greeted_mn = True
            if _greeted_mn:
                _gap = random.uniform(15, 25)
                logger.info("⏳ Waiting " + str(int(_gap)) + "s after greeting before sending descriptions")
                time.sleep(_gap)
            for i, n in enumerate(multi_nums):
                _sw_name_mn = _SLOT_TO_SOFTWARE_KEY.get(n, "slot" + str(n))
                # Slot 3 = Ultra → skip here, cookie handled by check_subscription after loop
                if n == 3:
                    logger.info("📍 " + phone + " multi-number includes 3 (ULTRA) — cookie flow will handle")
                    continue
                msg = NUMBER_MENU.get(n)
                _active_mn, _display_mn = _has_active_subscription_for_slot(phone, n)
                if _active_mn:
                    logger.info("⚠️ " + phone + " ALREADY HAS active " + _display_mn + " → sending support message")
                    _send_active_sub_support_msg(chat_id, _display_mn, phone)
                    continue
                if msg:
                    _remaining = len(multi_nums) - i - 1
                    if _remaining > 0:
                        logger.info("📤 Sending " + _sw_name_mn + " description to " + phone + " (" + str(_remaining) + " more queued after this)")
                    else:
                        logger.info("📤 Sending " + _sw_name_mn + " description to " + phone + " (last in queue)")
                    send_message(chat_id, msg)
                    set_desc_cooldown(chat_id, n)
            # If Ultra was among the numbers, fall through to cookie flow
            if 3 in multi_nums:
                _skip_menu = True
            else:
                return

    # ══ Subscription / creds flow — ALL messages reach here (text, image, image+caption)
    phone = chat_id.lstrip("+")

    results = check_subscription(phone)

    if results is None:
        # Retry once silently before giving up
        time.sleep(3)
        results = check_subscription(phone)
    if results is None:
        logger.error("Database check failed twice for " + chat_id)
        # Don't send fallback message — silent fail
        return

    # Ultra subscriber response — send creds file ONCE permanently, expiry notice if expired
    if results:
        active_results  = [r for r in results if r.get("active")]
        expired_results = [r for r in results if not r.get("active")]

        if active_results:
            with _creds_slot1_lock:
                _file_content    = _creds_slot1.get("file_content", "").strip()
                _current_version = _creds_slot1.get("cookie_version", 1)

            # Atomic check+mark to prevent race condition double-send
            with _creds_sent_phones_lock:
                _sent_version = _creds_sent_phones.get(phone)
                try:
                    _sent_v_int = int(_sent_version) if _sent_version is not None else None
                except (ValueError, TypeError):
                    _sent_v_int = 0  # Old format — treat as version 0, resend
                if _sent_v_int is not None and _sent_v_int >= _current_version:
                    logger.info("⏭️ " + phone + " already has v" + str(_sent_v_int) + " — silent")
                    return
                # Reserve the slot immediately inside the lock to block any concurrent thread
                if _file_content:
                    _creds_sent_phones[phone] = _current_version  # Will be confirmed or rolled back

            if not _file_content:
                logger.info("⏭️ No creds set — " + phone + " will get creds when updated")
            else:
                time.sleep(6)
                logger.info("📤 Sending Ultra creds v" + str(_current_version) + " to " + phone)
                _delivered = send_message(chat_id, _file_content)
                if _delivered:
                    save_memory()
                else:
                    # Rollback — delivery failed, allow retry on next message
                    with _creds_sent_phones_lock:
                        if _creds_sent_phones.get(phone) == _current_version:
                            del _creds_sent_phones[phone]
                    # Retry once after 10s
                    time.sleep(10)
                    logger.info("🔄 Retrying creds send to " + phone)
                    _delivered2 = send_message(chat_id, _file_content)
                    if _delivered2:
                        with _creds_sent_phones_lock:
                            _creds_sent_phones[phone] = _current_version
                        save_memory()
                    else:
                        logger.error("❌ Creds send failed after retry for " + phone)
            return

        elif expired_results:
            _expiry_key = phone + "_EXPIRED"
            with _expiry_msg_sent_lock:
                _already_notified = _expiry_key in _expiry_msg_sent
            if _already_notified:
                logger.info("⏭️ Expiry message already sent permanently to " + phone + " — skipping")
                return
            logger.info("📤 Sending Ultra expired notice + description to " + phone)
            send_message(chat_id, "Your Ultra has expired. Renew to continue.")
            with _expiry_msg_sent_lock:
                _expiry_msg_sent.add(_expiry_key)
            save_memory()
            time.sleep(random.uniform(8, 15))
            _ultra_desc = NUMBER_MENU.get(3)
            if _ultra_desc:
                send_message(chat_id, _ultra_desc)
            return

    # Check for PVT subscription — notify admin once per cooldown, tell customer to wait
    try:
        pvt_rows = get_sheet_rows()
        local_p  = normalize_phone(phone)
        for _i, _row in enumerate(pvt_rows):
            if _i == 0 or len(_row) < 3: continue
            if normalize_phone(str(_row[1])) != local_p: continue
            if get_general_software_key(str(_row[2]).strip()) == "ULTRA_PRIVATE":
                phone_disp = phone[2:] if phone.startswith("94") else phone
                if is_on_cooldown(chat_id):
                    # Customer messaged within cooldown — just tell them to wait
                    send_message(chat_id, "Please wait, we're getting your account ready. We'll send it shortly! 🙏")
                    set_cooldown(chat_id, 18000)  # 5 hour cooldown
                else:
                    # First message — notify admin and inform customer
                    set_cooldown(chat_id, 600)  # 10 min cooldown
                    send_message(chat_id, "Let me check into this really quick and get back to you! 🙏")
                    logger.info("PVT subscription request from " + phone_disp)
                return
    except Exception as _exc32:
        logger.error("PVT check error: " + str(_exc32))
        _notify_admin_failure("PVT subscription check | " + chat_id, str(_exc32))
        is_hi = text and text.strip().lower() == "hi"
        _active_keys_fb = _get_all_active_software_keys(phone)
        if get_intent_software_key(text) == "ULTRA":
            if is_hi:
                send_greeting(chat_id)
            if "ULTRA" not in _active_keys_fb:
                desc = NUMBER_MENU.get(3)
                if desc and not desc_on_cooldown(chat_id, 3):
                    send_message(chat_id, desc)
                    set_desc_cooldown(chat_id, 3)
        else:
            if is_hi:
                send_greeting(chat_id)
            intents = get_all_intents(text)
            for slot, msg in intents:
                if desc_on_cooldown(chat_id, slot):
                    continue
                _sk_fb = _SLOT_TO_SOFTWARE_KEY.get(slot) or ("ULTRA" if slot == 3 else None)
                if _sk_fb and _sk_fb in _active_keys_fb:
                    _send_active_sub_support_msg(chat_id, _SOFTWARE_DISPLAY_NAMES.get(_sk_fb, _sk_fb), phone)
                    continue
                send_message(chat_id, msg)
                set_desc_cooldown(chat_id, slot)
        return

    if not results:
        # No Ultra subscription found — new/non-ultra customer
        _text_low = text.strip().lower() if text else ""

        # Availability query
        if any(kw in _text_low for kw in _avail_keywords) or any(kw in (text or "") for kw in _sinhala_avail):
            _sl_hour2 = sl_now().hour
            _bank2 = "\n\n8007 327 854\nAhmed Bin Fawzer\nCommercial Bank\nKatugastota Branch"
            if 12 <= _sl_hour2 < 24:
                send_message(chat_id, "ඔව් ගන්න පුළුවන්. Payment කරලා receipt එක එවන්න. දැන්ම Active කරලා දෙන්නම්." + _bank2)
            else:
                send_message(chat_id, "ඔව් ගන්න පුළුවන්. Payment කරලා missed call එකක් දෙන්න. දෙන්ම active කරලා දෙන්නම්." + _bank2)
            return

        # Pure greeting — already handled by intents block if software was mentioned,
        # only reach here if pure hi with no software
        if _is_pure_hi:
            send_greeting(chat_id)
            return

        # Descriptions already sent by the background thread in the intents block above
        # STEP4 only handles edge cases: images or messages that bypassed intents block
        if is_image:
            # Image from non-ultra customer — no action needed
            return

        # Software mentioned but not caught by intents block (shouldn't happen often)
        _active_keys4 = _get_all_active_software_keys(phone)
        multi_nums = get_multi_number_intents(text)
        if multi_nums:
            for n in multi_nums:
                if desc_on_cooldown(chat_id, n):
                    continue
                _sk4 = _SLOT_TO_SOFTWARE_KEY.get(n) or ("ULTRA" if n == 3 else None)
                if _sk4 and _sk4 in _active_keys4:
                    _send_active_sub_support_msg(chat_id, _SOFTWARE_DISPLAY_NAMES.get(_sk4, _sk4), phone)
                    continue
                msg = NUMBER_MENU.get(n)
                if msg:
                    send_message(chat_id, msg)
                    set_desc_cooldown(chat_id, n)
        else:
            num = get_number_intent(text)
            if num is not None:
                if desc_on_cooldown(chat_id, num):
                    return
                _sk4 = _SLOT_TO_SOFTWARE_KEY.get(num) or ("ULTRA" if num == 3 else None)
                if _sk4 and _sk4 in _active_keys4:
                    _send_active_sub_support_msg(chat_id, _SOFTWARE_DISPLAY_NAMES.get(_sk4, _sk4), phone)
                else:
                    msg = NUMBER_MENU.get(num)
                    if msg:
                        send_message(chat_id, msg)
                        set_desc_cooldown(chat_id, num)
            else:
                intents4 = get_all_intents(text)
                for slot, msg in intents4:
                    if desc_on_cooldown(chat_id, slot):
                        continue
                    _sk4 = _SLOT_TO_SOFTWARE_KEY.get(slot) or ("ULTRA" if slot == 3 else None)
                    if _sk4 and _sk4 in _active_keys4:
                        _send_active_sub_support_msg(chat_id, _SOFTWARE_DISPLAY_NAMES.get(_sk4, _sk4), phone)
                        continue
                    send_message(chat_id, msg)
                    set_desc_cooldown(chat_id, slot)
        return

    results.sort(key=lambda s: s["purchase_dt"])
    latest = results[-1]

    # Check if expiry was already sent
    expiry_day = (latest["purchase_dt"] + timedelta(days=7)).date()
    dedup_key  = normalize_phone(phone) + "_ULTRA7_" + str(expiry_day)
    expiry_already_sent_flag = expiry_already_sent(dedup_key) or ultra_expired_already_sent_history(chat_id)

    # ── EXPIRY STRONGHOLD ──────────────────────────────────────────────────────
    # If latest row is expired — strictly check sheet for a valid NEWER active row
    if not latest["active"]:
        # Check if there's a genuinely active row with a FUTURE expiry (renewal)
        has_valid_renewal = any(
            r["active"] and r["purchase_dt"] > latest["purchase_dt"]
            for r in results
        )
        if has_valid_renewal:
            # Genuine renewal found — fall through to creds
            pass
        elif not expiry_already_sent_flag:
            # First time expiry — send the message
            expired_msg = "❌ Your ULTRA subscription has expired."
            send_message(chat_id, expired_msg)
            _record_chat_message(chat_id, expired_msg, "outgoing")
            _ultra_live_desc2 = NUMBER_MENU.get(3)
            if _ultra_live_desc2:
                time.sleep(1)
                send_message(chat_id, _ultra_live_desc2)
            set_cooldown(chat_id, 300)
            return
        else:
            # Expiry already sent — don't repeat expiry message.
            # Send the ULTRA description ONCE only (permanent flag, never again).
            _exp_desc_key = normalize_phone(phone) + "_expired_desc_sent_once"
            with _expiry_memory_lock:
                _desc_already_sent = _exp_desc_key in _expiry_sent_memory
            if not _desc_already_sent:
                desc = NUMBER_MENU.get(3)
                if desc:
                    send_message(chat_id, desc)
                with _expiry_memory_lock:
                    _expiry_sent_memory[_exp_desc_key] = sl_now()
                save_memory()
            set_cooldown(chat_id, 300)
            return

# ═══════════════════════════════════════════════════════════════════════════════
# VEO LICENSE MANAGER — HTTP calls to flow server
# ═══════════════════════════════════════════════════════════════════════════════

def _veo_api_get(path):
    try:
        r = requests.get(
            FLOW_SERVER_URL + path,
            params={"secret": FLOW_ADMIN_SECRET},
            timeout=10
        )
        return r.status_code, r.json()
    except Exception as e:
        return 500, {"error": str(e)}

def _veo_api_post(path, data):
    try:
        r = requests.post(
            FLOW_SERVER_URL + path,
            json={**data, "secret": FLOW_ADMIN_SECRET},
            timeout=10
        )
        return r.status_code, r.json()
    except Exception as e:
        return 500, {"error": str(e)}

def _veo_days_left(expires_str):
    try:
        try:
            expiry = datetime.fromisoformat(expires_str)
        except:
            expiry = datetime.strptime(expires_str, "%Y-%m-%d")
        # Use SL time for accurate local comparison
        now_sl = datetime.now(SL_TZ).replace(tzinfo=None)
        delta = expiry - now_sl
        return delta.days
    except:
        return None

def _veo_remaining_str(expires_str):
    try:
        if not expires_str:
            return "Unknown"
        try:
            expiry = datetime.fromisoformat(expires_str)
        except:
            expiry = datetime.strptime(expires_str, "%Y-%m-%d")
        # Use SL time for accurate local comparison
        now = datetime.now(SL_TZ).replace(tzinfo=None)
        delta = expiry - now
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "EXPIRED"
        days    = total_seconds // 86400
        hours   = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        if days > 0:
            return str(days) + "d " + str(hours) + "h"
        elif hours > 0:
            return str(hours) + "h " + str(minutes) + "m"
        else:
            return str(minutes) + "m"
    except:
        return "Unknown"

def _veo_fmt_date(d):
    try:
        if isinstance(d, str):
            try:
                dt = datetime.fromisoformat(d)
                if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                    return dt.strftime("%d %b %Y")
                return dt.strftime("%d %b %Y %H:%M")
            except:
                d = datetime.strptime(d, "%Y-%m-%d").date()
        return d.strftime("%d %b %Y")
    except:
        return str(d)

def _parse_veo_duration(args):
    import re
    combined = "".join(args).lower().strip()
    if re.fullmatch(r'\d+', combined):
        days = int(combined)
        return days * 24, str(days) + "d"
    days  = 0
    hours = 0
    d_match = re.search(r'(\d+)d', combined)
    h_match = re.search(r'(\d+)h', combined)
    if not d_match and not h_match:
        raise ValueError("Invalid duration")
    if d_match: days  = int(d_match.group(1))
    if h_match: hours = int(h_match.group(1))
    total_hours = days * 24 + hours
    if total_hours <= 0:
        raise ValueError("Duration must be > 0")
    parts = []
    if days:  parts.append(str(days) + "d")
    if hours: parts.append(str(hours) + "h")
    return total_hours, " ".join(parts)

def _handle_veo_command(chat_id, text):
    parts = text.split()
    if len(parts) < 2:
        _veo_help(chat_id)
        return
    sub = parts[1].lower()

    if sub == "add":
        if len(parts) < 4:
            send_message(chat_id, "❌ Usage: !veo add <key> <duration>\nExamples:\n!veo add 94778315058 30\n!veo add 94778315058 7d\n!veo add 94778315058 8h\n!veo add 94778315058 7d8h")
            return
        key = parts[2].strip()
        try:
            total_hours, dur_str = _parse_veo_duration(parts[3:])
        except ValueError:
            send_message(chat_id, "❌ Invalid duration. Use: 30 / 7d / 8h / 7d8h")
            return
        expires = (sl_now().replace(tzinfo=None) + timedelta(hours=total_hours)).strftime("%Y-%m-%dT%H:%M:%S")
        status, resp = _veo_api_post("/admin/add-license", {"key": key, "expires": expires, "plan": "ultra"})
        if status == 200:
            send_message(chat_id,
                "✅ *VEO License Added*\n\n"
                "🔑 Key: " + key + "\n"
                "📅 Expires: " + _veo_fmt_date(expires) + "\n"
                "⏳ Duration: " + dur_str
            )
        elif status == 409:
            existing = resp.get("expires", "")
            send_message(chat_id,
                "⚠️ *License Already Exists*\n\n"
                "🔑 Key: " + key + "\n"
                "📅 Current Expiry: " + _veo_fmt_date(existing) + "\n"
                "Use !veo extend or !veo revoke first."
            )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub == "revoke":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !veo revoke <key>")
            return
        key = parts[2].strip()
        status, resp = _veo_api_post("/admin/revoke-license", {"key": key})
        if status == 200:
            send_message(chat_id,
                "🚫 *VEO License Revoked*\n\n"
                "🔑 Key: " + key + "\n"
                "⚠️ User will be logged out within 3 minutes."
            )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub == "extend":
        if len(parts) < 4:
            send_message(chat_id, "❌ Usage: !veo extend <key> <duration>\nExamples: 7d / 8h / 7d8h")
            return
        key = parts[2].strip()
        try:
            total_hours, dur_str = _parse_veo_duration(parts[3:])
        except ValueError:
            send_message(chat_id, "❌ Invalid duration. Use: 30 / 7d / 8h / 7d8h")
            return
        status, resp = _veo_api_post("/admin/extend-license", {"key": key, "hours": total_hours})
        if status == 200:
            new_expiry = resp.get("expires", "")
            remaining  = _veo_remaining_str(new_expiry)
            send_message(chat_id,
                "✅ *VEO License Extended*\n\n"
                "🔑 Key: " + key + "\n"
                "📅 New Expiry: " + _veo_fmt_date(new_expiry) + "\n"
                "⏳ Added: " + dur_str + "\n"
                "⏳ Remaining: " + remaining
            )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub == "reduce":
        if len(parts) < 4:
            send_message(chat_id, "❌ Usage: !veo reduce <key> <duration>\nExamples: 7d / 8h / 7d8h")
            return
        key = parts[2].strip()
        try:
            total_hours, dur_str = _parse_veo_duration(parts[3:])
        except ValueError:
            send_message(chat_id, "❌ Invalid duration. Use: 30 / 7d / 8h / 7d8h")
            return
        status, resp = _veo_api_post("/admin/reduce-license", {"key": key, "hours": total_hours})
        if status == 200:
            new_expiry = resp.get("expires", "")
            remaining  = _veo_remaining_str(new_expiry)
            if remaining == "EXPIRED":
                # Auto-revoke since reduce pushed past today
                rev_status, rev_resp = _veo_api_post("/admin/revoke-license", {"key": key})
                if rev_status == 200:
                    send_message(chat_id,
                        "🚫 *License Auto-Revoked*\n\n"
                        "🔑 Key: " + key + "\n"
                        "⚠️ Reduce pushed expiry past today — license revoked automatically."
                    )
                else:
                    send_message(chat_id, "⚠️ Reduce pushed past today. Auto-revoke failed: " + rev_resp.get("error", ""))
            else:
                send_message(chat_id,
                    "✅ *VEO License Reduced*\n\n"
                    "🔑 Key: " + key + "\n"
                    "📅 New Expiry: " + _veo_fmt_date(new_expiry) + "\n"
                    "⏳ Removed: " + dur_str + "\n"
                    "⏳ Remaining: " + remaining
                )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub == "check":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !veo check <key>")
            return
        key = parts[2].strip()
        status, resp = _veo_api_get("/admin/check-license?key=" + key)
        if status == 200:
            entry        = resp.get("entry", {})
            expires      = entry.get("expires", "")
            plan         = entry.get("plan", "ultra")
            dl           = _veo_days_left(expires)
            exempt       = resp.get("exempt", False)
            device_limit = resp.get("device_limit", entry.get("device_limit", 1))
            # Handle both old single-string and new list format
            devices = resp.get("devices", resp.get("device", None))
            if isinstance(devices, str):
                devices = [devices] if devices else []
            elif devices is None:
                devices = []
            device_count = len(devices)
            remaining   = _veo_remaining_str(expires)
            if dl is None:       status_str = "❓ Unknown"
            elif dl < 0:         status_str = "🔴 EXPIRED (" + str(abs(dl)) + " days ago)"
            elif dl == 0:        status_str = "🟡 Expires TODAY (" + remaining + " left)"
            elif dl <= 3:        status_str = "🟡 " + remaining + " left"
            else:                status_str = "🟢 Active — " + remaining + " left"
            if exempt:
                device_str = "Any (exempt)"
            elif device_count == 0:
                device_str = "Not registered"
            else:
                device_str = str(device_count) + "/" + str(device_limit) + " slot(s) used"
            # Build device list lines
            device_lines = ""
            if not exempt and devices:
                for i, d in enumerate(devices, 1):
                    if isinstance(d, dict):
                        dev_id   = d.get("id", "")
                        reg_at   = d.get("registered_at", "Unknown")
                        last_seen = d.get("last_seen", None)
                        is_active = d.get("active", False)
                        short    = dev_id[:16] + "..." if len(dev_id) > 16 else dev_id
                        try:
                            reg_fmt = datetime.strptime(reg_at, "%Y-%m-%dT%H:%M:%S").strftime("%d %b %Y %H:%M")
                        except:
                            reg_fmt = reg_at
                        try:
                            if last_seen:
                                ls_clean = last_seen.replace('Z', '+00:00')
                                ls_dt = datetime.fromisoformat(ls_clean)
                                if ls_dt.tzinfo is None:
                                    from datetime import timezone
                                    ls_dt = ls_dt.replace(tzinfo=timezone.utc)
                                # Convert UTC to SL time (UTC+5:30)
                                ls_sl = ls_dt + timedelta(hours=5, minutes=30)
                                ls_fmt = ls_sl.strftime("%d %b %Y %H:%M") + " (SL)"
                            else:
                                ls_fmt = "Never"
                        except:
                            ls_fmt = last_seen or "Never"
                        status_icon = "🟢 Online" if is_active else "⚫ Offline"
                        device_lines += (
                            "\n   " + str(i) + ". " + short +
                            "\n      📅 Registered: " + reg_fmt +
                            "\n      🕐 Last Seen: " + ls_fmt +
                            "\n      📶 Status: " + status_icon
                        )
                    else:
                        short = str(d)[:16] + "..."
                        device_lines += "\n   " + str(i) + ". " + short
            send_message(chat_id,
                "📋 *VEO License Info*\n\n"
                "🔑 Key: " + key + "\n"
                "📦 Plan: " + plan.upper() + "\n"
                "📅 Expires: " + _veo_fmt_date(expires) + "\n"
                "📊 Status: " + status_str + "\n"
                "💻 Devices: " + device_str + device_lines
            )
        else:
            send_message(chat_id, "❌ License not found: " + key)

    elif sub == "resetdevice":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !veo resetdevice <key>")
            return
        key = parts[2].strip()
        status, resp = _veo_api_get("/admin/reset-device?key=" + key)
        if status == 200:
            revoked = resp.get("revoked_count", 0)
            logout_msg = "\n⚠️ " + str(revoked) + " device(s) will be logged out automatically." if revoked else ""
            send_message(chat_id,
                "✅ *Device Reset*\n\n"
                "🔑 Key: " + key + "\n"
                "💻 Device lock cleared — user can activate from any device." +
                logout_msg
            )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub == "list":
        status, resp = _veo_api_get("/admin/licenses")
        if status != 200:
            send_message(chat_id, "❌ Failed to fetch licenses: " + resp.get("error", ""))
            return
        licenses = resp.get("licenses", {})
        devices  = resp.get("devices", {})
        exempt   = resp.get("exempt", [])
        if not licenses:
            send_message(chat_id, "📋 No VEO licenses found.")
            return
        active = expired = expiring = 0
        lines = ["📋 *VEO License List*\n"]
        # Sort by remaining duration descending (highest first)
        def _sort_key(item):
            dl = _veo_days_left(item[1].get("expires", ""))
            return dl if dl is not None else -9999
        sorted_licenses = sorted(licenses.items(), key=_sort_key, reverse=True)
        for key, entry in sorted_licenses:
            expires = entry.get("expires", "")
            dl = _veo_days_left(expires)
            dev = devices.get(key, [])
            if isinstance(dev, str):
                dev = [dev] if dev else []
            dev_count    = len(dev)
            dev_limit    = int(entry.get("device_limit", 1))
            is_exempt    = key in exempt
            if dl is None:    icon = "❓"
            elif dl < 0:      icon = "🔴"; expired += 1
            elif dl <= 3:     icon = "🟡"; expiring += 1; active += 1
            else:             icon = "🟢"; active += 1
            if is_exempt:     device_str = "∞"
            elif dev_count:   device_str = str(dev_count) + "/" + str(dev_limit)
            else:             device_str = "–"
            lines.append(icon + " " + key + " — " + _veo_fmt_date(expires) + " (" + str(dl) + "d) [" + device_str + "]")
        lines.append("\n🟢 Active: " + str(active) + "  🟡 Expiring: " + str(expiring) + "  🔴 Expired: " + str(expired))
        lines.append("💻 [∞=exempt  ✓=registered  –=none]")
        send_message(chat_id, "\n".join(lines))

    elif sub == "purge":
        # Remove all expired/revoked licenses
        status, resp = _veo_api_get("/admin/licenses")
        if status != 200:
            send_message(chat_id, "❌ Failed to fetch licenses: " + resp.get("error", ""))
            return
        licenses = resp.get("licenses", {})
        to_purge = []
        for key, entry in licenses.items():
            dl = _veo_days_left(entry.get("expires", ""))
            if dl is None or dl < 0:
                to_purge.append(key)
        if not to_purge:
            send_message(chat_id, "✅ No expired licenses to purge.")
            return
        purged = []
        failed = []
        for key in to_purge:
            s, r = _veo_api_post("/admin/revoke-license", {"key": key})
            if s == 200:
                purged.append(key)
            else:
                failed.append(key)
        msg = "🗑 *Purge Complete*\n\n"
        msg += "✅ Purged " + str(len(purged)) + " expired license(s)\n"
        if purged:
            msg += "\n".join("  • " + k for k in purged)
        if failed:
            msg += "\n❌ Failed: " + ", ".join(failed)
        send_message(chat_id, msg)

    elif sub == "extendall":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !veo extendall <duration>\nExample: !veo extendall 7d")
            return
        try:
            total_hours, dur_str = _parse_veo_duration(parts[2:])
        except ValueError:
            send_message(chat_id, "❌ Invalid duration. Use: 30 / 7d / 8h / 7d8h")
            return
        status, resp = _veo_api_get("/admin/licenses")
        if status != 200:
            send_message(chat_id, "❌ Failed to fetch licenses: " + resp.get("error", ""))
            return
        licenses = resp.get("licenses", {})
        if not licenses:
            send_message(chat_id, "📋 No licenses found.")
            return
        success = failed = skipped = 0
        for key, entry in licenses.items():
            dl = _veo_days_left(entry.get("expires", ""))
            if dl is None or dl < 0:
                skipped += 1
                continue
            s, r = _veo_api_post("/admin/extend-license", {"key": key, "hours": total_hours})
            if s == 200:
                success += 1
            else:
                failed += 1
        send_message(chat_id,
            "✅ *Extend All Complete*\n\n"
            "⏳ Added: " + dur_str + " to each active license\n"
            "✅ Extended: " + str(success) + "\n"
            "⏭ Skipped (expired): " + str(skipped) + "\n"
            + ("❌ Failed: " + str(failed) if failed else "")
        )

    elif sub == "reduceall":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !veo reduceall <duration>\nExample: !veo reduceall 1d")
            return
        try:
            total_hours, dur_str = _parse_veo_duration(parts[2:])
        except ValueError:
            send_message(chat_id, "❌ Invalid duration. Use: 30 / 7d / 8h / 7d8h")
            return
        status, resp = _veo_api_get("/admin/licenses")
        if status != 200:
            send_message(chat_id, "❌ Failed to fetch licenses: " + resp.get("error", ""))
            return
        licenses = resp.get("licenses", {})
        if not licenses:
            send_message(chat_id, "📋 No licenses found.")
            return
        success = failed = auto_revoked = 0
        for key in licenses:
            s, r = _veo_api_post("/admin/reduce-license", {"key": key, "hours": total_hours})
            if s == 200:
                new_expiry = r.get("expires", "")
                remaining = _veo_remaining_str(new_expiry)
                if remaining == "EXPIRED":
                    rev_s, _ = _veo_api_post("/admin/revoke-license", {"key": key})
                    if rev_s == 200:
                        auto_revoked += 1
                    else:
                        failed += 1
                else:
                    success += 1
            else:
                failed += 1
        send_message(chat_id,
            "✅ *Reduce All Complete*\n\n"
            "⏳ Removed: " + dur_str + " from each license\n"
            "✅ Success: " + str(success) + "\n"
            + ("🚫 Auto-revoked (expired): " + str(auto_revoked) + "\n" if auto_revoked else "")
            + ("❌ Failed: " + str(failed) if failed else "")
        )

    elif sub == "setcookies":
        # Format: !veo setcookies\n<full netscape cookie string>
        lines = text.split("\n", 1)
        if len(lines) < 2 or not lines[1].strip():
            send_message(chat_id,
                "❌ Format:\n!veo setcookies\n<paste full Netscape cookie string here>"
            )
            return
        cookie_text = lines[1].strip()
        status, resp = _veo_api_post("/admin/set-cookies", {"cookies": cookie_text})
        if status == 200:
            send_message(chat_id,
                "✅ *Cookies Updated*\n\n"
                "📦 Length: " + str(resp.get("length", 0)) + " chars\n"
                "All active licenses will use the new cookies immediately."
            )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub == "getcookies":
        status, resp = _veo_api_get("/admin/get-cookies")
        if status == 200:
            length  = resp.get("length", 0)
            cookies = resp.get("cookies_netscape", "")
            preview = cookies[:80].replace("\n", "\\n") + ("..." if len(cookies) > 80 else "")
            send_message(chat_id,
                "🍪 *Current Cookies*\n\n"
                "📦 Length: " + str(length) + " chars\n"
                "👁 Preview: " + preview
            )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub == "setlimit":
        if len(parts) < 4:
            send_message(chat_id, "❌ Usage: !veo setlimit <key> <limit>\nExample: !veo setlimit 94771234567 3")
            return
        key = parts[2].strip()
        try:
            limit = int(parts[3].strip())
            if limit < 1:
                raise ValueError
        except:
            send_message(chat_id, "❌ Limit must be a positive number.")
            return
        status, resp = _veo_api_post("/admin/set-device-limit", {"key": key, "limit": limit})
        if status == 200:
            purged = resp.get("purged_count", 0)
            old_limit = resp.get("old_limit", 1)
            purge_msg = "\n⚠️ " + str(purged) + " device(s) randomly logged out." if purged > 0 else ""
            send_message(chat_id,
                "✅ *Device Limit Updated*\n\n"
                "🔑 Key: " + key + "\n"
                "📱 Old Limit: " + str(old_limit) + "\n"
                "📱 New Limit: " + str(limit) +
                purge_msg
            )
        else:
            send_message(chat_id, "❌ Failed: " + resp.get("error", "unknown error"))

    elif sub in ("help", "?"):
        _veo_help(chat_id)
    else:
        send_message(chat_id, "❓ Unknown: !veo " + sub + "\nSend !veo help for commands.")

def _veo_help(chat_id):
    send_message(chat_id,
        "🎬 *VEO 3 Ultra — License Manager*\n\n"
        "!veo add <key> <days> — Add license\n"
        "!veo revoke <key> — Revoke immediately\n"
        "!veo extend <key> <days> — Add days\n"
        "!veo reduce <key> <days> — Remove days\n"
        "!veo check <key> — Check status\n"
        "!veo resetdevice <key> — Clear device lock\n"
        "!veo setlimit <key> <limit> — Set device limit\n"
        "!veo list — All licenses (sorted by duration)\n"
        "!veo purge — Delete all expired licenses\n"
        "!veo extendall <duration> — Extend all licenses\n"
        "!veo reduceall <duration> — Reduce all licenses\n\n"
        "🍪 *Cookies*\n"
        "!veo setcookies\\n<paste> — Update cookies\n"
        "!veo getcookies — Check current cookies"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SGD ACCESS — LICENSE MANAGER (signaturedigital.asia cookie injection)
# ═══════════════════════════════════════════════════════════════════════════════

def _sgd_api(method, endpoint, payload=None):
    """Call the SGD Flask server with admin secret."""
    try:
        url = SGD_SERVER_URL + endpoint
        if method == "GET":
            params = {"secret": SGD_ADMIN_TOKEN}
            if payload:
                params.update(payload)
            res = requests.get(url, params=params, timeout=10)
        else:
            body = dict(payload or {})
            body["secret"] = SGD_ADMIN_TOKEN
            res = requests.post(url, json=body, timeout=10)
        if res.status_code == 401:
            return {"success": False, "error": "Unauthorized — check SGD_ADMIN_TOKEN matches on both server and bot"}
        if res.status_code == 404:
            return {"success": False, "error": "Not found"}
        if res.status_code == 409:
            data = res.json()
            return {"success": False, "error": "Key already exists. Expires: " + data.get("expires", "?")}
        return res.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

def _sgd_fmt_date(iso_str):
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y %I:%M %p")
    except Exception:
        return iso_str

def _handle_sgd_command(chat_id, text):
    parts = text.strip().split()
    sub   = parts[1].lower() if len(parts) > 1 else "help"

    # ── !sgd add <key> <days> ────────────────────────────────────────────────
    if sub == "add":
        if len(parts) < 4:
            send_message(chat_id, "❌ Usage: !sgd add <key> <days>\nExample: !sgd add 8273 30")
            return
        key = parts[2].strip().upper()
        try:
            days = int(parts[3].strip())
        except ValueError:
            send_message(chat_id, "❌ Days must be a number.")
            return
        from datetime import datetime, timedelta
        expires = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        result = _sgd_api("POST", "/admin/add-license", {"key": key, "expires": expires, "plan": "sg"})
        if result.get("success"):
            expiry = _sgd_fmt_date(result.get("expires", ""))
            send_message(chat_id,
                "✅ *SGD License Created*\n\n"
                "🔑 Key: " + key + "\n"
                "📅 Expires: " + expiry + "\n"
                "⏳ Duration: " + str(days) + " days"
            )
        else:
            send_message(chat_id, "❌ SGD Add Error: " + str(result.get("error", result)))

    # ── !sgd revoke <key> ─────────────────────────────────────────────────────
    elif sub == "revoke":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !sgd revoke <license_key>")
            return
        key    = parts[2].strip().upper()
        result = _sgd_api("POST", "/admin/revoke-license", {"key": key})
        if result.get("success"):
            send_message(chat_id, "✅ License *" + key + "* has been revoked.")
        else:
            send_message(chat_id, "❌ Error: " + str(result.get("error", result)))

    # ── !sgd extend <key> <days> ──────────────────────────────────────────────
    elif sub == "extend":
        if len(parts) < 4:
            send_message(chat_id, "❌ Usage: !sgd extend <license_key> <days>")
            return
        key = parts[2].strip().upper()
        try:
            days = int(parts[3].strip())
        except ValueError:
            send_message(chat_id, "❌ Days must be a number.")
            return
        result = _sgd_api("POST", "/admin/extend-license", {"key": key, "hours": days * 24})
        if result.get("success"):
            send_message(chat_id,
                "✅ *" + key + "* extended by " + str(days) + " days.\n"
                "📅 New expiry: " + _sgd_fmt_date(result.get("expires"))
            )
        else:
            send_message(chat_id, "❌ Error: " + str(result.get("error", result)))

    # ── !sgd check <key> ──────────────────────────────────────────────────────
    elif sub == "check":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !sgd check <license_key>")
            return
        key    = parts[2].strip().upper()
        result = _sgd_api("GET",  "/admin/check-license", {"key": key})
        if "entry" in result:
            lic     = result["entry"]
            devs    = result.get("devices", [])
            count   = result.get("device_count", 0)
            limit   = result.get("device_limit", 1)
            expiry  = _sgd_fmt_date(lic.get("expires"))
            actives = [d for d in devs if d.get("active")]
            dev_str = str(count) + "/" + str(limit) + " device(s)" + (" — " + str(len(actives)) + " active" if actives else "")
            send_message(chat_id,
                "🔑 *SGD License Info*\n\n"
                "Key: " + key + "\n"
                "📅 Expires: " + expiry + "\n"
                "🖥 Devices: " + dev_str + "\n"
                "📋 Plan: " + lic.get("plan", "sg").upper()
            )
        else:
            send_message(chat_id, "❌ " + result.get("error", "Not found"))

    # ── !sgd list [active|revoked|all] ────────────────────────────────────────
    elif sub == "list":
        filter_type = parts[2].strip().lower() if len(parts) >= 3 else "active"
        result = _sgd_api("GET", "/admin/licenses")
        if "error" in result:
            send_message(chat_id, "❌ Error: " + str(result.get("error", result)))
            return
        licenses_dict = result.get("licenses", {})
        now = __import__("datetime").datetime.now()
        items = []
        for k, v in licenses_dict.items():
            try:
                exp = __import__("datetime").datetime.fromisoformat(v.get("expires",""))
                status = "expired" if now > exp else "active"
            except Exception:
                status = "unknown"
            items.append((k, v, status))
        if filter_type != "all":
            items = [(k,v,s) for k,v,s in items if s == filter_type]
        if not items:
            send_message(chat_id, "No " + filter_type + " SGD licenses found.")
            return
        lines = ["📋 *SGD Licenses (" + filter_type.capitalize() + ")* — " + str(len(items)) + " total\n"]
        for k, v, status in items[:20]:
            emoji  = "✅" if status == "active" else "⏰"
            expiry = _sgd_fmt_date(v.get("expires"))
            lines.append(emoji + " " + k + " | 📅 " + expiry)
        if len(items) > 20:
            lines.append("...and " + str(len(items) - 20) + " more.")
        send_message(chat_id, "\n\n".join(lines))

    # ── !sgd resetdevice <key> ────────────────────────────────────────────────
    elif sub == "resetdevice":
        if len(parts) < 3:
            send_message(chat_id, "❌ Usage: !sgd resetdevice <license_key>")
            return
        key    = parts[2].strip().upper()
        result = _sgd_api("GET",  "/admin/reset-device", {"key": key})
        if result.get("success"):
            send_message(chat_id, "✅ Device reset for *" + key + "*. Customer can activate on a new device.")
        else:
            send_message(chat_id, "❌ Error: " + str(result.get("error", result)))

    # ── !sgd setcookies <json> ────────────────────────────────────────────────
    elif sub == "setcookies":
        prefix   = "!sgd setcookies"
        json_str = text.strip()[len(prefix):].strip()
        if not json_str:
            send_message(chat_id, "❌ Usage: !sgd setcookies <json array of cookie objects>")
            return
        try:
            cookies = json.loads(json_str)
            if not isinstance(cookies, list):
                send_message(chat_id, "❌ Cookies must be a JSON array.")
                return
        except json.JSONDecodeError as e:
            send_message(chat_id, "❌ Invalid JSON: " + str(e))
            return
        result = _sgd_api("POST", "/admin/set-cookies", {"cookies": json_str})
        if result.get("success"):
            send_message(chat_id, "✅ Cookies updated on SGD server (" + str(result.get("length", 0)) + " chars).")
        else:
            send_message(chat_id, "❌ Error: " + str(result.get("error", result)))

    # ── !sgd getcookies ───────────────────────────────────────────────────────
    elif sub == "getcookies":
        result = _sgd_api("GET", "/admin/get-cookies")
        if "cookies_netscape" in result:
            length  = result.get("length", 0)
            preview = result.get("cookies_netscape", "")[:200]
            send_message(chat_id,
                "🍪 *SGD Server Cookies*\n\n" +
                "Length: " + str(length) + " chars\n\n" +
                "Preview:\n" + preview + ("..." if length > 200 else "")
            )
        else:
            send_message(chat_id, "❌ Error: " + str(result.get("error", result)))

    elif sub in ("help", "?"):
        send_message(chat_id,
            "🌐 *SGD Access — License Manager*\n\n"
            "!sgd add <key> <days> — Create license\n"
            "!sgd revoke <key> — Revoke license\n"
            "!sgd extend <key> <days> — Extend expiry\n"
            "!sgd check <key> — Check status\n"
            "!sgd list [active|revoked|all] — List licenses\n"
            "!sgd resetdevice <key> — Reset device lock\n\n"
            "🍪 *Cookies*\n"
            "!sgd setcookies <json> — Update cookies\n"
            "!sgd getcookies — View current cookies"
        )
    else:
        send_message(chat_id, "❓ Unknown: !sgd " + sub + "\nSend !sgd help for commands.")


def handle_admin_command(chat_id, text):
    cmd = text.strip().lower()

    if cmd == "!admin":
        send_message(chat_id,
            "╔══════════════════════════╗\n"
            "║   🤖  ULTRA BOT ADMIN    ║\n"
            "╚══════════════════════════╝\n\n"

            "🔐 *Credentials & Slots*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!showcreds — All slots + emails\n"
            "!slotusers — Phones per slot\n"
            "!expiredusers — Expired slot holders\n"
            "!setcreds1\\nuser\\npass\\nextra — Set slot creds\n"
            "!setrefreshmsg <msg> — Custom refresh message\n\n"

            "📋 *Sheet & Records*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!setrecords\\n<phone> - <sub> - <price> — Add sale\n"
            "!deleteexpired — Delete expired rows\n"
            "!testsheet — Test sheet connection\n"
            "!refreshsheet — Force refresh cache\n\n"

            "📊 *Subscriptions & Expiry*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!sendallexpired — Send expiry to ALL users with passed date\n"
            "!checksub <software> — List all buyers\n"
            "!checkexpiry <phone> — Full sub details\n"
            "!adddays <days> <phone> <sub> — Extend sub\n"
            "!addultratime <phone> <hours> — Add hours to Ultra expiry\n"
            "!extendall <software> <hours>h — Extend ALL active customers of a software\n"
            "!sendsub <software> <message> — Broadcast message to all active customers\n"
            "!refund <phone> — Refund breakdown\n"
            "!showexpiry <date> — Subs expiring on date\n"
            "!sendexpiry <date> — Manually send expiry\n"
            "!expirylog — Expiry log (30 days)\n\n"

            "📝 *Descriptions & Greeting*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!showdesc — All descriptions (greeting order)\n"
            "!showdesc N — Preview position N\n"
            "!setdesc N\\n<text> — Update description\n"
            "!setgreeting\\n<msg> — Set greeting message\n"
            "!showgreeting — Preview greeting\n\n"

            "🚫 *Block & Cooldowns*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!blockuser <phone> — Block number\n"
            "!unblockuser <phone> — Unblock number\n"
            "!blocklist — Show blocked numbers\n"
            "!clearallcooldowns — Clear all cooldowns\n"
            "!clearcooldown <phone> — Clear one cooldown\n\n"

            "🧪 *Test*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!testgreeting  !testexpired\n"
            "!testall  !testrenew\n\n"

            "🎬 *VEO 3 Ultra Licenses*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
"!veo add <key> <days>\n"
            "!veo revoke <key>\n"
            "!veo extend <key> <days>\n"
            "!veo reduce <key> <days>\n"
            "!veo check <key>\n"
            "!veo resetdevice <key>\n"
            "!veo list\n\n"

            "🌐 *SGD Access Licenses*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!sgd add <key> <days>\n"
            "!sgd revoke <key>\n"
            "!sgd extend <key> <days>\n"
            "!sgd check <key>\n"
            "!sgd list [active|revoked|all]\n"
            "!sgd resetdevice <key>\n"
            "!sgd setcookies <json>\n"
            "!sgd getcookies\n\n"

            "🛑 *Bot Control*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
            "!botstop — Stop bot\n"
            "!startbot — Resume bot\n"
            "!pausebot <minutes> — Pause for N minutes\n"
        )
        return


    # ─── VEO LICENSE MANAGER ─────────────────────────────────────────────────
    if text.strip().lower().startswith("!veo"):
        _handle_veo_command(chat_id, text.strip())
        return

    # ─── SGD ACCESS LICENSE MANAGER ──────────────────────────────────────────
    if text.strip().lower().startswith("!sgd"):
        _handle_sgd_command(chat_id, text.strip())
        return

    # !setrecords — manually add records to sheet
    # Format (one per line after command): 75-578-8736 - ULTRA - 1950
    if text.strip().lower().startswith("!setrecords"):
        lines = text.strip().split("\n")[1:]
        lines = [l.strip() for l in lines if l.strip()]
        if not lines:
            send_message(chat_id,
                "Format:\n!setrecords\n75-578-8736 - ULTRA - 1950\n76-123-4567 - CHAT - 2750\n\nOne record per line. Date = today."
            )
            return
        today_str = (sl_now() - timedelta(days=1)).strftime("%d-%b-%y")
        records   = []
        errors    = []
        for line in lines:
            parts = [p.strip() for p in line.split("-")]
            if len(parts) < 4:
                errors.append("❌ " + line + " (bad format)")
                continue
            phone_raw = _re.sub(r'\D', '', parts[0] + parts[1] + parts[2])
            if len(phone_raw) == 9:
                phone_raw = "94" + phone_raw
            elif len(phone_raw) == 10 and phone_raw.startswith("0"):
                phone_raw = "94" + phone_raw[1:]
            sub_name    = parts[3].strip().upper()
            phone_sheet = _format_phone_for_sheet(phone_raw)
            records.append({"date_str": today_str, "phone": phone_sheet, "sub": sub_name})
        if not records:
            send_message(chat_id, "❌ No valid records found.\n\n" + "\n".join(errors))
            return
        success = _write_records_to_sheet(records)
        if success:
            msg = "✅ *Added " + str(len(records)) + " records to sheet*\n\n"
            for r in records:
                msg += "• " + r["phone"] + " — " + r["sub"] + "\n"
            if errors:
                msg += "\n⚠️ Skipped:\n" + "\n".join(errors)
            send_message(chat_id, msg)
        else:
            send_message(chat_id, "❌ Failed to write to sheet. Check logs.")
        return

        raw = parts[1].strip()
        phone_digits = _re.sub(r'\D', '', raw)
        if len(phone_digits) == 9:
            phone_digits = "94" + phone_digits
        # Find their latest ULT row and mark EXPIRED in column D
        try:
            creds_g  = get_google_credentials()
            client_g = gspread.authorize(creds_g)
            ss_g     = client_g.open_by_key(GOOGLE_SHEET_ID)
            try:
                sheet_g = ss_g.worksheet(SHEET_NAME)
            except Exception:
                sheet_g = ss_g.get_worksheet(0)
            rows_g  = sheet_g.get_all_values()
            local_g = normalize_phone(phone_digits)
            marked  = 0
            for i, row in enumerate(rows_g):
                if i == 0 or len(row) < 3: continue
                if normalize_phone(str(row[1])) != local_g: continue
                if not str(row[2]).strip().upper().startswith("ULT"): continue
                col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
                if col_d == "EXPIRED": continue
                sheet_g.update_cell(i + 1, 4, "EXPIRED")
                marked += 1
                time.sleep(0.3)
            invalidate_sheet_cache()
            if marked:
                send_message(chat_id, "✅ Marked *" + str(marked) + "* row(s) as EXPIRED for " + _format_phone_for_sheet(phone_digits) + "\nThey will no longer receive creds.")
            else:
                send_message(chat_id, "⚠️ No untagged ULT rows found for " + raw)
        except Exception as _exc33:
            send_message(chat_id, "❌ Failed: " + str(_exc33))
        return


    # !blockuser <phone> — block a number entirely
    if cmd.startswith("!blockuser"):
        parts = text.strip().split(None, 1)
        if len(parts) < 2:
            send_message(chat_id, "Format: !blockuser 77-831-5058")
            return
        raw     = parts[1].strip()
        digits  = _re.sub(r'\D', '', raw)
        if len(digits) == 9: digits = "94" + digits
        block_number(digits)
        save_memory()
        send_message(chat_id, "🚫 *Blocked:* " + _format_phone_for_sheet(digits) + "\nAll messages from this number will be silently ignored.")
        return

    # !unblockuser <phone> — unblock a number
    if cmd.startswith("!unblockuser"):
        parts = text.strip().split(None, 1)
        if len(parts) < 2:
            send_message(chat_id, "Format: !unblockuser 77-831-5058")
            return
        raw     = parts[1].strip()
        digits  = _re.sub(r'\D', '', raw)
        if len(digits) == 9: digits = "94" + digits
        unblock_number(digits)
        save_memory()
        send_message(chat_id, "✅ *Unblocked:* " + _format_phone_for_sheet(digits))
        return

    # !blocklist — show all blocked numbers
    if cmd == "!clearblocklistall":
        with _blocked_numbers_lock:
            count = len(_blocked_numbers)
            _blocked_numbers.clear()
        save_memory()
        send_message(chat_id, "✅ Cleared *" + str(count) + "* numbers from block list. All previously blocked users can now message the bot.")
        return

    if cmd == "!blocklist":
        with _blocked_numbers_lock:
            blocked = list(_blocked_numbers)
        if not blocked:
            send_message(chat_id, "✅ No blocked numbers.")
        else:
            msg = "🚫 *Blocked Numbers (" + str(len(blocked)) + ")*\n\n"
            for ph in blocked:
                msg += "• " + _format_phone_for_sheet(ph) + "\n"
            send_message(chat_id, msg)
        return

    # !clearallcooldowns — clear all active cooldowns for all customers
    if cmd == "!clearallcooldowns":
        with _cooldown_lock:
            count = len(_cooldowns)
            _cooldowns.clear()
        send_message(chat_id, "✅ Cleared *" + str(count) + "* active cooldowns. All customers can now message the bot.")
        return

    # !clearcooldown <phone> — clear cooldown for a stuck customer
    if cmd.startswith("!clearcooldown"):
        parts = text.strip().split(None, 1)
        if len(parts) < 2:
            send_message(chat_id, "Format: !clearcooldown 77-831-5058")
            return
        raw    = parts[1].strip()
        digits = _re.sub(r'\D', '', raw)
        if len(digits) == 9: digits = "94" + digits
        target = to_chat_id(digits)
        with _cooldown_lock:
            removed = _cooldowns.pop(target, None)
        send_message(chat_id,
            ("✅ Cooldown cleared for " + _format_phone_for_sheet(digits) + " — they can now message the bot.")
            if removed else
            ("ℹ️ No active cooldown found for " + _format_phone_for_sheet(digits))
        )
        return



    # !showgreeting — preview current greeting and number map
    if cmd == "!showgreeting":
        with _greeting_store_lock:
            custom_greeting = _greeting_store.get("greeting_text", "")
        with _greeting_number_map_lock:
            current_map = dict(_greeting_number_map)
        greeting_preview = custom_greeting if custom_greeting else "*(using default hardcoded greeting)*"
        map_lines = "\n".join(
            str(k) + " → " + SOFTWARE_NAMES.get(v, "slot " + str(v))
            for k, v in sorted(current_map.items())
        ) if current_map else "*(default map — numbers match slots directly)*"
        send_message(chat_id, "📋 *Current Greeting:*\n\n" + greeting_preview)
        send_message(chat_id, "🔢 *Number → Software Mapping:*\n\n" + map_lines)
        return

    # !setgreeting — update greeting message and remap number→slot from greeting order
    if cmd.startswith("!setgreeting"):
        parts = text.strip().split(None, 1)
        if len(parts) < 2:
            send_message(chat_id, "Format: !setgreeting\n<your greeting message here>")
            return
        new_greeting = parts[1].strip()
        # Parse map from greeting
        new_map = parse_greeting_number_map(new_greeting)
        if len(new_map) < 5:
            send_message(chat_id, "⚠️ Could only detect " + str(len(new_map)) + " software entries from greeting. Check format and try again.\nMapped: " + str(new_map))
            return
        # Apply map
        with _greeting_number_map_lock:
            _greeting_number_map.clear()
            _greeting_number_map.update(new_map)
        # Save to volume
        with _greeting_store_lock:
            _greeting_store["greeting_text"] = new_greeting
            _greeting_store["greeting_number_map"] = {str(k): v for k, v in new_map.items()}
        save_memory()
        map_lines = "\n".join("  " + str(k) + " → slot " + str(v) + " (" + SOFTWARE_NAMES.get(v, "?") + ")" for k, v in sorted(new_map.items()))
        send_message(chat_id,
            "✅ *Greeting updated!*\n\n"
            "📋 *Detected number → software mapping:*\n" + map_lines
        )
        return

    # !setcreds1 — set the single creds slot (username + password + optional extra)
    if text.strip().lower().startswith("!setcreds1"):
        try:
            lines = text.split("\n")
            content = "\n".join(lines[1:]).strip()
            if not content:
                send_message(chat_id,
                    "Format:\n!setcreds1\n<paste your full cookie/file content here>\n\nEverything after the first line will be saved and sent as a .txt file."
                )
                return
            with _creds_slot1_lock:
                _creds_slot1["file_content"] = content
                _creds_slot1["username"] = ""
                _creds_slot1["password"] = ""
                _creds_slot1["cookie_version"] = _creds_slot1.get("cookie_version", 1) + 1
                _new_v = _creds_slot1["cookie_version"]
            save_memory()
            preview = content[:200] + ("..." if len(content) > 200 else "")
            send_message(chat_id,
                "✅ *Cookie updated* (v" + str(_new_v) + ")\n\n"
                "📄 " + str(len(content)) + " characters saved\n\n"
                "Preview:\n_" + preview + "_"
            )

            save_memory()

        except Exception as _exc_sc:
            send_message(chat_id, "❌ Error: " + str(_exc_sc)[:200])
        return

    # !deletecreds — clear the single creds slot
    if cmd == "!deletecreds":
        with _creds_slot1_lock:
            _creds_slot1["file_content"] = ""
            _creds_slot1["username"] = ""
            _creds_slot1["password"] = ""
            _creds_slot1["extra"]    = ""
        save_memory()
        send_message(chat_id, "🗑️ Creds file content cleared.")
        return

    # !resetcredslog — clear the sent log so all customers can receive the file again
    if cmd == "!resetcredslog":
        with _creds_sent_phones_lock:
            count = len(_creds_sent_phones)
            _creds_sent_phones.clear()
        save_memory()
        send_message(chat_id, "✅ Creds sent log cleared — *" + str(count) + "* entries removed.\n\nAll active Ultra customers will receive the file again on next message.")
        return

    # !credslog — show which customers have received the cookie file
    if cmd == "!credslog":
        with _creds_sent_phones_lock:
            sent = dict(_creds_sent_phones)
        if not sent:
            send_message(chat_id, "📋 *Creds Log*\n\nNo customers have received the file yet.")
            return
        lines = ["📋 *Creds Sent* (" + str(len(sent)) + " customers)\n"]
        for p, v in sorted(sent.items()):
            lines.append("• " + _format_phone_for_sheet(p) + " (v" + str(v) + ")")
        send_message(chat_id, "\n".join(lines))
        return

    if cmd == "!credslog":
        with _creds_sent_phones_lock:
            log_copy = dict(_creds_sent_phones)
        if not log_copy:
            send_message(chat_id, "📋 *Creds Reply Log*\n\nNo replies sent yet.")
            return
        # Sort by most recent first
        items = []
        for phone_k, iso_ts in log_copy.items():
            try:
                dt = datetime.fromisoformat(iso_ts)
                items.append((dt, phone_k))
            except Exception:
                continue
        items.sort(key=lambda x: x[0], reverse=True)

        lines_out = ["📋 *Creds Reply Log* (" + str(len(items)) + " users)\n"]
        for dt, phone_k in items[:50]:  # Cap at 50
            _age_secs = (sl_now() - dt).total_seconds()
            if _age_secs < 3600:
                _age = str(int(_age_secs / 60)) + "m ago"
            elif _age_secs < 86400:
                _age = str(int(_age_secs / 3600)) + "h ago"
            else:
                _age = str(int(_age_secs / 86400)) + "d ago"
            _disp_phone = _format_phone_for_sheet(phone_k)
            lines_out.append("• " + _disp_phone + " — " + dt.strftime("%d %b %H:%M") + " (" + _age + ")")
        if len(items) > 50:
            lines_out.append("\n_...showing 50 most recent of " + str(len(items)) + "_")
        send_message(chat_id, "\n".join(lines_out))
        return

    if cmd == "!botstop":
        stop_bot()
        send_message(chat_id,
            "🛑 *BOT STOPPED*\n\n"
            "All background jobs halted.\n"
            "No more expiry messages will be sent.\n\n"
            "Send *!startbot* to resume."
        )
        return

    if cmd in ("!botresume", "!startbot"):
        resume_bot()
        send_message(chat_id, "✅ Bot resumed. Background jobs active again.")
        return

    # !pausebot 30m — pause for a duration then auto-resume
    if cmd.startswith("!pausebot"):
        import re as _re_pause
        parts = text.strip().split(None, 1)
        duration_str = parts[1].strip() if len(parts) > 1 else ""
        if not duration_str:
            send_message(chat_id, "Format: !pausebot 30m\nUnits: m (minutes), h (hours)")
            return
        m = _re_pause.match(r'^(\d+)\s*(m|h)$', duration_str.lower())
        if not m:
            send_message(chat_id, "❌ Invalid format. Examples: !pausebot 30m / !pausebot 2h")
            return
        amount = int(m.group(1))
        unit   = m.group(2)
        seconds = amount * 60 if unit == "m" else amount * 3600
        stop_bot()
        resume_at = sl_now() + timedelta(seconds=seconds)
        resume_str = resume_at.strftime("%I:%M %p")
        send_message(chat_id,
            "⏸️ *Bot paused for " + str(amount) + unit + "*\n\n"
            "Will auto-resume at *" + resume_str + "* SL time.\n"
            "Send *!startbot* to resume early."
        )
        def _auto_resume():
            time.sleep(seconds)
            if bot_is_stopped():
                resume_bot()
                logger.info("Bot auto-resumed after " + str(amount) + unit + " pause.")
        threading.Thread(target=_auto_resume, daemon=True).start()
        return

    if cmd == "!testsheet":
        try:
            rows = get_sheet_rows(force=True)
            msg  = "✅ Sheet connected! " + str(len(rows)) + " rows.\n\nFirst 3:\n"
            for row in rows[:3]:
                msg += str(row) + "\n"
            send_message(chat_id, msg)
        except Exception as _exc37:
            send_message(chat_id, "❌ Sheet error: " + str(_exc37))
        return

    if cmd == "!refreshsheet":
        invalidate_sheet_cache()
        try:
            rows = get_sheet_rows()
            send_message(chat_id, "✅ Sheet refreshed! " + str(len(rows)) + " rows.")
        except Exception as _exc38:
            send_message(chat_id, "❌ Refresh failed: " + str(_exc38))
        return








    # !setdesc <number>
    # <description text (can be multiline)>
    # Number is the GREETING position (e.g. 2 = CapCut in new greeting)
    if text.strip().lower().startswith("!setdesc"):
        lines = text.strip().split("\n", 1)
        header = lines[0].strip()
        parts  = header.split()
        if len(parts) < 2 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 24):
            send_message(chat_id,
                "Format:\n!setdesc <greeting number>\n<description>\n\n"
                "Example:\n!setdesc 2\n✅ CapCut Pro...\n\n"
                "Use !showdesc to see all greeting numbers."
            )
            return
        pos_n  = int(parts[1])
        # Resolve greeting position to canonical slot
        with _greeting_number_map_lock:
            slot_n = _greeting_number_map.get(pos_n, pos_n)
        if len(lines) < 2 or not lines[1].strip():
            send_message(chat_id,
                "❌ No description provided.\n\n"
                "Format:\n!setdesc " + str(pos_n) + "\n<your description here>"
            )
            return
        new_desc = lines[1].strip()
        NUMBER_MENU[slot_n] = new_desc
        _custom_menu_slots.add(slot_n)
        name = GREETING_POSITION_NAMES.get(pos_n, SOFTWARE_NAMES.get(slot_n, "Slot " + str(slot_n)))
        send_message(chat_id,
            "✅ *" + name + "* (greeting #" + str(pos_n) + " → slot " + str(slot_n) + ") description updated!\n\n"
            "Preview:\n" + new_desc
        )
        save_memory()
        return

    # !showdesc <number> — preview current description for a slot
    if cmd.startswith("!showdesc"):
        parts = cmd.split()
        if len(parts) < 2 or not parts[1].isdigit():
            # Show all slots by greeting position
            msg = "📋 *Software Descriptions Status (Greeting Order)*\n━━━━━━━━━━━━━━━━━━━━\n"
            with _greeting_number_map_lock:
                gmap = dict(_greeting_number_map)
            for pos in sorted(gmap.keys()):
                slot_n = gmap[pos]
                name   = GREETING_POSITION_NAMES.get(pos, SOFTWARE_NAMES.get(slot_n, "Slot " + str(slot_n)))
                has    = "✅" if NUMBER_MENU.get(slot_n) else "⬜ empty"
                msg   += str(pos) + ". " + name + " (slot " + str(slot_n) + ") — " + has + "\n"
            msg += "\nUse !showdesc <greeting number> for full preview."
            send_message(chat_id, msg)
            return
        pos_n  = int(parts[1])
        with _greeting_number_map_lock:
            slot_n = _greeting_number_map.get(pos_n, pos_n)
        name   = GREETING_POSITION_NAMES.get(pos_n, SOFTWARE_NAMES.get(slot_n, "Slot " + str(slot_n)))
        desc   = NUMBER_MENU.get(slot_n)
        if desc:
            send_message(chat_id, "📋 *" + name + "* (greeting #" + str(pos_n) + " → slot " + str(slot_n) + "):\n\n" + desc)
        else:
            send_message(chat_id, "⬜ Greeting #" + str(pos_n) + " (*" + name + "*) has no description yet.\nUse !setdesc " + str(slot_n) + " to set one.")
        return


    if cmd == "!testcreds":
        with _creds_slot1_lock:
            _file_content = _creds_slot1.get("file_content", "").strip()
        if not _file_content:
            send_message(chat_id, "❌ No creds file set. Use !setcreds1 first.")
            return
        logger.info("🧪 Admin testing creds file delivery")
        send_file_text(chat_id, _file_content, "ultra_account.txt")
        return

    if cmd == "!testgreeting":
        send_greeting(chat_id)
        return


    if cmd == "!testexpired":
        send_message(chat_id,
            "❌ Your ULTRA subscription has expired.\n\n"
            "📅 Purchase date: March 01, 2026\n"
            "⏰ Expiry: March 08, 2026"
        )
        _td = NUMBER_MENU.get(3)
        if _td:
            time.sleep(1)
            send_message(chat_id, _td)
        return

    if cmd == "!testall":
        send_message(chat_id, "🧪 *TEST 1 — New Customer:*")
        send_greeting(chat_id)
        time.sleep(1)
        send_message(chat_id, "🧪 *TEST 3 — Expired:*")
        send_message(chat_id,
            "❌ Your ULTRA subscription has expired.\n\n"
            "📅 Purchase date: March 01, 2026\n"
            "⏰ Expiry: March 08, 2026"
        )
        _td2 = NUMBER_MENU.get(3)
        if _td2:
            time.sleep(1)
            send_message(chat_id, _td2)
        return

    expiry_test_map = {
        "!testexpiry chatgpt":    "CHATGPT",
        "!testexpiry capcut":     "CAPCUT",
        "!testexpiry gemini":     "GEMINI",
        "!testexpiry envato":     "ENVATO",
        "!testexpiry freepik":    "FREEPIK",
        "!testexpiry perplexity": "PERPLEXITY",
        "!testexpiry veo":        "VEO",
        "!testexpiry gamma":      "GAMMA",
        "!testexpiry adobe":      "ADOBE",
        "!testexpiry grammarly":  "GRAMMARLY",
        "!testexpiry quillbot":   "QUILLBOT",
        "!testexpiry kahoot":     "KAHOOT",
    }
    if cmd in expiry_test_map:
        # Test sends bypass the 24h block — use raw send functions
        sw = expiry_test_map[cmd]
        send_message(chat_id,
            "❌ Your *" + sw + "* subscription has expired.\n\n"
            "📅 Purchased: February 10, 2026\n"
            "⏰ Expired on: March 10, 2026"
        )
        slot = SOFTWARE_KEY_TO_SLOT.get(sw)
        desc = NUMBER_MENU.get(slot) if slot else None
        if desc:
            send_message(chat_id, desc)
        return

    if cmd == "!testrenew":
        send_message(chat_id, RENEWAL_MESSAGES["CHATGPT"])
        return

    # !showexpiry mar17 — show all subscriptions expiring on a date
    if cmd.startswith("!showexpiry"):
        date_part   = text.strip()[len("!showexpiry"):].strip()
        target_date = _parse_short_date(date_part) if date_part else sl_today()
        if not target_date:
            send_message(chat_id,
                "❌ Could not parse date: *" + date_part + "*\n\n"
                "Examples:\n!showexpiry mar17\n!showexpiry mar 17\n!showexpiry 2026-03-17"
            )
            return
        try:
            rows  = get_sheet_rows()
            lines = []
            seen  = set()
            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                purchase_date = parse_date(str(row[0]).strip())
                phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
                sub_name      = str(row[2]).strip()
                software_key  = get_general_software_key(sub_name)
                if not software_key or not purchase_date:
                    continue
                if software_key == "ULTRA":
                    expiry_day = (purchase_date + timedelta(days=7)).date()
                    notify_day = expiry_day  # notification fires at 2PM on expiry day
                else:
                    expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()
                    notify_day = expiry_day
                if notify_day != target_date:
                    continue
                dedup_key = phone + "_" + software_key + "_" + str(expiry_day)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                target    = to_chat_id(phone)
                mem_key   = target + ("_ULTRA7_" + str(expiry_day) if software_key == "ULTRA" else "_" + software_key)
                sent      = "✅ sent" if expiry_already_sent(mem_key) else "⏳ pending"
                renewed   = " 🔄renewed" if has_renewed(phone, software_key, purchase_date, rows) else ""
                fmt_phone = _format_phone_for_sheet(phone)
                lines.append("• " + fmt_phone + " — " + software_key + " | " + sent + renewed)
            if not lines:
                send_message(chat_id, "✅ No subscriptions expiring on *" + str(target_date) + "*")
                return
            msg = "📋 *Expiring on " + target_date.strftime("%b %d, %Y") + " — " + str(len(lines)) + " subs*\n\n"
            msg += "\n".join(lines)
            send_message(chat_id, msg)
        except Exception as _exc39:
            send_message(chat_id, "❌ Failed: " + str(_exc39))
            logger.error(traceback.format_exc())
        return

    # !adddays <days> <phone> <software> — extend subscription by adding days to purchase date
    if cmd.startswith("!adddays "):
        parts = text.strip().split(None, 3)
        if len(parts) < 4:
            send_message(chat_id, "Usage: !adddays <days> <phone> <software>\nExample: !adddays 4 0778315058 chat")
            return
        try:
            days_to_add  = int(parts[1])
            ph_input     = parts[2].strip()
            sw_input     = parts[3].strip().upper()
            target_local = normalize_phone(ph_input)

            creds_gs  = get_google_credentials()
            client_gs = gspread.authorize(creds_gs)
            ss_gs     = client_gs.open_by_key(GOOGLE_SHEET_ID)
            try:
                sheet_gs = ss_gs.worksheet(SHEET_NAME)
            except Exception:
                sheet_gs = ss_gs.get_worksheet(0)
            raw_rows = sheet_gs.get_all_values()

            matched_row = None
            matched_idx = None
            for i, row in enumerate(raw_rows):
                if i == 0 or len(row) < 3:
                    continue
                if normalize_phone(str(row[1])) != target_local:
                    continue
                software_key = get_general_software_key(str(row[2]).strip())
                if software_key != get_general_software_key(sw_input):
                    continue
                col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
                if col_d == "EXPIRED":
                    continue
                matched_row = row
                matched_idx = i
                break

            if matched_row is None:
                send_message(chat_id, "❌ No active *" + sw_input + "* subscription found for " + ph_input + ".")
                return

            old_date_str  = str(matched_row[0]).strip()
            old_date      = parse_date(old_date_str)
            if not old_date:
                send_message(chat_id, "❌ Could not parse purchase date: " + old_date_str)
                return

            new_date      = old_date + timedelta(days=days_to_add)
            new_date_str  = new_date.strftime("%d-%b-%y")

            # Write as RAW to prevent Google Sheets auto-reformatting the date
            cell_addr = "A" + str(matched_idx + 1)
            sheet_gs.update(cell_addr, [[new_date_str]], value_input_option="RAW")
            invalidate_sheet_cache()

            # Recalculate expiry for confirmation message
            sub_name = str(matched_row[2]).strip()
            dur_val, dur_unit = extract_duration_from_subscription(sub_name)
            if dur_val and dur_unit:
                old_exp = calculate_expiry_date_from_duration(old_date, dur_val, dur_unit)
                new_exp = calculate_expiry_date_from_duration(new_date, dur_val, dur_unit)
                old_exp_str = old_exp.strftime("%d %b %Y") if old_exp else "N/A"
                new_exp_str = new_exp.strftime("%d %b %Y") if new_exp else "N/A"
            elif get_general_software_key(sub_name) == "ULTRA":
                old_exp_str = (old_date + timedelta(days=SUBSCRIPTION_DAYS)).strftime("%d %b %Y")
                new_exp_str = (new_date + timedelta(days=SUBSCRIPTION_DAYS)).strftime("%d %b %Y")
            else:
                months = get_sub_months(sub_name)
                old_exp_str = add_n_months(old_date, months).strftime("%d %b %Y")
                new_exp_str = add_n_months(new_date, months).strftime("%d %b %Y")

            msg = ("✅ *Subscription Extended*\n\n"
                   "👤 " + _format_phone_for_sheet(target_local) + "\n"
                   "📦 " + sub_name + "\n\n"
                   "📅 Purchase date: " + old_date.strftime("%d %b %Y") + " → *" + new_date.strftime("%d %b %Y") + "*\n"
                   "📆 Expiry:        " + old_exp_str + " → *" + new_exp_str + "*\n"
                   "➕ Days added:   " + str(days_to_add))
            send_message(chat_id, msg)
            logger.info("!adddays: " + str(days_to_add) + "d added to " + target_local + " | " + sub_name)

        except ValueError:
            send_message(chat_id, "❌ Invalid number of days: " + parts[1])
        except Exception as _exc40:
            send_message(chat_id, "❌ Failed: " + str(_exc40))
            logger.error(traceback.format_exc())
        return

    # !refund <phone> — show refund details for all active subs of a customer
    if cmd.startswith("!refund "):
        ph_input     = text.strip().split(None, 1)[1].strip() if len(text.strip().split(None, 1)) > 1 else ""
        if not ph_input:
            send_message(chat_id, "Usage: !refund <phone>\nExample: !refund 0778315058")
            return
        try:
            rows         = get_sheet_rows()
            today        = sl_today()
            refund_local = normalize_phone(ph_input)
            results      = []

            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                phone = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
                if normalize_phone(phone) != refund_local:
                    continue
                sub_name     = str(row[2]).strip()
                software_key = get_general_software_key(sub_name)
                col_d        = str(row[3]).strip().upper() if len(row) > 3 else ""
                purchase_date = parse_date(str(row[0]).strip())
                if not purchase_date or not software_key:
                    continue

                # Duration & expiry
                dur_val, dur_unit = extract_duration_from_subscription(sub_name)
                if dur_val and dur_unit:
                    unit_label   = {"W":"week(s)","M":"month(s)","D":"day(s)"}.get(dur_unit, dur_unit)
                    duration_str = str(dur_val) + " " + unit_label
                    exp_dt       = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                    expiry_day   = exp_dt.date() if exp_dt else None
                    total_days   = (expiry_day - purchase_date.date()).days if expiry_day else None
                elif software_key == "ULTRA":
                    duration_str = "7 days"
                    expiry_day   = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
                    total_days   = SUBSCRIPTION_DAYS
                else:
                    months       = get_sub_months(sub_name)
                    duration_str = str(months) + " month(s)"
                    expiry_day   = add_n_months(purchase_date, months).date()
                    total_days   = (expiry_day - purchase_date.date()).days

                # Skip expired rows
                if col_d == "EXPIRED" or (expiry_day and expiry_day < today):
                    continue

                days_left = (expiry_day - today).days if expiry_day else 0

                # Price
                if software_key == "ULTRA":
                    notify_time = parse_ultra_notify_time(sub_name)
                    all_nums    = re.findall(r'\b(\d{3,5})\b', sub_name)
                    candidates  = [n for n in all_nums if not (notify_time and len(n)==4 and 0<=int(n[:2])<=23 and 0<=int(n[2:])<=59 and (int(n[:2]),int(n[2:]))==notify_time)]
                    price_lkr   = int(candidates[-1]) if candidates else None
                else:
                    pm        = re.search(r'\b(\d{3,6})\b', sub_name)
                    price_lkr = int(pm.group(1)) if pm else None

                # Refund
                if price_lkr and total_days and total_days > 0 and days_left > 0:
                    daily_rate = round(price_lkr / total_days, 2)
                    refund_amt = round(daily_rate * days_left)
                else:
                    daily_rate = None
                    refund_amt = 0

                results.append({
                    "sub_name":     sub_name,
                    "purchase_str": purchase_date.strftime("%d %b %Y"),
                    "expiry_str":   expiry_day.strftime("%d %b %Y") if expiry_day else "N/A",
                    "duration_str": duration_str,
                    "days_left":    days_left,
                    "total_days":   total_days,
                    "price_lkr":    price_lkr,
                    "daily_rate":   daily_rate,
                    "refund_amt":   refund_amt,
                })

            if not results:
                send_message(chat_id, "❌ No active subscriptions found for *" + ph_input + "*.")
                return

            lines = ["💰 *Refund Summary — " + _format_phone_for_sheet(refund_local) + "*\n"]
            for r in results:
                entry  = "─────────────────\n"
                entry += "📦 " + r["sub_name"] + "\n"
                entry += "📅 Purchased:  " + r["purchase_str"] + "\n"
                entry += "📆 Expires:    " + r["expiry_str"] + "\n"
                entry += "⏳ Duration:   " + r["duration_str"] + " (" + str(r["total_days"]) + " days total)\n"
                entry += "📊 Remaining:  " + str(r["days_left"]) + " days\n"
                entry += "💵 Paid:       LKR " + (str(r["price_lkr"]) if r["price_lkr"] else "N/A") + "\n"
                if r["daily_rate"]:
                    entry += "📉 Daily rate: LKR " + str(r["daily_rate"]) + "/day\n"
                entry += "💸 *Refund:    LKR " + str(r["refund_amt"]) + "*"
                lines.append(entry)

            send_message(chat_id, "\n".join(lines))

        except Exception as _exc41:
            send_message(chat_id, "❌ Failed: " + str(_exc41))
            logger.error(traceback.format_exc())
        return

    # !checksub <software> — list all buyers of a software
    if cmd.startswith("!checksub "):
        parts      = text.strip().split(None, 2)
        sw_input   = parts[1].upper().strip() if len(parts) > 1 else ""
        show_refund = False
        refund_phone = None
        if not sw_input:
            send_message(chat_id, "Usage: !checksub <software> [refund [phone]]\nExample: !checksub adobe\nExample: !checksub adobe refund\nExample: !checksub adobe refund 0778315058")
            return
        try:
            rows  = get_sheet_rows()
            today = sl_today()
            now   = sl_now()
            matched = []

            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                sub_name     = str(row[2]).strip()
                software_key = get_general_software_key(sub_name)
                if not software_key:
                    continue
                # Match by software key OR raw sub name containing input
                if software_key != get_general_software_key(sw_input) and sw_input not in sub_name.upper():
                    continue

                purchase_date = parse_date(str(row[0]).strip())
                phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
                col_d         = str(row[3]).strip().upper() if len(row) > 3 else ""
                if not purchase_date:
                    continue

                # ── Duration & expiry ──────────────────────────────────────
                dur_val, dur_unit = extract_duration_from_subscription(sub_name)
                if dur_val and dur_unit:
                    unit_label   = {"W":"week(s)","M":"month(s)","D":"day(s)"}.get(dur_unit, dur_unit)
                    duration_str = str(dur_val) + " " + unit_label
                    exp_dt       = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                    expiry_day   = exp_dt.date() if exp_dt else None
                    total_days   = (expiry_day - purchase_date.date()).days if expiry_day else None
                elif software_key == "ULTRA":
                    duration_str = "7 days"
                    expiry_day   = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
                    total_days   = SUBSCRIPTION_DAYS
                else:
                    months       = get_sub_months(sub_name)
                    duration_str = str(months) + " month(s)"
                    expiry_day   = add_n_months(purchase_date, months).date()
                    total_days   = (expiry_day - purchase_date.date()).days

                # ── Price ──────────────────────────────────────────────────
                if software_key == "ULTRA":
                    notify_time = parse_ultra_notify_time(sub_name)
                    all_nums    = re.findall(r'\b(\d{3,5})\b', sub_name)
                    price_candidates = []
                    for n in all_nums:
                        if notify_time and len(n) == 4:
                            h, m = int(n[:2]), int(n[2:])
                            if 0 <= h <= 23 and 0 <= m <= 59 and (h, m) == notify_time:
                                continue
                        price_candidates.append(n)
                    price_lkr = int(price_candidates[-1]) if price_candidates else None
                else:
                    pm = re.search(r'\b(\d{3,6})\b', sub_name)
                    price_lkr = int(pm.group(1)) if pm else None

                # ── Status & days left ─────────────────────────────────────
                if col_d == "EXPIRED":
                    days_left  = 0
                    status_str = "🔴 EXPIRED"
                elif expiry_day and expiry_day < today:
                    days_left  = 0
                    status_str = "🔴 Expired"
                elif expiry_day and expiry_day == today:
                    days_left  = 0
                    status_str = "🟡 Today"
                else:
                    days_left  = (expiry_day - today).days if expiry_day else 0
                    status_str = "🟢 " + str(days_left) + "d left"

                # ── Refund calc ────────────────────────────────────────────
                refund_str = ""
                _this_phone_norm = normalize_phone(phone)
                _refund_applies  = show_refund and (refund_phone is None or refund_phone == _this_phone_norm)
                if _refund_applies and price_lkr and total_days and total_days > 0 and days_left > 0:
                    daily_rate = price_lkr / total_days
                    refund_amt = round(daily_rate * days_left)
                    refund_str = " | 💸 Refund: LKR " + str(refund_amt) + " (" + str(days_left) + "d unused)"

                matched.append({
                    "phone":        _format_phone_for_sheet(phone),
                    "sub_name":     sub_name,
                    "purchase_str": purchase_date.strftime("%d %b %Y"),
                    "expiry_str":   expiry_day.strftime("%d %b %Y") if expiry_day else "N/A",
                    "expiry_time":  ("{:02d}:{:02d}".format(*parse_ultra_notify_time(sub_name)) if software_key == "ULTRA" and parse_ultra_notify_time(sub_name) else None),
                    "duration_str": duration_str,
                    "price_str":    "LKR " + str(price_lkr) if price_lkr else "N/A",
                    "status_str":   status_str,
                    "refund_str":   refund_str,
                    "days_left":    days_left,
                    "col_d":        col_d,
                    "software_key": software_key,
                })

            if not matched:
                send_message(chat_id, "❌ No records found for *" + sw_input + "*.")
                return

            # Sort: active first (by days_left desc), expired last
            matched.sort(key=lambda x: (0 if x["days_left"] > 0 else 1, -x["days_left"]))

            active_count  = sum(1 for m in matched if m["days_left"] > 0)
            expired_count = len(matched) - active_count

            header = ("📦 *" + sw_input + " Subscribers*\n"
                      "Total: *" + str(len(matched)) + "* | "
                      "🟢 Active: *" + str(active_count) + "* | "
                      "🔴 Expired: *" + str(expired_count) + "*\n"
                      + ("_(Refund = unused days × daily rate)_\n" if show_refund else "")
                      + "─────────────────────\n")

            lines = [header]
            for m in matched:
                expiry_display = m["expiry_str"]
                if m.get("software_key") == "ULTRA" and m.get("expiry_time"):
                    expiry_display += " " + m["expiry_time"]
                line = (m["status_str"] + " | " + m["phone"] + "\n"
                        + "   📅 " + m["purchase_str"] + " → " + expiry_display
                        + " (" + m["duration_str"] + ")\n"
                        + "   💰 " + m["price_str"] + m["refund_str"] + "\n")
                lines.append(line)

            msg = "\n".join(lines)
            if len(msg) > 3800:
                chunks = [msg[i:i+3800] for i in range(0, len(msg), 3800)]
                for chunk in chunks:
                    send_message(chat_id, chunk)
                    time.sleep(0.5)
            else:
                send_message(chat_id, msg)

            # ── Date breakdown analytics message ───────────────────────────
            time.sleep(0.5)
            total_all = len(matched)
            # Group by purchase date
            from collections import OrderedDict as _OD
            date_counts = {}
            for m in matched:
                d = m["purchase_str"]
                date_counts[d] = date_counts.get(d, 0) + 1
            # Sort dates chronologically
            def _parse_dstr(s):
                try:
                    return datetime.strptime(s, "%d %b %Y")
                except Exception:
                    return datetime.min
            sorted_dates = sorted(date_counts.keys(), key=_parse_dstr)

            analytics_lines = [
                "📊 *" + sw_input + " — Purchase Breakdown*\n"
                "👥 Total: *" + str(total_all) + "* | "
                "🟢 Active: *" + str(active_count) + "* | "
                "🔴 Expired: *" + str(expired_count) + "*\n"
                "─────────────────────"
            ]
            for d in sorted_dates:
                count = date_counts[d]
                pct   = round((count / total_all) * 100, 1)
                bar   = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                analytics_lines.append(
                    "📅 *" + d + "*\n"
                    "   👤 " + str(count) + " user" + ("s" if count != 1 else "") +
                    "  |  " + str(pct) + "%\n"
                    "   " + bar
                )
            send_message(chat_id, "\n".join(analytics_lines))

        except Exception as _exc42:
            send_message(chat_id, "❌ Failed: " + str(_exc42))
            logger.error(traceback.format_exc())
        return

    # !check is no longer a normal admin command — see message.sent handler in process_webhook
    # !checkexpiry <phone> — show all subscriptions + expiry details for a customer
    if cmd.startswith("!checkexpiry "):
        target_raw = args.strip().replace("+","").replace(" ","").replace("-","").strip()
        if not target_raw:
            send_message(chat_id, "Usage: !checkexpiry <phone number>")
            return
        try:
            rows  = get_sheet_rows()
            local = normalize_phone(target_raw)
            today = sl_today()
            found_rows = []
            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                if normalize_phone(str(row[1])) != local:
                    continue
                found_rows.append(row)

            if not found_rows:
                send_message(chat_id, "❌ No records found for *" + target_raw + "* in the sheet.")
                return

            lines = ["📋 *Subscription Check — " + _format_phone_for_sheet(local) + "*\n"]
            for row in found_rows:
                sub_name     = str(row[2]).strip()
                software_key = get_general_software_key(sub_name)
                col_d        = str(row[3]).strip().upper() if len(row) > 3 else ""
                purchase_date = parse_date(str(row[0]).strip())

                if not purchase_date:
                    lines.append("⚠️ *" + sub_name + "* — unparseable purchase date (" + str(row[0]) + ")")
                    continue

                # ── Duration ──────────────────────────────────────────────────
                dur_val, dur_unit = extract_duration_from_subscription(sub_name)
                if dur_val and dur_unit:
                    unit_label = {"W": "week(s)", "M": "month(s)", "D": "day(s)"}.get(dur_unit, dur_unit)
                    duration_str = str(dur_val) + " " + unit_label
                    exp_dt   = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                    expiry_day = exp_dt.date() if exp_dt else None
                elif software_key == "ULTRA":
                    duration_str = "7 days (default)"
                    expiry_day   = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
                else:
                    months     = get_sub_months(sub_name)
                    duration_str = str(months) + " month(s)"
                    expiry_day = add_n_months(purchase_date, months).date()

                # ── Price ─────────────────────────────────────────────────────
                price_match = re.search(r'\b(\d{3,5})\b', sub_name)
                price_str   = "LKR " + price_match.group(1) if price_match else "N/A"
                # For ULTRA — the notify time looks like 1930 (19:30) which is 4 digits
                # Price is the OTHER 4-digit number that's ≥ 1000 and not a time (HH:MM)
                # Ultra format: "ULTRA <notify_time_HHMM> <price>"
                # e.g. "ULTRA 1930 1950" → time=19:30, price=1950 LKR
                if software_key == "ULTRA":
                    notify_time = parse_ultra_notify_time(sub_name)
                    all_nums    = re.findall(r'\b(\d{3,5})\b', sub_name)
                    price_candidates = []
                    for n in all_nums:
                        # Exclude the notify time (4 digits, HH:MM format)
                        if notify_time and len(n) == 4:
                            h = int(n[:2])
                            m = int(n[2:])
                            if 0 <= h <= 23 and 0 <= m <= 59 and (h, m) == notify_time:
                                continue
                        price_candidates.append(n)
                    price_str = "LKR " + price_candidates[-1] if price_candidates else "N/A"

                # ── Status ────────────────────────────────────────────────────
                if col_d == "EXPIRED":
                    status_str = "🔴 EXPIRED (marked in sheet)"
                elif expiry_day and expiry_day < today:
                    status_str = "🔴 Expired (not marked)"
                elif expiry_day and expiry_day == today:
                    status_str = "🟡 Expires TODAY"
                else:
                    days_left  = (expiry_day - today).days if expiry_day else "?"
                    status_str = "🟢 Active (" + str(days_left) + " days left)"

                # ── Build entry ───────────────────────────────────────────────
                entry  = "─────────────────\n"
                entry += "📦 *" + sub_name + "*\n"
                entry += "📅 Purchased: " + purchase_date.strftime("%d %b %Y") + "\n"
                entry += "⏳ Duration:  " + duration_str + "\n"
                entry += "📆 Expires:   " + (expiry_day.strftime("%d %b %Y") if expiry_day else "N/A") + "\n"
                entry += "💰 Price:     " + price_str + "\n"

                # Ultra notify time
                if software_key == "ULTRA":
                    nt = parse_ultra_notify_time(sub_name)
                    if nt:
                        from datetime import time as dtime
                        t_obj = dtime(nt[0], nt[1])
                        # Convert to 12h format
                        ampm = "AM" if nt[0] < 12 else "PM"
                        h12  = nt[0] % 12 or 12
                        entry += "🔔 Notify at: " + str(h12) + ":" + str(nt[1]).zfill(2) + " " + ampm + "\n"

                entry += "📊 Status:    " + status_str
                lines.append(entry)

            msg = "\n".join(lines)
            if len(msg) > 3800:
                chunks = [msg[i:i+3800] for i in range(0, len(msg), 3800)]
                for chunk in chunks:
                    send_message(chat_id, chunk)
                    time.sleep(0.5)
            else:
                send_message(chat_id, msg)
        except Exception as _exc43:
            send_message(chat_id, "❌ Failed: " + str(_exc43))
            logger.error(traceback.format_exc())
        return

    # !addultratime <phone> <hours> — add hours to a user's ULTRA expiry time
    # Updates col C sub_name in sheet, reschedules APScheduler job, updates queue
    # Usage: !addultratime 0771234567 8
    if cmd.startswith("!addultratime"):
        try:
            parts = text.strip().split()
            if len(parts) < 3:
                send_message(chat_id, "Format:\n!addultratime <phone> <hours>\n\nExample:\n!addultratime 0771234567 8")
                return
            target_phone = normalize_phone(parts[1])
            try:
                hours_to_add = int(parts[2])
                if hours_to_add <= 0:
                    raise ValueError
            except ValueError:
                send_message(chat_id, "❌ Hours must be a positive whole number.")
                return

            # Find their active ULTRA row in sheet
            rows = get_sheet_rows()
            matched_row_idx  = None
            matched_sub_name = None
            matched_purchase = None

            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                if normalize_phone(str(row[1])) != target_phone:
                    continue
                sub_raw = str(row[2]).strip().upper()
                if not (sub_raw.startswith("ULTRA") or sub_raw.startswith("ULT")):
                    continue
                col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
                if col_d == "EXPIRED":
                    continue
                matched_row_idx  = i
                matched_sub_name = str(row[2]).strip()
                matched_purchase = str(row[0]).strip()
                break

            if matched_row_idx is None:
                send_message(chat_id, "❌ No active ULTRA row found for " + parts[1])
                return

            # Parse current notify time from sub_name
            current_time = parse_ultra_notify_time(matched_sub_name)
            if current_time is None:
                send_message(chat_id, "❌ No time found in sub_name: " + matched_sub_name + "\nCannot add hours without an existing time.")
                return

            cur_h, cur_m = current_time
            old_hhmm = "{:02d}{:02d}".format(cur_h, cur_m)

            # Compute new time — handle midnight rollover
            from datetime import datetime as _adt, timedelta as _atd
            base_dt = _adt(2000, 1, 1, cur_h, cur_m, 0)
            new_dt  = base_dt + _atd(hours=hours_to_add)
            new_h   = new_dt.hour
            new_m   = new_dt.minute
            days_overflow = new_dt.day - 1  # 0 = same day, 1 = rolled into next day
            new_hhmm = "{:02d}{:02d}".format(new_h, new_m)

            # Replace old HHMM in sub_name with new HHMM
            import re as _re_at
            new_sub_name = _re_at.sub(r'\b' + old_hhmm + r'\b', new_hhmm, matched_sub_name)
            if new_sub_name == matched_sub_name:
                send_message(chat_id, "❌ Could not replace time " + old_hhmm + " in: " + matched_sub_name)
                return

            # Compute new col A date if rollover — preserve original format
            new_col_a = None
            if days_overflow > 0:
                _parsed_orig = parse_date(matched_purchase)
                if _parsed_orig:
                    _new_date = _parsed_orig + _atd(days=days_overflow)
                    _fmt_candidates = [
                        "%d-%b-%y", "%d-%b-%Y", "%d-%B-%y", "%d-%B-%Y",
                        "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d",
                        "%d %b %Y", "%d %b %y",
                    ]
                    new_col_a = _new_date.strftime("%d-%b-%y")  # fallback
                    for _try_fmt in _fmt_candidates:
                        try:
                            datetime.strptime(matched_purchase.strip(), _try_fmt)
                            new_col_a = _new_date.strftime(_try_fmt)
                            break
                        except Exception:
                            continue

            # Write to sheet — col C always, col A only on rollover
            try:
                _creds2  = get_google_credentials()
                _client2 = gspread.authorize(_creds2)
                _ss2     = _client2.open_by_key(GOOGLE_SHEET_ID)
                try:
                    _sheet2 = _ss2.worksheet(SHEET_NAME)
                except Exception:
                    _sheet2 = _ss2.get_worksheet(0)
                _sheet2.update_cell(matched_row_idx + 1, 3, new_sub_name)
                if new_col_a:
                    _sheet2.update_cell(matched_row_idx + 1, 1, new_col_a)
                invalidate_sheet_cache()
            except Exception as _exc_sheet:
                send_message(chat_id, "❌ Sheet update failed: " + str(_exc_sheet)[:200])
                return

            # Update queue entry + reschedule APScheduler job
            _queue = _load_ultra_expiry_queue()
            rescheduled = False
            for entry in _queue:
                if normalize_phone(entry.get("phone","")) == target_phone and not entry.get("fired"):
                    # Compute new fire datetime — rollover shifts the date forward
                    old_fire_str = entry.get("fire_time","")
                    try:
                        from datetime import datetime as _fdt
                        old_fire = _fdt.strptime(old_fire_str, "%Y-%m-%d %H:%M:%S")
                        new_fire = old_fire + _atd(hours=hours_to_add)
                        new_fire_str = new_fire.strftime("%Y-%m-%d %H:%M:%S")
                        new_fire_tz  = new_fire.replace(tzinfo=SL_TZ)

                        # Remove old job
                        old_job_id = "ultra_exp_" + entry["phone"] + "_" + old_fire_str.replace(" ", "_")
                        try:
                            scheduler.remove_job(old_job_id)
                        except Exception:
                            pass

                        # Update entry
                        entry["sub_name"]  = new_sub_name
                        entry["fire_time"] = new_fire_str
                        # If date rolled over, update expiry_day_str too
                        if days_overflow > 0:
                            try:
                                old_exp = _fdt.strptime(entry["expiry_day_str"], "%B %d, %Y")
                                new_exp = old_exp + _atd(days=days_overflow)
                                entry["expiry_day_str"] = new_exp.strftime("%B %d, %Y")
                            except Exception:
                                pass

                        # Schedule new job
                        new_job_id = "ultra_exp_" + entry["phone"] + "_" + new_fire_str.replace(" ", "_")
                        scheduler.add_job(
                            _fire_and_mark_ultra_entry,
                            trigger="date",
                            run_date=new_fire_tz,
                            args=[entry],
                            id=new_job_id,
                            replace_existing=True
                        )
                        rescheduled = True
                    except Exception as _exc_sched:
                        logger.error("!addultratime reschedule error: " + str(_exc_sched))
                    break

            _save_ultra_expiry_queue(_queue)

            # Build confirmation message
            old_fmt = "{:02d}:{:02d}".format(cur_h, cur_m)
            new_fmt = "{:02d}:{:02d}".format(new_h, new_m)
            overflow_note = " *(+1 day rollover)*" if days_overflow > 0 else ""
            date_note     = "\n📅 Date updated: *" + matched_purchase + "* → *" + new_col_a + "*" if new_col_a else ""
            sched_note    = "✅ Job rescheduled in scheduler." if rescheduled else "⚠️ Queue entry not found — job NOT rescheduled (may already be fired or not yet loaded)."
            send_message(chat_id,
                "✅ *ULTRA Time Extended*\n\n"
                "👤 " + _format_phone_for_sheet(target_phone) + "\n"
                "🕐 Old time: *" + old_fmt + "*\n"
                "🕐 New time: *" + new_fmt + "*" + overflow_note + "\n"
                "➕ Added: *" + str(hours_to_add) + "h*\n"
                + date_note + "\n\n"
                "📝 Sub name updated: `" + new_sub_name + "`\n"
                + sched_note
            )
        except Exception as _exc_at:
            logger.error("!addultratime error: " + str(_exc_at))
            send_message(chat_id, "❌ Error: " + str(_exc_at)[:300])
        return

    # !expirylog — show expiry status for all subs in past 30 days
    # !extendall <software> <hours>h — extend all active customers of a software by N hours
    # Example: !extendall ultra 24h
    if cmd.startswith("!extendall"):
        try:
            parts = text.strip().split()
            if len(parts) < 3:
                send_message(chat_id, "Format:\n!extendall <software> <hours>h\n\nExample:\n!extendall ultra 24h")
                return
            sw_input   = parts[1].strip().upper()
            hours_str  = parts[2].strip().lower().replace("h", "")
            try:
                hours_to_add = int(hours_str)
                if hours_to_add <= 0:
                    raise ValueError
            except ValueError:
                send_message(chat_id, "❌ Hours must be a positive whole number. e.g. !extendall ultra 24h")
                return

            # Resolve software key directly
            sw_key = get_general_software_key(sw_input)
            if not sw_key:
                send_message(chat_id, "❌ Unknown software: " + sw_input)
                return

            time.sleep(6)  # avoid 429 between two rapid admin messages
            send_message(chat_id, "⏳ Extending all active " + sw_key + " subscriptions by " + str(hours_to_add) + "h...")

            rows  = get_sheet_rows()
            today = sl_today()
            _creds2  = get_google_credentials()
            _client2 = gspread.authorize(_creds2)
            _ss2     = _client2.open_by_key(GOOGLE_SHEET_ID)
            try:
                _sheet2 = _ss2.worksheet(SHEET_NAME)
            except Exception:
                _sheet2 = _ss2.get_worksheet(0)

            updated = 0
            skipped = 0
            errors  = []
            from datetime import datetime as _edt, timedelta as _etd
            import re as _re_ext

            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                col_d    = str(row[3]).strip().upper() if len(row) > 3 else ""
                if col_d == "EXPIRED":
                    continue
                sub_name = str(row[2]).strip()
                if not sub_name:
                    continue
                row_key = get_general_software_key(sub_name)
                if row_key != sw_key:
                    continue

                purchase_date = parse_date(str(row[0]).strip())
                if not purchase_date:
                    logger.info("🔍 extendall skip row " + str(i+1) + ": cannot parse date '" + str(row[0]) + "'")
                    skipped += 1
                    continue

                # Check still active — Ultra is always 7 days from purchase
                dur_val, dur_unit = extract_duration_from_subscription(sub_name)
                if dur_val and dur_unit:
                    _exp = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                    expiry_day = _exp.date() if _exp else None
                elif sw_key == "ULTRA":
                    expiry_day = (purchase_date + _etd(hours=ULTRA_EXPIRY_HOURS)).date()
                else:
                    expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()

                logger.info("🔍 extendall row " + str(i+1) + " | phone=" + str(row[1]).strip() +
                            " | sub=" + sub_name + " | purchase=" + str(purchase_date.date()) +
                            " | expiry=" + str(expiry_day) + " | today=" + str(today))

                if not expiry_day or expiry_day < today:
                    skipped += 1
                    continue

                # For ULTRA — update time in sub_name + date in col A if rollover
                try:
                    if sw_key == "ULTRA":
                        notify_time = parse_ultra_notify_time(sub_name)
                        if not notify_time:
                            skipped += 1
                            continue
                        cur_h, cur_m = notify_time
                        old_hhmm = "{:02d}{:02d}".format(cur_h, cur_m)
                        base_dt  = _edt(2000, 1, 1, cur_h, cur_m, 0)
                        new_dt   = base_dt + _etd(hours=hours_to_add)
                        new_h, new_m = new_dt.hour, new_dt.minute
                        days_overflow = new_dt.day - 1
                        new_hhmm = "{:02d}{:02d}".format(new_h, new_m)

                        # Update sub_name only if time actually changed
                        if new_hhmm != old_hhmm:
                            new_sub_name = _re_ext.sub(r'\b' + old_hhmm + r'\b', new_hhmm, sub_name)
                            if new_sub_name != sub_name:
                                _sheet2.update_cell(i + 1, 3, new_sub_name)
                                time.sleep(0.4)

                        # Always update col A date by days_overflow (minimum 0)
                        # For 24h: time same, date +1. For 3h crossing midnight: time changes, date +1
                        total_days = days_overflow if new_hhmm != old_hhmm else (hours_to_add // 24)
                        if total_days > 0:
                            _parsed = parse_date(str(row[0]).strip())
                            if _parsed:
                                _new_date = _parsed + _etd(days=total_days)
                                _fmt_candidates = ["%d-%b-%y","%d-%b-%Y","%d-%B-%y","%d-%B-%Y","%d/%m/%Y","%Y-%m-%d"]
                                _new_col_a = _new_date.strftime("%d-%b-%y")
                                for _fmt in _fmt_candidates:
                                    try:
                                        _edt.strptime(str(row[0]).strip(), _fmt)
                                        _new_col_a = _new_date.strftime(_fmt)
                                        break
                                    except Exception:
                                        continue
                                _sheet2.update_cell(i + 1, 1, _new_col_a)
                                logger.info("📅 " + _format_phone_for_sheet(str(row[1]).strip()) +
                                            " | " + str(row[0]).strip() + " → " + _new_col_a +
                                            " | time: " + old_hhmm + " → " + new_hhmm)
                                time.sleep(0.4)
                        else:
                            logger.info("⏰ Extended: " + _format_phone_for_sheet(str(row[1]).strip()) +
                                        " | time: " + old_hhmm + " → " + new_hhmm + " (no date change)")
                    else:
                        # For non-Ultra — extend by adding hours to the expiry date
                        new_expiry = _edt.combine(expiry_day, _edt.min.time()) + _etd(hours=hours_to_add)
                        new_expiry_date = new_expiry.date()
                        _orig_date_str = str(row[0]).strip()
                        _fmt_candidates = ["%d-%b-%y","%d-%b-%Y","%d/%m/%Y","%Y-%m-%d"]
                        _new_col_a = new_expiry_date.strftime("%d-%b-%y")
                        for _fmt in _fmt_candidates:
                            try:
                                _edt.strptime(_orig_date_str, _fmt)
                                _new_col_a = new_expiry_date.strftime(_fmt)
                                break
                            except Exception:
                                continue
                        _sheet2.update_cell(i + 1, 1, _new_col_a)
                        time.sleep(0.4)

                    updated += 1
                    time.sleep(0.3)

                except Exception as _row_err:
                    _phone_disp = _format_phone_for_sheet(str(row[1]).strip())
                    _err_msg = _phone_disp + " row " + str(i + 1) + ": " + str(_row_err)[:120]
                    errors.append(_err_msg)
                    logger.error("!extendall row error: " + _err_msg)

            invalidate_sheet_cache()
            time.sleep(6)
            _summary = (
                "✅ *Extended " + sw_key + " by " + str(hours_to_add) + "h*\n\n"
                "📦 Updated: *" + str(updated) + "* customers\n"
                "⏭️ Skipped (expired/no time): *" + str(skipped) + "*"
            )
            if errors:
                _summary += "\n\n⚠️ *" + str(len(errors)) + " row(s) failed:*\n" + "\n".join("• " + e for e in errors)
            send_message(chat_id, _summary)
            logger.info("!extendall: extended " + str(updated) + " " + sw_key + " rows by " + str(hours_to_add) + "h, errors=" + str(len(errors)))
        except Exception as _exc_ea:
            logger.error("!extendall error: " + str(_exc_ea))
            send_message(chat_id, "❌ Error: " + str(_exc_ea)[:300])
        return

    # !sendsub <software> <message> — send a custom message to all active customers of a software
    # Usage: !sendsub ultra Your account has been updated. Please log in again.
    if cmd.startswith("!sendsub"):
        try:
            rest = text.strip()[len("!sendsub"):].strip()
            if not rest or " " not in rest:
                send_message(chat_id, "Format:\n!sendsub <software> <your message>\n\nExample:\n!sendsub ultra Your account has been updated.")
                return
            sw_raw   = rest.split()[0]
            msg_body = rest[len(sw_raw):].strip()
            if not msg_body:
                send_message(chat_id, "❌ Message cannot be empty.")
                return

            sw_key = get_general_software_key(sw_raw.upper())
            if not sw_key:
                send_message(chat_id, "❌ Unknown software: " + sw_raw)
                return

            rows  = get_sheet_rows()
            today = sl_today()
            targets = []

            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
                if col_d == "EXPIRED":
                    continue
                sub_name = str(row[2]).strip()
                if not sub_name:
                    continue
                if get_general_software_key(sub_name) != sw_key:
                    continue
                purchase_date = parse_date(str(row[0]).strip())
                if not purchase_date:
                    continue

                # Expiry check
                dur_val, dur_unit = extract_duration_from_subscription(sub_name)
                if dur_val and dur_unit:
                    _exp = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                    expiry_day = _exp.date() if _exp else None
                elif sw_key == "ULTRA":
                    expiry_day = (purchase_date + timedelta(hours=ULTRA_EXPIRY_HOURS)).date()
                else:
                    expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()

                if not expiry_day or expiry_day < today:
                    continue

                phone = normalize_phone(str(row[1]).strip())
                if phone and phone not in [t[0] for t in targets]:
                    targets.append((phone, "+94" + phone))

            if not targets:
                send_message(chat_id, "❌ No active " + sw_key + " customers found.")
                return

            time.sleep(6)
            send_message(chat_id, "📤 Sending to *" + str(len(targets)) + "* active " + sw_key + " customers...")

            sent_count  = 0
            fail_count  = 0
            for phone, target_id in targets:
                try:
                    time.sleep(random.uniform(20, 45))
                    send_message(target_id, msg_body)
                    sent_count += 1
                    logger.info("📤 !sendsub sent to " + phone)
                except Exception as _se:
                    fail_count += 1
                    logger.error("!sendsub fail for " + phone + ": " + str(_se))

            time.sleep(6)
            send_message(chat_id,
                "✅ *!sendsub complete*\n\n"
                "📦 Software: *" + sw_key + "*\n"
                "✅ Sent: *" + str(sent_count) + "*\n"
                "❌ Failed: *" + str(fail_count) + "*"
            )
            logger.info("!sendsub: sent to " + str(sent_count) + " " + sw_key + " customers")
        except Exception as _exc_ss:
            logger.error("!sendsub error: " + str(_exc_ss))
            send_message(chat_id, "❌ Error: " + str(_exc_ss)[:300])
        return

    # !fetchexpired — list all expired Canva, Duolingo and Lightroom users
    if cmd == "!fetchexpired":
        try:
            rows  = get_sheet_rows()
            today = sl_today()
            TARGET_KEYS = {"CANVA", "DUOLINGO", "LIGHTROOM"}
            expired  = []
            active   = []

            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                purchase_date = parse_date(str(row[0]).strip())
                phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
                sub_name      = str(row[2]).strip()
                software_key  = get_general_software_key(sub_name)
                col_d         = str(row[3]).strip().upper() if len(row) > 3 else ""

                if software_key not in TARGET_KEYS or not purchase_date:
                    continue

                dur_val, dur_unit = extract_duration_from_subscription(sub_name)
                if dur_val and dur_unit:
                    exp_dt     = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                    expiry_day = exp_dt.date() if exp_dt else None
                else:
                    expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()

                if not expiry_day:
                    continue

                phone_fmt    = _format_phone_for_sheet(phone)
                purchase_str = purchase_date.strftime("%d %b %Y")
                expiry_str   = expiry_day.strftime("%d %b %Y")
                label        = software_key + " | " + phone_fmt + " | " + purchase_str + " → " + expiry_str

                if expiry_day < today and col_d != "EXPIRED":
                    expired.append(label)
                elif expiry_day >= today:
                    active.append(label)

            lines = ["📋 *Canva / Duolingo / Lightroom Users*\n"]

            if expired:
                lines.append("🔴 *Expired (" + str(len(expired)) + ")*")
                lines += ["• " + e for e in expired]
            else:
                lines.append("🔴 *Expired* — none")

            lines.append("")

            if active:
                lines.append("🟢 *Active (" + str(len(active)) + ")*")
                lines += ["• " + a for a in active]
            else:
                lines.append("🟢 *Active* — none")

            msg = "\n".join(lines)
            if len(msg) > 3800:
                for chunk in [msg[i:i+3800] for i in range(0, len(msg), 3800)]:
                    send_message(chat_id, chunk)
                    time.sleep(0.3)
            else:
                send_message(chat_id, msg)
        except Exception as _exc_fe:
            send_message(chat_id, "❌ Error: " + str(_exc_fe)[:300])
            logger.error(traceback.format_exc())
        return

    if cmd == "!expirylog":
        try:
            rows  = get_sheet_rows()
            today = sl_today()
            cutoff = today - timedelta(days=30)
            lines = []
            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                purchase_date = parse_date(str(row[0]).strip())
                phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
                sub_name      = str(row[2]).strip()
                software_key  = get_general_software_key(sub_name)
                if not software_key or not purchase_date:
                    continue
                if software_key == "ULTRA":
                    expiry_day = (purchase_date + timedelta(days=7)).date()
                    notify_day = (purchase_date + timedelta(days=7)).date()  # same day as expiry, fires at 2PM
                else:
                    expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()
                    notify_day = expiry_day
                if notify_day < cutoff or notify_day > today:
                    continue
                # Check if expiry was sent
                target    = to_chat_id(phone)
                dedup_key = target + ("_ULTRA7_" + str(expiry_day) if software_key == "ULTRA" else "_" + software_key)
                status    = "✅ sent" if expiry_already_sent(dedup_key) else "❌ NOT sent"
                fmt_phone = _format_phone_for_sheet(phone)
                lines.append(expiry_day.strftime("%b %d") + " | " + fmt_phone + " | " + software_key + " | " + status)
            if not lines:
                send_message(chat_id, "📋 No expiries found in the past 30 days.")
                return
            msg = "📋 *Expiry Log — Last 30 Days*\n\n" + "\n".join(lines)
            # Split if too long
            if len(msg) > 3800:
                chunks = [msg[i:i+3800] for i in range(0, len(msg), 3800)]
                for chunk in chunks:
                    send_message(chat_id, chunk)
                    time.sleep(0.5)
            else:
                send_message(chat_id, msg)
        except Exception as _exc44:
            send_message(chat_id, "❌ Failed: " + str(_exc44))
            logger.error(traceback.format_exc())
        return

    # !sendallexpired — send expiry to ALL users whose date has passed (not yet marked EXPIRED in col D)
    if cmd == "!sendallexpired":
        if bot_is_stopped():
            send_message(chat_id, "⚠️ Bot is stopped. Send !botresume first.")
            return
        send_message(chat_id, "🔍 Scanning sheet for all users with passed expiry date not yet marked EXPIRED...\nThis may take a while.")
        def _run_sendallexpired(_cid=chat_id):
            try:
                creds_gs  = get_google_credentials()
                client_gs = gspread.authorize(creds_gs)
                ss_gs     = client_gs.open_by_key(GOOGLE_SHEET_ID)
                try:
                    sheet_gs = ss_gs.worksheet(SHEET_NAME)
                except Exception:
                    sheet_gs = ss_gs.get_worksheet(0)
                raw_rows = sheet_gs.get_all_values()
                invalidate_sheet_cache()

                today     = sl_today()
                now_sl    = sl_now()
                sent_list = []
                skip_list = []

                for i, row in enumerate(raw_rows):
                    if i == 0 or len(row) < 3:
                        continue
                    purchase_date = parse_date(str(row[0]).strip())
                    phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
                    sub_name      = str(row[2]).strip()
                    software_key  = get_general_software_key(sub_name)
                    col_d         = str(row[3]).strip().upper() if len(row) > 3 else ""

                    if not software_key or not purchase_date:
                        continue
                    if col_d == "EXPIRED":
                        skip_list.append(_format_phone_for_sheet(phone) + " — " + software_key + " (already EXPIRED)")
                        continue
                    if software_key == "ULTRA_PRIVATE":
                        continue

                    # Calculate expiry
                    dur_val, dur_unit = extract_duration_from_subscription(sub_name)
                    if dur_val and dur_unit:
                        exp_dt     = calculate_expiry_date_from_duration(purchase_date, dur_val, dur_unit)
                        expiry_day = exp_dt.date() if exp_dt else None
                    elif software_key == "ULTRA":
                        expiry_day = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
                    else:
                        expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()

                    if not expiry_day or expiry_day > today:
                        continue  # not expired yet

                    # For ULTRA — check notify time has passed
                    if software_key == "ULTRA":
                        notify_time = parse_ultra_notify_time(sub_name)
                        nh = notify_time[0] if notify_time else 23
                        nm = notify_time[1] if notify_time else 59
                        from datetime import datetime as _dt
                        fire_dt = _dt(expiry_day.year, expiry_day.month, expiry_day.day, nh, nm, 0).replace(tzinfo=SL_TZ)
                        if now_sl < fire_dt and expiry_day == today:
                            continue  # today but time not reached yet

                    if has_renewed(phone, software_key, purchase_date, raw_rows):
                        skip_list.append(_format_phone_for_sheet(phone) + " — " + software_key + " (renewed)")
                        continue

                    target = to_chat_id(phone)
                    purchase_str = purchase_date.strftime("%B %d, %Y")
                    expiry_str   = expiry_day.strftime("%B %d, %Y")

                    # Mark col D before sending
                    try:
                        sheet_gs.update_cell(i + 1, 4, "EXPIRED")
                        invalidate_sheet_cache()
                    except Exception as _ex:
                        logger.error("!sendallexpired col D mark failed: " + str(_ex))

                    if software_key == "ULTRA":
                        send_ultra_expiry_notification(target, sub_name, purchase_str, expiry_str)
                    else:
                        send_expiry_notification(target, software_key, purchase_str, expiry_str)

                    sent_list.append(_format_phone_for_sheet(phone) + " — " + software_key + " (expired " + expiry_str + ")")
                    time.sleep(random.uniform(20, 40))

                # Summary
                msg = "✅ *!sendallexpired Done*\n\n"
                msg += "📤 *Sent: " + str(len(sent_list)) + "*\n"
                if sent_list:
                    msg += "\n".join("• " + s for s in sent_list) + "\n\n"
                msg += "⏭️ *Skipped: " + str(len(skip_list)) + "*"
                if skip_list:
                    msg += "\n" + "\n".join("• " + s for s in skip_list[:20])
                    if len(skip_list) > 20:
                        msg += "\n_(+" + str(len(skip_list) - 20) + " more)_"

                if len(msg) > 3800:
                    for chunk in [msg[i:i+3800] for i in range(0, len(msg), 3800)]:
                        send_message(_cid, chunk)
                        time.sleep(0.5)
                else:
                    send_message(_cid, msg)

            except Exception as _e:
                send_message(_cid, "❌ Failed: " + str(_e))
                logger.error(traceback.format_exc())

        threading.Thread(target=_run_sendallexpired, daemon=True).start()
        return

    # !sendexpiry mar19 — send expiry for a specific date (Ultra + all others)
    if cmd.startswith("!sendexpiry "):
        if bot_is_stopped():
            send_message(chat_id, "⚠️ Bot is stopped. Send !botresume first.")
            return
        date_part   = text.strip()[len("!sendexpiry "):].strip()
        target_date = _parse_short_date(date_part)
        if not target_date:
            send_message(chat_id,
                "❌ Could not parse date: *" + date_part + "*\n\n"
                "Format examples:\n"
                "!sendexpiry mar19\n"
                "!sendexpiry mar 19\n"
                "!sendexpiry 2026-03-19"
            )
            return
        def run_send_expiry(d=target_date):
            try:
                rows  = get_sheet_rows()
                count = 0
                skipped = 0
                send_message(chat_id, "⚙️ Sending expiry for *" + str(d) + "*...")
                for i, row in enumerate(rows):
                    if bot_is_stopped():
                        send_message(chat_id, "🛑 Stopped by !botstop.")
                        return
                    if i == 0 or len(row) < 3:
                        continue
                    purchase_date = parse_date(str(row[0]).strip())
                    phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
                    sub_name      = str(row[2]).strip()
                    software_key  = get_general_software_key(sub_name)
                    if not software_key or not purchase_date:
                        continue
                    if software_key == "ULTRA":
                        expiry_day = (purchase_date + timedelta(days=7)).date()
                        notify_day = (purchase_date + timedelta(days=7)).date()  # same day as expiry, fires at 2PM
                    else:
                        expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()
                        notify_day = expiry_day
                    if notify_day != d:
                        continue
                    if has_renewed(phone, software_key, purchase_date, rows):
                        skipped += 1
                        continue
                    target = to_chat_id(phone)
                    if software_key == "ULTRA":
                        sent = send_ultra_expiry_notification(
                            target, sub_name,
                            purchase_date.strftime("%B %d, %Y"),
                            expiry_day.strftime("%B %d, %Y")
                        )
                    else:
                        sent = send_expiry_notification(
                            target, software_key,
                            purchase_date.strftime("%B %d, %Y"),
                            expiry_day.strftime("%B %d, %Y")
                        )
                    if sent:
                        count += 1
                        time.sleep(5)
                    else:
                        skipped += 1
                msg  = "✅ *Expiry done for " + str(d) + "*\n"
                msg += "📤 Sent: *" + str(count) + "*\n"
                msg += "⏭️ Skipped: *" + str(skipped) + "*"
                send_message(chat_id, msg)
            except Exception as _exc45:
                send_message(chat_id, "❌ Failed: " + str(_exc45))
                logger.error(traceback.format_exc())
        threading.Thread(target=run_send_expiry, daemon=True).start()
        send_message(chat_id, "🚀 Sending expiry for *" + str(target_date) + "* in background...")
        return

    # !deletesub <phone> <software> — delete a specific sheet row matching phone + software
    if cmd.startswith("!deletesub"):
        try:
            parts = text.strip().split()
            if len(parts) < 3:
                send_message(chat_id, "Format:\n!deletesub <phone> <software>\n\nExample:\n!deletesub 0778315058 Adobe")
                return
            target_phone = normalize_phone(parts[1])
            target_sw    = " ".join(parts[2:]).strip().upper()
            target_key   = get_general_software_key(target_sw)
            if not target_key:
                send_message(chat_id, "❌ Unknown software: " + target_sw)
                return

            rows = get_sheet_rows()
            matched = []
            for i, row in enumerate(rows):
                if i == 0 or len(row) < 3:
                    continue
                if normalize_phone(str(row[1])) != target_phone:
                    continue
                sub_name = str(row[2]).strip()
                row_key  = get_general_software_key(sub_name)
                if row_key == target_key:
                    matched.append((i, row))

            if not matched:
                send_message(chat_id, "❌ No row found for " + parts[1] + " with " + target_key)
                return

            if len(matched) > 1:
                _list = "\n".join("• Row " + str(idx+1) + ": " + str(r[0]) + " | " + str(r[2]) for idx, r in matched)
                send_message(chat_id,
                    "⚠️ Found " + str(len(matched)) + " matching rows:\n\n" + _list +
                    "\n\nDeleting all of them now..."
                )

            _creds  = get_google_credentials()
            _client = gspread.authorize(_creds)
            _ss     = _client.open_by_key(GOOGLE_SHEET_ID)
            try:
                _sheet = _ss.worksheet(SHEET_NAME)
            except Exception:
                _sheet = _ss.get_worksheet(0)

            # Delete from bottom-up so row indices don't shift mid-delete
            deleted_info = []
            for idx, row in sorted(matched, key=lambda x: -x[0]):
                try:
                    _sheet.delete_rows(idx + 1)
                    deleted_info.append(str(row[0]) + " | " + str(row[2]))
                    time.sleep(0.5)
                except Exception as _ex_d:
                    logger.error("Row delete failed for row " + str(idx+1) + ": " + str(_ex_d))

            invalidate_sheet_cache()
            send_message(chat_id,
                "✅ *Deleted " + str(len(deleted_info)) + " row(s)*\n\n"
                "👤 " + _format_phone_for_sheet(target_phone) + "\n"
                "📦 " + target_key + "\n\n"
                "Removed:\n" + "\n".join("• " + d for d in deleted_info)
            )
            logger.info("✅ !deletesub: deleted " + str(len(deleted_info)) + " row(s) for " + target_phone + " " + target_key)
        except Exception as _exc_ds:
            logger.error("!deletesub error: " + str(_exc_ds))
            send_message(chat_id, "❌ Error: " + str(_exc_ds)[:300])
        return

    # !deleteexpired — delete sheet rows where expiry was already sent
    if cmd == "!deleteexpired":
        if bot_is_stopped():
            send_message(chat_id, "⚠️ Bot is stopped. Send !botresume first.")
            return
        def run_delete():
            _delete_expired_rows(chat_id)
        threading.Thread(target=run_delete, daemon=True).start()
        send_message(chat_id, "🗑️ Scanning sheet for expired rows to delete...")
        return

    # !sendexpiry marDD — manually send expiry for a date e.g. !sendexpiry mar24
    if cmd.startswith("!sendexpiry"):
        parts = text.strip().split(None, 1)
        if len(parts) < 2:
            send_message(chat_id, "Format: !sendexpiry mar24\nor: !sendexpiry mar24 to mar28")
            return
        arg = parts[1].strip().lower()
        try:
            import re as _re_exp
            dates_found = _re_exp.findall(r'[a-z]+\d+', arg)
            months_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                          "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
            def _parse_exp_date(s):
                m = _re_exp.match(r'([a-z]+)(\d+)', s)
                if not m: return None
                mon = months_map.get(m.group(1)[:3])
                day = int(m.group(2))
                if not mon: return None
                return sl_today().replace(month=mon, day=day)
            if "to" in arg:
                d1 = _parse_exp_date(dates_found[0]) if dates_found else None
                d2 = _parse_exp_date(dates_found[1]) if len(dates_found) > 1 else None
                if not d1 or not d2:
                    send_message(chat_id, "❌ Could not parse dates.")
                    return
                current = d1
                while current <= d2:
                    _send_ultra_expiry_for_date(chat_id, current)
                    current += timedelta(days=1)
            else:
                d = _parse_exp_date(dates_found[0]) if dates_found else None
                if not d:
                    send_message(chat_id, "❌ Could not parse date.")
                    return
                _send_ultra_expiry_for_date(chat_id, d)
        except Exception as _exc46:
            send_message(chat_id, "❌ Error: " + str(_exc46))
        return

    # !showexpiry marDD to marDD — list expiries in a date range
    if cmd.startswith("!showexpiry"):
        parts = text.strip().split(None, 1)
        if len(parts) < 2:
            send_message(chat_id, "Format: !showexpiry mar24\nor: !showexpiry mar22 to mar28")
            return
        arg = parts[1].strip().lower()
        try:
            import re as _re_sexp
            dates_found = _re_sexp.findall(r'[a-z]+\d+', arg)
            months_map2 = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                           "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
            def _parse_sexp_date(s):
                m = _re_sexp.match(r'([a-z]+)(\d+)', s)
                if not m: return None
                mon = months_map2.get(m.group(1)[:3])
                day = int(m.group(2))
                if not mon: return None
                return sl_today().replace(month=mon, day=day)
            if "to" in arg:
                d1 = _parse_sexp_date(dates_found[0]) if dates_found else None
                d2 = _parse_sexp_date(dates_found[1]) if len(dates_found) > 1 else None
                if not d1 or not d2:
                    send_message(chat_id, "❌ Could not parse dates.")
                    return
                current = d1
                while current <= d2:
                    _list_ultra_expiry_for_date(chat_id, current)
                    current += timedelta(days=1)
            else:
                d = _parse_sexp_date(dates_found[0]) if dates_found else None
                if not d:
                    send_message(chat_id, "❌ Could not parse date.")
                    return
                _list_ultra_expiry_for_date(chat_id, d)
        except Exception as _exc47:
            send_message(chat_id, "❌ Error: " + str(_exc47))
        return



    send_message(chat_id, "❓ Unknown command. Send *!admin* to see all commands.")

# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK PROCESSOR — background thread (returns 200 instantly from Flask)
# WaSenderAPI webhook payload format:
#   Incoming: event="messages.received", data.messages.key.cleanedSenderPn, data.messages.messageBody
#   Outgoing: event="message.sent", data.key.remoteJid
# ═══════════════════════════════════════════════════════════════════════════════
def process_webhook(body):
    try:
        event = body.get("event", "")
        logger.info("process_webhook | event=" + event)

        # ── Outgoing message sent by bot/admin ──────────────────────────────
        if event == "message.sent":
            data     = body.get("data", {}) or {}
            remote   = (data.get("jid") or "").split("@")[0]
            if not remote:
                return
            result   = data.get("result", {}) or {}
            msg_obj  = result.get("message", {}) or {}
            out_text_raw = (
                msg_obj.get("conversation") or
                (msg_obj.get("extendedTextMessage") or {}).get("text") or
                data.get("body") or
                data.get("messageBody") or
                ""
            ).strip()
            # DEEP DEBUG — log payload structure when text is empty so we can see where the text actually is
            if not out_text_raw:
                import json as _json
                logger.info("🔍 message.sent payload (text empty): data_keys=" + str(list(data.keys())) +
                            " result_keys=" + str(list(result.keys())) +
                            " msg_obj_keys=" + str(list(msg_obj.keys())) +
                            " | full=" + _json.dumps(body)[:1000])
            else:
                logger.info("🔍 message.sent text extracted: " + repr(out_text_raw[:100]) + " to=" + remote)
            out_text = out_text_raw.lower()
            if not remote.startswith("+"):
                remote = "+" + remote.lstrip("+")
            if remote and "@g.us" not in remote:
                number = remote.lstrip("+")
                # !check sent by admin in a customer chat — DM customer their active subs
                if number not in ADMIN_NUMBERS and out_text.strip() == "!check":
                    logger.info("👨‍💼 Admin typed !check in chat with " + remote + " — sending active subs to customer")
                    threading.Thread(
                        target=_send_active_subs_to_chat,
                        args=(remote,),
                        daemon=True
                    ).start()
                if out_text_raw:
                    _record_chat_message(remote, out_text_raw, "outgoing")
            return

        # ── Incoming message ─────────────────────────────────────────────────
        # Only process messages.received — messages.upsert and
        # messages-personal.received fire for the same message and cause
        # double handling. Ignore them.
        if event != "messages.received":
            return

        msg_data    = (body.get("data") or {}).get("messages", {})
        if not msg_data or not isinstance(msg_data, dict):
            return

        key         = msg_data.get("key") or {}
        from_me     = key.get("fromMe", False)
        remote_jid  = key.get("remoteJid", "") or ""
        msg_id      = key.get("id", "") or ""

        # Drop messages the bot itself sent (echoed back as incoming by some providers)
        if from_me:
            return

        # Dedup — skip if already processed
        if msg_id:
            with _processed_msg_lock:
                if msg_id in _processed_msg_ids:
                    logger.info("Duplicate message skipped: " + msg_id)
                    return
                _processed_msg_ids.add(msg_id)
                if len(_processed_msg_ids) > _processed_msg_maxsize:
                    trimmed = list(_processed_msg_ids)[-400:]
                    _processed_msg_ids.clear()
                    _processed_msg_ids.update(trimmed)
        # Use cleanedSenderPn for private, cleanedParticipantPn for groups
        sender_pn   = (key.get("cleanedSenderPn") or
                       key.get("cleanedParticipantPn") or
                       key.get("senderPn") or "")

        # Skip group messages
        if "@g.us" in remote_jid:
            return

        # Build E.164 chat_id from cleanedSenderPn or remoteJid
        raw_num = sender_pn or ""

        # If no cleanedSenderPn, try to extract from remoteJid
        if not raw_num:
            if "@lid" in remote_jid:
                # @lid is WhatsApp's linked device ID — not a real phone number
                # Log and skip — we can't reliably get the number
                logger.warning("Received @lid JID — cannot extract phone: " + remote_jid)
                return
            raw_num = remote_jid.replace("@s.whatsapp.net", "").replace("@c.us", "").strip()

        if not raw_num:
            return

        # Strip any leading + and non-digits
        raw_num = raw_num.lstrip("+").strip()

        # Only prepend 94 if number looks like a local SL number (7-9 digits starting with 7)
        if len(raw_num) <= 9 and raw_num.startswith("7"):
            raw_num = "94" + raw_num
        elif not raw_num.startswith("94") and len(raw_num) < 10:
            # Short number with no country code — assume SL
            raw_num = "94" + raw_num

        # Validate — must be digits only and reasonable length
        if not raw_num.isdigit() or len(raw_num) < 10 or len(raw_num) > 15:
            logger.warning("Invalid phone number extracted: " + raw_num + " from JID: " + remote_jid)
            return

        chat_id = "+" + raw_num

        # Unified message body
        text = msg_data.get("messageBody") or ""
        if not text:
            _raw_msg_txt = msg_data.get("message") or {}
            text = (
                (_raw_msg_txt.get("conversation") if isinstance(_raw_msg_txt, dict) else None) or
                ((_raw_msg_txt.get("extendedTextMessage") or {}).get("text") if isinstance(_raw_msg_txt, dict) else None) or
                ""
            )

        sender_name = key.get("senderPn") or chat_id

        # Detect message type from raw message object
        raw_msg  = msg_data.get("message") or {}
        _dcm_raw = raw_msg.get("documentWithCaptionMessage") or {}
        _dcm_msg = _dcm_raw.get("message") or {} if isinstance(_dcm_raw, dict) else {}
        _doc_msg = raw_msg.get("documentMessage") or (_dcm_msg.get("documentMessage") if isinstance(_dcm_msg, dict) else None) or {}
        _mime    = (_doc_msg.get("mimetype") or "").lower() if isinstance(_doc_msg, dict) else ""
        is_pdf   = "pdf" in _mime or (isinstance(_doc_msg, dict) and "pdf" in str(_doc_msg.get("fileName", "")).lower())

        # Detect image/media messages — any message with no text body
        is_image_message = not text or text.strip() == ""

        logger.info("Incoming | " + sender_name + " (" + chat_id + ") | fromMe=" + str(from_me) +
                    (" | IMAGE/MEDIA" if is_image_message else ""))

        # Blocked number — silently drop everything
        if is_blocked(chat_id):
            logger.info("Blocked number — dropping: " + chat_id)
            return

        # Record in chat history cache
        _record_chat_message(chat_id, text, "incoming")

        # Images ALWAYS bypass cooldown and go straight to handle_message.
        # A customer sending any photo must always trigger the creds flow —
        # never silently drop it due to cooldown.
        if is_image_message:
            if is_pdf and chat_id.lstrip("+") not in ADMIN_NUMBERS:
                logger.info("PDF received from: " + chat_id)
                set_cooldown(chat_id, 120)
                return
            # Images bypass cooldown but use atomic check to prevent double processing
            if chat_id.lstrip("+") not in ADMIN_NUMBERS:
                with _cooldown_lock:
                    # Inline cooldown check — we already hold the lock, can't call is_on_cooldown()
                    _cd_entry = _cooldowns.get(chat_id)
                    if _cd_entry:
                        _cd_ts, _cd_secs = _cd_entry
                        if _cd_ts.tzinfo is None:
                            _cd_ts = _cd_ts.replace(tzinfo=SL_TZ)
                            _cooldowns[chat_id] = (_cd_ts, _cd_secs)
                        if (sl_now() - _cd_ts).total_seconds() < _cd_secs:
                            logger.info("Image — cooldown active, dropping: " + chat_id)
                            return
                    _cooldowns[chat_id] = (sl_now(), COOLDOWN_SECONDS)
            logger.info("Image/media message — routing to handler: " + chat_id)
            handle_message(chat_id, text, sender_name)
            return

        if chat_id.lstrip("+") in ADMIN_NUMBERS:
            handle_message(chat_id, text, sender_name)
            return

        # Software/number ask — bypass ALL cooldowns, always send description
        _t_stripped = text.strip().lower() if text else ""
        is_number_reply  = text.strip().isdigit() and 1 <= int(text.strip()) <= 24
        is_renewal_reply = text.lower().startswith("renew_")
        is_software_ask  = len(get_all_intents(text)) > 0
        is_multi_num     = len(get_multi_number_intents(text)) > 0
        is_single_num    = get_number_intent(text) is not None
        # OTP keyword bypass — "otp" / "code" / "one time code" / "one-time code"
        # anywhere in the message (substring match) is treated as a high-priority
        # OTP request and bypasses ALL cooldowns. The downstream OTP handler will
        # also ignore the OTP-specific cooldown when these keywords are present.
        _is_desc_request = is_number_reply or is_renewal_reply or is_software_ask or is_multi_num or is_single_num

        if _is_desc_request:
            # Description requests and OTP requests bypass the cooldown check but
            # do NOT clear it. If creds are in progress (cooldown set), still allow
            # these through but don't wipe the protection that prevents double creds.
            handle_message(chat_id, text, sender_name)
            return

        # Atomic cooldown check+set — prevents two concurrent threads both passing
        with _cooldown_lock:
            # Inline cooldown check — we already hold the lock, can't call is_on_cooldown()
            _cd_entry = _cooldowns.get(chat_id)
            if _cd_entry:
                _cd_ts, _cd_secs = _cd_entry
                if _cd_ts.tzinfo is None:
                    _cd_ts = _cd_ts.replace(tzinfo=SL_TZ)
                    _cooldowns[chat_id] = (_cd_ts, _cd_secs)
                if (sl_now() - _cd_ts).total_seconds() < _cd_secs:
                    logger.info("Cooldown active — dropping: " + chat_id)
                    return
            # Set immediately inside the lock so no other thread can slip through
            _cooldowns[chat_id] = (sl_now(), COOLDOWN_SECONDS)
        handle_message(chat_id, text, sender_name)

    except Exception as _pw_err:
        logger.error("process_webhook error: " + str(_pw_err))
        logger.error(traceback.format_exc())
        _notify_admin_failure("process_webhook", str(_pw_err))

# ═══════════════════════════════════════════════════════════════════════════════
# FLASK APP

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    """Returns 200 IMMEDIATELY. All logic runs in background thread."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        threading.Thread(target=process_webhook, args=(body,), daemon=True).start()
    except Exception as _exc50:
        logger.error("Webhook parse error: " + str(_exc50))
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return "ULTRA Bot is running! SL Time: " + str(sl_now().strftime("%Y-%m-%d %H:%M:%S")), 200

@app.route("/health", methods=["GET"])
def health():
    return {
        "status":   "ok",
        "sl_time":  sl_now().isoformat(),
        "stopped":  bot_is_stopped(),
    }, 200


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN AUTH — Secure login for Signature Digital admin panel
# ═══════════════════════════════════════════════════════════════════════════════
import hashlib, secrets

ADMIN_PWD_HASH = os.environ.get("ADMIN_PWD_HASH", "92f9befc174ae0f63bd3a8d5cc8dfa2e:8c1297e2833c5f8a02fb4c0aa52708222a9d01e7ee8a7ce2e81a952feecf64e8")
_admin_tokens = {}  # token -> expiry timestamp

def _verify_pwd(pwd):
    try:
        parts = ADMIN_PWD_HASH.split(":")
        if len(parts) != 2: return False
        salt, stored_hash = parts
        check = hashlib.sha256((salt + pwd).encode()).hexdigest()
        return secrets.compare_digest(check, stored_hash)
    except:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN STATUS — Track last online time
# ═══════════════════════════════════════════════════════════════════════════════
_admin_last_seen = 0  # Unix timestamp
_admin_online = False

def update_admin_seen():
    global _admin_last_seen, _admin_online
    _admin_last_seen = time.time()
    _admin_online = True

# Call update_admin_seen() whenever admin uses the bot
# Auto mark offline after 5 minutes of inactivity
def check_admin_online():
    global _admin_online
    if _admin_online and time.time() - _admin_last_seen > 300:
        _admin_online = False

@app.route("/status", methods=["GET"])
def get_status():
    check_admin_online()
    return {
        "online": _admin_online,
        "last_seen": _admin_last_seen,
        "last_seen_iso": datetime.utcfromtimestamp(_admin_last_seen).isoformat() + "Z" if _admin_last_seen else None
    }, 200

@app.route("/set-online", methods=["POST", "OPTIONS"])
def set_online():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json(force=True) or {}
        token = data.get("token", "")
        expiry = _admin_tokens.get(token, 0)
        if not token or time.time() > expiry:
            return {"error": "Unauthorized"}, 401
        online = data.get("online", True)
        global _admin_online, _admin_last_seen
        _admin_online = online
        if online:
            _admin_last_seen = time.time()
        return {"success": True}, 200
    except Exception as e:
        return {"error": str(e)}, 500


# ═══════════════════════════════════════════════════════════════════════════════
# FOLLOW SYSTEM — IP-based follow tracking
# ═══════════════════════════════════════════════════════════════════════════════
_follow_ips = set()  # IPs that have followed

@app.route("/follow", methods=["POST", "OPTIONS"])
def follow():
    if request.method == "OPTIONS":
        return "", 204
    try:
        ip = _otp_get_ip()
        data = request.get_json(force=True) or {}
        action = data.get("action", "follow")  # "follow" or "unfollow"
        already = ip in _follow_ips
        if action == "follow":
            if already:
                return {"success": True, "already": True}, 200
            _follow_ips.add(ip)
            return {"success": True, "followed": True}, 200
        else:
            if not already:
                return {"success": True, "already": True}, 200
            _follow_ips.discard(ip)
            return {"success": True, "followed": False}, 200
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/follow-status", methods=["GET"])
def follow_status():
    ip = _otp_get_ip()
    return {"following": ip in _follow_ips}, 200

@app.route("/admin-login", methods=["POST", "OPTIONS"])
def admin_login():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json(force=True) or {}
        pwd = data.get("password", "")
        if not _verify_pwd(pwd):
            time.sleep(1)  # slow down brute force
            return {"error": "Incorrect password"}, 401
        token = secrets.token_hex(32)
        _admin_tokens[token] = time.time() + 86400 * 7  # 7 days
        logger.info("Admin login from " + request.headers.get("X-Forwarded-For", request.remote_addr or "unknown"))
        return {"token": token}, 200
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/admin-verify", methods=["POST", "OPTIONS"])
def admin_verify():
    if request.method == "OPTIONS":
        return "", 204
    try:
        data = request.get_json(force=True) or {}
        token = data.get("token", "")
        expiry = _admin_tokens.get(token, 0)
        if not token or time.time() > expiry:
            _admin_tokens.pop(token, None)
            return {"valid": False}, 401
        # Refresh token expiry
        _admin_tokens[token] = time.time() + 86400 * 7
        return {"valid": True}, 200
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/files/<token>/<filename>", methods=["GET"])
def serve_file(token, filename):
    """Serve backup files — protected by secret token."""
    import os as _os
    from flask import send_from_directory, abort
    expected_token = os.environ.get("FILE_TOKEN", ADMIN_NUMBER)
    if token != expected_token:
        abort(403)
    # Check /data first, then /tmp
    for folder in ["/data", "/tmp"]:
        if _os.path.exists(folder + "/" + filename):
            return send_from_directory(folder, filename, as_attachment=True)
    abort(404)

# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK REGISTRATION — WaSenderAPI: set webhook URL in your dashboard
# Go to: wasenderapi.com/dashboard → Sessions → your session → Settings → Webhook URL
# Set it to: https://YOUR_RAILWAY_URL/webhook
# Enable events: messages.received  and  message.sent
# No auto-registration needed here.
# ═══════════════════════════════════════════════════════════════════════════════
webhook_url = os.environ.get("WEBHOOK_URL", "")
if webhook_url:
    logger.info("Webhook URL (set this in WaSenderAPI dashboard): " + webhook_url.rstrip("/") + "/webhook")
else:
    logger.warning("WEBHOOK_URL not set — remember to configure webhook in WaSenderAPI dashboard")

# Load persisted memory from volume (if exists)
load_memory()
_veo_load()   # Load VEO licenses from /data/veo_licenses.json

# Background auto-save every 60 seconds
_auto_save_thread = threading.Thread(target=_auto_save_loop, daemon=True)
_auto_save_thread.start()
logger.info("Auto-save thread started — saving to " + DATA_FILE + " every 60s")

# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER — fires daily at 00:00 Sri Lanka time

# ═══════════════════════════════════════════════════════════════════════════════
# AUTO RECORD — nightly scan of outgoing messages to build sheet records
# ═══════════════════════════════════════════════════════════════════════════════

# Pending records waiting for admin confirmation: list of {date, phone, sub}
_pending_records     = []
_pending_records_lock = threading.Lock()

# Known subscription abbreviations — what gets written to sheet
_SUB_ALIASES = {
    "ULT":        ["ULT", "ULTRA"],
    "CHATGPT":    ["CHAT", "GPT", "CHATGPT", "CHGPT", "JETGPT", "JET GPT", "JETCHAT"],
    "GEMINI":     ["GEM", "GEMINI"],
    "CAPCUT":     ["CAP", "CAPCUT"],
    "ENVATO":     ["ENV", "ENVATO"],
    "ADOBE":      ["ADO", "ADOBE"],
    "FREEPIK":    ["FRPK", "FREEP", "FREEPIK"],
    "GAMMA":      ["GAMMA"],
    "PERPLEXITY": ["PERP", "PERPLEXITY"],
    "QUILLBOT":   ["QUILL", "QUILLBOT"],
    "GRAMMARLY":  ["GRAM", "GRAMMARLY"],
    "CANVA":      ["CANVA", "CAN"],
    "DUOLINGO":   ["DUOLINGO", "DUO"],
    "LIGHTROOM":  ["LIGHTROOM", "LR"],
    "OFFICE":     ["OFFICE", "365"],
    "KAHOOT":     ["KAHOOT"],
    "VEO":        ["VEO"],
}

def _fuzzy_match_sub(word):
    """
    Given a word from admin's message, return the closest subscription key.
    Uses startswith matching with aliases. Returns None if no match.
    """
    upper = word.upper().strip()
    # Remove trailing digits/price e.g. "CAP1750" → "CAP"
    base = _re_sub.sub(r'\d+.*$', '', upper).strip()
    for sub_key, aliases in _SUB_ALIASES.items():
        for alias in aliases:
            if base == alias or base.startswith(alias) or alias.startswith(base):
                return sub_key
    return None

def _format_phone_for_sheet(raw):
    """
    Format phone number for sheet:
    - Sri Lanka (+94 / 94): XX-XXX-XXXX (9 digits local)
    - Foreign: add dashes every 3-4 digits, never add +
    """
    # Strip everything non-digit
    digits = _re_sub.sub(r'\D', '', raw)
    # Remove country code
    if digits.startswith("94") and len(digits) == 11:
        local = digits[2:]  # 9 digits
        return local[0:2] + "-" + local[2:5] + "-" + local[5:]
    elif len(digits) == 9:
        return digits[0:2] + "-" + digits[2:5] + "-" + digits[5:]
    else:
        # Foreign — dash every 3 digits
        parts = [digits[i:i+3] for i in range(0, len(digits), 3)]
        return "-".join(parts)

def _scan_todays_outgoing_for_records():
    """
    Scans today's outgoing messages from in-memory chat history.
    Looks for admin messages containing subscription name + price pattern.
    Returns list of {phone, sub, date_str}.
    """
    import re
    today_str = sl_now().strftime("%d-%B-%y")
    results   = []
    seen      = set()

    TOKEN_RE = re.compile(r'([A-Za-z][A-Za-z0-9]*)\s+(\d{3,5})')

    with _chat_history_lock:
        all_chats = dict(_chat_history_cache)

    # Naive cutoff (no timezone) to safely compare with any timestamp
    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for chat_id, messages in all_chats.items():
        if chat_id.lstrip("+") in ADMIN_NUMBERS:
            continue
        if "@g.us" in chat_id:
            continue

        for msg in messages:
            try:
                if msg.get("type") != "outgoing":
                    continue
                ts = msg.get("timestamp")
                if ts:
                    # Make naive for comparison
                    ts_naive = ts.replace(tzinfo=None) if hasattr(ts, 'tzinfo') and ts.tzinfo else ts
                    if ts_naive < cutoff:
                        continue

                text = msg.get("text", "")
                if not text:
                    continue

                matches = TOKEN_RE.findall(text.upper())
                for word, price in matches:
                    sub_key = _fuzzy_match_sub(word)
                    if not sub_key:
                        continue
                    dedup_key = chat_id + "_" + sub_key
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    phone_fmt = _format_phone_for_sheet(chat_id)
                    results.append({
                        "phone":     phone_fmt,
                        "raw_phone": chat_id,
                        "sub":       sub_key,
                        "date_str":  today_str,
                    })
            except Exception:
                continue

    return results

def _delete_expired_rows(admin_chat_id):
    """
    Deletes rows from sheet where expiry was already sent.
    Sends a report of deleted rows to admin.
    Deletes from bottom to top to avoid row index shifting.
    """
    try:
        creds  = get_google_credentials()
        client = gspread.authorize(creds)
        ss     = client.open_by_key(GOOGLE_SHEET_ID)
        try:
            sheet = ss.worksheet(SHEET_NAME)
        except Exception:
            sheet = ss.get_worksheet(0)

        rows  = sheet.get_all_values()
        today = sl_today()

        # Find rows to delete (index 1-based for gspread, skip header row 0)
        to_delete = []  # list of (sheet_row_index, date_str, phone, sub)
        for i, row in enumerate(rows):
            if i == 0 or len(row) < 3:
                continue
            purchase_date = parse_date(str(row[0]).strip())
            phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
            sub_name      = str(row[2]).strip()
            software_key  = get_general_software_key(sub_name)
            if not software_key or not purchase_date:
                continue

            # Check column D for EXPIRED flag
            col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
            if col_d == "EXPIRED":
                to_delete.append((i + 1, row[0], phone, sub_name))
                continue

            # Calculate expiry day with duration parsing
            if software_key == "ULTRA":
                # Check for duration markers
                duration_value, duration_unit = extract_duration_from_subscription(sub_name)
                if duration_value and duration_unit:
                    expiry_datetime = calculate_expiry_date_from_duration(purchase_date, duration_value, duration_unit)
                    expiry_day = expiry_datetime.date() if expiry_datetime else (purchase_date + timedelta(days=7)).date()
                else:
                    expiry_day = (purchase_date + timedelta(days=7)).date()
                notify_day = expiry_day
            else:
                # Check for duration markers first
                duration_value, duration_unit = extract_duration_from_subscription(sub_name)
                if duration_value and duration_unit:
                    expiry_datetime = calculate_expiry_date_from_duration(purchase_date, duration_value, duration_unit)
                    expiry_day = expiry_datetime.date() if expiry_datetime else add_n_months(purchase_date, get_sub_months(sub_name)).date()
                else:
                    expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()
                notify_day = expiry_day
                
            if notify_day > today:
                continue
            # Check if expiry was sent via memory dedup
            target    = to_chat_id(phone)
            dedup_key = target + ("_ULTRA7_" + str(expiry_day) if software_key == "ULTRA" else "_" + software_key)
            if not expiry_already_sent(dedup_key):
                continue
            to_delete.append((i + 1, row[0], phone, sub_name))  # i+1 = 1-based sheet row

        if not to_delete:
            send_message(admin_chat_id, "✅ No expired rows to delete — all expiry-sent rows already cleaned up.")
            return

        send_message(admin_chat_id, "🗑️ Deleting *" + str(len(to_delete)) + "* expired rows...")

        # Delete from bottom to top so row indices stay valid
        deleted = []
        for sheet_row, date_str, phone, sub in reversed(to_delete):
            try:
                sheet.delete_rows(sheet_row)
                fmt_phone = _format_phone_for_sheet(phone)
                deleted.append("• " + date_str + " | " + fmt_phone + " | " + sub)
                time.sleep(0.5)
            except Exception as _exc51:
                logger.error("delete row error: " + str(_exc51))

        invalidate_sheet_cache()

        # Report
        msg = "✅ *Deleted " + str(len(deleted)) + " rows from sheet:*\n\n"
        msg += "\n".join(deleted)
        # Split if too long
        if len(msg) > 3800:
            chunks = [msg[i:i+3800] for i in range(0, len(msg), 3800)]
            for chunk in chunks:
                send_message(admin_chat_id, chunk)
                time.sleep(0.5)
        else:
            send_message(admin_chat_id, msg)

        logger.info("Deleted " + str(len(deleted)) + " expired rows from sheet")
    except Exception as _exc52:
        send_message(admin_chat_id, "❌ Delete failed: " + str(_exc52))
        logger.error("_delete_expired_rows error: " + str(_exc52))
        logger.error(traceback.format_exc())


def _write_records_to_sheet(records):
    """Write list of {date_str, phone, sub} to Google Sheet. Adds a blank separator row before new records if sheet already has data."""
    try:
        creds  = get_google_credentials()
        client = gspread.authorize(creds)
        ss     = client.open_by_key(GOOGLE_SHEET_ID)
        try:
            sheet = ss.worksheet(SHEET_NAME)
        except Exception:
            sheet = ss.get_worksheet(0)
        # Only add blank separator if sheet already has data rows
        existing = sheet.get_all_values()
        has_data = any(any(cell.strip() for cell in row) for row in existing)
        if has_data:
            sheet.append_row(["", "", ""])
            time.sleep(0.3)
        for r in records:
            sheet.append_row([r["date_str"], r["phone"], r["sub"]])
            time.sleep(0.3)
        invalidate_sheet_cache()
        logger.info("Written " + str(len(records)) + " records to sheet (with leading gap row)")
        return True
    except Exception as _exc53:
        logger.error("_write_records_to_sheet error: " + str(_exc53))
        _notify_admin_failure("write_records_to_sheet", str(_exc53))
        return False


def check_missed_expiry_notifications():
    """
    Runs every 2 hours. Scans the ENTIRE sheet for any subscription (ULTRA or other)
    whose expiry date has passed but col D is NOT yet marked EXPIRED.

    Col D = EXPIRED is the single source of truth:
      - Col D = "EXPIRED"  →  expiry already sent, skip entirely.
      - Col D = blank + expiry passed  →  expiry was missed, send now + mark col D.

    This survives bot restarts (in-memory dedup is gone, but col D persists).
    Covers ULTRA, all other subscriptions. Skips PVT (manual), renewed, future expiries.
    Admin notified with a summary only when at least one missed expiry is sent.
    """
    logger.info("=== Missed expiry retry check started (SL time: " + str(sl_now()) + ") ===")
    if bot_is_stopped():
        logger.info("Missed expiry check skipped — bot stopped")
        return
    try:
        # Always fetch fresh from sheet — do not use cache
        creds_gs = get_google_credentials()
        client_gs = gspread.authorize(creds_gs)
        ss_gs = client_gs.open_by_key(GOOGLE_SHEET_ID)
        try:
            sheet_gs = ss_gs.worksheet(SHEET_NAME)
        except Exception:
            sheet_gs = ss_gs.get_worksheet(0)
        raw_rows = sheet_gs.get_all_values()
        invalidate_sheet_cache()  # keep cache in sync

        today = sl_today()
        now   = sl_now()
        sent_count    = 0
        skipped_count = 0
        sent_list     = []

        for i, row in enumerate(raw_rows):
            if bot_is_stopped():
                logger.info("Missed expiry retry halted by !botstop")
                return
            if i == 0 or len(row) < 3:
                continue

            purchase_date = parse_date(str(row[0]).strip())
            phone         = str(row[1]).replace("+","").replace(" ","").replace("-","").strip()
            sub_name      = str(row[2]).strip()
            software_key  = get_general_software_key(sub_name)

            if not software_key or not purchase_date:
                continue
            if software_key == "ULTRA_PRIVATE":
                continue  # PVT is handled manually — never auto-send

            # ── Col D = EXPIRED → already handled, skip ──────────────────────
            col_d = str(row[3]).strip().upper() if len(row) > 3 else ""
            if col_d == "EXPIRED":
                skipped_count += 1
                continue

            target = to_chat_id(phone)

            # ── ULTRA ─────────────────────────────────────────────────────────
            if software_key == "ULTRA":
                duration_value, duration_unit = extract_duration_from_subscription(sub_name)
                if duration_value and duration_unit:
                    expiry_dt  = calculate_expiry_date_from_duration(purchase_date, duration_value, duration_unit)
                    expiry_day = expiry_dt.date() if expiry_dt else (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()
                else:
                    expiry_day = (purchase_date + timedelta(days=SUBSCRIPTION_DAYS)).date()

                if expiry_day > today:
                    continue  # not expired yet

                notify_time    = parse_ultra_notify_time(sub_name)
                notify_hour    = notify_time[0] if notify_time else 23
                notify_min     = notify_time[1] if notify_time else 59
                expiry_dt_full = datetime(expiry_day.year, expiry_day.month, expiry_day.day,
                                          notify_hour, notify_min, 59, tzinfo=SL_TZ)
                if now < expiry_dt_full:
                    continue  # expiry time hasn't arrived yet today

                if has_renewed(phone, "ULTRA", purchase_date, raw_rows):
                    skipped_count += 1
                    continue

                # Col D blank + expiry passed = missed — send now
                logger.info("Missed ULTRA expiry detected: " + phone + " expiry=" + str(expiry_day))
                purchase_str = purchase_date.strftime("%B %d, %Y")
                expiry_str   = expiry_day.strftime("%B %d, %Y")

                # Mark col D immediately before sending (prevents race with normal scheduler)
                try:
                    sheet_gs.update_cell(i + 1, 4, "EXPIRED")
                    invalidate_sheet_cache()
                except Exception as ex:
                    logger.error("Missed expiry — col D mark failed (ULTRA): " + str(ex))

                # Mark in-memory dedup so normal scheduler won't double-send
                dedup_key = normalize_phone(phone) + "_ULTRA7_" + str(expiry_day)
                mark_expiry_sent("+" + phone + "_ULTRA7_" + str(expiry_day))
                mark_expiry_sent(dedup_key)

                send_ultra_expiry_notification(target, sub_name, purchase_str, expiry_str)
                sent_count += 1
                sent_list.append(_format_phone_for_sheet(phone) + " — ULTRA (" + str(expiry_day) + ")")

            # ── ALL OTHER SUBSCRIPTIONS ────────────────────────────────────────
            else:
                duration_value, duration_unit = extract_duration_from_subscription(sub_name)
                if duration_value and duration_unit:
                    expiry_dt  = calculate_expiry_date_from_duration(purchase_date, duration_value, duration_unit)
                    expiry_day = expiry_dt.date() if expiry_dt else add_n_months(purchase_date, get_sub_months(sub_name)).date()
                else:
                    expiry_day = add_n_months(purchase_date, get_sub_months(sub_name)).date()

                if expiry_day > today:
                    continue  # not expired yet

                if has_renewed(phone, software_key, purchase_date, raw_rows):
                    skipped_count += 1
                    continue

                # Col D blank + expiry passed = missed — send now
                logger.info("Missed expiry detected: " + phone + " | " + software_key + " expiry=" + str(expiry_day))
                purchase_str = purchase_date.strftime("%B %d, %Y")
                expiry_str   = expiry_day.strftime("%B %d, %Y")

                # Mark col D immediately before sending
                try:
                    sheet_gs.update_cell(i + 1, 4, "EXPIRED")
                    invalidate_sheet_cache()
                except Exception as ex:
                    logger.error("Missed expiry — col D mark failed (" + software_key + "): " + str(ex))

                # Mark in-memory dedup
                dedup_key = target + "_" + software_key
                mark_expiry_sent(dedup_key)

                send_expiry_notification(target, software_key, purchase_str, expiry_str)
                sent_count += 1
                sent_list.append(_format_phone_for_sheet(phone) + " — " + software_key + " (" + str(expiry_day) + ")")

        logger.info("=== Missed expiry retry done | Sent: " + str(sent_count) + " | Skipped: " + str(skipped_count) + " ===")

        # Notify admins only if something was actually sent
        if sent_count > 0:
            msg  = "🔄 *Missed Expiry Retry — " + sl_now().strftime("%d %b %Y %I:%M %p") + "*\n\n"
            logger.info("Missed expiry sent: " + str(sent_count) + " — " + ", ".join(sent_list))

    except Exception as _exc54:
        logger.error("check_missed_expiry_notifications error: " + str(_exc54))
        logger.error(traceback.format_exc())
        _notify_admin_failure("missed_expiry_retry_scheduler", str(_exc54))


scheduler = BackgroundScheduler(timezone="Asia/Colombo")
scheduler.add_job(check_and_send_expiry_notifications,  "cron", hour=0,  minute=2)
scheduler.add_job(load_todays_ultra_expiries,            "cron", hour=0,  minute=5)
# Missed-expiry retry runs every 12 hours — 00:30 and 12:30
scheduler.add_job(check_missed_expiry_notifications,     "cron", hour="0,12", minute=30)
scheduler.start()
logger.info("Schedulers started — daily expiry 00:00, ultra load 00:05, missed-expiry odd hours :30 SL time")

# On startup: reload today's queue from volume and reschedule any unfired jobs
try:
    _startup_queue = _load_ultra_expiry_queue()
    if _startup_queue:
        _today_str = sl_today().strftime("%Y-%m-%d")
        _todays = [e for e in _startup_queue if e.get("fire_time", "").startswith(_today_str)]
        if _todays:
            logger.info("Startup: restoring " + str(len(_todays)) + " ultra expiry jobs from volume")
            _schedule_ultra_expiry_jobs(_todays)
        else:
            logger.info("Startup: queue exists but no entries for today")
    else:
        logger.info("Startup: no ultra expiry queue found")
except Exception as _se:
    logger.error("Startup queue restore error: " + str(_se))

if __name__ == "__main__":
    logger.info("Bot starting in dev mode | SL time: " + str(sl_now()))
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
