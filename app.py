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
GOOGLE_SHEET_ID = os.getenv("SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID", "")
DRIVERS_SHEET_NAME = os.getenv("DRIVERS_SHEET_NAME", "drivers")
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders")
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
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/{DRIVERS_SHEET_NAME}!A1:Z"
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        r.raise_for_status()
        data = r.json().get("values", [])
        if not data: return []
        
        headers = [h.strip().lower() for h in data[0]]
        rows = data[1:]

        def _find(names):
            for n in names:
                if n in headers:
                    return headers.index(n)
            return -1

        # Fuzzy + positional fallback: the drivers sheet is A=car_number,
        # B=driver_name, C=telegram_id, D=status, E=current_order_id, so if
        # the header row doesn't literally say these words, fall back to
        # column position instead of returning nothing.
        idx_car    = _find(['car_number', 'car', 'mashina'])
        idx_name   = _find(['driver_name', 'name', 'haydovchi'])
        idx_tid    = _find(['telegram_id', 'tg_id', 'telegramid'])
        idx_status = _find(['status', 'holat'])
        idx_order  = _find(['current_order_id', 'order_id', 'orders'])
        if idx_car == -1:    idx_car = 0
        if idx_name == -1:   idx_name = 1
        if idx_tid == -1:    idx_tid = 2
        if idx_status == -1: idx_status = 3
        if idx_order == -1:  idx_order = 4

        cars = []
        for row in rows:
            if len(row) <= max(idx_car, idx_name, idx_status):
                continue

            cn = row[idx_car].strip()
            dn = row[idx_name].strip()
            if not cn: continue

            tid = row[idx_tid].strip() if len(row) > idx_tid else ""
            raw_status = row[idx_status].strip().upper() if len(row) > idx_status else ""
            status = "BAND" if raw_status in ("BAND", "YUK ORTYAPTI", "YUK OGAN", "YO'LDA", "YETIB BORDI") else "BO'SH"
            order_id = row[idx_order].strip() if len(row) > idx_order else ""

            cars.append({
                "car_number": cn,
                "driver_name": dn,
                "telegram_id": tid,
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

def get_driver_live_stages() -> dict:
    """
    Returns {telegram_id(str): stage} for every driver with an active order,
    where stage is 'YUK_ORTYAPTI' (accepted, still loading/prepping — the
    order hasn't left yet) or 'YOLDA' (driver tapped "Yo'lga chiqdim" and is
    actually en route). Drivers with no active order simply have no entry
    here, i.e. they're free (BO'SH).

    The Drivers Google Sheet only ever stores two coarse states (BO'SH /
    YUK OGAN) — it's never updated when a driver actually departs. The real,
    live per-order stage lives in Supabase (current_status), so that's the
    source of truth for this 3-way split. Cached 8 seconds.
    """
    cached = _cached("live_stages", ttl=8)
    if cached is not None:
        return cached

    rows = _sb_rows("orders", {
        "select": "driver_telegram_id,current_status",
        "current_status": "not.in.(YAKUNLANDI,BEKOR_QILINDI,ESKI_YOPILDI,NEW)",
    })

    # Stage priority if a driver somehow has more than one active order —
    # 'on the way' wins over 'still loading', so the board shows the more
    # advanced (more informative) state.
    priority = {"YOLDA": 2, "YUK_ORTYAPTI": 1}
    stages = {}
    for r in rows:
        tid = str(r.get('driver_telegram_id') or '').strip()
        if not tid:
            continue
        raw = (r.get('current_status') or '').strip().upper()
        stage = "YOLDA" if raw == "YOLDA" else "YUK_ORTYAPTI"
        if tid not in stages or priority[stage] > priority[stages[tid]]:
            stages[tid] = stage

    _set("live_stages", stages)
    return stages


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
    live_stages = get_driver_live_stages()

    # 3-way live board: Bo'sh / Yuk ortyapti / Yo'lda — driven by Supabase's
    # real per-order status (live_stages), not the sheet's coarse 2-state
    # column, so "Yo'lda" is actually accurate.
    board = {"BOSH": [], "YUK_ORTYAPTI": [], "YOLDA": []}
    for car in cars:
        stage = live_stages.get(car["telegram_id"], "") if car.get("telegram_id") else ""
        if stage == "YOLDA":
            board["YOLDA"].append(car)
        elif stage == "YUK_ORTYAPTI":
            board["YUK_ORTYAPTI"].append(car)
        else:
            board["BOSH"].append(car)

    total = len(cars)
    free = len(board["BOSH"])
    busy = total - free

    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={
            "drivers": cars,
            "board": board,
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
        "live_stages": get_driver_live_stages(),
        "sheet_id": GOOGLE_SHEET_ID[:8] + "..." if GOOGLE_SHEET_ID else "MISSING",
        "creds": "SET" if _sa_info.get("client_email") else "MISSING",
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
