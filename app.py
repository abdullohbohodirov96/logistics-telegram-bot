"""
Dunyabunya Logistics Dashboard.
100% standalone — does NOT import from core/ or bot code.

Data flow:
  Google Sheets (drivers!A2:C) = MASTER list of all cars/drivers
  Supabase (orders table)      = active jobs + finished today
  → Dashboard merges them: car has active job = BAND, no active job = BO'SH
"""
import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import httpx
import uvicorn
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
TZ = timezone(timedelta(hours=5))  # Asia/Tashkent = UTC+5

# Parse Google Service Account JSON
_sa_info = {}
try:
    _sa_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}"))
except:
    pass

app = FastAPI(title="Logistics Dashboard")
templates = Jinja2Templates(directory="templates")

# ── Cache (TTL-based) ──────────────────────────────────────────────
_cache = {}

def _cached(key, ttl=10):
    e = _cache.get(key)
    if e and time.time() - e[1] < ttl:
        return e[0]
    return None

def _set(key, val):
    _cache[key] = (val, time.time())

# ── Google Sheets reader (via REST + google-auth) ──────────────────
_gtoken = None
_gtoken_exp = 0

def _get_google_token():
    """Get a valid Google access token using service account credentials."""
    global _gtoken, _gtoken_exp
    if _gtoken and time.time() < _gtoken_exp - 60:
        return _gtoken
    if not _sa_info:
        return None
    try:
        from google.oauth2 import service_account as sa
        import google.auth.transport.requests
        creds = sa.Credentials.from_service_account_info(
            _sa_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        creds.refresh(google.auth.transport.requests.Request())
        _gtoken = creds.token
        _gtoken_exp = time.time() + 3500  # tokens last ~3600s
        return _gtoken
    except Exception as e:
        logger.error(f"Google auth error: {e}")
        return None

def sheets_get_all_cars() -> list:
    """Read full car/driver list from Google Sheets (drivers!A2:C).
    Returns: [{"car_number": "01A777AA", "driver_name": "Alisher", "telegram_id": 123}, ...]
    Cached for 30 seconds.
    """
    cached = _cached("sheets_cars", ttl=30)
    if cached is not None:
        return cached

    token = _get_google_token()
    if not token or not GOOGLE_SHEET_ID:
        return []

    try:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/drivers!A2:C"
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=8)
        r.raise_for_status()
        rows = r.json().get("values", [])
        cars = []
        for row in rows:
            if len(row) >= 3:
                cars.append({
                    "car_number": row[0].strip(),
                    "driver_name": row[1].strip(),
                    "telegram_id": row[2].strip()
                })
        _set("sheets_cars", cars)
        return cars
    except Exception as e:
        logger.error(f"Sheets read error: {e}")
        return []

# ── Supabase REST helpers ──────────────────────────────────────────
_sb_headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def _sb(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"

def _sb_count(params) -> int:
    try:
        r = httpx.get(
            _sb("orders"),
            headers={**_sb_headers, "Prefer": "count=exact"},
            params={**params, "select": "id", "limit": "0"},
            timeout=8
        )
        cr = r.headers.get("content-range", "*/0")
        return int(cr.split("/")[-1] or 0)
    except:
        return 0

def _sb_rows(table, params) -> list:
    try:
        r = httpx.get(_sb(table), headers=_sb_headers, params=params, timeout=8)
        return r.json() if r.status_code == 200 else []
    except:
        return []

# ── Business logic ─────────────────────────────────────────────────

def get_active_car_numbers() -> set:
    """Get set of car_numbers that currently have unfinished jobs.
    Cached 10 seconds."""
    cached = _cached("active_cars", ttl=10)
    if cached is not None:
        return cached

    rows = _sb_rows("orders", {
        "select": "car_number",
        "current_status": "neq.DONE",
    })
    result = set(r["car_number"] for r in rows if r.get("car_number"))
    _set("active_cars", result)
    return result

def build_dashboard_data():
    """Build full dashboard state. Cached 10 seconds."""
    cached = _cached("dashboard", ttl=10)
    if cached is not None:
        return cached

    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    # 1) Master car list from Google Sheets
    all_cars = sheets_get_all_cars()

    # 2) Active car numbers from Supabase
    active_cars = get_active_car_numbers()

    # 3) Build driver list with status
    drivers = []
    for car in all_cars:
        cn = car["car_number"]
        if cn in active_cars:
            status = "BAND"
        else:
            status = "BO'SH"
        drivers.append({
            "car_number": cn,
            "driver_name": car["driver_name"],
            "status": status,
        })

    total = len(drivers)
    free = sum(1 for d in drivers if d["status"] == "BO'SH")
    busy = total - free

    # 4) Today finished count
    finished_today = _sb_count({
        "current_status": "eq.DONE",
        "completed_at": f"gte.{today_start}",
    })

    # 5) Failed/error count
    failed = _sb_count({
        "current_status": "eq.ERROR_BOT_BLOCKED",
    })

    # 6) Recent 20 updates
    updates = _sb_rows("orders", {
        "select": "order_id,car_number,current_status,driver_name,address",
        "order": "created_at.desc",
        "limit": "20"
    })

    result = {
        "drivers": drivers,
        "total": total,
        "free": free,
        "busy": busy,
        "finished_today": finished_today,
        "active": busy,
        "failed": failed,
        "updates": updates,
    }
    _set("dashboard", result)
    return result

# ── Routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    data = build_dashboard_data()
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={
            "drivers": data["drivers"],
            "total": data["total"],
            "free": data["free"],
            "busy": data["busy"],
            "stats": {
                "finished_today": data["finished_today"],
                "active": data["active"],
                "failed": data["failed"],
                "updates": data["updates"],
            }
        }
    )

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
