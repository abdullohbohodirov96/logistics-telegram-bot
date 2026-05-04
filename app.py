"""
Lightweight Dunyabunya Logistics Dashboard.
100% standalone — does NOT import from core/ or bot code.
Uses direct Supabase REST API (PostgREST) via httpx.
"""
import os
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

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
TZ = timezone(timedelta(hours=5))  # Asia/Tashkent = UTC+5

app = FastAPI(title="Logistics Dashboard")
templates = Jinja2Templates(directory="templates")

# ── Simple 10-second cache ──────────────────────────────────────────
_cache = {}

def _cached(key, ttl=10):
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < ttl:
        return entry[0]
    return None

def _set_cache(key, val):
    _cache[key] = (val, time.time())

# ── REST helpers ────────────────────────────────────────────────────
_headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

def _rest(table): return f"{SUPABASE_URL}/rest/v1/{table}"

def _count(table, params) -> int:
    """Get count of rows matching params."""
    try:
        r = httpx.get(
            _rest(table),
            headers={**_headers, "Prefer": "count=exact"},
            params={**params, "select": "id", "limit": "0"},
            timeout=8
        )
        cr = r.headers.get("content-range", "*/0")
        return int(cr.split("/")[-1] or 0)
    except:
        return 0

def _rows(table, params) -> list:
    """Get rows matching params."""
    try:
        r = httpx.get(_rest(table), headers=_headers, params=params, timeout=8)
        return r.json() if r.status_code == 200 else []
    except:
        return []

# ── Data fetchers ───────────────────────────────────────────────────

def get_drivers():
    cached = _cached("drivers")
    if cached is not None: return cached
    data = _rows("drivers_status", {"select": "*"})
    _set_cache("drivers", data)
    return data

def get_stats():
    cached = _cached("stats")
    if cached is not None: return cached

    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Bugun yetkazilganlar (DONE + completed_at bugun)
    finished_today = _count("orders", {
        "current_status": "eq.DONE",
        "completed_at": f"gte.{today_start}",
    })

    # Aktiv (DONE bo'lmagan)
    active = _count("orders", {
        "current_status": "not.eq.DONE",
        "current_status": "neq.ERROR_BOT_BLOCKED",
    })

    # Muammoli
    failed = _count("orders", {
        "current_status": "eq.ERROR_BOT_BLOCKED",
    })

    # Oxirgi 20 ta yangilanish
    updates = _rows("orders", {
        "select": "order_id,car_number,current_status,driver_name,address",
        "order": "created_at.desc",
        "limit": "20"
    })

    result = {
        "finished_today": finished_today,
        "active": active,
        "failed": failed,
        "updates": updates
    }
    _set_cache("stats", result)
    return result

# ── Routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    drivers = get_drivers()
    stats = get_stats()

    total = len(drivers)
    free = sum(1 for d in drivers if "BO" in ((d.get("status") or "").upper()))
    busy = total - free

    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={
            "drivers": drivers,
            "total": total,
            "free": free,
            "busy": busy,
            "stats": stats
        }
    )

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
