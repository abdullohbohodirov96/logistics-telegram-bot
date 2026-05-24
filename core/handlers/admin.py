import logging
import time
import asyncio
from datetime import datetime, timedelta
import pytz

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import ADMIN_IDS, TIMEZONE
from core.db import get_history, get_active_orders, get_orders_by_date_range
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

async def _safe_send(message, text: str, reply_markup=None, parse_mode="Markdown"):
    """Send text, splitting into chunks if it exceeds Telegram's 4096 char limit."""
    limit = 4000
    chunks = [text[i:i+limit] for i in range(0, len(text), limit)]
    for idx, chunk in enumerate(chunks):
        kb = reply_markup if idx == len(chunks) - 1 else None
        try:
            if idx == 0:
                try:
                    await message.edit_text(chunk, parse_mode=parse_mode, reply_markup=kb)
                except Exception:
                    await message.answer(chunk, parse_mode=parse_mode, reply_markup=kb)
            else:
                await message.answer(chunk, parse_mode=parse_mode, reply_markup=kb)
        except Exception:
            await message.answer(chunk, reply_markup=kb)

def get_admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Aktiv buyurtmalar",     callback_data="adm_active")],
        [InlineKeyboardButton(text="📊 Bugungi hisobot",       callback_data="adm_today")],
        [InlineKeyboardButton(text="📊 Kechagi hisobot",       callback_data="adm_yesterday")],
        [InlineKeyboardButton(text="📊 Oxirgi 7 kun",          callback_data="adm_7days")],
        [InlineKeyboardButton(text="📊 Oxirgi 30 kun",         callback_data="adm_30days")],
        [InlineKeyboardButton(text="📅 Sana tanlash",          callback_data="adm_manual_date")],
        [InlineKeyboardButton(text="🚗 Mashina bo'yicha",      callback_data="adm_by_car")],
        [InlineKeyboardButton(text="👤 Haydovchi bo'yicha",    callback_data="adm_by_driver")],
        [InlineKeyboardButton(text="🏆 Umumiy reyting",        callback_data="adm_rating")],
        [InlineKeyboardButton(text="🚛 Haydovchilar holati",   callback_data="adm_cars_status")],
        [InlineKeyboardButton(text="📣 Guruhga hisobot yuborish", callback_data="adm_send_group_report")],
        [InlineKeyboardButton(text="📥 Menga hisobot yuborish",  callback_data="adm_send_me_report")],
        [InlineKeyboardButton(text="❌ Yopish",                 callback_data="adm_close")],
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
    active = [o for o in orders if o.get("current_status") not in ("YAKUNLANDI",) and not o.get("current_status", "").startswith("ERROR")]
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
        f"❌ Xatoliklar: *{len(errors)}*",
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
    lines = [f"📋 *Aktiv buyurtmalar: {len(orders)} ta*\n"]
    for o in orders[:20]:
        acc = parse_dt(o.get("accepted_at"))
        acc_str = acc.strftime("%H:%M") if acc else "?"
        lines.append(
            f"🔹 #{o['order_id']} | {o.get('car_number','-')} | {o.get('driver_name','-')}\n"
            f"   📍 {o.get('address','-')}\n"
            f"   ⏰ Qabul: {acc_str} | Holat: {o.get('current_status','-')}"
        )
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
        await _safe_send(callback.message, text, reply_markup=_back_kb())
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
        await _safe_send(callback.message, text, reply_markup=_back_kb())
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
        await _safe_send(callback.message, text, reply_markup=_back_kb())
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

        await _safe_send(callback.message, text, reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"adm_rating error: {e}")
        await callback.message.answer("⚠️ Xatolik.", reply_markup=_back_kb())

# ─── Send group report manually ───────────────────────────────────────────────

@router.callback_query(F.data == "adm_send_group_report")
async def send_group_report(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    await callback.answer("⏳ Hisobot tayyorlanmoqda...")
    try:
        from core.scheduler import _build_daily_report_text
        from core.db import get_orders_by_date_range, get_active_orders
        from core.config import BRANCHES, TIMEZONE
        import pytz
        from datetime import datetime

        tzinfo = pytz.timezone(TIMEZONE)
        now = datetime.now(tzinfo)
        date_str = now.strftime('%d.%m.%Y')
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt   = now.replace(hour=23, minute=59, second=59, microsecond=0)

        done_orders   = await asyncio.to_thread(get_orders_by_date_range, start_dt.isoformat(), end_dt.isoformat())
        all_active    = await asyncio.to_thread(get_active_orders)
        active_orders = [o for o in all_active if o.get('current_status') not in ('YAKUNLANDI', 'NEW')]

        report_text = _build_daily_report_text(date_str, done_orders, active_orders, now)

        sent_groups = set()
        sent_names  = []
        for branch_name, branch_cfg in BRANCHES.items():
            group_id = branch_cfg.get("group_id")
            if not group_id or group_id in sent_groups:
                continue
            try:
                await callback.bot.send_message(
                    chat_id=group_id,
                    text=report_text,
                    parse_mode="Markdown"
                )
                sent_groups.add(group_id)
                sent_names.append(branch_name)
            except Exception as e:
                logger.error(f"adm_send_group_report: failed to send to {branch_name}: {e}")

        if sent_names:
            await callback.message.answer(
                f"✅ Hisobot yuborildi: {', '.join(sent_names)}",
                reply_markup=_back_kb()
            )
        else:
            await callback.message.answer(
                "⚠️ Hech qaysi guruhga yuborib bo'lmadi. Guruh ID larini tekshiring.",
                reply_markup=_back_kb()
            )
    except Exception as e:
        logger.error(f"adm_send_group_report error: {e}")
        await callback.message.answer("⚠️ Xatolik yuz berdi.", reply_markup=_back_kb())


# ─── Send report to admin's own chat ──────────────────────────────────────────

async def _get_today_report_text() -> str:
    """Build today's full report text (reusable)."""
    from core.scheduler import _build_daily_report_text
    from core.db import get_orders_by_date_range, get_active_orders
    from core.config import TIMEZONE
    import pytz
    from datetime import datetime
    import asyncio as _asyncio

    tzinfo = pytz.timezone(TIMEZONE)
    now = datetime.now(tzinfo)
    date_str = now.strftime('%d.%m.%Y')
    start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt   = now.replace(hour=23, minute=59, second=59, microsecond=0)

    done_orders   = await _asyncio.to_thread(get_orders_by_date_range, start_dt.isoformat(), end_dt.isoformat())
    all_active    = await _asyncio.to_thread(get_active_orders)
    active_orders = [o for o in all_active if o.get('current_status') not in ('YAKUNLANDI', 'NEW')]
    return _build_daily_report_text(date_str, done_orders, active_orders, now)


@router.callback_query(F.data == "adm_send_me_report")
async def send_me_report(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    await callback.answer("⏳ Hisobot tayyorlanmoqda...")
    try:
        text = await _get_today_report_text()
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=chunk,
                parse_mode="Markdown"
            )
        await callback.message.answer("✅ Hisobot shaxsiy chatga yuborildi.", reply_markup=_back_kb())
    except Exception as e:
        logger.error(f"adm_send_me_report error: {e}")
        await callback.message.answer("⚠️ Xatolik yuz berdi.", reply_markup=_back_kb())


@router.message(Command("hisobot"))
async def cmd_hisobot(message: Message):
    """Admin-only: get today's full report in private chat."""
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Bu buyruq faqat adminlar uchun.")
        return
    try:
        text = await _get_today_report_text()
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await message.answer(chunk, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"/hisobot error: {e}")
        await message.answer("⚠️ Hisobot olishda xatolik yuz berdi.")


# ─── Stale/unknown callbacks ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm_"))
async def stale_admin_callback(callback: CallbackQuery):
    await callback.answer(
        "Bu amal eskirgan. Iltimos, /admin buyrug'i bilan panelni qayta oching.",
        show_alert=True
    )
