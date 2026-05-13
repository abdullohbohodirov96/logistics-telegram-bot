import pytz
from datetime import datetime
from core.config import TIMEZONE

tz = pytz.timezone(TIMEZONE)

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
            # If naive, assume it's UTC (common for DB timestamps)
            dt = pytz.utc.localize(dt)
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

def get_seconds_diff(start, end):
    """
    Calculates difference in seconds between two timestamps.
    Supports datetime objects and ISO strings.
    """
    try:
        if start is None or end is None:
            return 0
            
        dt1 = start if isinstance(start, datetime) else parse_dt(str(start))
        dt2 = end if isinstance(end, datetime) else parse_dt(str(end))
        
        if not dt1 or not dt2:
            return 0
            
        diff = (dt2 - dt1).total_seconds()
        return max(0, int(diff))
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error in get_seconds_diff: {e}")
        return 0
