from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from app.db import get_history, get_order, get_order_steps
import app.keyboards as kb

router = Router()

def format_delivery_short(order):
    steps = get_order_steps(order['order_id'])
    start_time = "Noma'lum"
    end_time = "Noma'lum"
    has_finish_step = False
    for s in steps:
        if s['step_name'] == 'take_delivery':
            start_time = s['time_text']
        if s['step_name'] == 'finish':
            end_time = s['time_text']
            has_finish_step = True
            
    is_done = (
        order.get('current_status') == 'DONE' or 
        order.get('completed_at') is not None or 
        has_finish_step
    )
    status = "✅ Yakunlandi" if is_done else "🚚 Davom etmoqda"
    
    text = f"#{order['order_id']} | {order['car_number']}\n"
    text += f"Manzil: {order['address']}\n"
    text += f"Yuk: {order['cargo']}\n"
    text += f"Oldi: {start_time}\n"
    text += f"Yetkazdi: {end_time}\n"
    if order.get('duration_minutes'):
        h = order['duration_minutes'] // 60
        m = order['duration_minutes'] % 60
        text += f"Ketgan vaqt: {f'{h} soat ' if h > 0 else ''}{m} daqiqa\n"
    text += f"Holat: {status}\n"
    return text

def format_delivery_detailed(order):
    steps = get_order_steps(order['order_id'])
    
    text = f"📦 Buyurtma: #{order['order_id']}\n"
    text += f"Haydovchi: {order['driver_name']} (ID: {order['driver_telegram_id']})\n"
    text += f"Mashina: {order['car_number']}\n"
    text += f"Manzil: {order['address']}\n"
    text += f"Yuk: {order['cargo']}\n"
    if order.get('comment'):
        text += f"Izoh: {order['comment']}\n"
        
    start_time = ""
    end_time = ""
    loc_lat = None
    loc_lng = None
    photo_load = "Yo'q"
    photo_obj = "Yo'q"
    has_finish_step = False
    
    for s in steps:
        if s['step_name'] == 'take_delivery':
            start_time = s['time_text']
        elif s['step_name'] == 'finish':
            end_time = s['time_text']
            has_finish_step = True
        elif s['step_name'] == 'location':
            loc_lat = s.get('location_lat')
            loc_lng = s.get('location_lng')
        elif s['step_name'] == 'photo_load':
            photo_load = "Bor"
        elif s['step_name'] == 'photo_obj':
            photo_obj = "Bor"
            
    is_done = (
        order.get('current_status') == 'DONE' or 
        order.get('completed_at') is not None or 
        has_finish_step
    )
    status = "✅ Yakunlandi" if is_done else "🚚 Davom etmoqda"
    text += f"\nHolat: {status}\n"
    if start_time: text += f"Boshlangan: {start_time}\n"
    if end_time: text += f"Tugagan: {end_time}\n"
    
    if order.get('duration_minutes'):
        h = order['duration_minutes'] // 60
        m = order['duration_minutes'] % 60
        text += f"Ketgan vaqt: {f'{h} soat ' if h > 0 else ''}{m} daqiqa\n"
    
    if loc_lat and loc_lng:
        text += f"Lokatsiya: https://maps.google.com/?q={loc_lat},{loc_lng}\n"
        
    text += f"\nRasmlar:\n📸 Yuklangan rasm: {photo_load}\n📸 Manzildagi rasm: {photo_obj}\n"
    
    text += f"\nBosqichlar:\n"
    for step in steps:
        name = step['step_name']
        val = step.get('step_value', '')
        t = step.get('time_text', '')
        
        if name == 'take_delivery':
            text += f"✅ Yetkazib berishni oldi — {t}\n"
        elif name.startswith('zone_'):
            zone_letter = name.split('_')[1]
            sign = "✅" if val == 'y' else "❌"
            action = "oldi" if val == 'y' else "olmadi"
            text += f"{sign} {zone_letter}-blok: {action} — {t}\n"
        elif name == 'photo_load':
            text += f"📸 Yuklangan rasm — {t}\n"
        elif name == 'start_drive':
            text += f"🚚 Yo‘lga chiqdi — {t}\n"
        elif name == 'arrived':
            text += f"📍 Manzilga yetdi — {t}\n"
        elif name == 'location':
            text += f"📍 Lokatsiya yuborildi — {t}\n"
        elif name == 'photo_obj':
            text += f"📸 Manzildagi rasm — {t}\n"
        elif name == 'finish':
            text += f"✅ Yakunlandi — {t}\n"
            
    return text

@router.message(F.text == "📋 Mening tarixim")
async def my_history(message: Message):
    tid = message.from_user.id
    history = get_history('driver', str(tid))
    if not history:
        await message.answer("Sizda hali tarix yo'q.")
        return
        
    page = 1
    total_pages = (len(history) + 4) // 5
    items = history[:5]
    
    await message.answer("📋 Mening tarixim:")
    for order in items:
        text = format_delivery_short(order)
        await message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
        
    if total_pages > 1:
        await message.answer(f"Sahifa {page}/{total_pages}", reply_markup=kb.get_driver_pagination_kb(page, total_pages))

@router.callback_query(F.data.startswith("m:p:") | F.data.startswith("m:n:"))
async def paginate_my_history(callback: CallbackQuery):
    action, page_str = callback.data.split(":")[1:]
    page = int(page_str)
    
    tid = callback.from_user.id
    history = get_history('driver', str(tid))
    
    total_pages = (len(history) + 4) // 5
    start_idx = (page - 1) * 5
    end_idx = start_idx + 5
    items = history[start_idx:end_idx]
    
    await callback.message.delete()
    
    for order in items:
        text = format_delivery_short(order)
        await callback.message.answer(text, reply_markup=kb.get_order_detail_kb(order['order_id']))
        
    await callback.message.answer(f"Sahifa {page}/{total_pages}", reply_markup=kb.get_driver_pagination_kb(page, total_pages))
    await callback.answer()

@router.callback_query(F.data.startswith("detail:"))
async def show_delivery_details(callback: CallbackQuery):
    order_id = callback.data.split("detail:")[1]
    order = get_order(order_id)
    if not order:
        await callback.answer("Buyurtma topilmadi.", show_alert=True)
        return
        
    text = format_delivery_detailed(order)
    await callback.message.answer(text)
    await callback.answer()
