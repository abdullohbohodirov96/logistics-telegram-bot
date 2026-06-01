import logging
import time
import asyncio
from datetime import datetime, timedelta
import pytz

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import ADMIN_IDS, TIMEZONE
from core.db import get_history, get_active_orders, get_orders_by_date_range, get_order, update_order
from core.states import AdminProcess
from core.handlers.history import format_delivery_short
from core.utils import format_duration_detailed, get_seconds_diff, parse_dt

router = Router()
logger = logging.getLogger(__name__)
tz = pytz.timezone(TIMEZONE)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def _back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

def get_admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Aktiv buyurtmalar",       callback_data="adm_active")],
        [InlineKeyboardButton(text="❌ Buyurtmani bekor qilish",  callback_data="adm_cancel_order")],
        [InlineKeyboardButton(text="📊 Bugungi hisobot",         callback_data="adm_today")],
        [InlineKeyboardButton(text="📊 Kechagi hisobot",         callback_data="adm_yesterday")],
        [InlineKeyboardButton(text="📊 Oxirgi 7 kun",            callback_data="adm_7days")],
        [InlineKeyboardButton(text="📊 Oxirgi 30 kun",           callback_data="adm_30days")],
        [InlineKeyboardButton(text="📅 Sana tanlash",            callback_data="adm_manual_date")],
        [InlineKeyboardButton(text="🚗 Mashina bo'yicha",        callback_data="adm_by_car")],
        [InlineKeyboardButton(text="👤 Haydovchi bo'yicha",      callback_data="adm_by_driver")],
        [InlineKeyboardButton(text="🏆 Umumiy reyting",          callback_data="adm_rating")],
        [InlineKeyboardButton(text="🚛 Haydovchilar holati",     callback_data="adm_cars_status")],
        [InlineKeyboardButton(text="❌ Yopish",                  callback_data="adm_close")],
    ])

def _date_range(now, kind: str):
    """Return (start_iso, end_iso) for a named range."""
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
    """Parse user-entered date. Returns (start_iso, end_iso) or raises ValueError."""
    text = text.strip().lower()

    now = datetime.now(tzinfo)

    if text == "bugun":
        s = now.replace(hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return s.isoformat(), e.isoformat()

    if text == "kecha":
        y = now - timedelta(days=1)
        s = y.replace(hour=0, minute=0, second=0, microsecond=0)
        e = y.replace(hour=23, minute=59, second=59, microsecond=0)
        return s.isoformat(), e.isoformat()

    if text in ("7 kun", "7kun"):
        s = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return s.isoformat(), e.isoformat()

    if text in ("30 kun", "30kun"):
        s = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        e = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return s.isoformat(), e.isoformat()

    # Try range: "03.05.2026 - 05.05.2026"
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

    # Single date
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(text.strip(), fmt)
            s_dt = tzinfo.localize(dt).replace(hour=0, minute=0, second=0)
            e_dt = tzinfo.localize(dt).replace(hour=23, minute=59, second=59)
            return s_dt.isoformat(), e_dt.isoformat()
        except Exception:
            continue

    raise ValueError("bad_date")


def _build_summary_report(orders: list, label: str) -> str:
    """Build a full stats report for a list of orders."""
    total = len(orders)
    done = [o for o in orders if o.get("current_status") == "YAKUNLANDI"]
    cancelled = [o for o in orders if o.get("current_status") == "BEKOR_QILINDI"]
    active = [o for o in orders if o.get("current_status") not in ("YAKUNLANDI", "BEKOR_QILINDI") and not o.get("current_status", "").startswith("ERROR")]
    errors = [o for o in orders if o.get("current_status", "").startswith("ERROR")]

    # Per-driver stats
    drv_stats = {}
    for o in done:
        tid = o.get("driver_telegram_id", "noname")
        name = o.get("driver_name", "-")
        car = o.get("car_number", "-")
        if tid not in drv_stats:
            drv_stats[tid] = {"name": name, "car": car, "count": 0, "total_sec": 0}
        drv_stats[tid]["count"] += 1
        diff = get_seconds_diff(o.get("accepted_at"), o.get("completed_at") or o.get("finished_at"))
        if diff:
            drv_stats[tid]["total_sec"] += diff

    ranking = sorted(drv_stats.values(), key=lambda x: x["count"], reverse=True)

    lines = [
        f"📊 *Hisobot: {label}*",
        f"",
        f"📦 Jami buyurtmalar: *{total}*",
        f"✅ Yakunlangan: *{len(done)}*",
        f"🚚 Yo'lda / Aktiv: *{len(active)}*",
        f"❌ Bekor qilingan: *{len(cancelled)}*",
        f"⚠️ Xatoliklar: *{len(errors)}*",
    ]

    if ranking:
        lines.append("")
        lines.append("👥 *Haydovchilar kesimi:*")
        medals = ["🥇", "🥈", "🥉"]
        for i, s in enumerate(ranking[:10]):
            avg = int(s["total_sec"] / s["count"] / 60) if s["count"] > 0 else 0
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{medal} {s['car']} — {s['name']} — {s['count']} reys (o'rtacha {avg} min)")

    return "\n".join(lines)


def _build_active_text(orders: list) -> str:
    if not orders:
        return "✅ Hozir aktiv buyurtmalar yo'q."
    now = datetime.now(tz)
    lines = [f"📋 *Aktiv buyurtmalar: {len(orders)} ta*\n"]
    for o in orders[:20]:
        acc = parse_dt(o.get("accepted_at"))
        acc_str = acc.strftime("%H:%M") if acc else "Qabul qilinmagan"
        elapsed = ""
        if acc:
            mins = int((now - acc).total_seconds() / 60)
            elapsed = f" ({mins} dq)"
        status = o.get('current_status', '-')
        status_icons = {
            'SENT': '📤', 'QABUL_QILINDI': '📥', 'YOLDA': '🛣', 'YAKUNLANDI': '✅',
        }
        icon = status_icons.get(status, '🔄')
        lines.append(
            f"🔹 *#{o['order_id']}* | {o.get('car_number','-')} | {o.get('driver_name','-')}\n"
            f"   📍 {o.get('address','-')}\n"
            f"   ⏰ {acc_str}{elapsed} | {icon} {status}"
        )
    lines.append("\n💡 Bekor qilish uchun: *❌ Buyurtmani bekor qilish* ni bosing")
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
        "🛠 *Admin panel*\nQuyidagi bo'limlardan birini tanlang:",
        reply_markup=get_admin_panel_kb(),
        parse_mode="Markdown"
    )

# ─── Back / close ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_back")
async def back_to_admin(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_text(
            "🛠 *Admin panel*\nQuyidagi bo'limlardan birini tanlang:",
            reply_markup=get_admin_panel_kb(),
            parse_mode="Markdown"
        )
    except Exception:
        await callback.message.answer(
            "🛠 *Admin panel*\nQuyidagi bo'limlardan birini tanlang:",
            reply_markup=get_admin_panel_kb(),
            parse_mode="Markdown"
        )

@router.callback_query(F.data == "adm_close")
async def close_admin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    try: await callback.message.delete()
    except: pass

@router.callback_query(F.data == "adm_refresh")
async def refresh_panel(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer("✅ Yangilandi")
    await state.clear()
    try:
        await callback.message.edit_text(
            "🛠 *Admin panel*\nQuyidagi bo'limlardan birini tanlang:",
            reply_markup=get_admin_panel_kb(),
            parse_mode="Markdown"
        )
    except: pass

# ─── Active orders ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_active")
async def show_active(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    try:
        orders = await asyncio.to_thread(get_active_orders)
        text = _build_active_text(orders)
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=_back_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"adm_active error: {e}")
        await callback.message.answer("⚠️ Xatolik yuz berdi.", reply_markup=_back_kb())

# ─── Date-range reports ────────────────────────────────────────────────────────

async def _send_date_report(callback: CallbackQuery, kind: str, label: str):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    try:
        now = datetime.now(tz)
        df, dt = _date_range(now, kind)
        orders = await asyncio.to_thread(get_history, "all", None, df, dt, limit=200)
        text = _build_summary_report(orders, label)
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=_back_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"date report ({kind}) error: {e}")
        await callback.message.answer("⚠️ Xatolik yuz berdi.", reply_markup=_back_kb())

@router.callback_query(F.data == "adm_today")
async def report_today(callback: CallbackQuery):
    await _send_date_report(callback, "today", "Bugun")

@router.callback_query(F.data == "adm_yesterday")
async def report_yesterday(callback: CallbackQuery):
    await _send_date_report(callback, "yesterday", "Kecha")

@router.callback_query(F.data == "adm_7days")
async def report_7days(callback: CallbackQuery):
    await _send_date_report(callback, "7days", "Oxirgi 7 kun")

@router.callback_query(F.data == "adm_30days")
async def report_30days(callback: CallbackQuery):
    await _send_date_report(callback, "30days", "Oxirgi 30 kun")

# ─── Manual date input ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_manual_date")
async def ask_manual_date(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminProcess.waiting_for_manual_date)
    await state.update_data(filter_type="all", filter_val=None)
    try:
        await callback.message.edit_text(
            "📅 *Sana kiriting:*\n\n"
            "Formatlar:\n"
            "• `bugun` yoki `kecha`\n"
            "• `7 kun` yoki `30 kun`\n"
            "• `2026-05-15`\n"
            "• `15.05.2026`\n"
            "• `01.05.2026 - 15.05.2026` (oraliq)",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer(
            "📅 Sana kiriting (masalan: `2026-05-15` yoki `bugun`):",
            parse_mode="Markdown"
        )

@router.message(AdminProcess.waiting_for_manual_date, F.text)
async def process_manual_date(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    try:
        df, dt = _parse_manual_date(message.text, tz)
    except ValueError:
        await message.answer(
            "❌ Sana noto'g'ri kiritildi.\n"
            "Masalan: `2026-05-15` yoki `15.05.2026` yoki `bugun`",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    ft = data.get("filter_type", "all")
    fv = data.get("filter_val")
    await state.clear()

    try:
        orders = await asyncio.to_thread(get_history, ft, fv, df, dt, limit=200)
        d1 = datetime.fromisoformat(df).strftime("%d.%m.%Y")
        d2 = datetime.fromisoformat(dt).strftime("%d.%m.%Y")
        label = f"{d1} — {d2}" if d1 != d2 else d1
        if fv:
            label += f" | {ft}: {fv}"
        text = _build_summary_report(orders, label)
        await message.answer(text, parse_mode="Markdown", reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"manual date report error: {e}")
        await message.answer("⚠️ Xatolik yuz berdi.", reply_markup=_back_kb())

# ─── By car ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_by_car")
async def show_cars(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    try:
        from core.sheets import get_all_cars_list
        cars = await asyncio.to_thread(get_all_cars_list)
        if not cars:
            await callback.message.edit_text("🚗 Mashinalar topilmadi.", reply_markup=_back_kb())
            return
        builder = InlineKeyboardBuilder()
        for car in cars:
            builder.button(text=car, callback_data=f"adm_car_{car[:20]}")
        builder.adjust(2)
        builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back"))
        await callback.message.edit_text("🚗 Qaysi mashina bo'yicha hisobot?", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"adm_by_car: {e}")
        await callback.message.answer("⚠️ Xatolik.", reply_markup=_back_kb())

@router.callback_query(F.data.startswith("adm_car_"))
async def select_car(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    car = callback.data[len("adm_car_"):]
    await state.update_data(filter_type="car", filter_val=car)
    await state.set_state(AdminProcess.waiting_for_manual_date)
    try:
        await callback.message.edit_text(
            f"🚗 *{car}* mashinasi uchun sana kiriting:\n\n"
            "Formatlar: `bugun`, `kecha`, `7 kun`, `2026-05-15`",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer(f"🚗 {car} uchun sana kiriting:")

# ─── By driver ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_by_driver")
async def show_drivers(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    try:
        from core.sheets import get_all_drivers_list
        drivers = await asyncio.to_thread(get_all_drivers_list)
        if not drivers:
            await callback.message.edit_text("👤 Haydovchilar topilmadi.", reply_markup=_back_kb())
            return
        builder = InlineKeyboardBuilder()
        for name, tid in drivers:
            btn_text = name[:20] if name else tid
            builder.button(text=btn_text, callback_data=f"adm_drv_{str(tid)[:15]}")
        builder.adjust(1)
        builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back"))
        await callback.message.edit_text("👤 Qaysi haydovchi bo'yicha hisobot?", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"adm_by_driver: {e}")
        await callback.message.answer("⚠️ Xatolik.", reply_markup=_back_kb())

@router.callback_query(F.data.startswith("adm_drv_"))
async def select_driver(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    tid = callback.data[len("adm_drv_"):]
    await state.update_data(filter_type="drv", filter_val=tid)
    await state.set_state(AdminProcess.waiting_for_manual_date)
    try:
        await callback.message.edit_text(
            f"👤 Haydovchi (ID: {tid}) uchun sana kiriting:\n\n"
            "Formatlar: `bugun`, `kecha`, `7 kun`, `2026-05-15`",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer(f"Haydovchi uchun sana kiriting:")

# ─── Drivers real-time status ──────────────────────────────────────────────────

@router.callback_query(F.data == "adm_cars_status")
async def show_cars_status(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    try:
        from core.db import get_drivers_admin_stats
        data = await asyncio.to_thread(get_drivers_admin_stats)
        if not data:
            await callback.message.edit_text("⚠️ Ma'lumot olinmadi.", reply_markup=_back_kb())
            return
        lines = ["🚛 *Haydovchilar holati (Real-time):*\n"]
        for d in data:
            icon = "🔴" if d['active_count'] >= 3 else ("🟡" if d['active_count'] > 0 else "🟢")
            lines.append(
                f"{icon} *{d['car_number']}* — {d['driver_name']}\n"
                f"   📦 Aktiv: {d['active_count']} | Bugun: {d['today_count']} | Jami: {d['total_count']}"
            )
        text = "\n".join(lines)
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=_back_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"adm_cars_status error: {e}")
        await callback.message.answer("⚠️ Xatolik.", reply_markup=_back_kb())

# ─── Rating ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_rating")
async def show_rating(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    try:
        now = datetime.now(tz)
        df, dt = _date_range(now, "today")
        orders = await asyncio.to_thread(get_orders_by_date_range, df, dt)

        drv_stats = {}
        for o in orders:
            tid = o.get("driver_telegram_id", "?")
            if tid not in drv_stats:
                drv_stats[tid] = {
                    "name": o.get("driver_name", "-"),
                    "car": o.get("car_number", "-"),
                    "count": 0, "total_sec": 0
                }
            drv_stats[tid]["count"] += 1
            diff = get_seconds_diff(o.get("accepted_at"), o.get("completed_at") or o.get("finished_at"))
            if diff:
                drv_stats[tid]["total_sec"] += diff

        ranking = sorted(drv_stats.values(), key=lambda x: x["count"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        date_str = now.strftime("%d.%m.%Y")

        if not ranking:
            text = f"📉 *Bugun ({date_str}) yakunlangan reyslar yo'q.*"
        else:
            lines = [f"🏆 *Kunlik reyting ({date_str})*\n"]
            for i, s in enumerate(ranking):
                avg = int(s["total_sec"] / s["count"] / 60) if s["count"] > 0 else 0
                medal = medals[i] if i < 3 else f"{i+1}."
                lines.append(f"{medal} *{s['car']}* — {s['name']} — {s['count']} reys (avg {avg} min)")
            text = "\n".join(lines)

        try:
            await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=_back_kb())
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"adm_rating error: {e}")
        await callback.message.answer("⚠️ Xatolik.", reply_markup=_back_kb())

# ─── Order cancellation flow ────────────────────────────────────────────────────

@router.callback_query(F.data == "adm_cancel_order")
async def start_cancel_order(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()
    await state.set_state(AdminProcess.waiting_for_order_id)
    await state.update_data(cancel_mode=True)
    try:
        await callback.message.edit_text(
            "❌ *Buyurtmani bekor qilish*\n\n"
            "Bekor qilmoqchi bo'lgan buyurtma *ID* sini kiriting:\n\n"
            "Masalan: `P14095` yoki `S32013`\n\n"
            "📋 Aktiv buyurtmalar ro'yxatini ko'rish uchun «Aktiv buyurtmalar» bo'limiga kiring.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
    except Exception:
        await callback.message.answer(
            "❌ Bekor qilinadigan buyurtma ID sini kiriting:",
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
            f"❌ *Buyurtma topilmadi:* `{order_id}`\n\n"
            f"ID to'g'ri ekanligini tekshiring va qaytadan kiriting.\n"
            f"Yoki /admin dan Aktiv buyurtmalar bo'limiga kiring.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        return

    status = order.get('current_status', '-')
    if status == 'BEKOR_QILINDI':
        await message.answer(
            f"⚠️ #{order_id} allaqachon *BEKOR QILINGAN*.",
            parse_mode="Markdown",
            reply_markup=_back_kb()
        )
        await state.clear()
        return

    acc = parse_dt(order.get('accepted_at'))
    sent_at = parse_dt(order.get('created_at'))
    acc_str = acc.strftime('%d.%m.%Y %H:%M') if acc else '—'
    sent_str = sent_at.strftime('%d.%m.%Y %H:%M') if sent_at else '—'

    text = (
        f"📋 *Buyurtma ma'lumotlari:*\n\n"
        f"🆔 ID: `{order.get('order_id')}`\n"
        f"👤 Haydovchi: {order.get('driver_name', '—')}\n"
        f"🚗 Mashina: {order.get('car_number', '—')}\n"
        f"📍 Manzil: {order.get('address', '—')}\n"
        f"📦 Yuk: {order.get('cargo', '—')}\n"
        f"📊 Holat: *{status}*\n"
        f"📤 Yuborilgan: {sent_str}\n"
        f"✅ Qabul: {acc_str}\n\n"
        f"Quyidan amal tanlang:"
    )

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"adm_confirm_cancel_{order_id}"),
            InlineKeyboardButton(text="✅ Yakunlash", callback_data=f"adm_confirm_finish_{order_id}"),
        ],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

    await state.update_data(cancel_order_id=order_id)
    await message.answer(text, parse_mode="Markdown", reply_markup=confirm_kb)


@router.callback_query(F.data.startswith("adm_confirm_cancel_"))
async def confirm_cancel_order(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()

    order_id = callback.data[len("adm_confirm_cancel_"):]

    from core.db import cancel_order_in_db
    from core.sheets import cancel_order_in_sheets, remove_order_from_driver_sheet
    from core.config import BRANCHES, GROUP_CHAT_ID

    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.message.answer("❌ Buyurtma topilmadi.")
        await state.clear()
        return

    try:
        await callback.message.edit_text(f"⏳ #{order_id} bekor qilinmoqda...")
    except Exception:
        pass

    car_number  = order.get('car_number', '')
    driver_tid  = order.get('driver_telegram_id')
    filial      = order.get('filial', '')
    address     = order.get('address', '-')
    driver_name = order.get('driver_name', '-')

    results = []

    # 1. Update DB
    ok_db = await asyncio.to_thread(cancel_order_in_db, order_id)
    results.append(f"{'✅' if ok_db else '⚠️'} Ma'lumotlar bazasi")

    # 2. Update orders sheet
    ok_sheet = await asyncio.to_thread(cancel_order_in_sheets, order_id)
    results.append(f"{'✅' if ok_sheet else '⚠️'} Google Sheets (buyurtmalar)")

    # 3. Remove from driver sheet
    if car_number:
        ok_driver_sheet = await asyncio.to_thread(remove_order_from_driver_sheet, car_number, order_id)
        results.append(f"{'✅' if ok_driver_sheet else '⚠️'} Google Sheets (haydovchi)")

    # 4. Notify driver
    if driver_tid:
        try:
            await bot.send_message(
                chat_id=driver_tid,
                text=(
                    f"❌ *Buyurtma bekor qilindi!*\n\n"
                    f"🆔 #{order_id} buyurtmangiz *admin tomonidan bekor qilindi*.\n"
                    f"📍 Manzil: {address}\n\n"
                    f"Yangi buyurtmalar uchun botni kuting."
                ),
                parse_mode="Markdown"
            )
            results.append("✅ Haydovchiga xabar")
        except Exception as e:
            logger.warning(f"Could not notify driver {driver_tid}: {e}")
            results.append("⚠️ Haydovchiga xabar (yuborilmadi)")

    # 5. Notify group
    group_id = GROUP_CHAT_ID
    if filial and filial in BRANCHES:
        group_id = BRANCHES[filial].get('group_id') or GROUP_CHAT_ID

    if group_id and str(group_id) != "0":
        try:
            await bot.send_message(
                chat_id=group_id,
                text=(
                    f"❌ *Buyurtma bekor qilindi!*\n\n"
                    f"🆔 #{order_id}\n"
                    f"👤 {driver_name} | 🚗 {car_number}\n"
                    f"📍 {address}\n\n"
                    f"📊 Admin tomonidan bekor qilindi."
                ),
                parse_mode="Markdown"
            )
            results.append("✅ Guruhga xabar")
        except Exception as e:
            logger.warning(f"Could not notify group {group_id}: {e}")
            results.append("⚠️ Guruhga xabar (yuborilmadi)")

    # 6. Delete group interim message
    msg_id = order.get('group_message_id')
    if msg_id and group_id and str(group_id) != "0":
        try:
            await bot.delete_message(chat_id=group_id, message_id=int(msg_id))
        except Exception:
            pass

    await state.clear()

    results_text = "\n".join(results)
    try:
        await callback.message.edit_text(
            f"✅ *#{order_id} buyurtmasi muvaffaqiyatli bekor qilindi!*\n\n"
            f"{results_text}\n\n"
            f"Boshqa buyurtma bekor qilish uchun yana ID kiriting.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Yana bekor qilish", callback_data="adm_cancel_order")],
                [InlineKeyboardButton(text="🔙 Admin panel", callback_data="adm_back")]
            ])
        )
    except Exception:
        await callback.message.answer(
            f"✅ #{order_id} bekor qilindi.\n{results_text}",
            reply_markup=_back_kb()
        )


@router.callback_query(F.data.startswith("adm_confirm_finish_"))
async def confirm_finish_order(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True); return
    await callback.answer()

    order_id = callback.data[len("adm_confirm_finish_"):]

    from core.sheets import cancel_order_in_sheets, remove_order_from_driver_sheet, update_order_status_by_order_id
    from core.config import BRANCHES, GROUP_CHAT_ID
    import datetime, pytz

    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.message.answer("❌ Buyurtma topilmadi.")
        await state.clear()
        return

    try:
        await callback.message.edit_text(f"⏳ #{order_id} yakunlanmoqda...")
    except Exception:
        pass

    car_number  = order.get('car_number', '')
    driver_tid  = order.get('driver_telegram_id')
    filial      = order.get('filial', '')
    address     = order.get('address', '-')
    driver_name = order.get('driver_name', '-')

    tz = pytz.timezone('Asia/Tashkent')
    now_iso = datetime.datetime.now(tz).isoformat()

    results = []

    # 1. Update DB to YAKUNLANDI
    await asyncio.to_thread(update_order, order_id, {
        'current_status': 'YAKUNLANDI',
        'completed_at': now_iso,
    })
    results.append("✅ Ma'lumotlar bazasi")

    # 2. Update orders sheet
    ok_sheet = await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI')
    results.append(f"✅ Google Sheets (buyurtmalar)")

    # 3. Remove from driver sheet
    if car_number:
        ok_driver = await asyncio.to_thread(remove_order_from_driver_sheet, car_number, order_id)
        results.append(f"{'✅' if ok_driver else '⚠️'} Google Sheets (haydovchi)")

    # 4. Notify driver
    if driver_tid:
        try:
            await bot.send_message(
                chat_id=driver_tid,
                text=(
                    f"✅ *Buyurtma yakunlandi!*\n\n"
                    f"🆔 #{order_id} buyurtmangiz *admin tomonidan yakunlandi*.\n"
                    f"📍 Manzil: {address}"
                ),
                parse_mode="Markdown"
            )
            results.append("✅ Haydovchiga xabar")
        except Exception as e:
            logger.warning(f"Could not notify driver {driver_tid}: {e}")
            results.append("⚠️ Haydovchiga xabar (yuborilmadi)")

    # 5. Notify group
    from core.scheduler import _ORDER_BRANCH
    group_id = GROUP_CHAT_ID
    branch = _ORDER_BRANCH.get(order_id) or filial
    if branch and branch in BRANCHES:
        group_id = BRANCHES[branch].get('group_id') or GROUP_CHAT_ID
    if group_id and str(group_id) != "0":
        try:
            await bot.send_message(
                chat_id=group_id,
                text=(
                    f"✅ *Buyurtma yakunlandi!*\n\n"
                    f"🆔 #{order_id}\n"
                    f"👤 {driver_name} | 🚗 {car_number}\n"
                    f"📍 {address}\n\n"
                    f"📊 Admin tomonidan yakunlandi."
                ),
                parse_mode="Markdown"
            )
            results.append("✅ Guruhga xabar")
        except Exception as e:
            logger.warning(f"Could not notify group {group_id}: {e}")
            results.append("⚠️ Guruhga xabar (yuborilmadi)")

    # 6. Delete group interim message
    msg_id = order.get('group_message_id')
    if msg_id and group_id and str(group_id) != "0":
        try:
            await bot.delete_message(chat_id=group_id, message_id=int(msg_id))
        except Exception:
            pass

    await state.clear()

    results_text = "\n".join(results)
    try:
        await callback.message.edit_text(
            f"✅ *#{order_id} yakunlandi!*\n\n"
            f"{results_text}\n\n"
            f"Boshqa buyurtma uchun yana ID kiriting.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="adm_cancel_order")],
                [InlineKeyboardButton(text="🔙 Admin panel", callback_data="adm_back")]
            ])
        )
    except Exception:
        await callback.message.answer(
            f"✅ #{order_id} yakunlandi.\n{results_text}",
            reply_markup=_back_kb()
        )


# ─── Stale/unknown callbacks ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_"))
async def stale_admin_callback(callback: CallbackQuery):
    await callback.answer(
        "Bu amal eskirgan. Iltimos, /admin buyrug'i bilan panelni qayta oching.",
        show_alert=True
    )
