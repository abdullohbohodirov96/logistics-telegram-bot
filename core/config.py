import os
import json
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Google Sheets Configuration
GOOGLE_SHEET_ID = os.getenv("SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID")
DRIVERS_SHEET_NAME = os.getenv("DRIVERS_SHEET_NAME", "drivers")
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders")

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

ADMIN_IDS = []
admin_ids_str = os.getenv("ADMIN_IDS", "")
if admin_ids_str:
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]

service_account_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
try:
    GOOGLE_SERVICE_ACCOUNT_INFO = json.loads(service_account_json_str)
except json.JSONDecodeError:
    GOOGLE_SERVICE_ACCOUNT_INFO = {}

TIMEZONE = "Asia/Tashkent"

# Control Flags
USE_SHEETS = True
IS_SHEETS_ENABLED = True

def is_sheets_configured():
    return bool(GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_INFO)
