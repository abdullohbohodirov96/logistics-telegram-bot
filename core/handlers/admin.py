import logging
import asyncio
import html as _html
from datetime import datetime, timedelta
import pytz

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import ADMIN_IDS, TIMEZONE
from core.db import get_active_orders, get_orders_by_date_range, get_order, update_order
from core.states import AdminProcess
from core.utils import get_seconds_diff, parse_dt

router = Router()
logger = logging.getLogger(__name__)
tz = pytz.timezone(TIMEZONE)

def _e(text) -> str:
    """Escape text for HTML parse_mode."""
    return _html.escape(str(text) if text is not None else "")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def _back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

def get_admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Aktiv buyurtmalar",             callback_data="adm_active")],
        [InlineKeyboardButton(text="❌ Buyurtmani bekor qilish",       callback_data="adm_cancel_order")],
        [InlineKeyboardButton(text="📊 Hisobotni olish",               callback_data="adm_report_menu")],
        [InlineKeyboardButton(text="🏆 Umumiy reyting",                callback_data="adm_rating")],
        [InlineKeyboardButton(text="🚗 Mashina / Haydovchi hisoboti",  callback_data="adm_by_filter")],
        [InlineKeyboardButton(text="🧹 Eski buyurtmalarni yopish (Reset)", callback_data="adm_reset_all")],
    ])

def _report_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Bugungi hisobot",   callback_data="adm_today")],
        [InlineKeyboardButton(text="📅 Kechagi hisobot",   callback_data="adm_yesterday")],
        [InlineKeyboardButton(text="📅 Hafta hisoboti",    callback_data="adm_7days")],
        [InlineKeyboardButton(text="🗓 Sana tanlash",      callback_data="adm_manual_date")],
        [InlineKeyboardButton(text="🔙 Orqaga",            callback_data="adm_back")],
    ])

def _by_filter_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Mashina bo'yicha",   callback_data="adm_by_car")],
        [InlineKeyboardButton(text="👤 Haydovchi bo'yicha", callback_data="adm_by_driver")],
        [InlineKeyboardButton(text="🔙 Orqaga",             callback_data="adm_back")],
    ])


def _date_range(now, kind: str):
    if kind == "today":
        s = now.replace(hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif kind == "yesterday":
        y = now - timedelta(days=1)
        s = y.replace(hour=0, minute=0, second=0, microsecond=0)
        e = y.replace(hour=23, minute=59, second=59, microsecond=0)
    elif kind == "7days":
        s = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif kind == "30days":
        s = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        return None, None
    return s.isoformat(), e.isoformat()


def _parse_manual_date(text: str, tzinfo):
    text = text.strip().lower()
    now = datetime.now(tzinfo)

    shortcuts = {
        "bugun": ("today", 0), "kecha": ("yesterday", 1),
        "7 kun": ("7d", 7), "7kun": ("7d", 7),
        "30 kun": ("30d", 30), "30kun": ("30d", 30),
    }
    if text in shortcuts:
        key, days = shortcuts[text]
        if days == 0:
            s = now.replace(hour=0, minute=0, second=0, microsecond=0)
            e = now.replace(hour=23, minute=59, second=59, microsecond=0)
        elif key == "yesterday":
            y = now - timedelta(days=1)
            s = y.replace(hour=0, minute=0, second=0, microsecond=0)
            e = y.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            s = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
            e = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return s.isoformat(), e.isoformat()

    if " - " in text:
        parts = text.split(" - ")
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                s_dt = tzinfo.localize(datetime.strptime(parts[0].strip(), fmt)).replace(hour=0, minute=0, second=0)
                e_dt = tzinfo.localize(datetime.strptime(parts[1].strip(), fmt)).replace(hour=23, minute=59, second=59)
                return s_dt.isoformat(), e_dt.isoformat()
            except Exception:
                continue
        raise ValueError("bad_range")

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text.strip(), fmt)
            s_dt = tzinfo.localize(dt).replace(hour=0, minute=0, second=0)
            e_dt = tzinfo.localize(dt).replace(hour=23, minute=59, second=59)
            return s_dt.isoformat(), e_dt.isoformat()
        except Exception:
            continue

    raise ValueError("bad_date")


def _get_orders_in_range(date_from: str, date_to: str, filter_type: str = None, filter_val: str = None) -> list:
    """Read all orders in date range from DB. Filters by created_at."""
    from core.db import supabase
    if not supabase:
        return []
    try:
        q = supabase.table('orders').select('*')
        if filter_type == 'car' and filter_val:
            q = q.eq('car_number', filter_val)
        elif filter_type == 'drv' and filter_val:
            q = q.eq('driver_telegram_id', filter_val)
        if date_from:
            q = q.gte('created_at', date_from)
        if date_to:
            q = q.lte('created_at', date_to)
        q = q.order('created_at', desc=True).limit(300)
        resp = q.execute()
        return resp.data or []
    except Exception as e:
        logger.error(f"_get_orders_in_range error: {e}")
        return []


def _elapsed(dt_ref, now) -> str:
    if not dt_ref:
        return ""
    mins = int((now - dt_ref).total_seconds() / 60)
    if mins < 0:
        return ""
    h, m = divmod(mins, 60)
    return f"{h}s {m}d" if h else f"{m} dq"


def _build_report(orders: list, label: str, filter_label: str = "") -> str:
    total = len(orders)
    if total == 0:
        return f"📊 <b>Hisobot: {_e(label)}</b>\n\n📭 Bu davrda buyurtmalar topilmadi."

    done      = [o for o in orders if o.get("current_status") == "YAKUNLANDI"]
    cancelled = [o for o in orders if o.get("current_status") == "BEKOR_QILINDI"]
    active    = [o for o in orders if o.get("current_status") not in ("YAKUNLANDI", "BEKOR_QILINDI")]

    drv_stats: dict = {}
    for o in done:
        tid  = o.get("driver_telegram_id") or "noname"
        name = o.get("driver_name") or "-"
        car  = o.get("car_number") or "-"
        if tid not in drv_stats:
            drv_stats[tid] = {"name": name, "car": car, "count": 0, "total_sec": 0}
        drv_stats[tid]["count"] += 1
        diff = get_seconds_diff(o.get("accepted_at"), o.get("completed_at") or o.get("finished_at"))
        if diff:
            drv_stats[tid]["total_sec"] += diff

    ranking = sorted(drv_stats.values(), key=lambda x: x["count"], reverse=True)
    medals  = ["🥇", "🥈", "🥉"]

    lines = [f"📊 <b>Hisobot: {_e(label)}</b>"]
    if filter_label:
        lines.append(f"🔍 {_e(filter_label)}")
    lines += [
        "",
        f"📦 Jami buyurtmalar: <b>{total}</b>",
        f"✅ Yakunlangan: <b>{len(done)}</b>",
        f"🚚 Aktiv / Yo'lda: <b>{len(active)}</b>",
        f"❌ Bekor qilingan: <b>{len(cancelled)}</b>",
    ]

    if ranking:
        lines.append("")
        lines.append("👥 <b>Haydovchilar natijalari:</b>")
        for i, s in enumerate(ranking[:10]):
            avg = int(s["total_sec"] / s["count"] / 60) if s["count"] > 0 else 0
            medal = medals[i] if i < 3 else f"  {i+1}."
            avg_str = f" | o'rtacha {avg} min" if avg > 0 else ""
            lines.append(f"{medal} <b>{_e(s['car'])}</b> — {_e(s['name'])} — {s['count']} reys{avg_str}")
    else:
        lines.append("\n📭 Yakunlangan reyslar yo'q.")

    return "\n".join(lines)


STAGE_LABELS = {
    'NEW':            '🆕 Yangi (qabul kutilmoqda)',
    'SENT':           '📤 Yuborilgan (haydovchi qabul qilmagan)',
    'QABUL_QILINDI':  '📥 Qabul qilindi',
    'BLOCK_MENU':     '🏗 Blok tanlayapti',
    'TRANSIT':        '🚚 Transit savoli',
    'YOLDA':          '🛣 Yo\'lga chiqdi',
    'LOADED_PHOTO':   '📸 Yuk rasmi kutilmoqda',
    'ACT_PHOTO':      '🧾 Akt rasmi kutilmoqda',
    'DELIVERED_LOC':  '📍 Lokatsiya kutilmoqda',
    'WAITING_FINISH': '⏳ Yakunlash kutilmoqda',
}

def _build_active_text(orders: list) -> str:
    if not orders:
        return "✅ Hozir yakunlanmagan buyurtmalar yo'q."

    now = datetime.now(tz)
    lines = [f"📋 <b>Yakunlanmagan buyurtmalar: {len(orders)} ta</b>\n"]

    for o in orders[:30]:
        status   = o.get('current_status', '-')
        st_label = STAGE_LABELS.get(status, f"❓ {status}")
        created  = parse_dt(o.get("created_at"))
        acc      = parse_dt(o.get("accepted_at"))

        # Time since accepted (if accepted), otherwise since created
        if acc:
            elapsed = _elapsed(acc, now)
            time_str = f" | ⏱ {elapsed}" if elapsed else ""
        elif created:
            elapsed = _elapsed(created, now)
            time_str = f" | ⏳ kutmoqda {elapsed}" if elapsed else ""
        else:
            time_str = ""

        lines.append(
            f"🔹 <b>#{_e(o['order_id'])}</b> | 🚗 {_e(o.get('car_number','-'))} | {_e(o.get('driver_name','-'))}\n"
            f"   📍 {_e(o.get('address','-'))}\n"
            f"   {st_label}{time_str}"
        )

    if len(orders) > 30:
        lines.append(f"\n...va yana {len(orders)-30} ta buyurtma")
    lines.append("\n💡 Bekor/yakunlash uchun: <b>❌ Buyurtmani bekor qilish</b> bosing")
    return "\n".join(lines)


# ─── /admin command ────────────────────────────────────────────────────────────

@router.message(Command("admin"))
@router.message(F.text == "🛠 Admin panel")
async def admin_panel(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Sizda admin huquqi yo'q.")
        return
    await state.clear()
    await message.answer(
        "🛠 <b>Admin panel</b>\nBo'limni tanlang:",
        reply_markup=get_admin_panel_kb(),
        parse_mode="HTML"
    )


# ─── Back / Close ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_back")
async def back_to_admin(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.clear()
    text = "🛠 <b>Admin panel</b>\nBo'limni tanlang:"
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_panel_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=get_admin_panel_kb(), parse_mode="HTML")

@router.callback_query(F.data == "adm_close")
async def close_admin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try: await callback.message.delete()
    except: pass


# ─── Aktiv buyurtmalar ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_active")
async def show_active(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    try:
        orders = await asyncio.to_thread(get_active_orders)
        text   = _build_active_text(orders)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_active")],
            [InlineKeyboardButton(text="🔙 Orqaga",    callback_data="adm_back")],
        ])
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"adm_active error: {e}", exc_info=True)
        await callback.message.answer(f"⚠️ Xatolik:\n<code>{e}</code>", parse_mode="HTML", reply_markup=_back_kb())


# ─── Hisobot submenu ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_report_menu")
async def report_menu(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_text(
            "📊 *Hisobotni olish*\nDavrni tanlang:",
            reply_markup=_report_menu_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "📊 *Hisobotni olish*\nDavrni tanlang:",
            reply_markup=_report_menu_kb(),
            parse_mode="HTML"
        )


async def _send_report(callback: CallbackQuery, kind: str, label: str):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer("⏳ Hisobot tayyorlanmoqda...")
    try:
        now = datetime.now(tz)
        df, dt = _date_range(now, kind)
        orders = await asyncio.to_thread(_get_orders_in_range, df, dt)
        text   = _build_report(orders, label)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Hisobot menyusi", callback_data="adm_report_menu")],
        ])
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"report ({kind}) error: {e}", exc_info=True)
        await callback.message.answer(f"⚠️ Hisobot xatolik:\n<code>{e}</code>", parse_mode="HTML", reply_markup=_back_kb())

@router.callback_query(F.data == "adm_today")
async def report_today(callback: CallbackQuery):
    await _send_report(callback, "today", f"Bugun ({datetime.now(tz).strftime('%d.%m.%Y')})")

@router.callback_query(F.data == "adm_yesterday")
async def report_yesterday(callback: CallbackQuery):
    await _send_report(callback, "yesterday", f"Kecha ({(datetime.now(tz)-timedelta(days=1)).strftime('%d.%m.%Y')})")

@router.callback_query(F.data == "adm_7days")
async def report_7days(callback: CallbackQuery):
    await _send_report(callback, "7days", "Oxirgi 7 kun")


# ─── Manual date input ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_manual_date")
async def ask_manual_date(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminProcess.waiting_for_manual_date)
    await state.update_data(filter_type=None, filter_val=None, filter_label=None)
    try:
        await callback.message.edit_text(
            "🗓 *Sana kiriting:*\n\n"
            "Formatlar:\n"
            "• <code>bugun</code> yoki <code>kecha</code>\n"
            "• `7 kun` yoki `30 kun`\n"
            "• `15.05.2026`\n"
            "• `01.05.2026 - 15.05.2026` (oraliq)",
            parse_mode="HTML",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer(
            "🗓 Sana kiriting (masalan: `15.05.2026` yoki `bugun`):",
            parse_mode="HTML",
            reply_markup=_back_kb()
        )

@router.message(AdminProcess.waiting_for_manual_date, F.text)
async def process_manual_date(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id): return
    try:
        df, dt = _parse_manual_date(message.text, tz)
    except ValueError:
        await message.answer(
            "❌ Sana noto'g'ri kiritildi.\n"
            "Masalan: `15.05.2026` yoki `01.05.2026 - 15.05.2026` yoki `bugun`",
            parse_mode="HTML"
        )
        return

    data         = await state.get_data()
    filter_type  = data.get("filter_type")
    filter_val   = data.get("filter_val")
    filter_label = data.get("filter_label", "")
    await state.clear()

    try:
        orders = await asyncio.to_thread(_get_orders_in_range, df, dt, filter_type, filter_val)
        d1 = datetime.fromisoformat(df).strftime("%d.%m.%Y")
        d2 = datetime.fromisoformat(dt).strftime("%d.%m.%Y")
        date_label = f"{d1} — {d2}" if d1 != d2 else d1
        text = _build_report(orders, date_label, filter_label)
        await message.answer(text, parse_mode="HTML", reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"manual date report error: {e}", exc_info=True)
        await message.answer(f"⚠️ Hisobot xatolik:\n<code>{e}</code>", parse_mode="HTML", reply_markup=_back_kb())


# ─── Mashina / Haydovchi bo'yicha ─────────────────────────────────────────────

@router.callback_query(F.data == "adm_by_filter")
async def by_filter_menu(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_text(
            "🔍 Hisobot turini tanlang:",
            reply_markup=_by_filter_kb()
        )
    except Exception:
        await callback.message.answer("🔍 Hisobot turini tanlang:", reply_markup=_by_filter_kb())


@router.callback_query(F.data == "adm_by_car")
async def show_cars(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer("⏳ Yuklanmoqda...")
    try:
        from core.db import get_unique_cars
        cars = await asyncio.to_thread(get_unique_cars)
        if not cars:
            await callback.message.edit_text("🚗 Mashinalar topilmadi.", reply_markup=_back_kb())
            return
        builder = InlineKeyboardBuilder()
        for car in sorted(cars):
            builder.button(text=car, callback_data=f"adm_car_{car[:20]}")
        builder.adjust(2)
        builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_by_filter"))
        try:
            await callback.message.edit_text("🚗 Mashina tanlang:", reply_markup=builder.as_markup())
        except Exception:
            await callback.message.answer("🚗 Mashina tanlang:", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"adm_by_car: {e}", exc_info=True)
        await callback.message.answer(f"⚠️ Mashinalar xatolik:\n<code>{e}</code>", parse_mode="HTML", reply_markup=_back_kb())


@router.callback_query(F.data.startswith("adm_car_"))
async def select_car(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    car = callback.data[len("adm_car_"):]
    await state.update_data(filter_type="car", filter_val=car, filter_label=f"Mashina: {car}")
    await state.set_state(AdminProcess.waiting_for_manual_date)
    try:
        await callback.message.edit_text(
            f"🚗 <b>{_e(car)}</b> mashinasi uchun sana kiriting:\n\n"
            "• <code>bugun</code> yoki <code>kecha</code>\n"
            "• `7 kun` yoki `30 kun`\n"
            "• `15.05.2026`\n"
            "• `01.05.2026 - 15.05.2026`",
            parse_mode="HTML",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer(f"🚗 {car} uchun sana kiriting:", reply_markup=_back_kb())


@router.callback_query(F.data == "adm_by_driver")
async def show_drivers(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer("⏳ Yuklanmoqda...")
    try:
        from core.db import get_unique_drivers
        drivers = await asyncio.to_thread(get_unique_drivers)
        if not drivers:
            await callback.message.edit_text("👤 Haydovchilar topilmadi.", reply_markup=_back_kb())
            return
        builder = InlineKeyboardBuilder()
        for tid, name in drivers.items():
            btn_text = (name[:22] if name else str(tid))
            builder.button(text=btn_text, callback_data=f"adm_drv_{str(tid)[:15]}")
        builder.adjust(1)
        builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_by_filter"))
        try:
            await callback.message.edit_text("👤 Haydovchi tanlang:", reply_markup=builder.as_markup())
        except Exception:
            await callback.message.answer("👤 Haydovchi tanlang:", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"adm_by_driver: {e}", exc_info=True)
        await callback.message.answer(f"⚠️ Haydovchilar xatolik:\n<code>{e}</code>", parse_mode="HTML", reply_markup=_back_kb())


@router.callback_query(F.data.startswith("adm_drv_"))
async def select_driver(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    tid = callback.data[len("adm_drv_"):]
    await state.update_data(filter_type="drv", filter_val=tid, filter_label=f"Haydovchi ID: {tid}")
    await state.set_state(AdminProcess.waiting_for_manual_date)
    try:
        await callback.message.edit_text(
            f"👤 Haydovchi (ID: `{tid}`) uchun sana kiriting:\n\n"
            "• <code>bugun</code> yoki <code>kecha</code>\n"
            "• `7 kun` yoki `30 kun`\n"
            "• `15.05.2026`\n"
            "• `01.05.2026 - 15.05.2026`",
            parse_mode="HTML",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer("👤 Haydovchi uchun sana kiriting:", reply_markup=_back_kb())


# ─── Umumiy reyting ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_rating")
async def show_rating(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer("⏳ Reyting hisoblanmoqda...")
    try:
        now = datetime.now(tz)

        # This month
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end   = now.replace(hour=23, minute=59, second=59, microsecond=0)
        month_orders = await asyncio.to_thread(
            get_orders_by_date_range, month_start.isoformat(), month_end.isoformat()
        )

        # Today
        day_start  = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_orders = await asyncio.to_thread(
            get_orders_by_date_range, day_start.isoformat(), month_end.isoformat()
        )

        def build_ranking(orders):
            stats = {}
            for o in orders:
                tid  = o.get("driver_telegram_id") or "?"
                if tid not in stats:
                    stats[tid] = {
                        "name": o.get("driver_name") or "-",
                        "car":  o.get("car_number") or "-",
                        "count": 0, "total_sec": 0
                    }
                stats[tid]["count"] += 1
                diff = get_seconds_diff(o.get("accepted_at"), o.get("completed_at") or o.get("finished_at"))
                if diff:
                    stats[tid]["total_sec"] += diff
            return sorted(stats.values(), key=lambda x: x["count"], reverse=True)

        today_rank = build_ranking(today_orders)
        month_rank = build_ranking(month_orders)
        medals = ["🥇", "🥈", "🥉"]

        lines = ["🏆 <b>Umumiy reyting</b>\n"]

        # Today
        date_str = now.strftime("%d.%m.%Y")
        lines.append(f"📅 <b>Bugun ({date_str}):</b>")
        if today_rank:
            for i, s in enumerate(today_rank[:5]):
                avg = int(s["total_sec"] / s["count"] / 60) if s["count"] > 0 else 0
                medal = medals[i] if i < 3 else f"  {i+1}."
                avg_str = f" | avg {avg} min" if avg > 0 else ""
                lines.append(f"{medal} <b>{_e(s['car'])}</b> — {_e(s['name'])} — {s['count']} reys{avg_str}")
        else:
            lines.append("  📭 Bugun yakunlangan reys yo'q")

        # This month
        lines.append(f"\n📆 <b>{now.strftime('%B %Y')} oyi:</b>")
        if month_rank:
            for i, s in enumerate(month_rank[:5]):
                avg = int(s["total_sec"] / s["count"] / 60) if s["count"] > 0 else 0
                medal = medals[i] if i < 3 else f"  {i+1}."
                avg_str = f" | avg {avg} min" if avg > 0 else ""
                lines.append(f"{medal} <b>{_e(s['car'])}</b> — {_e(s['name'])} — {s['count']} reys{avg_str}")
        else:
            lines.append("  📭 Bu oyda yakunlangan reys yo'q")

        text = "\n".join(lines)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_rating")],
            [InlineKeyboardButton(text="🔙 Orqaga",    callback_data="adm_back")],
        ])
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"adm_rating error: {e}", exc_info=True)
        await callback.message.answer(f"⚠️ Reyting xatolik:\n<code>{e}</code>", parse_mode="HTML", reply_markup=_back_kb())


# ─── Buyurtmani bekor qilish / yakunlash ──────────────────────────────────────

@router.callback_query(F.data == "adm_cancel_order")
async def start_cancel_order(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminProcess.waiting_for_order_id)
    await state.update_data(cancel_mode=True)
    try:
        await callback.message.edit_text(
            "🔎 *Buyurtma ID kiriting:*\n\n"
            "Masalan: `S32676` yoki `P14095`\n\n"
            "Aktiv buyurtmalar ID larini ko'rish uchun *📋 Aktiv buyurtmalar* ga kiring.",
            parse_mode="HTML",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer(
            "🔎 Buyurtma ID kiriting:",
            reply_markup=_back_kb()
        )


@router.message(AdminProcess.waiting_for_order_id, F.text)
async def process_order_id_for_cancel(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id): return
    data = await state.get_data()
    if not data.get('cancel_mode'):
        return

    order_id = message.text.strip()
    order = await asyncio.to_thread(get_order, order_id)

    if not order:
        await message.answer(
            f"❌ <b>#{_e(order_id)} buyurtmasi topilmadi.</b>\n\n"
            f"ID to'g'riligini tekshiring yoki <b>📋 Aktiv buyurtmalar</b> dan ID ni ko'ring.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Aktiv buyurtmalar", callback_data="adm_active")],
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")],
            ])
        )
        return

    status = order.get('current_status', '-')
    if status == 'BEKOR_QILINDI':
        await message.answer(
            f"⚠️ <b>#{_e(order_id)} allaqachon bekor qilingan.</b>\n"
            f"Boshqa buyurtma ID kiriting yoki orqaga qayting.",
            parse_mode="HTML",
            reply_markup=_back_kb()
        )
        await state.clear()
        return

    status_labels = {
        'NEW': '🆕 Yangi', 'SENT': '📤 Yuborilgan', 'QABUL_QILINDI': '📥 Qabul qilindi',
        'YOLDA': '🛣 Yo\'lda', 'YAKUNLANDI': '✅ Yakunlangan',
        'BLOCK_MENU': '🏗 Blok menyuda', 'TRANSIT': '🚚 Transit',
        'LOADED_PHOTO': '📸 Rasm kutilmoqda', 'ACT_PHOTO': '🧾 Akt kutilmoqda',
        'DELIVERED_LOC': '📍 Lokatsiya kutilmoqda', 'WAITING_FINISH': '⏳ Yakunlash kutilmoqda',
    }
    st_label = status_labels.get(status, status)

    created = parse_dt(order.get('created_at'))
    acc     = parse_dt(order.get('accepted_at'))
    created_str = created.strftime('%d.%m.%Y %H:%M') if created else '—'
    acc_str     = acc.strftime('%d.%m.%Y %H:%M') if acc else 'Qabul qilinmagan'

    text = (
        f"📋 <b>Buyurtma #{_e(order_id)}</b>\n\n"
        f"👤 Haydovchi: <b>{_e(order.get('driver_name','—'))}</b>\n"
        f"🚗 Mashina: <b>{_e(order.get('car_number','—'))}</b>\n"
        f"📍 Manzil: {_e(order.get('address','—'))}\n"
        f"📦 Yuk: {_e(order.get('cargo','—'))}\n"
        f"📊 Holat: {_e(st_label)}\n"
        f"📤 Yaratilgan: {created_str}\n"
        f"✅ Qabul: {acc_str}\n\n"
        f"<b>Quyidagi amalni tanlang:</b>"
    )

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"adm_confirm_cancel_{order_id}"),
            InlineKeyboardButton(text="✅ Yakunlash",    callback_data=f"adm_confirm_finish_{order_id}"),
        ],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

    await state.update_data(cancel_order_id=order_id)
    await message.answer(text, parse_mode="HTML", reply_markup=confirm_kb)


async def _notify_cancel_finish(bot: Bot, order: dict, order_id: str, action: str):
    """Send notifications to driver and group. action: 'cancel' or 'finish'."""
    from core.config import BRANCHES, GROUP_CHAT_ID
    from core.scheduler import _ORDER_BRANCH

    car_number  = order.get('car_number', '')
    driver_tid  = order.get('driver_telegram_id')
    address     = order.get('address', '-')
    driver_name = order.get('driver_name', '-')
    filial      = _ORDER_BRANCH.get(order_id) or order.get('filial', '')

    group_id = GROUP_CHAT_ID
    if filial and filial in BRANCHES:
        group_id = BRANCHES[filial].get('group_id') or GROUP_CHAT_ID

    results = []

    if action == 'cancel':
        drv_text   = f"❌ <b>Buyurtma bekor qilindi!</b>\n\n🆔 #{_e(order_id)} buyurtmangiz admin tomonidan bekor qilindi.\n📍 {_e(address)}\n\nYangi buyurtmalar uchun kuting."
        group_text = f"❌ <b>Buyurtma bekor qilindi!</b>\n\n🆔 #{_e(order_id)}\n👤 {_e(driver_name)} | 🚗 {_e(car_number)}\n📍 {_e(address)}\n\n📊 Admin tomonidan bekor qilindi."
    else:
        drv_text   = f"✅ <b>Buyurtma yakunlandi!</b>\n\n🆔 #{_e(order_id)} buyurtmangiz admin tomonidan yakunlandi.\n📍 {_e(address)}"
        group_text = f"✅ <b>Buyurtma yakunlandi!</b>\n\n🆔 #{_e(order_id)}\n👤 {_e(driver_name)} | 🚗 {_e(car_number)}\n📍 {_e(address)}\n\n📊 Admin tomonidan yakunlandi."

    if driver_tid:
        try:
            await bot.send_message(chat_id=driver_tid, text=drv_text, parse_mode="HTML")
            results.append("✅ Haydovchiga xabar yuborildi")
        except Exception as e:
            logger.warning(f"notify driver {driver_tid}: {e}")
            results.append("⚠️ Haydovchiga xabar yuborilmadi")
    else:
        results.append("— Haydovchi Telegram ID yo'q")

    if group_id and str(group_id) not in ("0", "", "None"):
        try:
            await bot.send_message(chat_id=group_id, text=group_text, parse_mode="HTML")
            results.append("✅ Guruhga xabar yuborildi")
        except Exception as e:
            logger.warning(f"notify group {group_id}: {e}")
            results.append("⚠️ Guruhga xabar yuborilmadi")

    msg_id = order.get('group_message_id')
    if msg_id and group_id and str(group_id) not in ("0", "", "None"):
        try:
            await bot.delete_message(chat_id=group_id, message_id=int(msg_id))
        except Exception:
            pass

    return results, car_number


@router.callback_query(F.data.startswith("adm_confirm_cancel_"))
async def confirm_cancel_order(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()

    order_id = callback.data[len("adm_confirm_cancel_"):]
    from core.db import cancel_order_in_db
    from core.sheets import cancel_order_in_sheets, remove_order_from_driver_sheet

    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.message.answer(f"❌ #{order_id} buyurtmasi topilmadi.")
        await state.clear()
        return

    try:
        await callback.message.edit_text(f"⏳ #{order_id} bekor qilinmoqda...")
    except Exception:
        pass

    results = []

    ok_db = await asyncio.to_thread(cancel_order_in_db, order_id)
    results.append(f"{'✅' if ok_db else '⚠️'} Ma'lumotlar bazasi")

    ok_sheet = await asyncio.to_thread(cancel_order_in_sheets, order_id)
    results.append(f"{'✅' if ok_sheet else '⚠️'} Google Sheets (buyurtmalar)")

    car_number = order.get('car_number', '')
    if car_number:
        ok_drv = await asyncio.to_thread(remove_order_from_driver_sheet, car_number, order_id)
        results.append(f"{'✅' if ok_drv else '⚠️'} Google Sheets (haydovchi)")

    notif_results, _ = await _notify_cancel_finish(bot, order, order_id, 'cancel')
    results += notif_results

    await state.clear()
    results_text = "\n".join(results)
    done_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Yana bekor qilish", callback_data="adm_cancel_order")],
        [InlineKeyboardButton(text="🔙 Admin panel",       callback_data="adm_back")],
    ])
    try:
        await callback.message.edit_text(
            f"✅ <b>#{_e(order_id)} bekor qilindi!</b>\n\n{results_text}",
            parse_mode="HTML", reply_markup=done_kb
        )
    except Exception:
        await callback.message.answer(
            f"✅ #{order_id} bekor qilindi.\n{results_text}", reply_markup=done_kb
        )


@router.callback_query(F.data.startswith("adm_confirm_finish_"))
async def confirm_finish_order(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()

    order_id = callback.data[len("adm_confirm_finish_"):]
    from core.sheets import remove_order_from_driver_sheet, update_order_status_by_order_id

    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.message.answer(f"❌ #{order_id} buyurtmasi topilmadi.")
        await state.clear()
        return

    try:
        await callback.message.edit_text(f"⏳ #{order_id} yakunlanmoqda...")
    except Exception:
        pass

    now_iso = datetime.now(tz).isoformat()
    results = []

    await asyncio.to_thread(update_order, order_id, {
        'current_status': 'YAKUNLANDI',
        'completed_at':   now_iso,
    })
    results.append("✅ Ma'lumotlar bazasi")

    await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI')
    results.append("✅ Google Sheets (buyurtmalar)")

    car_number = order.get('car_number', '')
    if car_number:
        ok_drv = await asyncio.to_thread(remove_order_from_driver_sheet, car_number, order_id)
        results.append(f"{'✅' if ok_drv else '⚠️'} Google Sheets (haydovchi)")

    notif_results, _ = await _notify_cancel_finish(bot, order, order_id, 'finish')
    results += notif_results

    await state.clear()
    results_text = "\n".join(results)
    done_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish",  callback_data="adm_cancel_order")],
        [InlineKeyboardButton(text="🔙 Admin panel",   callback_data="adm_back")],
    ])
    try:
        await callback.message.edit_text(
            f"✅ <b>#{_e(order_id)} yakunlandi!</b>\n\n{results_text}",
            parse_mode="HTML", reply_markup=done_kb
        )
    except Exception:
        await callback.message.answer(
            f"✅ #{order_id} yakunlandi.\n{results_text}", reply_markup=done_kb
        )


@router.callback_query(F.data == "adm_reset_all")
async def start_reset_all(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, hammasini yop", callback_data="adm_reset_all_confirm")],
        [InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="adm_back")],
    ])
    text = (
        "⚠️ <b>Diqqat! To'liq reset qilinadi:</b>\n\n"
        "• Bazadagi barcha yakunlanmagan buyurtmalar <b>ESKI_YOPILDI</b> deb belgilanadi\n"
        "• Barcha filial sheetlaridagi <b>SEND</b> holatidagi (hali yuborilmagan) qatorlar yopiladi\n"
        "• Haydovchilar jadvalidagi band/yuklangan statuslar tozalanadi (hammasi <b>BO'SH</b>)\n\n"
        "Haydovchilarga hech qanday xabar yuborilmaydi (ular hech narsa qabul qilmagan). "
        "Bu amalni ortga qaytarib bo'lmaydi. Davom etasizmi?"
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=confirm_kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=confirm_kb)


@router.callback_query(F.data == "adm_reset_all_confirm")
async def confirm_reset_all(callback: CallbackQuery, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()

    try:
        await callback.message.edit_text("⏳ Reset qilinmoqda... (bir necha soniya)")
    except Exception:
        pass

    from core.db import bulk_close_active_orders
    from core.sheets import bulk_close_stuck_orders_in_sheets, bulk_reset_drivers_sheet
    import core.scheduler as scheduler_mod

    db_count       = await asyncio.to_thread(bulk_close_active_orders)
    sheet_results  = await asyncio.to_thread(bulk_close_stuck_orders_in_sheets)
    drivers_count  = await asyncio.to_thread(bulk_reset_drivers_sheet)

    # Forget everything in memory too, so nothing lingers from before the reset.
    scheduler_mod.PROCESSED_ORDERS.clear()
    scheduler_mod.REMINDER_SENT.clear()

    sheet_total = sum(sheet_results.values())
    sheet_lines = "\n".join(f"   • {_e(name)}: {n} ta" for name, n in sheet_results.items()) or "   —"

    summary = (
        "✅ <b>To'liq reset yakunlandi!</b>\n\n"
        f"🗄 Bazada yopildi: <b>{db_count} ta</b> buyurtma\n"
        f"📋 Sheetlarda yopildi: <b>{sheet_total} ta</b>\n{sheet_lines}\n"
        f"🚗 Haydovchilar tozalandi: <b>{drivers_count} ta</b>\n\n"
        f"👤 Bajardi: {_e(callback.from_user.full_name)}\n\n"
        "Tizim endi yangidan boshlaydi — bundan buyon kelgan yangi buyurtmalar "
        "odatdagidek qayta ishlanadi."
    )

    done_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin panel", callback_data="adm_back")],
    ])
    try:
        await callback.message.edit_text(summary, parse_mode="HTML", reply_markup=done_kb)
    except Exception:
        await callback.message.answer(summary, parse_mode="HTML", reply_markup=done_kb)

    # Bot itself announces the reset to every other admin too, not just whoever clicked.
    for admin_id in ADMIN_IDS:
        if admin_id == callback.from_user.id:
            continue
        try:
            await bot.send_message(chat_id=admin_id, text=summary, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"reset_all: failed to notify admin {admin_id}: {e}")


# ─── Stale callbacks ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_"))
async def stale_admin_callback(callback: CallbackQuery):
    await callback.answer(
        "Bu amal eskirgan. /admin buyrug'i bilan panelni qayta oching.",
        show_alert=True
    )
