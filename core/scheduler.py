import logging
import asyncio
from datetime import datetime, timedelta
import pytz

from aiogram import Bot
from core.sheets import (
    get_new_orders, update_order_status, get_drivers,
    update_driver_status_sheet, remove_order_from_driver_sheet,
    write_driver_order_count_to_orders_sheet, write_order_result_note
)
from core.db import (
    create_order, get_order, update_order, archive_duplicate_order_id,
    get_active_orders_count, get_driver_sent_orders_count,
)
import core.keyboards as kb
from core.utils import normalize_car_number
from core.config import ADMIN_IDS

logger = logging.getLogger(__name__)

PROCESSED_ORDERS = {}

# Lock: bir vaqtda faqat bitta check_sheets_job ishlaydi
_job_lock = asyncio.Lock()

# Reminder tracking: {order_id: {'type': sent_datetime, ...}}
# Types: 'unaccepted_1hr', 'stale_2hr', 'stale_6hr'
REMINDER_SENT = {}

# branch_name memory: {order_id: branch_name} — used for group routing when
# filial column doesn't exist in Supabase yet
_ORDER_BRANCH: dict = {}

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


def _get_group_id_for_filial(filial: str) -> str:
    from core.config import BRANCHES, GROUP_CHAT_ID
    if filial and filial in BRANCHES:
        gid = BRANCHES[filial].get('group_id')
        return gid or GROUP_CHAT_ID
    return GROUP_CHAT_ID


async def _notify_admins_tg_failure(bot: Bot, order_id, driver, car_number, tg_err):
    """Ping admins in Telegram when a driver message fails to send
    (e.g. driver blocked the bot), so someone reacts immediately
    instead of only finding out later from the spreadsheet."""
    if not ADMIN_IDS:
        return
    driver_name = driver.get('driver_name', '-') if driver else '-'
    text = (
        f"🚨 *Diqqat! Haydovchiga yuborib bo'lmadi*\n\n"
        f"🆔 Buyurtma: #{order_id}\n"
        f"🚗 Mashina: {car_number}\n"
        f"👤 Haydovchi: {driver_name}\n"
        f"⚠️ Xato: {tg_err}\n\n"
        f"Haydovchi botni bloklagan bo'lishi mumkin — u bilan bog'laning "
        f"yoki buyurtmani boshqa haydovchiga qo'lda biriktiring."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"❌ Failed to notify admin {admin_id}: {e}")


# ─── 1. Check all branches for new orders ────────────────────────────────────

async def process_one_order(bot: Bot, branch_name: str, sheet_name: str, group_id, order: dict, drivers: dict):
    """
    Process ONE sheet row whose status is 'SEND': validate it, find the
    driver, dispatch the Telegram message, and update DB/sheets accordingly.

    Shared by the periodic safety-net poll (check_sheets_job) AND the
    instant webhook handler (core/webhook.py, triggered the moment a
    dispatcher flips a row's status to SEND in Google Sheets) — so there's
    exactly one implementation of the dispatch logic, not two copies that
    could quietly drift out of sync.
    """
    order_id = order['order_id']
    if order_id in PROCESSED_ORDERS:
        logger.info(f"⏭ Skipping #{order_id} (already processed in memory).")
        return

    if not order_id or not order['car_number']:
        logger.error(f"❌ Bo'sh qator: ID='{order_id}' car='{order['car_number']}' (row {order['row_index']}).")
        await asyncio.to_thread(update_order_status, order['row_index'], 'QATOR_BOSH', sheet_name)
        await asyncio.to_thread(
            write_order_result_note, order['row_index'],
            "❌ Bu qatorda ID yoki mashina raqami bo'sh. Sheetda shu qatorni tekshiring "
            "va to'ldiring, keyin ustunni 'SEND'ga qaytaring.", sheet_name
        )
        if order_id:
            PROCESSED_ORDERS[order_id] = True
        return

    try:
        logger.info(f"⚙️ [{branch_name}] Processing #{order_id} for car {order['car_number']}...")

        # Check DB — if this order ID was used before, decide what to do:
        #  - old order is FINISHED/CANCELLED  -> ID was just recycled by the
        #    sheet; archive the old row (frees the unique order_id) and
        #    proceed to send this as a brand-new order, with a warning note.
        #  - old order is still ACTIVE          -> a second dispatch under the
        #    same ID right now would collide with an in-progress delivery,
        #    so keep holding it and warn instead of silently double-sending.
        db_order = await asyncio.to_thread(get_order, order_id)
        if db_order and db_order.get('current_status') not in ('NEW', None, ''):
            existing_status = db_order.get('current_status', '?')

            # Dispatcher's explicit request: ALWAYS accept a SEND row even if
            # its order_id collides with an existing order — including one
            # that's still ACTIVE (mid-delivery), not just finished/cancelled
            # ones. The old row is archived (renamed order_id, so it keeps its
            # own history intact) and this SEND is dispatched as a brand-new
            # order under the now-freed ID. NOTE: if the old order is still
            # genuinely being delivered by a driver right now, their live
            # Telegram session still references the original order_id — once
            # archived, that ID now points at the NEW order, so the driver's
            # next tap could act on the wrong order. Kept as a warning note in
            # the sheet so it's visible, but not blocked.
            archived_id = await asyncio.to_thread(
                archive_duplicate_order_id, db_order.get('id'), order_id
            )
            logger.warning(
                f"⚠️ #{order_id} ID avval ishlatilgan (holat: {existing_status}). "
                f"Eski yozuv arxivlandi ({archived_id}); yangi buyurtma sifatida yuborilmoqda."
            )
            await asyncio.to_thread(
                write_order_result_note, order['row_index'],
                f"⚠️ Diqqat: bu ID avval ham ishlatilgan (holat: {existing_status}). "
                f"Baribir yangi buyurtma sifatida yuborildi.", sheet_name
            )
            db_order = None  # fall through to create_order below

        # Create in DB if missing
        # NOTE: filial is NOT included here — it's saved later via update_order
        # which validates column existence. Direct insert would fail if column missing.
        if not db_order:
            ok = await asyncio.to_thread(create_order, {
                'order_id':       order_id,
                'car_number':     normalize_car_number(order['car_number']),
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
                    await asyncio.to_thread(
                        write_order_result_note, order['row_index'],
                        "❌ Buyurtmani bazaga yozib bo'lmadi (Supabase xatosi). "
                        "Nima qilish kerak: internet/DB holatini tekshiring va statusni "
                        "qayta 'SEND' qiling — avtomatik qayta urinib ko'riladi.", sheet_name
                    )
                    PROCESSED_ORDERS[order_id] = True
                    return

        car_number = normalize_car_number(order['car_number'])
        driver = drivers.get(car_number)

        if not driver:
            logger.error(f"❌ Driver not found for car '{car_number}' (#{order_id}).")
            await asyncio.to_thread(update_order_status, order['row_index'], 'XAYDOVCHI_TOPILMADI', sheet_name)
            await asyncio.to_thread(
                write_order_result_note, order['row_index'],
                f"❌ '{car_number}' mashina raqami haydovchilar jadvalida topilmadi. "
                f"Nima qilish kerak: haydovchilar jadvaliga shu mashina raqamini qo'shing "
                f"(A ustun) yoki bu yerdagi raqamni to'g'rilang, keyin qayta 'SEND' qiling.", sheet_name
            )
            PROCESSED_ORDERS[order_id] = True
            return

        telegram_id = driver['telegram_id']
        if not telegram_id:
            logger.error(f"❌ No Telegram ID for driver '{driver.get('driver_name')}'.")
            await asyncio.to_thread(update_order_status, order['row_index'], 'TELEGRAM_ID_YOQ', sheet_name)
            await asyncio.to_thread(
                write_order_result_note, order['row_index'],
                f"❌ {driver.get('driver_name', car_number)} uchun Telegram ID kiritilmagan. "
                f"Nima qilish kerak: haydovchilar jadvalida shu haydovchining Telegram ID "
                f"ustunini to'ldiring, keyin qayta 'SEND' qiling.", sheet_name
            )
            PROCESSED_ORDERS[order_id] = True
            return

        # Check max 3 active orders
        active_count = await asyncio.to_thread(get_active_orders_count, telegram_id)
        if active_count >= 3:
            logger.warning(f"⚠️ [{car_number}] has {active_count} active orders (max 3). Skipping #{order_id}.")
            await asyncio.to_thread(update_order_status, order['row_index'], 'XAYDOVCHI_BAND', sheet_name)
            await asyncio.to_thread(
                write_order_result_note, order['row_index'],
                f"⛔ {driver.get('driver_name', car_number)} da allaqachon {active_count} ta aktiv buyurtma bor (max 3). "
                f"Nima qilish kerak: bu buyurtma avtomatik qayta yuborilmaydi — haydovchi "
                f"bo'shagach yoki boshqa haydovchiga berish uchun status ustunini qayta 'SEND' qiling.", sheet_name
            )
            PROCESSED_ORDERS[order_id] = True
            return

        # ── Sequential acceptance check ──────────────────────────
        # Don't send new order if driver has any unaccepted (SENT) orders
        sent_count = await asyncio.to_thread(get_driver_sent_orders_count, telegram_id)
        if sent_count > 0:
            logger.warning(
                f"⚠️ [{car_number}] has {sent_count} unaccepted SENT order(s). "
                f"Holding #{order_id} until they accept pending orders."
            )
            # Write reason to sheet so admin sees why — status stays SEND for retry
            await asyncio.to_thread(
                write_order_result_note, order['row_index'],
                f"⏳ Kutilmoqda — {driver.get('driver_name', car_number)} avvalgi buyurtmani hali qabul qilmagan", sheet_name
            )
            # Do NOT add to PROCESSED_ORDERS — retry next cycle
            return

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
            await asyncio.to_thread(update_order_status, order['row_index'], 'TELEGRAM_XATOSI', sheet_name)
            await asyncio.to_thread(
                write_order_result_note, order['row_index'],
                f"❌ Telegram xatosi: haydovchiga xabar yuborib bo'lmadi ({tg_err}). "
                f"Nima qilish kerak: agar 'bot was blocked' bo'lsa — haydovchiga botni "
                f"qayta /start bosishini ayting, keyin qayta 'SEND' qiling; aks holda "
                f"Telegram ID to'g'riligini tekshiring.", sheet_name
            )
            await _notify_admins_tg_failure(bot, order_id, driver, car_number, tg_err)
            PROCESSED_ORDERS[order_id] = True
            return

        # Update DB to SENT with driver info
        # branch_name is kept in _ORDER_BRANCH (in-memory) since
        # Supabase orders table may not have a filial column yet
        await asyncio.to_thread(update_order, order_id, {
            'current_status':     'SENT',
            'driver_telegram_id': str(telegram_id),
            'driver_name':        driver.get('driver_name', ''),
        })
        _ORDER_BRANCH[order_id] = branch_name

        # Update sheets — clear any previous "waiting" note
        await asyncio.sleep(1)
        await asyncio.to_thread(update_order_status, order['row_index'], 'SENT', sheet_name)
        await asyncio.to_thread(write_order_result_note, order['row_index'], '', sheet_name)

        await asyncio.sleep(1)
        await asyncio.to_thread(update_driver_status_sheet, car_number, 'YUK OGAN', order_id)

        await asyncio.sleep(1)
        new_active_count = active_count + 1
        await asyncio.to_thread(write_driver_order_count_to_orders_sheet, order_id, new_active_count)

        PROCESSED_ORDERS[order_id] = True

        # Group interim report — group_id = branch_cfg ning guruh ID si
        # (qaysi sheets sahifasidan kelgan, o'sha filialning guruhi)
        from core.handlers.delivery import update_group_report
        await update_group_report(bot, order_id, override_group_id=group_id)

    except Exception as e:
        logger.error(f"❌ Error processing #{order_id}: {e}")
        # Best-effort: always leave a visible reason on the sheet so a
        # stuck 'SEND' row is never silent. Don't mark PROCESSED_ORDERS
        # here — an unexpected/transient error (e.g. Sheets 429) should
        # be retried on the next cycle, not frozen forever.
        try:
            await asyncio.to_thread(
                write_order_result_note, order['row_index'],
                f"❌ Ichki xatolik: {e}. Tizim keyingi tekshiruvda avtomatik qayta "
                f"urinadi; bir necha marta takrorlansa, dasturchiga xabar bering.", sheet_name
            )
        except Exception as note_err:
            logger.error(f"❌ Also failed to write fallback note for #{order_id}: {note_err}")


# ─── 1. Check all branches for new orders (safety-net poll) ─────────────────

async def check_sheets_job(bot: Bot):
    """
    Polls all branch sheets for new orders.
    - Uses asyncio.Lock() to prevent overlap.
    - This is now a SAFETY NET, not the primary dispatch path — the webhook
      (core/webhook.py) reacts the instant a row is set to SEND. This job
      still runs periodically to catch anything the webhook missed (Apps
      Script misfire, deploy in progress, etc.), so nothing gets silently
      stuck if the push notification doesn't arrive.
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

                if branch_idx > 0:
                    await asyncio.sleep(3)

                logger.info(f"🔍 [{branch_name}] Checking sheet: '{sheet_name}'...")
                new_orders = await asyncio.to_thread(get_new_orders, sheet_name)

                if not new_orders:
                    logger.info(f"✅ [{branch_name}] No new orders in '{sheet_name}'.")
                    continue

                logger.info(f"📝 [{branch_name}] Found {len(new_orders)} new orders.")

                for order in new_orders:
                    await process_one_order(bot, branch_name, sheet_name, group_id, order, drivers)

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
            filial       = _ORDER_BRANCH.get(order_id) or order.get('filial', '')
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
            filial      = _ORDER_BRANCH.get(order_id) or order.get('filial', '')
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
