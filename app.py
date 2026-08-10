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
from core.vehicle_durations import get_expected_duration

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dashboard")

# ── Config ──────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GOOGLE_SHEET_ID = os.getenv("SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID", "")
DRIVERS_SHEET_NAME = os.getenv("DRIVERS_SHEET_NAME", "drivers")
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders")
QORASAROY_ORDERS_SHEET_NAME = os.getenv("QORASAROY_ORDERS_SHEET_NAME", "Qorasaroy orders")
# All branch order sheets to scan for problem rows (dedup, keep order stable).
ORDERS_SHEETS = list(dict.fromkeys([ORDERS_SHEET_NAME, QORASAROY_ORDERS_SHEET_NAME]))
TZ = timezone(timedelta(hours=5))  # Asia/Tashkent

# Sheet statuses that mean "this order did NOT go out and needs a human to
# look at it" — must match exactly what core/scheduler.py writes.
PROBLEM_STATUSES = {
    "XAYDOVCHI_TOPILMADI", "TELEGRAM_ID_YOQ", "TELEGRAM_XATOSI",
    "QATOR_BOSH", "XAYDOVCHI_BAND",
}

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

def sheets_read_problem_orders() -> list:
    """
    Scan every branch order sheet for rows in a PROBLEM_STATUSES status
    (order that did NOT go out to a driver and needs a human to look at
    it) and return them with their reason note (column H), so the
    dashboard's "Muammoli" count is a real, clickable list instead of a
    number nobody can act on. Cached 15 seconds.
    """
    cached = _cached("problem_orders", ttl=15)
    if cached is not None:
        return cached

    token = _get_google_token()
    if not token or not GOOGLE_SHEET_ID:
        return []

    problems = []
    for sheet_name in ORDERS_SHEETS:
        try:
            url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/{sheet_name}!A1:H"
            r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            r.raise_for_status()
            data = r.json().get("values", [])
            if not data:
                continue

            headers = [h.strip().lower() for h in data[0]]

            def _find(names, default=-1):
                for n in names:
                    if n in headers:
                        return headers.index(n)
                return default

            idx_id     = _find(['id', 'order_id', 'id_order'], 0)
            idx_car    = _find(['car', 'car_number', 'mashina', 'moshina'], 1)
            idx_status = _find(['status', 'holat'], 4)

            for i, row in enumerate(data[1:], start=2):
                status_val = row[idx_status].strip().upper() if len(row) > idx_status >= 0 else ""
                if status_val not in PROBLEM_STATUSES:
                    continue
                order_id = row[idx_id].strip() if len(row) > idx_id >= 0 else f"row_{i}"
                car_number = row[idx_car].strip() if len(row) > idx_car >= 0 else "-"
                note = row[7].strip() if len(row) > 7 else ""
                problems.append({
                    "order_id": order_id,
                    "car_number": car_number,
                    "status": status_val,
                    "note": note,
                    "sheet_name": sheet_name,
                })
        except Exception as e:
            logger.error(f"sheets_read_problem_orders: sheet={sheet_name} err: {e}")

    _set("problem_orders", problems)
    return problems


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

def _parse_iso(s):
    """Parse a Supabase timestamp string into an Asia/Tashkent-aware datetime.

    Every write in this codebase sends an already-Tashkent-local, offset-aware
    ISO string (e.g. "...T14:50:00+05:00") for timestamptz columns. If the
    column comes back from PostgREST WITH an offset (normal case — proper
    UTC, "+00:00"/"Z"), we just convert it to Tashkent as usual.

    But if a column ever comes back with NO offset at all (e.g. it's a plain
    `timestamp` without timezone, or the client dropped the offset), the raw
    number is exactly the Tashkent wall-clock time we originally wrote — NOT
    UTC. Treating it as UTC and converting would incorrectly add another 5
    hours on top (this was the bug behind delivered-times/ETAs showing
    5 hours in the future). So a naive value is tagged as Tashkent directly,
    never re-shifted.
    """
    if not s:
        return None
    try:
        s2 = str(s).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def get_driver_live_stages() -> dict:
    """
    Returns {telegram_id(str): {...}} for every driver with an active order:
      stage        'YUK_ORTYAPTI' (accepted, still loading/prepping) or
                   'YOLDA' (driver tapped "Yo'lga chiqdim", en route)
      order_id     the active order's id, for display
      eta_minutes  estimated minutes until this car is free again (can be
                   negative if it's running late), or None if this car
                   isn't in the vehicle-duration reference table
      eta_time     "HH:MM" estimated free-up clock time, or None

    Drivers with no active order simply have no entry here (they're BO'SH).

    The Drivers Google Sheet only ever stores two coarse states (BO'SH /
    YUK OGAN) — it's never updated when a driver actually departs, and it
    has no notion of expected duration at all. The real, live per-order
    stage + timing lives in Supabase, so that's the source of truth for
    this board. Cached 8 seconds.
    """
    cached = _cached("live_stages", ttl=8)
    if cached is not None:
        return cached

    rows = _sb_rows("orders", {
        "select": "driver_telegram_id,current_status,order_id,car_number,accepted_at,created_at",
        "current_status": "not.in.(YAKUNLANDI,BEKOR_QILINDI,ESKI_YOPILDI,NEW)",
    })

    # Stage priority if a driver somehow has more than one active order —
    # 'on the way' wins over 'still loading', so the board shows the more
    # advanced (more informative) state.
    priority = {"YOLDA": 2, "YUK_ORTYAPTI": 1}
    stages = {}
    now = datetime.now(TZ)
    for r in rows:
        tid = str(r.get('driver_telegram_id') or '').strip()
        if not tid:
            continue
        raw = (r.get('current_status') or '').strip().upper()
        stage = "YOLDA" if raw == "YOLDA" else "YUK_ORTYAPTI"
        if tid in stages and priority[stage] <= priority[stages[tid]['stage']]:
            continue

        anchor = _parse_iso(r.get('accepted_at')) or _parse_iso(r.get('created_at'))
        eta_minutes, eta_time = None, None
        expected = get_expected_duration(r.get('car_number') or '')
        if anchor and expected:
            _vehicle_type, minutes = expected
            eta_dt = anchor + timedelta(minutes=minutes)
            eta_minutes = int((eta_dt - now).total_seconds() / 60)
            eta_time = eta_dt.strftime('%H:%M')

        stages[tid] = {
            "stage": stage,
            "order_id": r.get('order_id') or '',
            "eta_minutes": eta_minutes,
            "eta_time": eta_time,
        }

    _set("live_stages", stages)
    return stages


def get_last_finished_times(telegram_ids: list) -> dict:
    """
    For the given telegram_ids (drivers currently showing as free), find
    each one's most recent YAKUNLANDI order's completed_at, so the board
    can show "bo'sh bo'lganiga X daqiqa" instead of just a bare "Bo'sh".

    One bulk query (driver_telegram_id in (...) + order by completed_at
    desc), not one query per driver. Cached 15 seconds per exact set of
    ids requested (fine here — the free-car list only changes when
    someone's status changes, which already busts other caches too).
    """
    ids = sorted({str(t) for t in telegram_ids if t})
    if not ids:
        return {}
    cache_key = "last_finished:" + ",".join(ids)
    cached = _cached(cache_key, ttl=15)
    if cached is not None:
        return cached

    rows = _sb_rows("orders", {
        "select": "driver_telegram_id,completed_at",
        "driver_telegram_id": f"in.({','.join(ids)})",
        "current_status": "eq.YAKUNLANDI",
        "order": "completed_at.desc",
        "limit": str(len(ids) * 5),
    })

    result = {}
    for r in rows:
        tid = str(r.get('driver_telegram_id') or '')
        if not tid or tid in result:
            continue  # first hit per driver is the most recent (already sorted desc)
        dt = _parse_iso(r.get('completed_at'))
        if dt:
            result[tid] = dt

    _set(cache_key, result)
    return result


def get_supabase_stats() -> dict:
    """
    Get today's finished count + the actual list of today's deliveries
    from Supabase. Cached 10s.

    NOTE: the finish status is 'YAKUNLANDI' (set by delivery.py /
    core/db.py everywhere) — this used to check for 'DONE', which no
    order ever actually has, so "Bugun yetkazildi" was always 0.
    """
    # Cache key includes today's Tashkent date, so the list can never leak
    # stale data across a midnight rollover — the moment the date changes,
    # it's a guaranteed cache miss and we recompute with a fresh boundary.
    now = datetime.now(TZ)
    today_start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start_dt = today_start_dt + timedelta(days=1)
    cache_key = f"sb_stats_{today_start_dt.strftime('%Y-%m-%d')}"

    cached = _cached(cache_key, ttl=10)
    if cached is not None:
        return cached

    today_start = today_start_dt.isoformat()
    tomorrow_start = tomorrow_start_dt.isoformat()

    delivered_today = _sb_rows("orders", {
        "select": "order_id,car_number,driver_name,completed_at",
        "current_status": "eq.YAKUNLANDI",
        "completed_at": f"gte.{today_start}",
        "and": f"(completed_at.lt.{tomorrow_start})",
        "order": "completed_at.desc",
        "limit": "200",
    })
    for o in delivered_today:
        dt = _parse_iso(o.get("completed_at"))
        # Always show the exact Asia/Tashkent time (HH:MM), never a raw
        # UTC/server timestamp.
        o["completed_time"] = dt.strftime("%H:%M") if dt else "-"

    result = {
        "finished_today": len(delivered_today),
        "delivered_today": delivered_today,
    }
    _set(cache_key, result)
    return result

# ── Routes ──────────────────────────────────────────────────────────

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def dashboard(request: Request):
    cars = sheets_read_cars()
    sb = get_supabase_stats()
    live_stages = get_driver_live_stages()
    problem_orders = sheets_read_problem_orders()

    # 3-way live board: Bo'sh / Yuk ortyapti / Yo'lda — driven by Supabase's
    # real per-order status (live_stages), not the sheet's coarse 2-state
    # column, so "Yo'lda" is actually accurate. Each car also gets an
    # estimated free-up time/countdown from the vehicle-duration table.
    board = {"BOSH": [], "YUK_ORTYAPTI": [], "YOLDA": []}
    for car in cars:
        info = live_stages.get(car["telegram_id"]) if car.get("telegram_id") else None
        if not info:
            board["BOSH"].append(car)
            continue

        car["order_id"]    = info.get("order_id")
        car["eta_minutes"] = info.get("eta_minutes")
        car["eta_time"]    = info.get("eta_time")

        if info["stage"] == "YOLDA":
            board["YOLDA"].append(car)
        else:
            board["YUK_ORTYAPTI"].append(car)

    # Soonest-free-first: cars about to become available float to the top,
    # cars with no known duration (not in the reference table) sort last.
    _no_eta = 10 ** 9
    for key in ("YUK_ORTYAPTI", "YOLDA"):
        board[key].sort(key=lambda c: c.get("eta_minutes") if c.get("eta_minutes") is not None else _no_eta)

    # How long has each free car actually been free? Pulled from their
    # last finished (YAKUNLANDI) order's completed_at.
    now = datetime.now(TZ)
    free_tids = [c["telegram_id"] for c in board["BOSH"] if c.get("telegram_id")]
    last_finished = get_last_finished_times(free_tids)
    for car in board["BOSH"]:
        finished_at = last_finished.get(car.get("telegram_id"))
        if finished_at:
            car["idle_minutes"] = int((now - finished_at).total_seconds() / 60)
            car["idle_since"] = finished_at.strftime("%H:%M")
        else:
            car["idle_minutes"] = None
            car["idle_since"] = None
    # Longest-idle-first — the car that's been sitting free the longest is
    # probably the one dispatchers want to use next, so surface it first.
    board["BOSH"].sort(key=lambda c: c.get("idle_minutes") if c.get("idle_minutes") is not None else -1, reverse=True)

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
            "problem_orders": problem_orders,
            "delivered_today": sb["delivered_today"],
            "last_updated": datetime.now(TZ).strftime("%H:%M:%S"),
            "stats": {
                "finished_today": sb["finished_today"],
                "active": busy,
                "failed": len(problem_orders),
            }
        }
    )

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/debug")
def debug():
    cars = sheets_read_cars()
    problems = sheets_read_problem_orders()
    return {
        "total_cars": len(cars),
        "cars": cars,
        "live_stages": get_driver_live_stages(),
        "problem_orders_count": len(problems),
        "problem_orders": problems,
        "orders_sheets_scanned": ORDERS_SHEETS,
        "sheet_id": GOOGLE_SHEET_ID[:8] + "..." if GOOGLE_SHEET_ID else "MISSING",
        "creds": "SET" if _sa_info.get("client_email") else "MISSING",
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
