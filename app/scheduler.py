import logging
from aiogram import Bot
from app.sheets import get_new_orders, get_drivers, update_order_status
from app.db import create_order
import app.keyboards as kb

logger = logging.getLogger(__name__)

async def check_sheets_job(bot: Bot):
    logger.info("Checking Google Sheets for new orders...")
    try:
        new_orders = get_new_orders()
        if not new_orders:
            return
            
        drivers = get_drivers()
        
        for order in new_orders:
            car_number = order['car_number']
            if car_number not in drivers:
                logger.warning(f"Driver not found for car {car_number}. Updating status to ERROR_DRIVER_NOT_FOUND.")
                update_order_status(order['row_index'], 'ERROR_DRIVER_NOT_FOUND')
                continue
                
            driver_info = drivers[car_number]
            telegram_id = driver_info['telegram_id']
            driver_name = driver_info['driver_name']
            
            db_order_data = {
                'order_id': order['order_id'],
                'car_number': car_number,
                'driver_name': driver_name,
                'driver_telegram_id': telegram_id,
                'address': order['address'],
                'cargo': order['cargo'],
                'comment': order['comment'],
                'current_status': 'SENT'
            }
            
            created = create_order(db_order_data)
            if created:
                text = f"🚚 Yangi yetkazib berish #{order['order_id']}\n"
                text += f"Mashina: {car_number}\n"
                text += f"Manzil: {order['address']}\n"
                text += f"Yuk: {order['cargo']}\n"
                if order.get('comment'):
                    text += f"Izoh: {order['comment']}\n"
                    
                try:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=text,
                        reply_markup=kb.get_take_delivery_kb(order['order_id'])
                    )
                    update_order_status(order['row_index'], 'SENT')
                    logger.info(f"Order {order['order_id']} sent to driver {driver_name}.")
                except Exception as e:
                    logger.error(f"Failed to send message to driver {driver_name} (ID: {telegram_id}): {e}")
                    update_order_status(order['row_index'], 'ERROR_BOT_BLOCKED')
            else:
                logger.info(f"Order {order['order_id']} already exists in db or failed to create.")
                
    except Exception as e:
        logger.error(f"Error in check_sheets_job: {e}")
