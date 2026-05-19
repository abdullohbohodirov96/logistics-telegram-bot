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
GOOGLE_SHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVERS_SHEET_NAME = os.getenv("DRIVERS_SHEET_NAME", "drivers").strip()
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders").strip()

# Telegram Group IDs
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")                          # Shiribod (asosiy) guruh
QORASAROY_GROUP_CHAT_ID = os.getenv("QORASAROY_GROUP_CHAT_ID", "") # Qorasaroy guruh

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

# ── Multi-branch (filial) configuration ───────────────────────────────────────
# drivers sheet G ustunidagi "filial" nomi bo'yicha guruh va orders sheet aniqlanadi
BRANCHES = {
    "Shiribom": {
        "group_id": GROUP_CHAT_ID,
        "orders_sheet": ORDERS_SHEET_NAME,
    },
    "Qorasaroy": {
        "group_id": QORASAROY_GROUP_CHAT_ID,
        "orders_sheet": os.getenv("QORASAROY_ORDERS_SHEET_NAME", "Qorasaroy orders").strip(),
    },
}

# Debug logging
logger.info("--- Google Sheets Config Debug ---")
logger.info(f"GOOGLE_CREDENTIALS_JSON exists: {bool(service_account_json_str)}")
logger.info(f"SPREADSHEET_ID exists: {bool(GOOGLE_SHEET_ID)}")
logger.info(f"DRIVERS_SHEET_NAME: {DRIVERS_SHEET_NAME}")
logger.info(f"ORDERS_SHEET_NAME: {ORDERS_SHEET_NAME}")
logger.info(f"GROUP_CHAT_ID (Shiribod): {GROUP_CHAT_ID}")
logger.info(f"QORASAROY_GROUP_CHAT_ID: {QORASAROY_GROUP_CHAT_ID}")
logger.info("----------------------------------")

TIMEZONE = "Asia/Tashkent"

USE_SHEETS = True
IS_SHEETS_ENABLED = True

def is_sheets_configured():
    return bool(GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_INFO)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8000"))
