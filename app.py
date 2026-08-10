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
import math
import logging
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import httpx
import uvicorn
from dotenv import load_dotenv
from core.vehicle_durations import get_expected_duration, get_vehicle_type

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dashboard")

# ── Config ──────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GOOGLE_SHEET_ID = os.getenv("SPREADSHEET_ID") or os.getenv("GOOGLE_SHEET_ID", "")
DRIVERS_SHEET_NAME = os.getenv("DRIVERS_SHEET_NAME", "drivers")
ORDERS_SHEET_NAME = os.getenv("ORDERS_SHEET_NAME", "orders")
# Qorasaroy filiali olib tashlandi — endi bitta orders sheet bor. List holida
# qoldirilgan (kelajakda yana filial qo'shilsa, shunchaki shu ro'yxatga
# qo'shiladi) — sheets_read_problem_orders() shu ustidan generik ishlaydi.
ORDERS_SHEETS = list(dict.fromkeys([ORDERS_SHEET_NAME]))
TZ = timezone(timedelta(hours=5))  # Asia/Tashkent

# "Dunyabunya" shop/warehouse fixed location — every driver returns here
# after unloading. Resolved from the shop's Yandex Maps pin
# (https://yandex.uz/maps/-/CTS2RU58 -> Toshkent tumani, Qorasaroy mahalla,
# Shohsada ko'chasi). Kept as a standalone constant here (not imported from
# core/) since this dashboard service is intentionally self-contained.
SHOP_LAT = 41.403393
SHOP_LNG = 69.231954

# How long (minutes) after a delivery finishes we keep showing the car in
# the "Do'konga qaytyapti" column before it just falls back to "Bo'sh". This
# is a ceiling in case the ETA estimate undershoots reality.
RETURN_TO_SHOP_MAX_MINUTES = 90

# A car sitting idle (Bo'sh) for longer than this without getting a new
# order is treated as off-duty for the day (driver finished their shift,
# etc) — dispatcher asked for these to disappear from the board entirely
# instead of cluttering the Bo'sh list with cars nobody's going to dispatch
# today. The moment such a car gets a new order, it naturally reappears
# (it routes into Yuk ortyapti/Yo'lda, which never runs through this
# filter at all).
REST_IDLE_THRESHOLD_MINUTES = 1400

# ── Wialon Local (live GPS) ────────────────────────────────────────────────
# Optional — without these two set, every car simply has no rayon shown
# (board still works fine, same as before this feature existed).
WIALON_URL = (os.getenv("WIALON_URL") or "").rstrip("/")
WIALON_TOKEN = os.getenv("WIALON_TOKEN", "").strip()

# Reused for coordinate -> rayon name, same key as the bot service uses
# (core/geocoding.py) — kept as its own standalone constant/call here since
# this dashboard is intentionally self-contained (doesn't import core/).
YANDEX_GEOCODER_API_KEY = os.getenv("YANDEX_GEOCODER_API_KEY", "").strip()

# Wialon unit names are like "Changan 01 D 974 UB" / "LABO 01 446 OLA" —
# a vehicle-type word followed by the plate. Stripped before matching
# against the drivers sheet's car_number.
_WIALON_TYPE_WORDS = {
    "CHANGAN", "LABO", "DAMAS", "GAZEL", "ISUZU", "GAZ", "BIG",
}


def _normalize_plate(value: str) -> str:
    """Same normalization used everywhere else (core/utils.normalize_car_number)
    — strip whitespace/dashes, uppercase — kept local since this file avoids
    importing core/."""
    if not value:
        return ""
    import re
    cleaned = str(value).strip().upper()
    return re.sub(r"[\s\-]+", "", cleaned)

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


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# Straight-line distance underestimates real road distance, and a delivery
# truck doesn't move at highway speed through city traffic — these two
# constants convert "as the crow flies" km into a realistic ETA. Tune if the
# estimate consistently runs too fast/slow versus what actually happens.
_ROAD_DISTANCE_FACTOR = 1.35
_AVG_SPEED_KMH = 28


def _estimate_minutes_to_shop(lat, lng):
    if lat is None or lng is None:
        return None
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    km = _haversine_km(lat, lng, SHOP_LAT, SHOP_LNG) * _ROAD_DISTANCE_FACTOR
    return max(1, round((km / _AVG_SPEED_KMH) * 60))


# ── Wialon Local (live GPS positions) ──────────────────────────────────────

_WIALON_SID = None
_WIALON_SID_TIME = 0.0


def _wialon_login():
    """
    Logs into Wialon Local with the API token and caches the session id
    (sid) for 20 minutes — every Wialon call needs a sid, and re-logging in
    on every single dashboard refresh would be wasteful and slow. If a
    later call reports the session as invalid, it clears the cached sid so
    the next call re-logs in automatically.
    """
    global _WIALON_SID, _WIALON_SID_TIME
    if _WIALON_SID and time.time() - _WIALON_SID_TIME < 1200:
        return _WIALON_SID
    if not WIALON_URL or not WIALON_TOKEN:
        return None
    try:
        r = httpx.get(
            f"{WIALON_URL}/wialon/ajax.html",
            params={"svc": "token/login", "params": json.dumps({"token": WIALON_TOKEN})},
            timeout=8,
        )
        data = r.json()
        sid = data.get("eid") if isinstance(data, dict) else None
        if not sid:
            logger.warning(f"[wialon] login failed: {data}")
            return None
        _WIALON_SID = sid
        _WIALON_SID_TIME = time.time()
        return sid
    except Exception as e:
        logger.warning(f"[wialon] login error: {e}")
        return None


def get_wialon_positions() -> dict:
    """
    Returns {normalized_car_number: {"lat": float, "lng": float}} — the
    latest known GPS position for every unit in Wialon, matched to our car
    numbers by stripping the vehicle-type word Wialon prefixes unit names
    with (e.g. "LABO 01 446 OLA" -> "01446OLA"). Cars not found in Wialon,
    or if Wialon isn't configured at all, simply aren't in the dict — every
    caller already treats a missing entry as "no rayon available" and
    degrades gracefully. Cached 15 seconds.
    """
    cached = _cached("wialon_positions", ttl=15)
    if cached is not None:
        return cached

    sid = _wialon_login()
    if not sid:
        return {}

    try:
        r = httpx.get(
            f"{WIALON_URL}/wialon/ajax.html",
            params={
                "svc": "core/search_items",
                "sid": sid,
                "params": json.dumps({
                    "spec": {
                        "itemsType": "avl_unit",
                        "propName": "sys_name",
                        "propValueMask": "*",
                        "sortType": "sys_name",
                    },
                    "force": 1,
                    "flags": 1025,  # base info (1) + last position (1024)
                    "from": 0,
                    "to": 0,
                }),
            },
            timeout=10,
        )
        data = r.json()
        if isinstance(data, dict) and data.get("error"):
            # Session likely expired — force a fresh login on the next call.
            global _WIALON_SID
            _WIALON_SID = None
            logger.warning(f"[wialon] search_items error: {data}")
            return {}

        items = data.get("items") if isinstance(data, dict) else None
        result = {}
        for item in (items or []):
            name = item.get("nm") or ""
            pos = item.get("pos")
            if not pos or pos.get("y") is None or pos.get("x") is None:
                continue
            words = name.strip().split()
            while words and words[0].upper() in _WIALON_TYPE_WORDS:
                words.pop(0)
            plate = _normalize_plate(" ".join(words))
            if not plate:
                continue
            result[plate] = {"lat": pos["y"], "lng": pos["x"]}

        _set("wialon_positions", result)
        return result
    except Exception as e:
        logger.warning(f"[wialon] search_items failed: {e}")
        return {}


_RAYON_CACHE = {}  # "lat_grid,lng_grid" -> (rayon_name_or_None, cached_at)

# Yandex's address data for Uzbekistan doesn't reliably tag a "district"
# component for every point inside Tashkent city — central streets like
# Amir Temur or Bobur often come back with only the bare city name
# ("Toshkent"), no tuman. As a fallback for exactly that case, this is an
# approximate center point for each of Tashkent city's 12 administrative
# districts (tumans) — when Yandex gives us a street but no district, we
# pick whichever of these 12 points is closest and use its name instead of
# showing nothing more specific than "Toshkent".
#
# These are approximate reference points (mostly sourced from Tashkent
# Metro station coordinates that sit inside the named district, per
# Wikipedia), NOT precise administrative boundaries — a car very close to
# a border between two districts could occasionally get attributed to the
# neighboring one. If a specific car is consistently shown in the wrong
# tuman, that's a sign this particular point needs nudging — tell me the
# car and the correct tuman and this table gets adjusted.
_TASHKENT_DISTRICTS = {
    "Yunusobod tumani":     (41.3714, 69.2794),
    "Mirzo Ulugbek tumani": (41.3294, 69.3475),
    "Chilonzor tumani":     (41.2722, 69.2017),
    "Yangihayot tumani":    (41.1833, 69.2167),
    "Bektemir tumani":      (41.2097, 69.3342),
    "Olmazor tumani":       (41.2556, 69.1960),
    "Shayxontohur tumani":  (41.3252, 69.2321),
    "Yashnobod tumani":     (41.2976, 69.3499),
    "Mirobod tumani":       (41.2950, 69.2750),
    "Yakkasaroy tumani":    (41.3050, 69.2653),
    "Sergeli tumani":       (41.2100, 69.2500),
    "Uchtepa tumani":       (41.2850, 69.1750),
}

# Rough bounding box around Tashkent CITY only — the nearest-district
# fallback should never fire for a car out in the region (Toshkent
# viloyati), where "nearest city tuman" would just be wrong.
# Tightened to roughly match the actual spread of the 12 district centers
# above (+ a small buffer) rather than a loose city-wide guess — a loose
# box was accidentally swallowing points that are legitimately OUTSIDE
# the city (like the shop itself, which is genuinely in the regional
# "Toshkent tumani", not one of the 12 city districts) and mislabeling
# them with the nearest city tuman instead of leaving them alone.
_TASHKENT_CITY_BBOX = (41.16, 41.39, 69.15, 69.37)  # (min_lat, max_lat, min_lng, max_lng)


def _nearest_tashkent_district(lat, lng):
    min_lat, max_lat, min_lng, max_lng = _TASHKENT_CITY_BBOX
    if not (min_lat <= lat <= max_lat and min_lng <= lng <= max_lng):
        return None
    best_name, best_dist = None, None
    for name, (dlat, dlng) in _TASHKENT_DISTRICTS.items():
        d = _haversine_km(lat, lng, dlat, dlng)
        if best_dist is None or d < best_dist:
            best_name, best_dist = name, d
    return best_name


def get_rayon(lat, lng):
    """
    Coordinate -> "Rayon, Ko'cha nomi" via Yandex Geocoder — same idea as
    core/geocoding.py on the bot side, duplicated here since this dashboard
    is self-contained (that one intentionally returns just the rayon for a
    short Telegram notification; this one is for the dashboard, where the
    dispatcher explicitly asked for both the district AND the main street,
    not a bare city name like "Toshkent").

    Yandex's address hierarchy has two different tags for "district" —
    "area" (a rural/regional tuman) and "district" (a city sub-district,
    e.g. Yunusobod, Chilonzor) — a given point only ever has one of them,
    so both are checked. If neither is present (sparse map data at that
    exact point), falls back to the street alone, or the bare locality
    (city) only as an absolute last resort, so we're never returning
    something less specific than what's actually available.

    Cached by a coarse ~1.1km grid cell (rounding to 2 decimal places) for
    10 minutes: a car doesn't change rayon every few meters, and the
    dashboard reloads every 8 seconds, so without this a single busy board
    could burn through the Yandex free quota in minutes.
    """
    if not YANDEX_GEOCODER_API_KEY or lat is None or lng is None:
        return None
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None

    grid_key = f"{round(lat, 2)},{round(lng, 2)}"
    cached = _RAYON_CACHE.get(grid_key)
    if cached and time.time() - cached[1] < 600:
        return cached[0]

    rayon = None
    try:
        r = httpx.get(
            "https://geocode-maps.yandex.ru/1.x/",
            params={
                "apikey": YANDEX_GEOCODER_API_KEY,
                "geocode": f"{lng},{lat}",
                "format": "json",
                "lang": "uz_UZ",
                "kind": "house",
                "results": 1,
            },
            timeout=6,
        )
        if r.status_code == 200:
            data = r.json()
            members = (
                data.get("response", {})
                .get("GeoObjectCollection", {})
                .get("featureMember", [])
            )
            if members:
                meta = members[0]["GeoObject"].get("metaDataProperty", {}).get("GeocoderMetaData", {})
                components = meta.get("Address", {}).get("Components", [])
                by_kind = {c.get("kind"): c.get("name") for c in components if c.get("kind") and c.get("name")}

                district = by_kind.get("area") or by_kind.get("district")
                street = by_kind.get("street")
                locality = by_kind.get("locality")

                # Yandex's "Toshkent tumani" is a REGIONAL district (the
                # rural one south/east of the city, where the shop itself
                # happens to sit) — but its Uzbekistan data applies that
                # same label too broadly, even to streets clearly inside
                # Tashkent CITY's own 12 districts (Yunusobod, Chilonzor,
                # Sergeli, etc). So it's treated the same as "no district
                # at all": prefer our own nearest-city-tuman match instead,
                # whenever the point actually falls within city bounds. If
                # it doesn't (a genuinely regional point), the nearest-tuman
                # lookup returns None and Yandex's own answer is kept.
                if not district or district == "Toshkent tumani":
                    fallback = _nearest_tashkent_district(lat, lng)
                    if fallback:
                        district = fallback

                parts = [p for p in (district or locality, street) if p]
                rayon = ", ".join(parts) if parts else None
    except Exception as e:
        logger.warning(f"[rayon] geocode failed for ({lat},{lng}): {e}")

    _RAYON_CACHE[grid_key] = (rayon, time.time())
    return rayon


def get_returning_to_shop() -> dict:
    """
    Returns {telegram_id(str): {"eta_minutes": int, "eta_time": "HH:MM",
    "order_id": str}} for drivers who just finished a delivery and are
    (estimated to be) still on their way back to the shop. eta_time is the
    estimated shop-arrival clock time, not just a countdown.

    We don't have continuous live GPS — only the single point the driver
    shared right before finishing (delivered_lat/delivered_lng). So this
    counts down from that one snapshot: estimate the drive time from that
    point to the shop, then subtract however many minutes have already
    passed since the order finished. Once that hits zero (or
    RETURN_TO_SHOP_MAX_MINUTES elapses as a safety ceiling), the car drops
    out of this list and just shows as plain "Bo'sh". Cached 8 seconds.
    """
    cached = _cached("returning_to_shop", ttl=8)
    if cached is not None:
        return cached

    now = datetime.now(TZ)
    lookback = (now - timedelta(minutes=RETURN_TO_SHOP_MAX_MINUTES)).isoformat()

    rows = _sb_rows("orders", {
        "select": "driver_telegram_id,order_id,completed_at,delivered_lat,delivered_lng",
        "current_status": "eq.YAKUNLANDI",
        "completed_at": f"gte.{lookback}",
        "order": "completed_at.desc",
        "limit": "200",
    })

    result = {}
    for r in rows:
        tid = str(r.get('driver_telegram_id') or '').strip()
        if not tid or tid in result:
            continue  # keep only each driver's most recent finish (rows are already desc)
        completed_at = _parse_iso(r.get('completed_at'))
        eta_total = _estimate_minutes_to_shop(r.get('delivered_lat'), r.get('delivered_lng'))
        if not completed_at or not eta_total:
            continue
        elapsed = int((now - completed_at).total_seconds() / 60)
        if elapsed < 0:
            # completed_at reads as being in the FUTURE relative to now —
            # bad/anomalous data (bad clock, bad write, etc), not a real
            # "just finished" order. Skip it rather than let a negative
            # elapsed blow up the countdown into something absurd like
            # "~19646 daq" (this was a real bug seen in production).
            logger.warning(
                f"[returning_to_shop] order #{r.get('order_id')} has completed_at "
                f"{completed_at.isoformat()} in the future vs now — skipping."
            )
            continue
        remaining = eta_total - elapsed
        if remaining <= 0:
            continue
        # Hard ceiling regardless of the estimate — a car is never actually
        # shown as "returning" for longer than RETURN_TO_SHOP_MAX_MINUTES,
        # even if the distance estimate came out unrealistically large.
        remaining = min(remaining, RETURN_TO_SHOP_MAX_MINUTES)
        arrival_time = completed_at + timedelta(minutes=eta_total)
        result[tid] = {
            "eta_minutes": remaining,
            "eta_time": arrival_time.strftime("%H:%M"),
            "order_id": r.get('order_id') or '',
        }

    _set("returning_to_shop", result)
    return result


def get_last_finished_times(telegram_ids: list) -> dict:
    """
    For the given telegram_ids (drivers currently showing as free), find
    each one's most recent YAKUNLANDI order and work out when they were
    actually free, so the board can show "bo'sh bo'lganiga X daqiqa"
    instead of just a bare "Bo'sh".

    "Actually free" is NOT simply completed_at — a car that just finished
    is still driving back to the shop for a while (see
    get_returning_to_shop / RETURN_TO_SHOP_MAX_MINUTES). So the idle timer
    here starts from completed_at + the estimated drive-back time (i.e. the
    moment it would actually arrive at the shop), not from the moment the
    delivery itself finished. Falls back to plain completed_at when there's
    no delivered_lat/lng to estimate a return trip from.

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
        "select": "driver_telegram_id,completed_at,delivered_lat,delivered_lng",
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
        if not dt:
            continue
        eta_to_shop = _estimate_minutes_to_shop(r.get('delivered_lat'), r.get('delivered_lng'))
        result[tid] = dt + timedelta(minutes=eta_to_shop) if eta_to_shop else dt

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
    returning = get_returning_to_shop()
    problem_orders = sheets_read_problem_orders()

    # Tag every car with its vehicle type (GAZEL / LABO / DAMAS / CHANGAN...)
    # from the reference table, so it's visible on the board and matchable
    # by the search box (e.g. typing "gazel" filters to just Gazels).
    # Also tag with its live rayon (district) from Wialon GPS + Yandex
    # reverse geocoding, when both are configured — degrades to no rayon
    # shown (not an error) if either one isn't set up.
    wialon_positions = get_wialon_positions()
    for car in cars:
        car["vehicle_type"] = get_vehicle_type(car.get("car_number") or "")
        pos = wialon_positions.get(_normalize_plate(car.get("car_number") or ""))
        if pos:
            car["rayon"] = get_rayon(pos["lat"], pos["lng"])
            # Kept alongside the text so the dashboard can link the rayon
            # straight to a live map pin at the car's last known position.
            car["gps_lat"] = pos["lat"]
            car["gps_lng"] = pos["lng"]
        else:
            car["rayon"] = None
            car["gps_lat"] = None
            car["gps_lng"] = None

    # 4-way live board: Bo'sh / Yuk ortyapti / Yo'lda / Do'konga qaytyapti —
    # driven by Supabase's real per-order status (live_stages), not the
    # sheet's coarse 2-state column, so "Yo'lda" is actually accurate. Each
    # car also gets an estimated free-up time/countdown from the
    # vehicle-duration table (loading/en-route) or from the delivered-point
    # -> shop distance (just-finished, heading back).
    board = {"BOSH": [], "YUK_ORTYAPTI": [], "YOLDA": [], "QAYTYAPTI": []}
    for car in cars:
        info = live_stages.get(car["telegram_id"]) if car.get("telegram_id") else None
        if not info:
            ret = returning.get(car["telegram_id"]) if car.get("telegram_id") else None
            if ret:
                car["order_id"]    = ret.get("order_id")
                car["eta_minutes"] = ret.get("eta_minutes")
                car["eta_time"]    = ret.get("eta_time")
                board["QAYTYAPTI"].append(car)
            else:
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
    for key in ("YUK_ORTYAPTI", "YOLDA", "QAYTYAPTI"):
        board[key].sort(key=lambda c: c.get("eta_minutes") if c.get("eta_minutes") is not None else _no_eta)

    # How long has each free car actually been free? Pulled from their
    # last finished (YAKUNLANDI) order's completed_at.
    now = datetime.now(TZ)
    free_tids = [c["telegram_id"] for c in board["BOSH"] if c.get("telegram_id")]
    last_finished = get_last_finished_times(free_tids)
    for car in board["BOSH"]:
        finished_at = last_finished.get(car.get("telegram_id"))
        if finished_at:
            # Clamp at 0 — if the underlying data ever puts finished_at in
            # the future (bad completed_at, clock skew, etc), a car can't
            # actually be idle for negative time, so just show "just freed
            # up" instead of a nonsensical negative countdown.
            car["idle_minutes"] = max(0, int((now - finished_at).total_seconds() / 60))
            car["idle_since"] = finished_at.strftime("%H:%M")
        else:
            car["idle_minutes"] = None
            car["idle_since"] = None
    # Longest-idle-first — the car that's been sitting free the longest is
    # probably the one dispatchers want to use next, so surface it first.
    board["BOSH"].sort(key=lambda c: c.get("idle_minutes") if c.get("idle_minutes") is not None else -1, reverse=True)

    # Cars idle for 1400+ minutes (~23+ hours) are dropped from the board
    # entirely — not shown as Bo'sh, not counted in the totals — since
    # they're effectively off-duty, not really "available right now".
    resting_count = sum(1 for c in board["BOSH"] if (c.get("idle_minutes") or 0) > REST_IDLE_THRESHOLD_MINUTES)
    board["BOSH"] = [
        c for c in board["BOSH"]
        if (c.get("idle_minutes") or 0) <= REST_IDLE_THRESHOLD_MINUTES
    ]

    total = len(cars) - resting_count
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
