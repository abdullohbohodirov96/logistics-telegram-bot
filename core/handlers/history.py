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
    """Format order summary for history list. Optimized to use stage_history."""
    is_done = (order.get('current_status') == 'YAKUNLANDI')
    status = "✅ Yakunlandi" if is_done else "🚚 Davom etmoqda"
    
    start_time_dt = parse_dt(order.get('accepted_at'))
    start_time = format_time(start_time_dt)
    end_time_dt = parse_dt(order.get('completed_at')) or parse_dt(order.get('finished_at'))
    end_time = format_time(end_time_dt)
    
    transit_status = order.get('transit_status') or "Yo'q"
    cargo = order.get('cargo') or "Noma'lum"
    address = order.get('address') or "Noma'lum"
    
    text = f"📦 #{order['order_id']} | {order['car_number']}\n"
    text += f"Haydovchi: {order['driver_name']} ({order.get('driver_telegram_id')})\n"
    text += f"Transit: {transit_status} | Holat: {status}\n"
    text += f"Yuk: {cargo}\n"
    text += f"Manzil: {address}\n"
    text += f"Vaqt: {start_time} - {end_time}"
    
    from core.utils import get_seconds_diff, format_duration_detailed
    diff = get_seconds_diff(order.get('accepted_at'), order.get('completed_at') or order.get('finished_at'))
    if diff:
        text += f" ({format_duration_detailed(diff)})"
    text += "\n"
    return text

def format_delivery_detailed(order, steps=None):
    """Detailed format with all fields and stage_history."""
    transit_status = order.get('transit_status') or "Yo'q"
    cargo = order.get('cargo') or "Noma'lum"
    address = order.get('address') or "Noma'lum"
    
    text = f"📦 Buyurtma: #{order['order_id']}\n"
    text += f"Haydovchi: {order['driver_name']} (ID: {order['driver_telegram_id']})\n"
    text += f"Mashina: {order['car_number']}\n"
    text += f"Transit: {transit_status}\n"
    text += f"Manzil: {address}\n"
    text += f"Yuk: {cargo}\n"
    if order.get('comment'): text += f"Izoh: {order['comment']}\n"
        
    start_time_dt = parse_dt(order.get('accepted_at'))
    start_time = format_time(start_time_dt)
    end_time_dt = parse_dt(order.get('completed_at')) or parse_dt(order.get('finished_at'))
    end_time = format_time(end_time_dt)
    
    is_done = (order.get('current_status') == 'YAKUNLANDI')
    status = "✅ Yakunlandi" if is_done else "🚚 Davom etmoqda"
    text += f"\nHolat: {status}\n"
    if start_time != "Noma'lum": text += f"Boshlangan: {start_time}\n"
    if end_time != "Noma'lum": text += f"Tugagan: {end_time}\n"
    
    from core.utils import get_seconds_diff, format_duration_detailed
    diff = get_seconds_diff(order.get('accepted_at'), order.get('completed_at') or order.get('finished_at'))
    if diff:
        text += f"Ketgan vaqt: {format_duration_detailed(diff)}\n"
        
    text += f"\nBosqichlar:\n"
    stage_history = order.get('stage_history') or []
    for item in stage_history:
        text += f"{item.get('emoji', '✅')} {item['stage']}: {item['status']} — {format_duration_detailed(item.get('duration_seconds'))} ({item.get('completed_at', '-')})\n"
        
    # Extra stages
    def get_stage_info(label, dt_key):
        dt = order.get(dt_key)
        if dt:
            return f"✅ {label} — {format_time(parse_dt(dt))}\n"
        return ""

    text += get_stage_info("Yuk rasmi", "loaded_photo_at")
    text += get_stage_info("Yo'lga chiqdi", "on_way_at")
    text += get_stage_info("Akt rasmi", "act_photo_at")
    text += get_stage_info("Lokatsiya", "delivered_location_at")
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
