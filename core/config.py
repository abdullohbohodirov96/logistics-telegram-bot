import os
import json
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
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

# Odoo Configuration
ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
ODOO_API_KEY = os.getenv("ODOO_API_KEY")

def is_odoo_configured():
    return all([ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY])
