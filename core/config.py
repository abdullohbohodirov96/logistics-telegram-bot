import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").replace("/rest/v1", "").strip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

# Google Sheets Configuration
# Accept either name — render.yaml's bot service historically documented
# this as "GOOGLE_SHEET_ID" while the code only ever read "SPREADSHEET_ID",
# so a service set up by copying render.yaml's key list (instead of
# deploying it as a Blueprint) would silently have no sheet ID at all.
# Both names now work no matter which one was actually typed into Render.
GOOGLE_SHEET_ID = os.getenv("SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID")
DRIVERS_SHEET_NAME = os.getenv("DRIVERS_SHEET_NAME", "drivers").strip()
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders").strip()

# Telegram Group IDs
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")                          # Asosiy guruh

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

ADMIN_IDS = []
admin_ids_str = os.getenv("ADMIN_IDS", "")
if admin_ids_str:
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]

# Credentials check
service_account_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
if not service_account_json_str:
    service_account_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

GOOGLE_SERVICE_ACCOUNT_INFO = {}
if service_account_json_str:
    try:
        GOOGLE_SERVICE_ACCOUNT_INFO = json.loads(service_account_json_str)
    except json.JSONDecodeError:
        logger.error("GOOGLE_CREDENTIALS_JSON is not a valid JSON string.")

# ── Branch configuration ───────────────────────────────────────────────────
# Qorasaroy filiali olib tashlandi (2026-08) — endi HAMMASI bitta "orders"
# sheetga yoziladi, bitta guruhga jo'natiladi. BRANCHES tuzilishi shunday
# qoldirilgan (bitta yozuv bilan) chunki scheduler.py/webhook.py/admin.py
# hammasi shu dict ustidan umumiy tarzda ishlaydi — kelajakda yana filial
# kerak bo'lsa, shunchaki bu yerga yangi yozuv qo'shiladi.
BRANCHES = {
    "Shiribom": {
        "group_id": GROUP_CHAT_ID,
        "orders_sheet": ORDERS_SHEET_NAME,
    },
}

# Debug logging
logger.info("--- Google Sheets Config Debug ---")
logger.info(f"GOOGLE_CREDENTIALS_JSON exists: {bool(service_account_json_str)}")
logger.info(f"SPREADSHEET_ID exists: {bool(GOOGLE_SHEET_ID)}")
logger.info(f"DRIVERS_SHEET_NAME: {DRIVERS_SHEET_NAME}")
logger.info(f"ORDERS_SHEET_NAME: {ORDERS_SHEET_NAME}")
logger.info(f"GROUP_CHAT_ID: {GROUP_CHAT_ID}")
logger.info("----------------------------------")

TIMEZONE = "Asia/Tashkent"

# ── "Dunyabunya" shop/warehouse — fixed reference point every driver returns
# to after unloading. Corrected directly by the dispatcher. Used to
# estimate "necha daqiqada dokonga qaytadi" after a delivery finishes.
SHOP_LAT = 41.398979
SHOP_LNG = 69.238353

# Yandex Geocoder API key — turns a driver's delivered_lat/delivered_lng into
# a human-readable address (rayon/mahalla/ko'cha). Optional: until this is
# set, notifications fall back to a plain Google Maps link instead of text.
YANDEX_GEOCODER_API_KEY = os.getenv("YANDEX_GEOCODER_API_KEY", "").strip()

USE_SHEETS = True
IS_SHEETS_ENABLED = True

def is_sheets_configured():
    return bool(GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_INFO)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8000"))

# Shared secret the Google Apps Script (onEdit trigger) must send so random
# internet traffic can't trigger order dispatch. Set the same value in the
# Apps Script and here (Render env var).
SHEET_WEBHOOK_SECRET = os.getenv("SHEET_WEBHOOK_SECRET", "")
