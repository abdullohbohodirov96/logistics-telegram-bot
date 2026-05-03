import logging
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_KEY

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
