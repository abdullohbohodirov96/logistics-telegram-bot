import logging
import asyncio
from aiogram import Bot
from core.sheets import get_new_orders, update_order_status, get_drivers
from core.db import create_order, get_order
import core.keyboards as kb

logger = logging.getLogger(__name__)

# Memory cache to prevent duplicate processing within a single session
# Format: {order_id: timestamp}
PROCESSED_ORDERS = {}

async def check_sheets_job(bot: Bot):
    """
    Background job to check Google Sheets for new orders.
    """
    try:
        new_orders = await asyncio.to_thread(get_new_orders)
        if not new_orders: return
        
        drivers = await asyncio.to_thread(get_drivers)
        
        for order in new_orders:
            order_id = order['order_id']
            
            # Check memory cache to prevent loop if Sheets update is slow
            if order_id in PROCESSED_ORDERS:
                continue
                
            try:
                # 1. Create in Supabase (if doesn't exist)
                db_order = await asyncio.to_thread(get_order, order_id)
                if not db_order:
                    await asyncio.to_thread(create_order, {
                        'order_id': order_id,
                        'car_number': order['car_number'],
                        'address': order['address'],
                        'cargo': order['cargo'],
                        'comment': order['comment'],
                        'current_status': 'NEW'
                    })
                
                car_number = order['car_number']
                driver = drivers.get(car_number)
                
                if not driver:
                    logger.warning(f"Driver not found for car {car_number}. Updating status to ERROR_DRIVER_NOT_FOUND.")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_NOT_FOUND')
                    continue
                
                driver_name = driver['driver_name']
                telegram_id = driver['telegram_id']
                
                # 2. Send to Driver
                msg_text = (
                    f"🆕 **YANGI BUYURTMA!**\n\n"
                    f"🆔 **ID:** {order_id}\n"
                    f"📍 **Manzil:** {order['address']}\n"
                    f"📦 **Yuk:** {order['cargo']}\n"
                    f"📝 **Izoh:** {order['comment']}\n"
                )
                
                await bot.send_message(
                    chat_id=telegram_id,
                    text=msg_text,
                    parse_mode="Markdown",
                    reply_markup=kb.get_take_delivery_kb(order_id)
                )
                
                # 3. Update Sheets status to 'SENT'
                await asyncio.to_thread(update_order_status, order['row_index'], 'SENT')
                logger.info(f"Order {order_id} sent to driver {driver_name} for car {car_number}")
                
                # Mark as processed in memory
                PROCESSED_ORDERS[order_id] = True
                
                # 4. Immediate Group Report
                from core.handlers.delivery import update_group_report
                await update_group_report(bot, order_id)
                
            except Exception as e:
                logger.error(f"Error processing order {order_id}: {e}")
                # We don't update Sheets to ERROR here to allow retry on next poll if it was a temp issue
                
    except Exception as e:
        logger.error(f"Error in check_sheets_job: {e}")
