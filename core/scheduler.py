import logging
import asyncio
from aiogram import Bot
from core.sheets import get_new_orders, get_drivers, update_order_status, update_driver_status_sheet
from core.db import create_order
import core.keyboards as kb

logger = logging.getLogger(__name__)

is_checking = False

async def check_sheets_job(bot: Bot):
    """
    Polls the ORDERS sheet for status='YANGI' or 'SEND'.
    Sends task to the assigned driver and marks as 'SENT'.
    """
    global is_checking
    if is_checking:
        return
    is_checking = True
    try:
        new_orders = await asyncio.to_thread(get_new_orders)
        if not new_orders:
            return
            
        drivers = await asyncio.to_thread(get_drivers)
        
        for order in new_orders:
            car_number = order['car_number']
            if car_number not in drivers:
                logger.warning(f"Driver not found for car {car_number}. Setting status to ERROR_DRIVER_NOT_FOUND.")
                await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_NOT_FOUND')
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
                text = f"🚚 **Yangi vazifa** #{order['order_id']}\n\n"
                text += f"**Mashina:** {car_number}\n"
                text += f"**Manzil:** {order['address']}\n"
                text += f"**Yuk:** {order['cargo']}\n"
                if order.get('comment'):
                    text += f"**Izoh:** {order['comment']}\n"
                
                text += "\nQabul qilish uchun quyidagi tugmani bosing:"
                    
                try:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=text,
                        parse_mode="Markdown",
                        reply_markup=kb.get_take_delivery_kb(order['order_id'])
                    )
                    # Mark as SENT in Sheets
                    await asyncio.to_thread(update_order_status, order['row_index'], 'SENT')
                    logger.info(f"Order {order['order_id']} sent to driver {driver_name}.")
                except Exception as e:
                    logger.error(f"Failed to send to driver {driver_name} ({telegram_id}): {e}")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_BOT_BLOCKED')
            else:
                # Order already processed/sent
                pass
                
    except Exception as e:
        logger.error(f"Error in check_sheets_job: {e}")
    finally:
        is_checking = False
