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
        logger.info("🔍 Checking Google Sheets for new orders...")
        new_orders = await asyncio.to_thread(get_new_orders)
        
        if not new_orders:
            logger.info("✅ No new orders (empty status rows) found in Sheets.")
            return
        
        logger.info(f"📝 Found {len(new_orders)} new order rows to process.")
        drivers = await asyncio.to_thread(get_drivers)
        
        for order in new_orders:
            order_id = order['order_id']
            if order_id in PROCESSED_ORDERS:
                logger.info(f"⏭ Skipping Order #{order_id} (already processed in this session).")
                continue
                
            try:
                logger.info(f"⚙️ Processing Order #{order_id} for car {order['car_number']}...")
                
                # Ensure it's in DB
                db_order = await asyncio.to_thread(get_order, order_id)
                if not db_order:
                    logger.info(f"📥 Creating Order #{order_id} in Supabase...")
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
                
                if driver and driver.get('status', '').strip().upper() == 'YUK OGAN':
                    logger.warning(f"⚠️ Driver '{car_number}' is already 'YUK OGAN'. Skipping Order #{order_id}.")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_BUSY')
                    continue
                
                if not driver:
                    available_cars = list(drivers.keys())
                    logger.error(f"❌ Driver not found for car '{car_number}' (Order #{order_id}). "
                                 f"Available in sheet: {available_cars}")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_NOT_FOUND')
                    continue
                
                telegram_id = driver['telegram_id']
                if not telegram_id:
                    logger.error(f"❌ Driver '{driver['driver_name']}' has no Telegram ID in sheet.")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_NO_TELEGRAM_ID')
                    continue

                # Send to Driver
                msg_text = (
                    f"🆕 **YANGI BUYURTMA!**\n\n"
                    f"🆔 **ID:** {order_id}\n"
                    f"📍 **Manzil:** {order['address']}\n"
                    f"📦 **Yuk:** {order['cargo']}\n"
                    f"📝 **Izoh:** {order['comment']}\n"
                )
                
                try:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=msg_text,
                        parse_mode="Markdown",
                        reply_markup=kb.get_take_delivery_kb(order_id)
                    )
                    logger.info(f"✉️ Order #{order_id} sent to Driver {driver['driver_name']} (TID: {telegram_id}).")
                except Exception as tg_err:
                    logger.error(f"❌ Failed to send Telegram message to {telegram_id}: {tg_err}")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_TG_SEND_FAILED')
                    continue
                
                # Update status
                await asyncio.to_thread(update_order_status, order['row_index'], 'SENT')
                await asyncio.to_thread(update_driver_status_sheet, car_number, 'YUK OGAN', order_id)
                
                PROCESSED_ORDERS[order_id] = True
                
                from core.handlers.delivery import update_group_report
                await update_group_report(bot, order_id)
                
            except Exception as e:
                logger.error(f"❌ Error processing order {order_id}: {e}")
                
    except Exception as e:
        logger.error(f"❌ Critical error in check_sheets_job: {e}")

async def send_daily_report_job(bot: Bot):
    try:
        from datetime import datetime, timedelta
        import pytz
        from core.config import GROUP_CHAT_ID, TIMEZONE
        from core.db import get_orders_by_date_range
        from core.utils import get_seconds_diff
        
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        
        # We report on the "full yesterday" (00:00 to 23:59:59)
        yesterday = now - timedelta(days=1)
        start_dt = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
        
        orders = await asyncio.to_thread(get_orders_by_date_range, start_dt.isoformat(), end_dt.isoformat())
        if not orders:
            logger.info("No orders found for yesterday's daily report.")
            return
            
        stats = {} # {telegram_id: {name, car, count, total_seconds}}
        for o in orders:
            tid = o.get('driver_telegram_id')
            if not tid: continue
            
            if tid not in stats:
                stats[tid] = {
                    'name': o.get('driver_name', '-'),
                    'car': o.get('car_number', '-'),
                    'count': 0,
                    'total_seconds': 0
                }
            
            stats[tid]['count'] += 1
            diff = get_seconds_diff(o.get('accepted_at'), o.get('completed_at'))
            if diff:
                stats[tid]['total_seconds'] += diff
        
        # Sort for ranking
        ranking = sorted(stats.items(), key=lambda x: x[1]['count'], reverse=True)
        
        # Build Group Message
        msg_group = f"📊 **KUNLIK HISOBOT ({yesterday.strftime('%d.%m.%Y')})**\n\n"
        msg_group += "🏆 **REYTING:**\n"
        
        emojis = ["🥇", "🥈", "🥉"]
        for i, (tid, s) in enumerate(ranking):
            medal = emojis[i] if i < 3 else f"{i+1}."
            avg_min = (s['total_seconds'] / s['count'] / 60) if s['count'] > 0 else 0
            msg_group += f"{medal} **{s['name']}** ({s['car']}): {s['count']} reys | Ø {int(avg_min)} min\n"
            
            # Build Private Message
            msg_private = (
                f"📊 **KUNLIK HISOBOTINGIZ ({yesterday.strftime('%d.%m.%Y')})**\n\n"
                f"✅ **Reyslar soni:** {s['count']}\n"
                f"⏱ **O'rtacha vaqt:** {int(avg_min)} minut\n"
            )
            if i < 3:
                msg_private += f"\nTabriklaymiz! Siz bugungi reytingda {i+1}-orinni egalladingiz! 🎊"
                
            try:
                await bot.send_message(chat_id=tid, text=msg_private, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send private report to {tid}: {e}")
        
        if GROUP_CHAT_ID:
            try:
                await bot.send_message(chat_id=GROUP_CHAT_ID, text=msg_group, parse_mode="Markdown")
                logger.info("Daily report sent to group.")
            except Exception as e:
                logger.error(f"Failed to send daily report to group: {e}")
                
    except Exception as e:
        logger.error(f"Error in send_daily_report_job: {e}")
