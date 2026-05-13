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

from core.i18n import _

def format_delivery_short(order, steps=None, lang="uz_latin"):
    """Format order summary for history list. Optimized to use stage_history."""
    is_done = (order.get('current_status') == 'YAKUNLANDI')
    status = f"✅ {_('finished', lang)}" if is_done else f"🚚 {_('on_way', lang)}"
    
    start_time_dt = parse_dt(order.get('accepted_at'))
    start_time = format_time(start_time_dt)
    end_time_dt = parse_dt(order.get('completed_at')) or parse_dt(order.get('finished_at'))
    end_time = format_time(end_time_dt)
    
    transit_status = order.get('transit_status') or "-"
    cargo = order.get('cargo') or "-"
    address = order.get('address') or "-"
    
    text = f"📦 #{order['order_id']} | {order['car_number']}\n"
    text += f"{_('driver', lang)}: {order['driver_name']}\n"
    text += f"{_('transit', lang)}: {transit_status} | {_('status', lang)}: {status}\n"
    text += f"{_('cargo', lang)}: {cargo}\n"
    text += f"{_('address', lang)}: {address}\n"
    text += f"Vaqt: {start_time} - {end_time}"
    
    from core.utils import get_seconds_diff, format_duration_detailed
    diff = get_seconds_diff(order.get('accepted_at'), order.get('completed_at') or order.get('finished_at'))
    if diff:
        text += f" ({format_duration_detailed(diff)})"
    text += "\n"
    return text

def format_delivery_detailed(order, steps=None, lang="uz_latin"):
    """Detailed format with all fields and stage_history."""
    transit_status = order.get('transit_status') or "-"
    cargo = order.get('cargo') or "-"
    address = order.get('address') or "-"
    
    text = f"📦 **{_('id', lang)}: #{order['order_id']}**\n"
    text += f"👤 **{_('driver', lang)}:** {order['driver_name']}\n"
    text += f"🚘 **{_('car', lang)}:** {order['car_number']}\n"
    text += f"🚚 **{_('transit', lang)}:** {transit_status}\n"
    text += f"📍 **{_('address', lang)}:** {address}\n"
    text += f"📦 **{_('cargo', lang)}:** {cargo}\n"
    if order.get('comment'): text += f"📝 **{_('comment', lang)}:** {order['comment']}\n"
        
    start_time_dt = parse_dt(order.get('accepted_at'))
    start_time = format_time(start_time_dt)
    end_time_dt = parse_dt(order.get('completed_at')) or parse_dt(order.get('finished_at'))
    end_time = format_time(end_time_dt)
    
    is_done = (order.get('current_status') == 'YAKUNLANDI')
    status = f"✅ {_('finished', lang)}" if is_done else f"🚚 {_('on_way', lang)}"
    text += f"\n📊 **{_('status', lang)}:** {status}\n"
    if start_time != "Noma'lum": text += f"⏰ **{_('started', lang)}:** {start_time}\n"
    if end_time != "Noma'lum": text += f"🏁 **{_('finished', lang)}:** {end_time}\n"
    
    from core.utils import get_seconds_diff, format_duration_detailed
    diff = get_seconds_diff(order.get('accepted_at'), order.get('completed_at') or order.get('finished_at'))
    if diff:
        text += f"⏳ **{_('total_time', lang)}:** {format_duration_detailed(diff)}\n"
        
    text += f"\n📋 **{_('stages', lang)}:**\n"
    stage_history = order.get('stage_history') or []
    for item in stage_history:
        text += f"{item.get('emoji', '✅')} {item['stage']}: {item['status']} — {format_duration_detailed(item.get('duration_seconds'))} ({item.get('completed_at', '-')})\n"
        
    # Extra stages
    def get_stage_info(label_key, dt_key):
        dt = order.get(dt_key)
        if dt:
            return f"✅ {_(label_key, lang)} — {format_time(parse_dt(dt))}\n"
        return ""

    text += get_stage_info("loaded_photo", "loaded_photo_at")
    text += get_stage_info("on_way", "on_way_at")
    text += get_stage_info("act_photo", "act_photo_at")
    text += get_stage_info("location", "delivered_location_at")
    return text

@router.message(F.text.in_({"📋 Mening tarixim", "📋 Менинг тарихим"}))
async def my_history(message: Message):
    t0 = time.time()
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    # Strictly filter by tid
    history = await asyncio.to_thread(get_history, 'drv', str(tid))
    if not history:
        await message.answer("Sizda hali tarix yo'q.")
        return
        
    page = 1
    total_pages = (len(history) + 4) // 5
    items = history[:5]
    
    await message.answer(f"📋 **{_('my_history', lang)}**:")
    for order in items:
        text = await asyncio.to_thread(format_delivery_short, order, None, lang)
        await message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id'], lang))
        
    if total_pages > 1:
        await message.answer(f"Sahifa {page}/{total_pages}", reply_markup=kb.get_driver_pagination_kb(page, total_pages))
    logger.info(f"my_history took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("m:p:") | F.data.startswith("m:n:"))
async def paginate_my_history(callback: CallbackQuery):
    t0 = time.time()
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    action, page_str = callback.data.split(":")[1:]
    page = int(page_str)
    history = await asyncio.to_thread(get_history, 'drv', str(tid))
    
    total_pages = (len(history) + 4) // 5
    start_idx = (page - 1) * 5
    items = history[start_idx:start_idx+5]
    
    await callback.message.delete()
    for order in items:
        text = await asyncio.to_thread(format_delivery_short, order, None, lang)
        await callback.message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id'], lang))
        
    await callback.message.answer(f"{_('page', lang)} {page}/{total_pages}", reply_markup=kb.get_driver_pagination_kb(page, total_pages, lang))
    logger.info(f"paginate_my_history took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("detail:"))
async def show_delivery_details(callback: CallbackQuery):
    t0 = time.time()
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    order_id = callback.data.split("detail:")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
        
    text = await asyncio.to_thread(format_delivery_detailed, order, None, lang)
    await callback.message.answer(text, parse_mode="Markdown")
    logger.info(f"detail took {time.time()-t0:.2f}s")
