"""
Lightweight Dunyabunya Logistics Dashboard.
This file is 100% standalone — it does NOT import from core/ or bot code.
Uses direct Supabase REST API (PostgREST) via httpx instead of heavy supabase-py SDK.
"""
import os
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

app = FastAPI(title="Logistics Dashboard")
templates = Jinja2Templates(directory="templates")

# ── Lightweight Supabase REST helpers ───────────────────────────────
# These use httpx to call PostgREST directly — no heavy SDK needed.

_headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

def _rest_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"

def db_get_drivers_status() -> list:
    """Fetch all rows from drivers_status table."""
    try:
        r = httpx.get(
            _rest_url("drivers_status"),
            headers={**_headers, "Prefer": "count=exact"},
            params={"select": "*"},
            timeout=8
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"REST drivers_status error: {e}")
        return []

def db_get_dashboard_stats() -> dict:
    """Fetch dashboard statistics using lightweight REST calls."""
    try:
        tz_offset = timedelta(hours=5)  # Asia/Tashkent = UTC+5
        now = datetime.now(timezone(tz_offset))
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        # Active orders count
        r1 = httpx.get(
            _rest_url("orders"),
            headers={**_headers, "Prefer": "count=exact"},
            params={
                "select": "id",
                "current_status": "not.eq.DONE",
                "limit": "0"
            },
            timeout=8
        )
        active = int(r1.headers.get("content-range", "*/0").split("/")[-1] or 0)

        # Finished today count
        r2 = httpx.get(
            _rest_url("orders"),
            headers={**_headers, "Prefer": "count=exact"},
            params={
                "select": "id",
                "current_status": "eq.DONE",
                "completed_at": f"gte.{today_start}",
                "limit": "0"
            },
            timeout=8
        )
        finished = int(r2.headers.get("content-range", "*/0").split("/")[-1] or 0)

        # Failed count
        r3 = httpx.get(
            _rest_url("orders"),
            headers={**_headers, "Prefer": "count=exact"},
            params={
                "select": "id",
                "current_status": "eq.ERROR_BOT_BLOCKED",
                "limit": "0"
            },
            timeout=8
        )
        failed = int(r3.headers.get("content-range", "*/0").split("/")[-1] or 0)

        # Recent 5 updates
        r4 = httpx.get(
            _rest_url("orders"),
            headers=_headers,
            params={
                "select": "order_id,car_number,current_status,driver_name",
                "order": "created_at.desc",
                "limit": "5"
            },
            timeout=8
        )
        updates = r4.json() if r4.status_code == 200 else []

        return {
            "active": active,
            "finished_today": finished,
            "failed": failed,
            "updates": updates
        }
    except Exception as e:
        logger.error(f"REST dashboard stats error: {e}")
        return {"active": 0, "finished_today": 0, "failed": 0, "updates": []}


# ── Routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    drivers = db_get_drivers_status()
    stats = db_get_dashboard_stats()

    total = len(drivers)
    free = sum(1 for d in drivers if "BO" in (d.get("status") or ""))
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
