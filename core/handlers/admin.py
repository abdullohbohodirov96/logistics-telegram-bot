import logging
import time
import asyncio
from datetime import datetime, timedelta
import pytz
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from core.config import ADMIN_IDS, TIMEZONE
from core.db import get_history, get_unique_cars, get_unique_drivers, get_active_orders
from core.cache import cache_get, cache_set
import core.keyboards as kb
from core.sheets import get_drivers_status
from core.states import AdminProcess
from core.handlers.history import format_delivery_short, format_delivery_detailed

router = Router()
tz = pytz.timezone(TIMEZONE)

@router.message(F.text == "🛠 Admin panel")
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🛠 Admin paneliga xush kelibsiz. Quyidagilardan birini tanlang:", reply_markup=kb.get_admin_panel_kb())

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
    
    # Use cached car list (TTL 60s)
    cars = cache_get('unique_cars', 60)
    if cars is None:
        cars = await asyncio.to_thread(get_unique_cars)
        cache_set('unique_cars', cars)
    
    if not cars:
        await callback.message.edit_text("Mashinalar topilmadi.", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
        return
    await callback.message.edit_text("Qaysi mashina bo'yicha tarixni ko'rmoqchisiz?", reply_markup=kb.get_cars_kb(cars))

@router.callback_query(F.data == "adm_hist_drv")
async def show_drivers(callback: CallbackQuery):
    await callback.answer()
    
    # Use cached driver list (TTL 60s)
    drivers = cache_get('unique_drivers', 60)
    if drivers is None:
        drivers = await asyncio.to_thread(get_unique_drivers)
        cache_set('unique_drivers', drivers)
    
    if not drivers:
        await callback.message.edit_text("Haydovchilar topilmadi.", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
        return
    await callback.message.edit_text("Qaysi haydovchi bo'yicha tarixni ko'rmoqchisiz?", reply_markup=kb.get_drivers_kb(drivers))

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
    await callback.message.edit_text(f"🚗 {car} mashinasi bo'yicha sana oralig'ini tanlang:", reply_markup=kb.get_date_range_kb())

@router.callback_query(F.data.startswith("drv_"))
async def select_drv_date(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tid = callback.data.split("drv_")[1]
    await state.update_data(filter_type='drv', filter_val=tid)
    await callback.message.edit_text(f"👤 Haydovchi bo'yicha sana oralig'ini tanlang:", reply_markup=kb.get_date_range_kb())

@router.callback_query(F.data.startswith("dt_"))
async def handle_date_range(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    range_type = callback.data.split("dt_")[1]
    
    now = datetime.now(tz)
    date_from = None
    date_to = None
    
    if range_type == 'today':
        date_from = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        date_to = now.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == 'yesterday':
        yesterday = now - timedelta(days=1)
        date_from = yesterday.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        date_to = yesterday.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == '7days':
        date_from = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        date_to = now.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == '30days':
        date_from = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        date_to = now.replace(hour=23, minute=59, second=59).isoformat()
    elif range_type == 'manual':
        await state.set_state(AdminProcess.waiting_for_manual_date)
        await callback.message.edit_text("Iltimos, sanani quyidagi formatda kiriting:\n\n`03.05.2026 - 05.05.2026`", parse_mode="Markdown")
        return
        
    await state.update_data(date_from=date_from, date_to=date_to)
    await show_history_results(callback.message, state, 1)

@router.message(AdminProcess.waiting_for_manual_date, F.text)
async def process_manual_date(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        start_str, end_str = text.split("-")
        start_date = datetime.strptime(start_str.strip(), "%d.%m.%Y")
        end_date = datetime.strptime(end_str.strip(), "%d.%m.%Y")
        
        start_date = tz.localize(start_date.replace(hour=0, minute=0, second=0))
        end_date = tz.localize(end_date.replace(hour=23, minute=59, second=59))
        
        date_from = start_date.isoformat()
        date_to = end_date.isoformat()
        
        await state.update_data(date_from=date_from, date_to=date_to)
        await show_history_results(message, state, 1, edit=False)
        await state.set_state(None)
    except Exception as e:
        await message.answer("Noto'g'ri format. Iltimos, qaytadan urinib ko'ring:\n`03.05.2026 - 05.05.2026`", parse_mode="Markdown")

async def show_history_results(message: Message, state: FSMContext, page: int, edit=True):
    t0 = time.time()
    data = await state.get_data()
    filter_type = data.get('filter_type', 'all')
    filter_val = data.get('filter_val')
    date_from = data.get('date_from')
    date_to = data.get('date_to')
    
    # Strictly fetch 50 records as per requirement for better performance
    history = await asyncio.to_thread(get_history, filter_type, filter_val, date_from, date_to, limit=50)
    
    if not history:
        text = "Ushbu oraliqda ma'lumot topilmadi."
        kb_markup = kb.get_pagination_kb(1, 1)
        if edit:
            await message.edit_text(text, reply_markup=kb_markup)
        else:
            await message.answer(text, reply_markup=kb_markup)
        return

    total_pages = (len(history) + 4) // 5
    start_idx = (page - 1) * 5
    end_idx = start_idx + 5
    items = history[start_idx:end_idx]
    
    title = ""
    if filter_type == 'car': 
        title = f"🚗 {filter_val} mashinasi tarixi\n"
    elif filter_type == 'drv': 
        # Optionally look up name from first item if available
        drv_name = items[0].get('driver_name', 'Noma\'lum') if items else 'Noma\'lum'
        title = f"👤 Haydovchi: {drv_name} (ID: {filter_val}) tarixi\n"
    else: 
        title = "📋 Barcha tarix\n"
    
    d1 = datetime.fromisoformat(date_from).strftime('%d.%m.%Y') if date_from else ""
    d2 = datetime.fromisoformat(date_to).strftime('%d.%m.%Y') if date_to else ""
    
    if edit:
        try:
            await message.delete()
        except:
            pass
        
    await message.answer(f"{title}📅 {d1} - {d2}")
    
    for order in items:
        text = format_delivery_short(order)
        await message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
        
    if total_pages > 1:
        await message.answer(f"Sahifa {page}/{total_pages}", reply_markup=kb.get_pagination_kb(page, total_pages))
    
    elapsed = time.time() - t0
    logging.getLogger(__name__).info(f"show_history_results: {elapsed:.2f}s")

@router.callback_query(F.data.startswith("h:p:") | F.data.startswith("h:n:"))
async def paginate_admin_history(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action, page_str = callback.data.split(":")[1:]
    page = int(page_str)
    await show_history_results(callback.message, state, page, edit=True)

@router.callback_query(F.data == "adm_active")
async def show_active(callback: CallbackQuery):
    await callback.answer()
    
    active = await asyncio.to_thread(get_active_orders)
    if not active:
        await callback.message.edit_text("Aktiv vazifalar topilmadi.", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
        return
        
    page = 1
    total_pages = (len(active) + 4) // 5
    items = active[:5]
    
    await callback.message.delete()
    await callback.message.answer("📊 Aktiv vazifalar:")
    
    for order in items:
        text = format_delivery_short(order)
        await callback.message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
        
    if total_pages > 1:
        await callback.message.answer(f"Sahifa {page}/{total_pages}", reply_markup=kb.get_pagination_kb(page, total_pages))

@router.callback_query(F.data == "adm_cars_status")
async def show_cars_status(callback: CallbackQuery):
    await callback.answer("Yuklanmoqda...", show_alert=False)
    
    status_data = await asyncio.to_thread(get_drivers_status)
    if not status_data:
        await callback.message.edit_text("Mashinalar holati topilmadi (Google Sheets yoki baza bo'sh).", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
        return
        
    text = "🚗 **Mashinalar holati:**\n\n"
    for row in status_data:
        if len(row) >= 4:
            car = row[0]
            status = row[3]
            order_id = row[4] if len(row) > 4 else ""
            
            if order_id:
                text += f"🔹 {car} — {status} — #{order_id}\n"
            else:
                text += f"🔹 {car} — {status}\n"
                
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.InlineKeyboardMarkup(inline_keyboard=[[kb.InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]]))
