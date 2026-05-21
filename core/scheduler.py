import logging
import asyncio
from datetime import datetime, timedelta
import pytz

from aiogram import Bot
from core.sheets import (
    get_new_orders, update_order_status, get_drivers,
    update_driver_status_sheet, remove_order_from_driver_sheet,
    write_driver_order_count_to_orders_sheet
)
from core.db import create_order, get_order
import core.keyboards as kb

logger = logging.getLogger(__name__)

PROCESSED_ORDERS = {}

# Lock: bir vaqtda faqat bitta check_sheets_job ishlaydi
_job_lock = asyncio.Lock()


def _get_group_id_for_driver(driver: dict) -> str:
    """Return the correct Telegram group_id based on driver's filial."""
    from core.config import BRANCHES, GROUP_CHAT_ID
    filial = (driver.get('filial') or '').strip()
    if filial and filial in BRANCHES:
        gid = BRANCHES[filial].get('group_id', GROUP_CHAT_ID)
        return gid or GROUP_CHAT_ID
    return GROUP_CHAT_ID


# ─── 1. Check all branches for new orders ────────────────────────────────────

async def check_sheets_job(bot: Bot):
    """
    Polls all branch sheets for new orders.
    - Uses asyncio.Lock() to prevent overlap.
    - Adds 3s delay between branches to avoid 429 rate limit.
    - Adds 1s delay between orders within a branch.
    """
    if _job_lock.locked():
        logger.warning("[SCHEDULER] Previous job still running, skipping this tick.")
        return

    async with _job_lock:
        try:
            from core.config import BRANCHES
            drivers = await asyncio.to_thread(get_drivers)

            for branch_idx, (branch_name, branch_cfg) in enumerate(BRANCHES.items()):
                sheet_name = branch_cfg.get("orders_sheet")
                group_id = branch_cfg.get("group_id")

                if not sheet_name:
                    continue

                # 3s delay between branches (except first)
                if branch_idx > 0:
                    await asyncio.sleep(3)

                logger.info(f"🔍 [{branch_name}] Checking sheet: '{sheet_name}'...")
                new_orders = await asyncio.to_thread(get_new_orders, sheet_name)

                if not new_orders:
                    logger.info(f"✅ [{branch_name}] No new orders in '{sheet_name}'.")
                    continue

                logger.info(f"📝 [{branch_name}] Found {len(new_orders)} new orders.")

                for order in new_orders:
                    order_id = order['order_id']
                    if order_id in PROCESSED_ORDERS:
                        logger.info(f"⏭ Skipping #{order_id} (already processed).")
                        continue

                    try:
                        logger.info(f"⚙️ [{branch_name}] Processing #{order_id} for car {order['car_number']}...")

                        # Ensure in DB
                        db_order = await asyncio.to_thread(get_order, order_id)
                        if not db_order:
                            await asyncio.to_thread(create_order, {
                                'order_id':      order_id,
                                'car_number':    order['car_number'],
                                'address':       order['address'],
                                'cargo':         order['cargo'],
                                'comment':       order['comment'],
                                'current_status':'NEW'
                            })

                        car_number = order['car_number'].strip().upper()
                        driver = drivers.get(car_number)

                        if not driver:
                            logger.error(f"❌ Driver not found for car '{car_number}' (#{order_id}).")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_NOT_FOUND', sheet_name)
                            continue

                        telegram_id = driver['telegram_id']
                        if not telegram_id:
                            logger.error(f"❌ No Telegram ID for driver '{driver.get('driver_name')}'.")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_NO_TELEGRAM_ID', sheet_name)
                            continue

                        # Max 3 active orders per driver
                        from core.db import get_active_orders_count
                        active_count = await asyncio.to_thread(get_active_orders_count, telegram_id)
                        if active_count >= 3:
                            logger.warning(f"⚠️ [{car_number}] has {active_count} active orders. Skipping #{order_id}.")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_BUSY_3', sheet_name)
                            continue

                        active_note = (
                            f"\n\n⚠️ *Diqqat: sizda allaqachon {active_count} ta aktiv buyurtma bor!*"
                            if active_count > 0 else ""
                        )
                        msg_text = (
                            f"🆕 *YANGI BUYURTMA!*\n\n"
                            f"🆔 *ID:* {order_id}\n"
                            f"🏢 *Filial:* {branch_name}\n"
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
                            logger.info(f"✉️ #{order_id} sent to {driver.get('driver_name')} (TID: {telegram_id}).")
                        except Exception as tg_err:
                            logger.error(f"❌ Failed to send to {telegram_id}: {tg_err}")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_TG_SEND_FAILED', sheet_name)
                            continue

                        # Update Sheets — 1 second delay before each write to avoid 429
                        await asyncio.sleep(1)
                        await asyncio.to_thread(update_order_status, order['row_index'], 'SENT', sheet_name)

                        await asyncio.sleep(1)
                        await asyncio.to_thread(update_driver_status_sheet, car_number, 'YUK OGAN', order_id)

                        await asyncio.sleep(1)
                        new_active_count = active_count + 1
                        await asyncio.to_thread(write_driver_order_count_to_orders_sheet, order_id, new_active_count)

                        PROCESSED_ORDERS[order_id] = True

                        # Group report
                        driver_group_id = _get_group_id_for_driver(driver) or group_id
                        from core.handlers.delivery import update_group_report
                        await update_group_report(bot, order_id, override_group_id=driver_group_id)

                    except Exception as e:
                        logger.error(f"❌ Error processing #{order_id}: {e}")

        except Exception as e:
            logger.error(f"❌ Critical error in check_sheets_job: {e}")


# ─── 2. 30-minute driver reminders ───────────────────────────────────────────

async def send_driver_reminders(bot: Bot):
    try:
        from core.db import get_active_orders
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

            if not tid or tid in reminded:
                continue
            if status in ('YAKUNLANDI', 'NEW'):
                continue

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

            if minutes_idle < 30:
                continue

            status_labels = {
                "QABUL_QILINDI":  "Buyurtma qabul qilingan, bloklar tanlanmagan",
                "TRANSIT":        "Transit kutilmoqda",
                "LOADED_PHOTO":   "Yuk rasmi kutilmoqda",
                "ON_WAY":         "Yo'lga chiqish bosilmagan",
                "YOLDA":          "Yo'lda — akt rasmi kutilmoqda",
                "ACT_PHOTO":      "Akt rasmi kutilmoqda",
                "DELIVERED_LOC":  "Lokatsiya kutilmoqda",
                "WAITING_FINISH": "Yakunlash tugmasi bosilmagan",
            }
            step_label = status_labels.get(status, status)

            try:
                await bot.send_message(
                    chat_id=tid,
                    text=(
                        f"⏰ *Eslatma!*\n\n"
                        f"📦 Buyurtma: #{order_id}\n"
                        f"📋 Joriy qadam: {step_label}\n"
                        f"⌛ {int(minutes_idle)} daqiqadan beri harakat yo'q.\n\n"
                        f"Iltimos, keyingi qadamni bajaring yoki /start bosing."
                    ),
                    parse_mode="Markdown"
                )
                reminded.add(tid)
                logger.info(f"[REMINDER] Sent to TID={tid} order={order_id} (idle {int(minutes_idle)}min)")
            except Exception as e:
                logger.error(f"[REMINDER] Failed to send to {tid}: {e}")

    except Exception as e:
        logger.error(f"[REMINDER] Critical error: {e}")


# ─── 3. Daily report at 22:00 ────────────────────────────────────────────────

async def send_daily_report_job(bot: Bot):
    try:
        from core.config import GROUP_CHAT_ID, TIMEZONE, BRANCHES
        from core.db import get_orders_by_date_range, get_active_orders
        from core.utils import get_seconds_diff, parse_dt

        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        date_str = now.strftime('%d.%m.%Y')

        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt   = now.replace(hour=23, minute=59, second=59, microsecond=0)

        done_orders = await asyncio.to_thread(get_orders_by_date_range, start_dt.isoformat(), end_dt.isoformat())
        all_active  = await asyncio.to_thread(get_active_orders)
        active_orders = [o for o in all_active if o.get('current_status') not in ('YAKUNLANDI', 'NEW')]

        drivers_info = await asyncio.to_thread(get_drivers)
        tid_to_filial = {}
        for car, d in drivers_info.items():
            tid = str(d.get('telegram_id', ''))
            if tid:
                tid_to_filial[tid] = d.get('filial', '')

        # Group by filial
        filial_stats = {}
        for o in done_orders:
            tid    = str(o.get('driver_telegram_id', '') or '')
            filial = tid_to_filial.get(tid, 'Shiribod') or 'Shiribod'
            if filial not in filial_stats:
                filial_stats[filial] = {}
            if tid not in filial_stats[filial]:
                filial_stats[filial][tid] = {
                    'name': o.get('driver_name', '-'),
                    'car':  o.get('car_number', '-'),
                    'count': 0, 'total_seconds': 0
                }
            filial_stats[filial][tid]['count'] += 1
            diff = get_seconds_diff(o.get('accepted_at'), o.get('completed_at'))
            if diff and diff > 0:
                filial_stats[filial][tid]['total_seconds'] += diff

        filial_active = {}
        for o in active_orders:
            tid    = str(o.get('driver_telegram_id', '') or '')
            filial = tid_to_filial.get(tid, 'Shiribod') or 'Shiribod'
            filial_active.setdefault(filial, []).append(o)

        medals = ["🥇", "🥈", "🥉"]

        for branch_name, branch_cfg in BRANCHES.items():
            group_id = branch_cfg.get("group_id")
            if not group_id:
                continue

            stats   = filial_stats.get(branch_name, {})
            active  = filial_active.get(branch_name, [])
            ranking = sorted(stats.items(), key=lambda x: x[1]['count'], reverse=True)

            lines = [
                f"📊 *Kunlik hisobot — {date_str}*",
                f"🏢 *Filial: {branch_name}*\n",
                f"✅ Yakunlangan reyslar: *{sum(s['count'] for s in stats.values())} ta*",
                f"⏳ Hali yakunlanmagan: *{len(active)} ta*\n",
            ]

            if ranking:
                lines.append("🏆 *Haydovchilar reytingi:*")
                for i, (tid, s) in enumerate(ranking):
                    count     = s['count']
                    total_sec = s['total_seconds']
                    if count > 0 and total_sec > 0:
                        avg_sec = total_sec // count
                        avg_h   = avg_sec // 3600
                        avg_m   = (avg_sec % 3600) // 60
                        avg_str = f"{avg_h}s {avg_m}dq" if avg_h > 0 else f"{avg_m} daqiqa"
                    else:
                        avg_str = "—"
                    medal = medals[i] if i < 3 else f"{i+1}."
                    lines.append(f"{medal} *{s['car']}* — {s['name']} — {count} reys | avg: {avg_str}")
            else:
                lines.append("📉 Bugun yakunlangan reys yo'q.")

            if active:
                lines.append("\n⏳ *Yakunlanmagan buyurtmalar:*")
                for o in active[:10]:
                    acc    = o.get('accepted_at')
                    acc_dt = parse_dt(acc) if acc else None
                    elapsed = int((now - acc_dt).total_seconds() / 60) if acc_dt else 0
                    lines.append(
                        f"🔴 {o.get('car_number','-')} — {o.get('driver_name','-')} "
                        f"| #{o.get('order_id','-')} | {elapsed} daqiqa"
                    )

            try:
                await bot.send_message(
                    chat_id=group_id,
                    text="\n".join(lines),
                    parse_mode="Markdown"
                )
                logger.info(f"✅ Daily report sent to {branch_name} ({group_id}).")
            except Exception as e:
                logger.error(f"Failed to send daily report to {branch_name}: {e}")

        # Private stats to each driver
        all_stats = {}
        for filial_s in filial_stats.values():
            all_stats.update(filial_s)

        for i, (tid, s) in enumerate(sorted(all_stats.items(), key=lambda x: x[1]['count'], reverse=True)):
            count     = s['count']
            total_sec = s['total_seconds']
            avg_str   = f"{(total_sec // count) // 60} daqiqa" if count > 0 and total_sec > 0 else "—"
            medal = medals[i] if i < 3 else ""
            rank_note = f"\n🎊 Siz bugungi reytingda {i+1}-orinni egalladingiz! {medal}" if i < 3 else ""
            try:
                await bot.send_message(
                    chat_id=tid,
                    text=(
                        f"📊 *Kunlik hisobotingiz ({date_str})*\n\n"
                        f"✅ Reyslar: *{count} ta*\n"
                        f"⏱ O'rtacha vaqt: *{avg_str}*"
                        f"{rank_note}"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send private report to {tid}: {e}")

    except Exception as e:
        logger.error(f"Error in send_daily_report_job: {e}")
