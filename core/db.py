import logging
import time
from supabase import create_client, Client
from core.config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

supabase: Client | None = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
else:
    logger.warning("Supabase URL or KEY not provided. DB operations will fail.")

def create_order(order_data: dict) -> bool:
    try:
        if not supabase: return False
        response = supabase.table('orders').select('id').eq('order_id', order_data['order_id']).execute()
        if response.data:
            logger.info(f"Order {order_data['order_id']} already exists in Supabase.")
            return False

        supabase.table('orders').insert(order_data).execute()
        logger.info(f"Order {order_data['order_id']} created in Supabase.")
        return True
    except Exception as e:
        logger.error(f"Error creating order in Supabase: {e}")
        return False

def get_order(order_id: str):
    try:
        if not supabase: return None
        response = supabase.table('orders').select('*').eq('order_id', order_id).execute()
        if response.data:
            return response.data[0]
        return None
    except Exception as e:
        logger.error(f"Error getting order: {e}")
        return None

def update_order(order_id: str, data: dict):
    try:
        if not supabase: return
        supabase.table('orders').update(data).eq('order_id', order_id).execute()
    except Exception as e:
        logger.error(f"Error updating order {order_id}: {e}")

def save_order_step(step_data: dict):
    try:
        if not supabase: return
        supabase.table('order_steps').insert(step_data).execute()
        logger.info(f"Step saved for order {step_data.get('order_id')}: {step_data.get('step_name')}")
    except Exception as e:
        logger.error(f"Error saving order step: {e}")

def get_order_steps(order_id: str):
    try:
        if not supabase: return []
        response = supabase.table('order_steps').select('*').eq('order_id', order_id).order('created_at').execute()
        return response.data
    except Exception as e:
        logger.error(f"Error getting order steps: {e}")
        return []

def get_unique_cars():
    try:
        if not supabase: return []
        # Only select needed column
        response = supabase.table('orders').select('car_number').execute()
        cars = set(row['car_number'] for row in response.data if row.get('car_number'))
        return list(cars)
    except Exception as e:
        logger.error(f"Error getting unique cars: {e}")
        return []

def get_unique_drivers():
    try:
        if not supabase: return []
        # Only select needed columns
        response = supabase.table('orders').select('driver_telegram_id, driver_name').execute()
        drivers = {}
        for row in response.data:
            tid = row.get('driver_telegram_id')
            name = row.get('driver_name')
            if tid and name:
                drivers[tid] = name
        return drivers
    except Exception as e:
        logger.error(f"Error getting unique drivers: {e}")
        return {}

def get_active_orders():
    try:
        if not supabase: return []
        response = supabase.table('orders').select('*').neq('current_status', 'DONE').order('created_at', desc=True).limit(50).execute()
        return response.data
    except Exception as e:
        logger.error(f"Error getting active orders: {e}")
        return []

def get_history(filter_type: str, filter_val: str, date_from: str = None, date_to: str = None, limit: int = 50):
    t0 = time.time()
    try:
        if not supabase: return []
        
        query = supabase.table('orders').select('*')
        
        if filter_type == 'drv':
            # Strictly filter by telegram_id (filter_val should be string tid)
            query = query.eq('driver_telegram_id', filter_val)
        elif filter_type == 'car':
            # Strictly filter by car_number
            query = query.eq('car_number', filter_val)
            
        if date_from:
            query = query.gte('created_at', date_from)
        if date_to:
            query = query.lte('created_at', date_to)
            
        query = query.order('created_at', desc=True).limit(limit)
        response = query.execute()
        
        elapsed = time.time() - t0
        logger.info(f"get_history({filter_type}, {filter_val}): {len(response.data)} rows in {elapsed:.2f}s")
        return response.data
    except Exception as e:
        logger.error(f"Error getting history: {e}")
        return []


def get_dashboard_stats():
    try:
        if not supabase: return {"active": 0, "finished_today": 0, "failed": 0, "updates": []}
        import datetime
        import pytz
        tz = pytz.timezone('Asia/Tashkent')
        now = datetime.datetime.now(tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        # Active orders (not DONE, not ERROR)
        active_resp = supabase.table('orders').select('id', count='exact').neq('current_status', 'DONE').neq('current_status', 'ERROR_BOT_BLOCKED').execute()
        active_count = active_resp.count if active_resp.count else 0
        
        # Finished today
        finished_resp = supabase.table('orders').select('id', count='exact').eq('current_status', 'DONE').gte('completed_at', today_start).execute()
        finished_count = finished_resp.count if finished_resp.count else 0
        
        failed_resp = supabase.table('orders').select('id', count='exact').eq('current_status', 'ERROR_BOT_BLOCKED').execute()
        failed_count = failed_resp.count if failed_resp.count else 0
        
        recent_resp = supabase.table('orders').select('order_id, car_number, current_status, driver_name').order('created_at', desc=True).limit(5).execute()
        recent_updates = recent_resp.data if recent_resp.data else []
        
        return {
            "active": active_count,
            "finished_today": finished_count,
            "failed": failed_count,
            "updates": recent_updates
        }
    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")
        return {"active": 0, "finished_today": 0, "failed": 0, "updates": []}
