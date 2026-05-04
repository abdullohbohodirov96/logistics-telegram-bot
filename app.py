"""
Dunyabunya Logistics Dashboard.
100% standalone — does NOT import from core/ or bot code.

Logic:
  Google Sheets (drivers!A2:C) = MASTER list of ALL cars
  Supabase (orders)            = active jobs only
  For each car in Sheets:
    - has active unfinished job in Supabase → BAND
    - no active unfinished job             → BO'SH
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dashboard")

# ── Config ──────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
TZ = timezone(timedelta(hours=5))  # Asia/Tashkent

# Try both env var names for Google credentials
_sa_raw = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "{}"
_sa_info = {}
try:
    _sa_info = json.loads(_sa_raw)
except Exception as e:
    logger.error(f"Failed to parse Google credentials JSON: {e}")

# Debug: log config status on startup
logger.info(f"GOOGLE_SHEET_ID: {'SET (' + GOOGLE_SHEET_ID[:8] + '...)' if GOOGLE_SHEET_ID else 'MISSING'}")
logger.info(f"GOOGLE_CREDENTIALS: {'SET' if _sa_info.get('client_email') else 'MISSING'}")
logger.info(f"SUPABASE_URL: {'SET' if SUPABASE_URL else 'MISSING'}")
logger.info(f"SUPABASE_KEY: {'SET' if SUPABASE_KEY else 'MISSING'}")

app = FastAPI(title="Logistics Dashboard")
templates = Jinja2Templates(directory="templates")

# ── Cache ───────────────────────────────────────────────────────────
_cache = {}

def _cached(key, ttl=10):
    e = _cache.get(key)
    if e and time.time() - e[1] < ttl:
        return e[0]
    return None

def _set(key, val):
    _cache[key] = (val, time.time())

# ── Normalize car number for matching ──────────────────────────────
def _norm(car_number: str) -> str:
    """Normalize car number: uppercase, strip all spaces."""
    if not car_number:
        return ""
    return car_number.strip().upper().replace(" ", "")

# ── Google Sheets reader ───────────────────────────────────────────
_gtoken = None
_gtoken_exp = 0

def _get_google_token():
    global _gtoken, _gtoken_exp
    if _gtoken and time.time() < _gtoken_exp - 60:
        return _gtoken
    if not _sa_info.get("client_email"):
        logger.warning("No Google service account credentials available")
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
        _gtoken_exp = time.time() + 3500
        logger.info("Google token refreshed OK")
        return _gtoken
    except Exception as e:
        logger.error(f"Google auth error: {e}")
        return None

def sheets_get_all_cars() -> list:
    """Read FULL car list from Google Sheets drivers!A2:C.
    Returns: [{"car_number": "01A777AA", "driver_name": "Alisher"}, ...]
    Cached 30 seconds.
    """
    cached = _cached("all_cars", ttl=30)
    if cached is not None:
        return cached

    token = _get_google_token()
    if not token or not GOOGLE_SHEET_ID:
        logger.error("Cannot read Sheets: no token or SHEET_ID")
        return []

    try:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/drivers!A2:C"
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        rows = r.json().get("values", [])
        cars = []
        for row in rows:
            if len(row) >= 2:
                cn = row[0].strip()
                dn = row[1].strip()
                if cn:
                    cars.append({"car_number": cn, "driver_name": dn})
        logger.info(f"Sheets: loaded {len(cars)} cars")
        for c in cars:
            logger.info(f"  Sheets car: {c['car_number']} / {c['driver_name']}")
        _set("all_cars", cars)
        return cars
    except Exception as e:
        logger.error(f"Sheets read error: {e}")
        return []

# ── Supabase REST ──────────────────────────────────────────────────
_sb_h = {
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
            headers={**_sb_h, "Prefer": "count=exact"},
            params={**params, "select": "id", "limit": "0"},
            timeout=8
        )
        return int(r.headers.get("content-range", "*/0").split("/")[-1] or 0)
    except:
        return 0

def _sb_rows(table, params) -> list:
    try:
        r = httpx.get(_sb(table), headers=_sb_h, params=params, timeout=8)
        return r.json() if r.status_code == 200 else []
    except:
        return []

def get_active_job_car_numbers() -> set:
    """Get normalized car_numbers that have ACTIVE unfinished jobs.
    Active = current_status NOT in finished statuses AND completed_at is null.
    Cached 10 seconds.
    """
    cached = _cached("active_cars", ttl=10)
    if cached is not None:
        return cached

    # Get orders that are NOT done (active/unfinished)
    # PostgREST: current_status not in (DONE, ERROR_BOT_BLOCKED) AND completed_at is null
    rows = _sb_rows("orders", {
        "select": "car_number,current_status",
        "completed_at": "is.null",
        "current_status": "not.in.(DONE,ERROR_BOT_BLOCKED,ERROR_DRIVER_NOT_FOUND)",
    })

    active = set()
    for r in rows:
        cn = r.get("car_number")
        if cn:
            active.add(_norm(cn))

    logger.info(f"Supabase: {len(active)} active job car_numbers: {active}")
    _set("active_cars", active)
    return active

# ── Build dashboard data ───────────────────────────────────────────

def build_dashboard():
    """Merge Sheets master list + Supabase active jobs. Cached 10s."""
    cached = _cached("dashboard", ttl=10)
    if cached is not None:
        return cached

    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    # 1) All cars from Sheets (master)
    all_cars = sheets_get_all_cars()

    # 2) Active car numbers from Supabase
    active_set = get_active_job_car_numbers()

    # 3) Build driver list with BAND/BO'SH
    drivers = []
    for car in all_cars:
        cn_raw = car["car_number"]
        cn_norm = _norm(cn_raw)
        is_busy = cn_norm in active_set

        status = "BAND" if is_busy else "BO'SH"
        logger.info(f"  {cn_raw} ({cn_norm}) -> {status}")

        drivers.append({
            "car_number": cn_raw,
            "driver_name": car["driver_name"],
            "status": status,
        })

    total = len(drivers)
    free = sum(1 for d in drivers if d["status"] == "BO'SH")
    busy = total - free

    logger.info(f"Dashboard: total={total}, busy={busy}, free={free}")

    # 4) Today finished count
    finished_today = _sb_count({
        "current_status": "eq.DONE",
        "completed_at": f"gte.{today_start}",
    })

    # 5) Failed count
    failed = _sb_count({
        "current_status": "in.(ERROR_BOT_BLOCKED,ERROR_DRIVER_NOT_FOUND)",
    })

    # 6) Recent 20 updates
    updates = _sb_rows("orders", {
        "select": "order_id,car_number,current_status,driver_name,address",
        "order": "created_at.desc",
        "limit": "20",
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
    data = build_dashboard()
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

@app.get("/debug")
def debug():
    """Debug endpoint: returns raw data for troubleshooting."""
    all_cars = sheets_get_all_cars()
    active = list(get_active_job_car_numbers())
    return {
        "sheets_cars": all_cars,
        "active_car_numbers": active,
        "google_sheet_id": GOOGLE_SHEET_ID[:8] + "..." if GOOGLE_SHEET_ID else "MISSING",
        "google_creds": "SET" if _sa_info.get("client_email") else "MISSING",
        "supabase": "SET" if SUPABASE_URL else "MISSING",
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
