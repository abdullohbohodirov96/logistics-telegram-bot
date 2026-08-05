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

def archive_duplicate_order_id(order_pk_id: str, old_order_id: str):
    """
    Free up an order_id that a finished/cancelled order is still holding,
    so a brand-new shipment can reuse the same ID (the sheet's order IDs
    can recycle over time). order_id has a UNIQUE constraint in Supabase,
    so we can't just insert a second row with the same ID — instead we
    rename the OLD row's order_id (matched precisely by its primary key,
    not by order_id, since order_id is exactly what we're changing) to a
    timestamped archive value, keeping its full history intact under a
    new name while freeing the original ID for reuse.

    Returns the new archived id string, or None on failure.
    """
    try:
        if not supabase or not order_pk_id:
            return None
        archived_id = f"{old_order_id}~ARCH{int(time.time())}"
        supabase.table('orders').update({'order_id': archived_id}).eq('id', order_pk_id).execute()
        logger.info(f"Archived reused order_id '{old_order_id}' (pk={order_pk_id}) -> '{archived_id}'")
        return archived_id
    except Exception as e:
        logger.error(f"Error archiving duplicate order_id '{old_order_id}': {e}")
        return None

def update_order(order_id: str, data: dict):
    try:
        if not supabase or not data: return
        existing = get_order(order_id)
        if not existing:
            logger.warning(f"Order {order_id} not found for update")
            return
        valid_data = {}
        for key, value in data.items():
            if key in existing:
                valid_data[key] = value
            else:
                logger.warning(f"Skipping update field '{key}' for order {order_id}: column does not exist")
        if not valid_data:
            return
        supabase.table('orders').update(valid_data).eq('order_id', order_id).execute()
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
        response = (supabase.table('orders').select('*')
            .neq('current_status', 'YAKUNLANDI')
            .neq('current_status', 'BEKOR_QILINDI')
            .neq('current_status', 'ESKI_YOPILDI')
            .order('created_at', desc=True).limit(50).execute())
        return response.data
    except Exception as e:
        logger.error(f"Error getting active orders: {e}")
        return []

def get_active_orders_by_driver(driver_tid):
    """Returns all non-finished orders for a specific driver (for multi-order panel)."""
    try:
        if not supabase: return []
        response = (
            supabase.table('orders')
            .select('*')
            .eq('driver_telegram_id', str(driver_tid))
            .neq('current_status', 'YAKUNLANDI')
            .neq('current_status', 'BEKOR_QILINDI')
            .neq('current_status', 'ESKI_YOPILDI')
            .order('created_at', desc=True)
            .limit(10)
            .execute()
        )
        return response.data
    except Exception as e:
        logger.error(f"Error getting active orders by driver {driver_tid}: {e}")
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
        
        # Active orders (not YAKUNLANDI, not ERROR)
        active_resp = supabase.table('orders').select('id', count='exact').neq('current_status', 'YAKUNLANDI').neq('current_status', 'ERROR_BOT_BLOCKED').neq('current_status', 'ESKI_YOPILDI').execute()
        active_count = active_resp.count if active_resp.count else 0
        
        # Finished today
        finished_resp = supabase.table('orders').select('id', count='exact').eq('current_status', 'YAKUNLANDI').gte('completed_at', today_start).execute()
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

def get_orders_by_date_range(start_iso: str, end_iso: str):
    try:
        if not supabase: return []
        response = supabase.table('orders').select('*').gte('completed_at', start_iso).lte('completed_at', end_iso).eq('current_status', 'YAKUNLANDI').execute()
        return response.data
    except Exception as e:
        logger.error(f"Error getting orders by date range: {e}")
        return []

def get_active_orders_count(driver_tid: int) -> int:
    try:
        if not supabase: return 0
        response = (supabase.table('orders').select('id', count='exact')
            .eq('driver_telegram_id', driver_tid)
            .neq('current_status', 'YAKUNLANDI')
            .neq('current_status', 'BEKOR_QILINDI')
            .neq('current_status', 'ESKI_YOPILDI')
            .execute())
        return response.count if response.count is not None else 0
    except Exception as e:
        logger.error(f"Error getting active orders count: {e}")
        return 0

def get_orders_by_status(status: str) -> list:
    """Returns all orders with a specific current_status."""
    try:
        if not supabase: return []
        response = supabase.table('orders').select('*').eq('current_status', status).execute()
        return response.data
    except Exception as e:
        logger.error(f"Error get_orders_by_status({status}): {e}")
        return []

def get_driver_sent_orders_count(driver_tid) -> int:
    """Returns count of SENT (dispatched but not accepted) orders for a driver."""
    try:
        if not supabase: return 0
        response = (supabase.table('orders').select('id', count='exact')
            .eq('driver_telegram_id', str(driver_tid))
            .eq('current_status', 'SENT')
            .execute())
        return response.count if response.count is not None else 0
    except Exception as e:
        logger.error(f"Error get_driver_sent_orders_count: {e}")
        return 0

def bulk_close_active_orders() -> int:
    """
    Full-reset action: mark EVERY order that isn't already YAKUNLANDI/
    BEKOR_QILINDI as ESKI_YOPILDI (a distinct 'closed during backlog reset'
    status, so these are never confused with orders a driver actually
    finished or a dispatcher actually cancelled).

    Used when the sheet backlog has grown so large the scheduler is
    struggling to keep up — wipes the slate clean so processing can
    start fresh. Returns the number of rows updated.
    """
    try:
        if not supabase:
            return 0
        resp = (
            supabase.table('orders')
            .update({'current_status': 'ESKI_YOPILDI'})
            .neq('current_status', 'YAKUNLANDI')
            .neq('current_status', 'BEKOR_QILINDI')
            .neq('current_status', 'ESKI_YOPILDI')
            .execute()
        )
        count = len(resp.data or [])
        logger.info(f"bulk_close_active_orders: {count} orders -> ESKI_YOPILDI in Supabase")
        return count
    except Exception as e:
        logger.error(f"Error bulk_close_active_orders: {e}")
        return 0


def cancel_order_in_db(order_id: str) -> bool:
    """Soft-cancel an order: set status to BEKOR_QILINDI."""
    try:
        if not supabase: return False
        supabase.table('orders').update({'current_status': 'BEKOR_QILINDI'}).eq('order_id', order_id).execute()
        logger.info(f"Order {order_id} → BEKOR_QILINDI in DB")
        return True
    except Exception as e:
        logger.error(f"Error cancelling order {order_id}: {e}")
        return False

def get_today_completed_count(driver_tid: int) -> int:
    try:
        if not supabase: return 0
        import datetime
        import pytz
        tz = pytz.timezone('Asia/Tashkent')
        today_start = datetime.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        response = supabase.table('orders').select('id', count='exact').eq('driver_telegram_id', driver_tid).eq('current_status', 'YAKUNLANDI').gte('completed_at', today_start).execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logger.error(f"Error today completed count: {e}")
        return 0

def get_total_completed_count(driver_tid: int) -> int:
    try:
        if not supabase: return 0
        response = supabase.table('orders').select('id', count='exact').eq('driver_telegram_id', driver_tid).eq('current_status', 'YAKUNLANDI').execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logger.error(f"Error total completed count: {e}")
        return 0

def get_drivers_admin_stats():
    """Returns a list of stats for all drivers who have had orders."""
    try:
        if not supabase: return []
        # Get unique drivers from orders
        resp = supabase.table('orders').select('car_number, driver_name, driver_telegram_id').execute()
        if not resp.data: return []
        
        drivers = {}
        for r in resp.data:
            tid = r.get('driver_telegram_id')
            if tid:
                drivers[tid] = {'car': r.get('car_number', '-'), 'name': r.get('driver_name', '-')}
        
        # Get counts for today's completed orders
        import datetime
        import pytz
        tz = pytz.timezone('Asia/Tashkent')
        today_start = datetime.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        stats = []
        for tid, info in drivers.items():
            # Active count (not finished, not cancelled)
            active_res = (supabase.table('orders').select('id', count='exact')
                .eq('driver_telegram_id', tid)
                .neq('current_status', 'YAKUNLANDI')
                .neq('current_status', 'BEKOR_QILINDI')
                .neq('current_status', 'ESKI_YOPILDI')
                .execute())
            active = active_res.count if active_res.count is not None else 0
            
            # Today completed
            today_res = supabase.table('orders').select('id', count='exact').eq('driver_telegram_id', tid).eq('current_status', 'YAKUNLANDI').gte('completed_at', today_start).execute()
            today = today_res.count if today_res.count is not None else 0
            
            # Total completed
            total_res = supabase.table('orders').select('id', count='exact').eq('driver_telegram_id', tid).eq('current_status', 'YAKUNLANDI').execute()
            total = total_res.count if total_res.count is not None else 0
            
            stats.append({
                'car_number': info['car'],
                'driver_name': info['name'],
                'telegram_id': tid,
                'active_count': active,
                'today_count': today,
                'total_count': total
            })
        return stats
    except Exception as e:
        logger.error(f"Error getting admin stats: {e}")
        return []
