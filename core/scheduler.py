import logging
import asyncio
from aiogram import Bot
from core.sheets import get_new_orders, update_order_status, get_drivers, update_driver_status_sheet
from core.db import create_order, get_order
import core.keyboards as kb

logger = logging.getLogger(__name__)

PROCESSED_ORDERS = {}

async def check_sheets_job(bot: Bot):
    try:
        new_orders = await asyncio.to_thread(get_new_orders)
        if not new_orders: return
        
        drivers = await asyncio.to_thread(get_drivers)
        
        for order in new_orders:
            order_id = order['order_id']
            if order_id in PROCESSED_ORDERS: continue
                
            try:
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
                
                car_number = order['car_number'].strip().upper()
                driver = drivers.get(car_number)
                
                if not driver:
                    available_cars = list(drivers.keys())
                    logger.error(f"❌ Driver not found for car '{car_number}' (Order #{order_id}). "
                                 f"Available cars in sheet: {available_cars}")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_NOT_FOUND')
                    continue
                
                telegram_id = driver['telegram_id']
                if not telegram_id:
                    logger.error(f"❌ Driver '{driver['driver_name']}' has no Telegram ID in sheet.")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_NO_TELEGRAM_ID')
                    continue

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
                
                await asyncio.to_thread(update_order_status, order['row_index'], 'SENT')
                await asyncio.to_thread(update_driver_status_sheet, car_number, 'BUSY', order_id)
                
                PROCESSED_ORDERS[order_id] = True
                
                from core.handlers.delivery import update_group_report
                await update_group_report(bot, order_id)
                
            except Exception as e:
                logger.error(f"Error processing order {order_id}: {e}")
                
    except Exception as e:
        logger.error(f"Error in check_sheets_job: {e}")
