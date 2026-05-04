import asyncio
import time
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from core.db import get_history, get_order, get_order_steps
import core.keyboards as kb
from core.utils import parse_dt, format_time, format_duration, get_order_start_time

router = Router()
logger = logging.getLogger(__name__)

def format_delivery_short(order, steps=None):
    """Format order summary for history list. Optimized to accept steps."""
    is_done = (order.get('current_status') == 'DONE' or order.get('completed_at') is not None)
    status = "✅ Yakunlandi" if is_done else "🚚 Davom etmoqda"
    
    if steps is None:
        steps = get_order_steps(order['order_id'])
        
    start_time_dt = get_order_start_time(order, steps)
    start_time = format_time(start_time_dt)
    end_time_dt = parse_dt(order.get('completed_at'))
    end_time = format_time(end_time_dt)
    
    transit = "Ha" if order.get('transit_exists') else "Yo'q"
    cargo = order.get('cargo') or "Noma'lum"
    address = order.get('address') or "Noma'lum"
    
    text = f"📦 #{order['order_id']} | {order['car_number']}\n"
    text += f"Haydovchi: {order['driver_name']} ({order.get('driver_telegram_id')})\n"
    text += f"Transit: {transit} | Holat: {status}\n"
    text += f"Yuk: {cargo}\n"
    text += f"Manzil: {address}\n"
    text += f"Vaqt: {start_time} - {end_time}"
    if order.get('duration_minutes'):
        text += f" ({format_duration(order['duration_minutes'])})"
    text += "\n"
    return text

def format_delivery_detailed(order, steps=None):
    """Detailed format with all fields and steps."""
    if steps is None:
        steps = get_order_steps(order['order_id'])
    
    transit = "Ha" if order.get('transit_exists') else "Yo'q"
    cargo = order.get('cargo') or "Noma'lum"
    address = order.get('address') or "Noma'lum"
    
    text = f"📦 Buyurtma: #{order['order_id']}\n"
    text += f"Haydovchi: {order['driver_name']} (ID: {order['driver_telegram_id']})\n"
    text += f"Mashina: {order['car_number']}\n"
    text += f"Transit: {transit}\n"
    text += f"Manzil: {address}\n"
    text += f"Yuk: {cargo}\n"
    if order.get('comment'): text += f"Izoh: {order['comment']}\n"
        
    loc_lat, loc_lng = None, None
    photo_load, photo_obj = "Yo'q", "Yo'q"
    has_finish_step = False
    
    for s in steps:
        if s['step_name'] == 'finish': has_finish_step = True
        elif s['step_name'] == 'location':
            loc_lat, loc_lng = s.get('location_lat'), s.get('location_lng')
        elif s['step_name'] == 'photo_load': photo_load = "Bor"
        elif s['step_name'] == 'photo_obj': photo_obj = "Bor"

    start_time_dt = get_order_start_time(order, steps)
    start_time = format_time(start_time_dt)
    end_time_dt = parse_dt(order.get('completed_at'))
    end_time = format_time(end_time_dt)
    
    if not end_time_dt and has_finish_step:
        for s in steps:
            if s['step_name'] == 'finish':
                end_time = s['time_text']
                break
            
    is_done = (order.get('current_status') == 'DONE' or order.get('completed_at') is not None or has_finish_step)
    status = "✅ Yakunlandi" if is_done else "🚚 Davom etmoqda"
    text += f"\nHolat: {status}\n"
    if start_time != "Noma'lum": text += f"Boshlangan: {start_time}\n"
    if end_time != "Noma'lum": text += f"Tugagan: {end_time}\n"
    if order.get('duration_minutes'): text += f"Ketgan vaqt: {format_duration(order['duration_minutes'])}\n"
    if loc_lat and loc_lng: text += f"Lokatsiya: https://maps.google.com/?q={loc_lat},{loc_lng}\n"
    text += f"\nRasmlar:\n📸 Yuklangan rasm: {photo_load}\n📸 Manzildagi rasm: {photo_obj}\n"
    
    text += f"\nBosqichlar:\n"
    for step in steps:
        name, val, t = step['step_name'], step.get('step_value', ''), step.get('time_text', '')
        if name == 'take_delivery': text += f"✅ Yetkazib berishni oldi — {t}\n"
        elif name.startswith('zone_'):
            zone_letter = name.split('_')[1]
            sign = "✅" if val == 'y' else "❌"
            action = "oldi" if val == 'y' else "olmadi"
            text += f"{sign} {zone_letter}-blok: {action} — {t}\n"
        elif name == 'photo_load': text += f"📸 Yuklangan rasm — {t}\n"
        elif name == 'start_drive': text += f"🚚 Yo'lga chiqdi — {t}\n"
        elif name == 'arrived': text += f"📍 Manzilga yetdi — {t}\n"
        elif name == 'location': text += f"📍 Lokatsiya yuborildi — {t}\n"
        elif name == 'photo_obj': text += f"📸 Manzildagi rasm — {t}\n"
        elif name == 'finish': text += f"✅ Yakunlandi — {t}\n"
            
    return text

@router.message(F.text == "📋 Mening tarixim")
async def my_history(message: Message):
    t0 = time.time()
    tid = message.from_user.id
    history = await asyncio.to_thread(get_history, 'drv', str(tid))
    if not history:
        await message.answer("Sizda hali tarix yo'q.")
        return
        
    page = 1
    total_pages = (len(history) + 4) // 5
    items = history[:5]
    
    await message.answer("📋 Mening tarixim:")
    for order in items:
        # Pass steps=None to let it fetch inside, but wrap in thread
        text = await asyncio.to_thread(format_delivery_short, order)
        await message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
        
    if total_pages > 1:
        await message.answer(f"Sahifa {page}/{total_pages}", reply_markup=kb.get_driver_pagination_kb(page, total_pages))
    logger.info(f"my_history took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("m:p:") | F.data.startswith("m:n:"))
async def paginate_my_history(callback: CallbackQuery):
    t0 = time.time()
    await callback.answer()
    action, page_str = callback.data.split(":")[1:]
    page = int(page_str)
    tid = callback.from_user.id
    history = await asyncio.to_thread(get_history, 'drv', str(tid))
    
    total_pages = (len(history) + 4) // 5
    start_idx = (page - 1) * 5
    items = history[start_idx:start_idx+5]
    
    await callback.message.delete()
    for order in items:
        text = await asyncio.to_thread(format_delivery_short, order)
        await callback.message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
        
    await callback.message.answer(f"Sahifa {page}/{total_pages}", reply_markup=kb.get_driver_pagination_kb(page, total_pages))
    logger.info(f"paginate_my_history took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("detail:"))
async def show_delivery_details(callback: CallbackQuery):
    t0 = time.time()
    await callback.answer()
    order_id = callback.data.split("detail:")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
        
    text = await asyncio.to_thread(format_delivery_detailed, order)
    await callback.message.answer(text)
    logger.info(f"detail took {time.time()-t0:.2f}s")
