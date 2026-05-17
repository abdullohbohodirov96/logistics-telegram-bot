import logging
import asyncio
from datetime import datetime, timedelta
import pytz

from aiogram import Bot
from core.sheets import get_new_orders, update_order_status, get_drivers, update_driver_status_sheet
from core.db import create_order, get_order
import core.keyboards as kb

logger = logging.getLogger(__name__)

PROCESSED_ORDERS = {}


# ─── 1. Check Google Sheets for new orders ─────────────────────────────────────

async def check_sheets_job(bot: Bot):
    try:
        logger.info("🔍 Checking Google Sheets for new orders...")
        new_orders = await asyncio.to_thread(get_new_orders)

        if not new_orders:
            logger.info("✅ No new orders found in Sheets.")
            return

        logger.info(f"📝 Found {len(new_orders)} new order rows to process.")
        drivers = await asyncio.to_thread(get_drivers)

        for order in new_orders:
            order_id = order['order_id']
            if order_id in PROCESSED_ORDERS:
                logger.info(f"⏭ Skipping Order #{order_id} (already processed).")
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

                if not driver:
                    logger.error(f"❌ Driver not found for car '{car_number}' (Order #{order_id}). "
                                 f"Available: {list(drivers.keys())}")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_NOT_FOUND')
                    continue

                telegram_id = driver['telegram_id']
                if not telegram_id:
                    logger.error(f"❌ Driver '{driver.get('driver_name')}' has no Telegram ID.")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_NO_TELEGRAM_ID')
                    continue

                # Active orders count check (max 3)
                from core.db import get_active_orders_count
                active_count = await asyncio.to_thread(get_active_orders_count, telegram_id)

                if active_count >= 3:
                    logger.warning(f"⚠️ Driver '{car_number}' has {active_count} active orders. Skipping Order #{order_id}.")
                    await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_BUSY_3')
                    continue

                # Build message — add warning note if driver already has active orders
                active_note = ""
                if active_count > 0:
                    active_note = f"\n\n⚠️ *Diqqat: sizda allaqachon {active_count} ta aktiv buyurtma bor!*"

                msg_text = (
                    f"🆕 *YANGI BUYURTMA!*\n\n"
                    f"🆔 *ID:* {order_id}\n"
                    f"📍 *Manzil:* {order['address']}\n"
                    f"📦 *Yuk:* {order['cargo']}\n"
                    f"📝 *Izoh:* {order['comment']}"
                    f"{active_note}"
                )

                try:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=msg_text,
                        parse_mode="Markdown",
                        reply_markup=kb.get_take_delivery_kb(order_id)
                    )
                    logger.info(f"✉️ Order #{order_id} sent to Driver {driver.get('driver_name')} (TID: {telegram_id}).")
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


# ─── 2. 30-minute reminder for stalled drivers ─────────────────────────────────

REMINDER_THRESHOLD_MINUTES = 30

async def send_driver_reminders(bot: Bot):
    """Every 30 minutes: remind drivers who have active orders but haven't moved."""
    try:
        from core.db import get_active_orders
        import pytz
        from core.config import TIMEZONE
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)

        active_orders = await asyncio.to_thread(get_active_orders)
        if not active_orders:
            return

        reminded = set()
        for order in active_orders:
            tid = order.get('driver_telegram_id')
            order_id = order.get('order_id', '-')
            status = order.get('current_status', '')

            # Skip if already reminded this cycle or if status is terminal
            if not tid or tid in reminded:
                continue
            if status in ('YAKUNLANDI', 'NEW'):
                continue

            # Determine last activity time
            last_at = (
                order.get('delivered_location_at') or
                order.get('act_photo_at') or
                order.get('on_way_at') or
                order.get('loaded_photo_at') or
                order.get('accepted_at') or
                order.get('created_at')
            )

            if not last_at:
                continue

            try:
                from core.utils import parse_dt
                last_dt = parse_dt(last_at)
                if not last_dt:
                    continue
                minutes_idle = (now - last_dt).total_seconds() / 60
            except Exception:
                continue

            if minutes_idle < REMINDER_THRESHOLD_MINUTES:
                continue  # Not stalled yet

            # Status label for clarity
            status_labels = {
                "QABUL_QILINDI": "Buyurtma qabul qilingan, bloklar tanlanmagan",
                "TRANSIT":       "Transit kutilmoqda",
                "LOADED_PHOTO":  "Yuk rasmi kutilmoqda",
                "ON_WAY":        "Yo'lga chiqish bosilmagan",
                "YOLDA":         "Yo'lda — akt rasmi kutilmoqda",
                "ACT_PHOTO":     "Akt rasmi kutilmoqda",
                "DELIVERED_LOC": "Lokatsiya kutilmoqda",
                "WAITING_FINISH":"Yakunlash tugmasi bosilmagan",
            }
            step_label = status_labels.get(status, status)
            idle_min = int(minutes_idle)

            try:
                await bot.send_message(
                    chat_id=tid,
                    text=(
                        f"⏰ *Eslatma!*\n\n"
                        f"📦 Buyurtma: #{order_id}\n"
                        f"📋 Joriy qadam: {step_label}\n"
                        f"⌛ {idle_min} daqiqadan beri harakat yo'q.\n\n"
                        f"Iltimos, keyingi qadamni bajaring yoki /start bosing."
                    ),
                    parse_mode="Markdown"
                )
                reminded.add(tid)
                logger.info(f"[REMINDER] Sent to driver TID={tid} for order {order_id} (idle {idle_min}min)")
            except Exception as e:
                logger.error(f"[REMINDER] Failed to send to {tid}: {e}")

    except Exception as e:
        logger.error(f"[REMINDER] Critical error: {e}")


# ─── 3. Daily report at 22:00 ──────────────────────────────────────────────────

async def send_daily_report_job(bot: Bot):
    try:
        from core.config import GROUP_CHAT_ID, TIMEZONE
        from core.db import get_orders_by_date_range, get_active_orders
        from core.utils import get_seconds_diff
        from core.sheets import get_drivers_status_list

        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        date_str = now.strftime('%d.%m.%Y')

        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=0)

        # Completed orders today
        done_orders = await asyncio.to_thread(get_orders_by_date_range, start_dt.isoformat(), end_dt.isoformat())
        # Still active orders
        all_active = await asyncio.to_thread(get_active_orders)
        active_orders = [o for o in all_active if o.get('current_status') not in ('YAKUNLANDI', 'NEW')]

        # ─── Per-driver stats from completed orders ───
        stats = {}
        for o in done_orders:
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

        ranking = sorted(stats.items(), key=lambda x: x[1]['count'], reverse=True)
        medals = ["🥇", "🥈", "🥉"]

        # ─── Build group message ───
        lines = [
            f"📊 *Kunlik hisobot — {date_str}*\n",
            f"✅ Yakunlangan reyslar: *{len(done_orders)} ta*",
            f"⏳ Hali yakunlanmagan: *{len(active_orders)} ta*\n",
        ]

        if ranking:
            lines.append("🏆 *Haydovchilar reytingi:*")
            for i, (tid, s) in enumerate(ranking):
                avg_min = int(s['total_seconds'] / s['count'] / 60) if s['count'] > 0 else 0
                medal = medals[i] if i < 3 else f"{i+1}."
                lines.append(f"{medal} *{s['car']}* — {s['name']} — {s['count']} reys (avg {avg_min} min)")
        else:
            lines.append("📉 Bugun yakunlangan reys yo'q.")

        # Active (unfinished) section
        if active_orders:
            lines.append("\n⏳ *Yakunlanmagan buyurtmalar:*")
            for o in active_orders[:10]:
                acc = o.get('accepted_at')
                if acc:
                    from core.utils import parse_dt
                    acc_dt = parse_dt(acc)
                    elapsed = int((now - acc_dt).total_seconds() / 60) if acc_dt else 0
                    lines.append(
                        f"🔴 {o.get('car_number','-')} — {o.get('driver_name','-')} "
                        f"| #{o.get('order_id','-')} | {elapsed} daqiqa"
                    )
                else:
                    lines.append(f"🔴 {o.get('car_number','-')} — {o.get('driver_name','-')} | #{o.get('order_id','-')}")

        msg_group = "\n".join(lines)

        # Send to group
        if GROUP_CHAT_ID:
            try:
                await bot.send_message(chat_id=GROUP_CHAT_ID, text=msg_group, parse_mode="Markdown")
                logger.info("✅ Daily report sent to group.")
            except Exception as e:
                logger.error(f"Failed to send daily report to group: {e}")

        # Send private stats to each driver
        for i, (tid, s) in enumerate(ranking):
            avg_min = int(s['total_seconds'] / s['count'] / 60) if s['count'] > 0 else 0
            medal = medals[i] if i < 3 else ""
            rank_note = f"\n🎊 Siz bugungi reytingda {i+1}-orinni egalladingiz! {medal}" if i < 3 else ""
            msg_private = (
                f"📊 *Kunlik hisobotingiz ({date_str})*\n\n"
                f"✅ Reyslar: *{s['count']} ta*\n"
                f"⏱ O'rtacha vaqt: *{avg_min} minut*"
                f"{rank_note}"
            )
            try:
                await bot.send_message(chat_id=tid, text=msg_private, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send private report to {tid}: {e}")

    except Exception as e:
        logger.error(f"Error in send_daily_report_job: {e}")
