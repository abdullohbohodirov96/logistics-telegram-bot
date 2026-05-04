"""
Dunyabunya Logistics Dashboard — reads live data from Google Sheets.

Google Sheets `drivers` sheet is the SINGLE SOURCE OF TRUTH for car statuses.
Bot updates columns D-G when deliveries start/finish.
Dashboard just reads and displays them.

Supabase is only used for: today completed count + recent history list.
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

# Google credentials — try both env var names
_sa_raw = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "{}"
_sa_info = {}
try:
    _sa_info = json.loads(_sa_raw)
except Exception as e:
    logger.error(f"Failed to parse Google credentials: {e}")

logger.info(f"CONFIG: SHEET_ID={'SET' if GOOGLE_SHEET_ID else 'MISSING'}, "
            f"GOOGLE_CREDS={'SET' if _sa_info.get('client_email') else 'MISSING'}, "
            f"SUPABASE={'SET' if SUPABASE_URL else 'MISSING'}")

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

# ── Google Auth ─────────────────────────────────────────────────────
_gtoken = None
_gtoken_exp = 0

def _get_google_token():
    global _gtoken, _gtoken_exp
    if _gtoken and time.time() < _gtoken_exp - 60:
        return _gtoken
    if not _sa_info.get("client_email"):
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
        logger.info("Google token OK")
        return _gtoken
    except Exception as e:
        logger.error(f"Google auth error: {e}")
        return None

# ── Google Sheets: read drivers sheet ──────────────────────────────

def sheets_read_cars() -> list:
    """Read all cars from Google Sheets `drivers` sheet.
    
    Expected columns:
    A=car_number | B=driver_name | C=telegram_id | D=status | E=current_order_id | F=started_at | G=updated_at
    
    Returns list of dicts. Cached 15 seconds.
    """
    cached = _cached("cars", ttl=15)
    if cached is not None:
        return cached

    token = _get_google_token()
    if not token or not GOOGLE_SHEET_ID:
        logger.error("Cannot read Sheets: no token or SHEET_ID")
        return []

    try:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/drivers!A2:G"
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        rows = r.json().get("values", [])
        
        cars = []
        for row in rows:
            if len(row) < 2:
                continue
            cn = row[0].strip()
            dn = row[1].strip()
            if not cn:
                continue
            
            # D column = status (index 3)
            raw_status = row[3].strip().upper() if len(row) > 3 and row[3].strip() else ""
            
            # Determine status
            if raw_status in ("BAND", "YUK ORTYAPTI", "YO'LDA", "YETIB BORDI"):
                status = "BAND"
            else:
                status = "BO'SH"
            
            # E column = current_order_id (index 4)
            order_id = row[4].strip() if len(row) > 4 else ""
            
            cars.append({
                "car_number": cn,
                "driver_name": dn,
                "status": status,
                "raw_status": raw_status or "BO'SH",
                "current_order_id": order_id,
            })
        
        logger.info(f"Sheets: {len(cars)} cars loaded")
        for c in cars:
            logger.info(f"  {c['car_number']} / {c['driver_name']} -> {c['status']} ({c['raw_status']})")
        
        _set("cars", cars)
        return cars
    except Exception as e:
        logger.error(f"Sheets read error: {e}")
        return []

# ── Supabase REST (only for today count + history) ─────────────────

_sb_h = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}
def _sb(table): return f"{SUPABASE_URL}/rest/v1/{table}"

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

def get_supabase_stats() -> dict:
    """Get today finished count + recent updates from Supabase. Cached 10s."""
    cached = _cached("sb_stats", ttl=10)
    if cached is not None:
        return cached

    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    finished_today = _sb_count({
        "current_status": "eq.DONE",
        "completed_at": f"gte.{today_start}",
    })

    failed = _sb_count({
        "current_status": "in.(ERROR_BOT_BLOCKED,ERROR_DRIVER_NOT_FOUND)",
    })

    updates = _sb_rows("orders", {
        "select": "order_id,car_number,current_status,driver_name,address",
        "order": "created_at.desc",
        "limit": "20",
    })

    result = {
        "finished_today": finished_today,
        "failed": failed,
        "updates": updates,
    }
    _set("sb_stats", result)
    return result

# ── Routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    cars = sheets_read_cars()
    sb = get_supabase_stats()

    total = len(cars)
    free = sum(1 for c in cars if c["status"] == "BO'SH")
    busy = total - free

    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={
            "drivers": cars,
            "total": total,
            "free": free,
            "busy": busy,
            "stats": {
                "finished_today": sb["finished_today"],
                "active": busy,
                "failed": sb["failed"],
                "updates": sb["updates"],
            }
        }
    )

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/debug")
def debug():
    cars = sheets_read_cars()
    return {
        "total_cars": len(cars),
        "cars": cars,
        "sheet_id": GOOGLE_SHEET_ID[:8] + "..." if GOOGLE_SHEET_ID else "MISSING",
        "creds": "SET" if _sa_info.get("client_email") else "MISSING",
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
