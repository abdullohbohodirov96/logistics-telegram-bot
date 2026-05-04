import pytz
from datetime import datetime
from core.config import TIMEZONE

tz = pytz.timezone(TIMEZONE)

def get_now():
    return datetime.now(tz)

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
            # If naive, assume it's UTC (common for DB timestamps)
            dt = pytz.utc.localize(dt)
        return dt.astimezone(tz)
    except Exception:
        return None

def format_time(dt):
    if not dt: return "Noma'lum"
    return dt.strftime("%H:%M")

def format_duration(minutes: int):
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
