import logging
import time
import asyncio
from datetime import datetime, timedelta
import pytz
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from core.config import ADMIN_IDS, TIMEZONE, is_sheets_configured
from core.db import get_history, get_active_orders
from core.cache import cache_get, cache_set
import core.keyboards as kb
from core.sheets import get_drivers_status_list, get_all_drivers_list, get_all_cars_list
from core.states import AdminProcess
from core.handlers.history import format_delivery_short

router = Router()
logger = logging.getLogger(__name__)
tz = pytz.timezone(TIMEZONE)

from aiogram.filters import Command

@router.message(Command("admin"))
@router.message(F.text == "/drivers")
@router.message(F.text == "🛠 Admin panel")
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        logger.warning(f"Admin access denied user_id: {message.from_user.id}")
        await message.answer("Siz admin emassiz.")
        return
    
    try:
        logger.info(f"Admin panel opened by user_id: {message.from_user.id}")
        
        if message.text == "/drivers":
            await show_cars_status_message(message)
            return

        await message.answer("🛠 Admin paneliga xush kelibsiz. Quyidagilardan birini tanlang:", reply_markup=kb.get_admin_panel_kb())
    except Exception as e:
        logger.error(f"Error opening admin panel: {e}")
        await message.answer("⚠️ Admin panelini ochishda xatolik yuz berdi.")

async def show_cars_status_message(message: Message):
    if not is_sheets_configured():
        await message.answer("❌ Google Sheets ulanmagan. Iltimos konfiguratsiyani tekshiring.")
        return

    t0 = time.time()
    try:
        data = await asyncio.to_thread(get_drivers_status_list)
    except Exception as e:
        logger.error(f"Admin data load error: {e}")
        data = None
        
    if not data:
        await message.answer("⚠️ Ma'lumot olinmadi.")
        return
        
    text = "🚛 **Haydovchilar holati:**\n\n"
    for d in data:
        status_icon = "🟢" if d['status'] == "BO'SH" else "🔴"
        text += f"{status_icon} **{d['car_number']}** — {d['driver_name']} — {d['status']}"
        if d['order_id']: text += f" — #{d['order_id']}"
        text += "\n"
    
    await message.answer(text, parse_mode="Markdown")
    logger.info(f"admin drivers status took {time.time()-t0:.2f}s")

@router.callback_query(F.data == "adm_cars_status")
async def show_cars_status_callback(callback: CallbackQuery):
    logger.info("Admin callback received: adm_cars_status")
    await callback.answer()
    await show_cars_status_message(callback.message)

@router.callback_query(F.data == "adm_refresh")
async def refresh_admin_panel(callback: CallbackQuery):
    logger.info("Admin callback received: adm_refresh")
    await callback.answer("Yangilandi ✅")
    try:
        await callback.message.edit_reply_markup(reply_markup=kb.get_admin_panel_kb())
    except:
        pass

@router.callback_query(F.data == "adm_close")
async def close_admin(callback: CallbackQuery):
    await callback.answer()
    await callback.message.delete()

@router.callback_query(F.data == "adm_back")
async def back_to_admin(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("🛠 Admin paneliga xush kelibsiz. Quyidagilardan birini tanlang:", reply_markup=kb.get_admin_panel_kb())

@router.callback_query(F.data == "adm_hist_car")
async def show_cars(callback: CallbackQuery):
    await callback.answer()
    cars = await asyncio.to_thread(get_all_cars_list)
    if not cars:
        await callback.message.edit_text("Mashinalar topilmadi.", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
        return
    await callback.message.edit_text("Qaysi mashina bo'yicha tarixni ko'rmoqchisiz?", reply_markup=kb.get_cars_kb(cars))

@router.callback_query(F.data == "adm_hist_drv")
async def show_drivers(callback: CallbackQuery):
    await callback.answer()
    drivers = await asyncio.to_thread(get_all_drivers_list)
    if not drivers:
        await callback.message.edit_text("Haydovchilar topilmadi.", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
        return
    drivers_dict = {tid: name for name, tid in drivers}
    await callback.message.edit_text("Qaysi haydovchi bo'yicha tarixni ko'rmoqchisiz?", reply_markup=kb.get_drivers_kb(drivers_dict))

@router.callback_query(F.data == "adm_hist_all")
async def show_all_history_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(filter_type='all', filter_val='0')
    await callback.message.edit_text("Sana oralig'ini tanlang:", reply_markup=kb.get_date_range_kb())

@router.callback_query(F.data.startswith("car_"))
async def select_car_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    car = callback.data.split("car_")[1]
    await state.update_data(filter_type='car', filter_val=car)
    await callback.message.edit_text(f"🚗 {car} mashinasi sana oralig'ini tanlang:", reply_markup=kb.get_date_range_kb())

@router.callback_query(F.data.startswith("drv_"))
async def select_drv_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tid = callback.data.split("drv_")[1]
    await state.update_data(filter_type='drv', filter_val=tid)
    await callback.message.edit_text(f"👤 Haydovchi sana oralig'ini tanlang:", reply_markup=kb.get_date_range_kb())

@router.callback_query(F.data.startswith("dt_"))
async def handle_date_range(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    range_type = callback.data.split("dt_")[1]
    now = datetime.now(tz)
    df, dt = None, None
    if range_type == 'today':
        df = now.replace(hour=0, minute=0, second=0).isoformat()
        dt = now.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == 'yesterday':
        y = now - timedelta(days=1)
        df = y.replace(hour=0, minute=0, second=0).isoformat()
        dt = y.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == '7days':
        df = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0).isoformat()
        dt = now.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == '30days':
        df = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0).isoformat()
        dt = now.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == 'manual':
        await state.set_state(AdminProcess.waiting_for_manual_date)
        await callback.message.edit_text("Format: `03.05.2026 - 05.05.2026`", parse_mode="Markdown")
        return
    await state.update_data(date_from=df, date_to=dt)
    await show_history_results(callback.message, state, 1)

@router.message(AdminProcess.waiting_for_manual_date, F.text)
async def process_manual_date(message: Message, state: FSMContext):
    try:
        s, e = message.text.split("-")
        df = tz.localize(datetime.strptime(s.strip(), "%d.%m.%Y").replace(hour=0, minute=0, second=0)).isoformat()
        dt = tz.localize(datetime.strptime(e.strip(), "%d.%m.%Y").replace(hour=23, minute=59, second=59)).isoformat()
        await state.update_data(date_from=df, date_to=dt)
        await show_history_results(message, state, 1, edit=False)
        await state.set_state(None)
    except: await message.answer("Xato format. Qayta urinib ko'ring.")

async def show_history_results(message: Message, state: FSMContext, page: int, edit=True):
    t0 = time.time()
    data = await state.get_data()
    ft, fv = data.get('filter_type', 'all'), data.get('filter_val')
    df, dt = data.get('date_from'), data.get('date_to')
    
    history = await asyncio.to_thread(get_history, ft, fv, df, dt, limit=50)
    if not history:
        msg = "Tarix yo'q."
        if edit: await message.edit_text(msg, reply_markup=kb.get_pagination_kb(1, 1))
        else: await message.answer(msg, reply_markup=kb.get_pagination_kb(1, 1))
        return

    tp = (len(history) + 4) // 5
    items = history[(page-1)*5 : page*5]
    
    if edit: 
        try: await message.delete()
        except: pass
        
    d1 = datetime.fromisoformat(df).strftime('%d.%m.%Y') if df else ""
    d2 = datetime.fromisoformat(dt).strftime('%d.%m.%Y') if dt else ""
    await message.answer(f"📋 Tarix: {d1} - {d2}")
    
    for order in items:
        text = await asyncio.to_thread(format_delivery_short, order)
        await message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
        
    if tp > 1:
        await message.answer(f"Sahifa {page}/{tp}", reply_markup=kb.get_pagination_kb(page, tp))
    logger.info(f"admin history filter query took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("h:p:") | F.data.startswith("h:n:"))
async def paginate_admin_history(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.split(":")[2])
    await show_history_results(callback.message, state, page, edit=True)

@router.callback_query(F.data == "adm_active")
async def show_active(callback: CallbackQuery):
    t0 = time.time()
    await callback.answer()
    active = await asyncio.to_thread(get_active_orders)
    if not active:
        await callback.message.edit_text("Aktiv vazifalar yo'q.", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
        return
    await callback.message.delete()
    await callback.message.answer("📊 Aktiv vazifalar:")
    for order in active[:5]:
        text = await asyncio.to_thread(format_delivery_short, order)
        await callback.message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
    tp = (len(active) + 4) // 5
    if tp > 1: await callback.message.answer(f"Sahifa 1/{tp}", reply_markup=kb.get_pagination_kb(1, tp))
    logger.info(f"admin active list took {time.time()-t0:.2f}s")
