import re
import math
import pytz
from datetime import datetime
from core.config import TIMEZONE, SHOP_LAT, SHOP_LNG

tz = pytz.timezone(TIMEZONE)

# Straight-line (haversine) distance underestimates real road distance, and
# a delivery truck doesn't drive at highway speed through city traffic — these
# two constants convert "as the crow flies" km into a realistic ETA. Tune them
# if the estimate consistently runs too fast/slow versus what actually happens.
ROAD_DISTANCE_FACTOR = 1.35   # road km ≈ straight-line km * this
AVG_SPEED_KMH = 28            # assumed average city driving speed


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance between two lat/lng points, in kilometers."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def estimate_minutes_to_shop(lat, lng):
    """
    Approximate minutes to drive from (lat, lng) back to the Dunyabunya shop,
    based on straight-line distance adjusted for real road travel. Returns
    None if lat/lng aren't available.
    """
    if lat is None or lng is None:
        return None
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    km = haversine_km(lat, lng, SHOP_LAT, SHOP_LNG) * ROAD_DISTANCE_FACTOR
    hours = km / AVG_SPEED_KMH
    return max(1, round(hours * 60))

def normalize_car_number(value):
    """
    Normalize a car plate number for reliable matching between the
    orders sheet and the drivers sheet.

    Handles inconsistent spacing/dashes that operators type by hand,
    e.g. "01 655 OLA", "01655OLA", "01-655-OLA" and "01 655OLA" must
    all resolve to the same key so a driver lookup never fails just
    because of whitespace formatting differences.
    """
    if not value:
        return ""
    # Uppercase first (handles Cyrillic/Latin casing too), then strip
    # every whitespace and dash character so only the meaningful
    # alphanumeric plate characters remain.
    cleaned = str(value).strip().upper()
    cleaned = re.sub(r"[\s\-]+", "", cleaned)
    return cleaned

def get_now():
    return datetime.now(tz)

def get_current_time():
    """Returns datetime object for Asia/Tashkent."""
    return get_now()

def parse_dt(dt_str):
    if not dt_str: return None
    try:
        # Handle various ISO formats, replacing Z with +00:00 for fromisoformat
        clean_str = dt_str.replace('Z', '+00:00')
        # Some DBs return space instead of T
        if ' ' in clean_str and 'T' not in clean_str:
            clean_str = clean_str.replace(' ', 'T')

        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            # Every write in this codebase sends an already-Tashkent-local,
            # offset-aware timestamp (get_now().isoformat()). If it comes
            # back with no offset at all (naive), that raw number IS the
            # Tashkent wall-clock time already — NOT UTC. Assuming UTC here
            # and converting would incorrectly add another 5 hours on top
            # (this was the bug behind finish/start times shown 5h in the
            # future on both the bot and the dashboard).
            dt = tz.localize(dt)
            return dt
        return dt.astimezone(tz)
    except Exception:
        return None

def format_time(dt):
    if not dt: return "Noma'lum"
    if isinstance(dt, str): return dt
    return dt.strftime("%H:%M")

def format_duration_detailed(seconds: int):
    if seconds is None or seconds < 0: return "—"
    m = seconds // 60
    s = seconds % 60
    if m > 0:
        return f"{m} daqiqa {s} soniya"
    return f"{s} soniya"

def get_seconds_diff(start_iso, end_iso):
    """Return difference in seconds between two ISO datetime strings. Returns None on error."""
    if not start_iso or not end_iso: return None
    try:
        s = parse_dt(start_iso)
        e = parse_dt(end_iso)
        if s and e:
            return int((e - s).total_seconds())
        return None
    except Exception:
        return None


def format_duration(minutes: int):
    """Old simplified version for backward compatibility if needed."""
    if minutes is None: return ""
    h = minutes // 60
    m = minutes % 60
    if h > 0:
        return f"{h} soat {m} daqiqa"
    return f"{m} daqiqa"

def get_order_start_time(order, steps):
    """Get start time from order field or fallback to steps."""
    st = parse_dt(order.get('start_time'))
    if st: return st
    
    # Fallback to take_delivery step
    for s in steps:
        if s['step_name'] == 'take_delivery':
            if s.get('created_at'):
                return parse_dt(s['created_at'])
    return None
