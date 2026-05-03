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

service_account_json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
try:
    GOOGLE_SERVICE_ACCOUNT_INFO = json.loads(service_account_json_str)
except json.JSONDecodeError:
    GOOGLE_SERVICE_ACCOUNT_INFO = {}

TIMEZONE = "Asia/Tashkent"
