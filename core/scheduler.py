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
from core.db import create_order, get_order, update_order
import core.keyboards as kb

logger = logging.getLogger(__name__)

PROCESSED_ORDERS = {}

# Lock: bir vaqtda faqat bitta check_sheets_job ishlaydi
_job_lock = asyncio.Lock()

# Reminder tracking: {order_id: {'type': sent_datetime, ...}}
# Types: 'unaccepted_1hr', 'stale_2hr', 'stale_6hr'
REMINDER_SENT = {}

TIMEZONE = "Asia/Tashkent"
_tz = pytz.timezone(TIMEZONE)


def _now():
    return datetime.now(_tz)


def _should_send_reminder(order_id: str, reminder_type: str, cooldown_hours: float = 20) -> bool:
    """Return True if this reminder type hasn't been sent yet (or cooldown expired)."""
    reminders = REMINDER_SENT.get(order_id, {})
    last = reminders.get(reminder_type)
    if last is None:
        return True
    return (_now() - last).total_seconds() / 3600 >= cooldown_hours


def _mark_reminder_sent(order_id: str, reminder_type: str):
    if order_id not in REMINDER_SENT:
        REMINDER_SENT[order_id] = {}
    REMINDER_SENT[order_id][reminder_type] = _now()


def _get_group_id_for_driver(driver: dict) -> str:
    """Return the correct Telegram group_id based on driver's filial."""
    from core.config import BRANCHES, GROUP_CHAT_ID
    filial = (driver.get('filial') or '').strip()
    if filial and filial in BRANCHES:
        gid = BRANCHES[filial].get('group_id', GROUP_CHAT_ID)
        return gid or GROUP_CHAT_ID
    return GROUP_CHAT_ID


def _get_group_id_for_filial(filial: str) -> str:
    from core.config import BRANCHES, GROUP_CHAT_ID
    if filial and filial in BRANCHES:
        gid = BRANCHES[filial].get('group_id')
        return gid or GROUP_CHAT_ID
    return GROUP_CHAT_ID


# ─── 1. Check all branches for new orders ────────────────────────────────────

async def check_sheets_job(bot: Bot):
    """
    Polls all branch sheets for new orders.
    - Uses asyncio.Lock() to prevent overlap.
    - Checks driver has no pending unaccepted (SENT) orders before sending new one.
    - Updates DB status to SENT when order is dispatched to driver.
    """
    if _job_lock.locked():
        logger.warning("[SCHEDULER] Previous job still running, skipping this tick.")
        return

    async with _job_lock:
        try:
            from core.config import BRANCHES
            from core.db import get_active_orders_count, get_driver_sent_orders_count
            drivers = await asyncio.to_thread(get_drivers)

            for branch_idx, (branch_name, branch_cfg) in enumerate(BRANCHES.items()):
                sheet_name = branch_cfg.get("orders_sheet")
                group_id = branch_cfg.get("group_id")

                if not sheet_name:
                    continue

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
                        logger.info(f"⏭ Skipping #{order_id} (already processed in memory).")
                        continue

                    try:
                        logger.info(f"⚙️ [{branch_name}] Processing #{order_id} for car {order['car_number']}...")

                        # Check DB — if order already SENT or later, skip
                        db_order = await asyncio.to_thread(get_order, order_id)
                        if db_order and db_order.get('current_status') not in ('NEW', None, ''):
                            PROCESSED_ORDERS[order_id] = True
                            logger.info(f"⏭ #{order_id} already in DB with status={db_order.get('current_status')}. Skipping.")
                            continue

                        # Create in DB if missing
                        # NOTE: filial is NOT included here — it's saved later via update_order
                        # which validates column existence. Direct insert would fail if column missing.
                        if not db_order:
                            ok = await asyncio.to_thread(create_order, {
                                'order_id':       order_id,
                                'car_number':     order['car_number'].strip().upper(),
                                'address':        order['address'],
                                'cargo':          order['cargo'],
                                'comment':        order['comment'],
                                'current_status': 'NEW'
                            })
                            if not ok:
                                # Re-check: maybe it was created by a parallel run
                                db_order = await asyncio.to_thread(get_order, order_id)
                                if not db_order:
                                    logger.error(f"❌ #{order_id} could not be created in DB. Skipping.")
                                    PROCESSED_ORDERS[order_id] = True
                                    continue

                        car_number = order['car_number'].strip().upper()
                        driver = drivers.get(car_number)

                        if not driver:
                            logger.error(f"❌ Driver not found for car '{car_number}' (#{order_id}).")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_NOT_FOUND', sheet_name)
                            PROCESSED_ORDERS[order_id] = True
                            continue

                        telegram_id = driver['telegram_id']
                        if not telegram_id:
                            logger.error(f"❌ No Telegram ID for driver '{driver.get('driver_name')}'.")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_NO_TELEGRAM_ID', sheet_name)
                            PROCESSED_ORDERS[order_id] = True
                            continue

                        # Check max 3 active orders
                        active_count = await asyncio.to_thread(get_active_orders_count, telegram_id)
                        if active_count >= 3:
                            logger.warning(f"⚠️ [{car_number}] has {active_count} active orders (max 3). Skipping #{order_id}.")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_DRIVER_BUSY_3', sheet_name)
                            PROCESSED_ORDERS[order_id] = True
                            continue

                        # ── Sequential acceptance check ──────────────────────────
                        # Don't send new order if driver has any unaccepted (SENT) orders
                        sent_count = await asyncio.to_thread(get_driver_sent_orders_count, telegram_id)
                        if sent_count > 0:
                            logger.warning(
                                f"⚠️ [{car_number}] has {sent_count} unaccepted SENT order(s). "
                                f"Holding #{order_id} until they accept pending orders."
                            )
                            # Do NOT add to PROCESSED_ORDERS — retry next cycle
                            continue

                        # Build driver message — no filial shown
                        active_note = (
                            f"\n\n⚠️ *Diqqat: sizda allaqachon {active_count} ta aktiv buyurtma bor!*"
                            if active_count > 0 else ""
                        )
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
                            logger.info(f"✉️ #{order_id} sent to {driver.get('driver_name')} (TID: {telegram_id}).")
                        except Exception as tg_err:
                            logger.error(f"❌ Failed to send to {telegram_id}: {tg_err}")
                            await asyncio.to_thread(update_order_status, order['row_index'], 'ERROR_TG_SEND_FAILED', sheet_name)
                            PROCESSED_ORDERS[order_id] = True
                            continue

                        # Update DB to SENT with driver info
                        filial_for_order = driver.get('filial', branch_name) or branch_name
                        await asyncio.to_thread(update_order, order_id, {
                            'current_status':    'SENT',
                            'driver_telegram_id': str(telegram_id),
                            'driver_name':        driver.get('driver_name', ''),
                            'filial':             filial_for_order,
                        })

                        # Update sheets
                        await asyncio.sleep(1)
                        await asyncio.to_thread(update_order_status, order['row_index'], 'SENT', sheet_name)

                        await asyncio.sleep(1)
                        await asyncio.to_thread(update_driver_status_sheet, car_number, 'YUK OGAN', order_id)

                        await asyncio.sleep(1)
                        new_active_count = active_count + 1
                        await asyncio.to_thread(write_driver_order_count_to_orders_sheet, order_id, new_active_count)

                        PROCESSED_ORDERS[order_id] = True

                        # Group interim report
                        driver_group_id = _get_group_id_for_driver(driver) or group_id
                        from core.handlers.delivery import update_group_report
                        await update_group_report(bot, order_id, override_group_id=driver_group_id)

                    except Exception as e:
                        logger.error(f"❌ Error processing #{order_id}: {e}")

        except Exception as e:
            logger.error(f"❌ Critical error in check_sheets_job: {e}")


# ─── 2. Driver reminders (30min / 2hr / 6hr + 1hr unaccepted) ────────────────

async def send_driver_reminders(bot: Bot):
    """
    Multi-level reminder system:
    - SENT orders (not accepted) > 1 hour  → remind driver + notify group
    - Active orders idle > 2 hours         → strong reminder to driver
    - Active orders idle > 6 hours         → urgent reminder to driver + group (big text)
    - Active orders idle 30-120 min        → basic reminder to driver
    """
    try:
        from core.db import get_active_orders, get_orders_by_status
        from core.utils import parse_dt
        from core.config import GROUP_CHAT_ID

        now = _now()

        # ── A: Check unaccepted SENT orders (> 1 hour) ──────────────────────
        sent_orders = await asyncio.to_thread(get_orders_by_status, 'SENT')
        for order in sent_orders:
            order_id = order.get('order_id', '-')
            tid       = order.get('driver_telegram_id')
            if not tid:
                continue

            sent_dt = parse_dt(order.get('created_at'))
            if not sent_dt:
                continue
            minutes_since_sent = (now - sent_dt).total_seconds() / 60

            if minutes_since_sent < 60:
                continue  # Not yet 1 hour

            if not _should_send_reminder(order_id, 'unaccepted_1hr', cooldown_hours=3):
                continue

            driver_name  = order.get('driver_name', '-')
            car_number   = order.get('car_number', '-')
            address      = order.get('address', '-')
            filial       = order.get('filial', '')
            group_id     = _get_group_id_for_filial(filial)

            # Remind driver
            try:
                await bot.send_message(
                    chat_id=tid,
                    text=(
                        f"⚠️ *Eslatma!*\n\n"
                        f"📦 #{order_id} buyurtmasi sizga yuborilgan,\n"
                        f"lekin hali *QABUL QILINMAGAN*!\n\n"
                        f"📍 Manzil: {address}\n"
                        f"⏰ {int(minutes_since_sent)} daqiqadan beri kutilmoqda.\n\n"
                        f"Iltimos, buyurtmani qabul qiling!"
                    ),
                    parse_mode="Markdown",
                    reply_markup=kb.get_take_delivery_kb(order_id)
                )
            except Exception as e:
                logger.error(f"[REMINDER] unaccepted send to driver {tid}: {e}")

            # Notify group
            if group_id and str(group_id) != "0":
                try:
                    await bot.send_message(
                        chat_id=group_id,
                        text=(
                            f"⚠️ *DIQQAT! Buyurtma qabul qilinmayapti!*\n\n"
                            f"🆔 #{order_id}\n"
                            f"👤 Haydovchi: {driver_name}\n"
                            f"🚗 Mashina: {car_number}\n"
                            f"📍 Manzil: {address}\n"
                            f"⏰ {int(minutes_since_sent)} daqiqadan beri kutilmoqda!\n\n"
                            f"❗ Chora ko'rish talab etiladi!"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"[REMINDER] unaccepted group notify {group_id}: {e}")

            _mark_reminder_sent(order_id, 'unaccepted_1hr')
            logger.info(f"[REMINDER] Sent unaccepted_1hr for #{order_id} (idle {int(minutes_since_sent)} min)")

        # ── B: Check active (accepted) orders for idle time ─────────────────
        active_orders = await asyncio.to_thread(get_active_orders)
        if not active_orders:
            return

        reminded_tids = set()  # Basic 30-min reminder: one per driver per run

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

        for order in active_orders:
            tid      = order.get('driver_telegram_id')
            order_id = order.get('order_id', '-')
            status   = order.get('current_status', '')

            if not tid or status in ('YAKUNLANDI', 'NEW', 'SENT', 'BEKOR_QILINDI'):
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

            from core.utils import parse_dt
            last_dt = parse_dt(last_at)
            if not last_dt:
                continue
            minutes_idle = (now - last_dt).total_seconds() / 60

            step_label = status_labels.get(status, status)
            driver_name = order.get('driver_name', '-')
            car_number  = order.get('car_number', '-')
            address     = order.get('address', '-')
            filial      = order.get('filial', '')
            group_id    = _get_group_id_for_filial(filial)

            # ── 6-hour urgent reminder: driver + group ──────────────────────
            if minutes_idle >= 360:
                if not _should_send_reminder(order_id, 'stale_6hr', cooldown_hours=6):
                    continue
                hours_idle = int(minutes_idle // 60)
                try:
                    await bot.send_message(
                        chat_id=tid,
                        text=(
                            f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n\n"
                            f"‼️ *JIDDIY OGOHLANTIRISH!* ‼️\n\n"
                            f"📦 *#{order_id}* buyurtmasi\n"
                            f"*{hours_idle} SOATDAN KOP VAQT* bajarilmagan!\n\n"
                            f"📋 Joriy qadam: *{step_label}*\n"
                            f"📍 Manzil: {address}\n\n"
                            f"🔴🔴 *DARHOL BUYURTMANI YAKUNLANG!* 🔴🔴\n\n"
                            f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"[REMINDER] 6hr driver {tid}: {e}")

                if group_id and str(group_id) != "0":
                    try:
                        await bot.send_message(
                            chat_id=group_id,
                            text=(
                                f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨\n\n"
                                f"‼️ *JIDDIY OGOHLANTIRISH!* ‼️\n\n"
                                f"🆔 *#{order_id}* buyurtmasi\n"
                                f"*{hours_idle} SOATDAN KOP VAQT* bajarilmagan!\n\n"
                                f"👤 Haydovchi: *{driver_name}*\n"
                                f"🚗 Mashina: *{car_number}*\n"
                                f"📋 Qadam: {step_label}\n"
                                f"📍 Manzil: {address}\n\n"
                                f"🔴🔴 *DARHOL CHORA KO'RING!* 🔴🔴\n\n"
                                f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨"
                            ),
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"[REMINDER] 6hr group {group_id}: {e}")

                _mark_reminder_sent(order_id, 'stale_6hr')
                logger.info(f"[REMINDER] Sent stale_6hr for #{order_id} (idle {int(minutes_idle)} min)")

            # ── 2-hour strong reminder: driver only ─────────────────────────
            elif minutes_idle >= 120:
                if not _should_send_reminder(order_id, 'stale_2hr', cooldown_hours=2):
                    continue
                try:
                    await bot.send_message(
                        chat_id=tid,
                        text=(
                            f"⚠️⚠️ *KUCHLI ESLATMA!* ⚠️⚠️\n\n"
                            f"📦 #{order_id} buyurtmasi\n"
                            f"*{int(minutes_idle // 60)} soat {int(minutes_idle % 60)} daqiqadan beri* kutilmoqda!\n\n"
                            f"📋 Joriy qadam: *{step_label}*\n"
                            f"📍 Manzil: {address}\n\n"
                            f"⏰ Iltimos, keyingi qadamni DARHOL bajaring!\n"
                            f"Aks holda 6 soatdan keyin guruhga xabar yuboriladi."
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"[REMINDER] 2hr driver {tid}: {e}")

                _mark_reminder_sent(order_id, 'stale_2hr')
                logger.info(f"[REMINDER] Sent stale_2hr for #{order_id} (idle {int(minutes_idle)} min)")

            # ── 30-min basic reminder: driver only (once per run) ───────────
            elif minutes_idle >= 30:
                if tid in reminded_tids:
                    continue
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
                    reminded_tids.add(tid)
                    logger.info(f"[REMINDER] Sent 30min for TID={tid} order={order_id} (idle {int(minutes_idle)} min)")
                except Exception as e:
                    logger.error(f"[REMINDER] 30min send to {tid}: {e}")

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
        active_orders = [o for o in all_active if o.get('current_status') not in ('YAKUNLANDI', 'NEW', 'SENT', 'BEKOR_QILINDI')]

        drivers_info = await asyncio.to_thread(get_drivers)
        tid_to_filial = {}
        for car, d in drivers_info.items():
            tid = str(d.get('telegram_id', ''))
            if tid:
                tid_to_filial[tid] = d.get('filial', '')

        default_branch = list(BRANCHES.keys())[0]

        filial_stats = {}
        for o in done_orders:
            tid    = str(o.get('driver_telegram_id', '') or '')
            filial = tid_to_filial.get(tid, '') or ''
            if filial not in BRANCHES:
                filial = default_branch
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
            filial = tid_to_filial.get(tid, '') or ''
            if filial not in BRANCHES:
                filial = default_branch
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
