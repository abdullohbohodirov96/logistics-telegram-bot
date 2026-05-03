from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from app.db import get_history, get_order_steps
import app.keyboards as kb

router = Router()

def format_delivery(order):
    steps = get_order_steps(order['order_id'])
    text = f"1) #{order['order_id']}\n"
    text += f"Haydovchi: {order['driver_name']}\n"
    text += f"Manzil: {order['address']}\n"
    text += f"Yuk: {order['cargo']}\n"
    
    start_time = ""
    end_time = ""
    for s in steps:
        if s['step_name'] == 'take_delivery':
            start_time = s['time_text']
        if s['step_name'] == 'finish':
            end_time = s['time_text']
            
    if start_time: text += f"Boshlangan: {start_time}\n"
    if end_time: text += f"Tugagan: {end_time}\n"
    
    status = "DONE" if order.get('current_status') == 'DONE' else order.get('current_status', 'IN_PROGRESS')
    text += f"Holat: {status}\n\nBosqichlar:\n"
    
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
            text += f"{sign} {zone_letter} zona: {action} — {t}\n"
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
    
    text = "📋 Mening tarixim:\n\n"
    for order in history[:5]:
        text += format_delivery(order) + "\n----------------\n"
        
    await message.answer(text, reply_markup=kb.get_driver_pagination_kb(page, total_pages))

@router.callback_query(F.data.startswith("myhist_"))
async def paginate_my_history(callback: CallbackQuery):
    page = int(callback.data.split("_")[1])
    tid = callback.from_user.id
    history = get_history('driver', str(tid))
    
    total_pages = (len(history) + 4) // 5
    start_idx = (page - 1) * 5
    end_idx = start_idx + 5
    
    text = "📋 Mening tarixim:\n\n"
    for order in history[start_idx:end_idx]:
        text += format_delivery(order) + "\n----------------\n"
        
    await callback.message.edit_text(text, reply_markup=kb.get_driver_pagination_kb(page, total_pages))
    await callback.answer()
