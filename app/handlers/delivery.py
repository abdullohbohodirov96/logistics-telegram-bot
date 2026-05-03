import logging
from datetime import datetime
import pytz
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.config import TIMEZONE, GROUP_CHAT_ID
from app.db import get_order, update_order, save_order_step, get_order_steps
from app.sheets import update_order_status
from app.states import DeliveryProcess
import app.keyboards as kb

router = Router()
logger = logging.getLogger(__name__)

tz = pytz.timezone(TIMEZONE)

def get_current_time():
    return datetime.now(tz).strftime("%H:%M")

def should_send_to_group():
    return bool(GROUP_CHAT_ID and str(GROUP_CHAT_ID) != "0")

async def update_group_message(bot: Bot, order_id: str):
    if not should_send_to_group():
        return
        
    order = get_order(order_id)
    if not order or not order.get('group_message_id'):
        return

    steps = get_order_steps(order_id)
    
    # Determine status
    status_text = "📦 Yuk ortilmoqda"
    if steps:
        last_step = steps[-1].get('step_name')
        if last_step == 'finish':
            status_text = "✅ Yetkazib berish yakunlandi"
        elif last_step == 'photo_obj':
            status_text = "📍 Manzilda"
        elif last_step == 'location':
            status_text = "📍 Lokatsiya yuborildi"
        elif last_step == 'arrived':
            status_text = "📍 Manzilga yetib keldi"
        elif last_step == 'start_drive':
            status_text = "🚚 Yo‘lga chiqdi"
        elif last_step == 'photo_load':
            status_text = "📦 Yuklangan"

    text = f"🚚 Yetkazib berish #{order_id}\n\n"
    text += f"Haydovchi: {order['driver_name']}\n"
    text += f"Telegram ID: {order['driver_telegram_id']}\n"
    text += f"Mashina: {order['car_number']}\n"
    text += f"Manzil: {order['address']}\n"
    text += f"Yuk: {order['cargo']}\n"
    if order.get('comment'):
        text += f"Izoh: {order['comment']}\n"
    
    text += f"\nHolat: {status_text}\n\n"

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
            text += f"📍 Manzilga yetib keldi — {t}\n"
        elif name == 'location':
            text += f"📍 Lokatsiya yuborildi — {t}\n"
        elif name == 'photo_obj':
            text += f"📸 Manzildagi rasm — {t}\n"
        elif name == 'finish':
            text += f"✅ Yetkazib berish yakunlandi — {t}\n"

    try:
        await bot.edit_message_text(
            chat_id=GROUP_CHAT_ID,
            message_id=order['group_message_id'],
            text=text
        )
    except Exception as e:
        logger.error(f"Error editing group message: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("_")[1]
    order = get_order(order_id)
    if not order:
        await callback.answer("Buyurtma topilmadi.", show_alert=True)
        return

    t = get_current_time()
    save_order_step({
        'order_id': order_id,
        'step_name': 'take_delivery',
        'time_text': t
    })

    from app.sheets import get_sheets_service
    from app.config import GOOGLE_SHEET_ID
    try:
        sheets = get_sheets_service()
        if sheets:
            res = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='orders!A:F').execute()
            vals = res.get('values', [])
            for i, row in enumerate(vals):
                if len(row) > 0 and row[0].strip() == order_id:
                    update_order_status(i + 1, 'IN_PROGRESS')
                    break
    except Exception as e:
        pass

    if should_send_to_group():
        text = f"🚚 Yetkazib berish #{order_id}\n\n"
        text += f"Haydovchi: {order['driver_name']}\n"
        text += f"Telegram ID: {order['driver_telegram_id']}\n"
        text += f"Mashina: {order['car_number']}\n"
        text += f"Manzil: {order['address']}\n"
        text += f"Yuk: {order['cargo']}\n"
        if order.get('comment'):
            text += f"Izoh: {order['comment']}\n\n"
        text += f"Holat: 📦 Yuk ortilmoqda\n\n"
        text += f"✅ Yetkazib berishni oldi — {t}\n"

        try:
            msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text)
            update_order(order_id, {'group_message_id': msg.message_id, 'current_status': 'take_delivery'})
        except Exception as e:
            logger.error(f"Error sending to group: {e}")

    await callback.message.edit_text("✅ Yetkazib berish qabul qilindi.")
    await callback.message.answer("A zona bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb("A", order_id))
    await callback.answer()

ZONES = ["A", "B", "C", "D", "E"]

@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot, state: FSMContext):
    parts = callback.data.split("_")
    zone = parts[1]
    val = parts[2]
    order_id = parts[3]
    
    t = get_current_time()
    save_order_step({
        'order_id': order_id,
        'step_name': f'zone_{zone}',
        'step_value': val,
        'time_text': t
    })
    
    await update_group_message(bot, order_id)
    
    action_text = "✅ Oldim" if val == 'y' else "❌ Olmadim"
    await callback.message.edit_text(f"{zone} zona: {action_text}")
    
    current_index = ZONES.index(zone)
    if current_index + 1 < len(ZONES):
        next_zone = ZONES[current_index + 1]
        await callback.message.answer(f"{next_zone} zona bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb(next_zone, order_id))
    else:
        await state.update_data(order_id=order_id, message_id=callback.message.message_id)
        await state.set_state(DeliveryProcess.waiting_for_load_photo)
        await callback.message.answer("📸 Mashinaga ortilgan yuk rasmini yuboring.")
    
    await callback.answer()

@router.message(DeliveryProcess.waiting_for_load_photo, F.photo)
async def process_load_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    photo_id = message.photo[-1].file_id
    t = get_current_time()
    
    save_order_step({
        'order_id': order_id,
        'step_name': 'photo_load',
        'time_text': t,
        'photo_file_id': photo_id
    })
    
    order = get_order(order_id)
    if should_send_to_group():
        caption = f"📸 Yuklangan rasm\nYetkazib berish #{order_id}\nHaydovchi: {order['driver_name']}\nVaqt: {t}"
        try:
            await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=photo_id, caption=caption)
        except Exception as e:
            logger.error(f"Error sending photo to group: {e}")
            
    await update_group_message(bot, order_id)
    
    await message.answer("Rasm qabul qilindi. Yo'lga chiqqaningizda tugmani bosing.", reply_markup=kb.get_driving_kb(order_id))
    await state.clear()

@router.callback_query(F.data.startswith("start_drive_"))
async def start_drive(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("start_drive_")[1]
    t = get_current_time()
    save_order_step({'order_id': order_id, 'step_name': 'start_drive', 'time_text': t})
    await update_group_message(bot, order_id)
    await callback.message.edit_text("Siz yo'ldasiz. Manzilga yetib kelgach tugmani bosing.", reply_markup=kb.get_arrived_kb(order_id))
    await callback.answer()

@router.callback_query(F.data.startswith("arrived_"))
async def arrived(callback: CallbackQuery, bot: Bot, state: FSMContext):
    order_id = callback.data.split("arrived_")[1]
    t = get_current_time()
    save_order_step({'order_id': order_id, 'step_name': 'arrived', 'time_text': t})
    await update_group_message(bot, order_id)
    
    await callback.message.edit_text("📍 Manzilga yetib keldingiz.")
    await state.update_data(order_id=order_id)
    await state.set_state(DeliveryProcess.waiting_for_location)
    await callback.message.answer("📍 Iltimos, lokatsiyani yuboring.", reply_markup=kb.get_request_location_kb())
    await callback.answer()

@router.message(DeliveryProcess.waiting_for_location, F.location)
async def process_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    lat = message.location.latitude
    lng = message.location.longitude
    t = get_current_time()
    
    save_order_step({
        'order_id': order_id,
        'step_name': 'location',
        'time_text': t,
        'location_lat': lat,
        'location_lng': lng
    })
    
    if should_send_to_group():
        try:
            await bot.send_location(chat_id=GROUP_CHAT_ID, latitude=lat, longitude=lng)
        except Exception as e:
            logger.error(f"Error sending location to group: {e}")
            
    await update_group_message(bot, order_id)
    
    await state.set_state(DeliveryProcess.waiting_for_unload_photo)
    await message.answer("Lokatsiya qabul qilindi. 📸 Manzilga yetib kelganingizdagi yuk/mashina rasmini yuboring.", reply_markup=kb.remove_reply_kb())

@router.message(DeliveryProcess.waiting_for_unload_photo, F.photo)
async def process_obj_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    photo_id = message.photo[-1].file_id
    t = get_current_time()
    
    save_order_step({
        'order_id': order_id,
        'step_name': 'photo_obj',
        'time_text': t,
        'photo_file_id': photo_id
    })
    
    order = get_order(order_id)
    if should_send_to_group():
        caption = f"📸 Manzildagi rasm\nYetkazib berish #{order_id}\nHaydovchi: {order['driver_name']}\nVaqt: {t}"
        try:
            await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=photo_id, caption=caption)
        except Exception as e:
            logger.error(f"Error sending photo to group: {e}")
            
    await update_group_message(bot, order_id)
    
    await message.answer("Rasm qabul qilindi. Yuk tushirib bo'lingach tugmani bosing.", reply_markup=kb.get_finish_kb(order_id))
    await state.clear()

@router.callback_query(F.data.startswith("finish_"))
async def finish_delivery(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("finish_")[1]
    t = get_current_time()
    save_order_step({'order_id': order_id, 'step_name': 'finish', 'time_text': t})
    await update_group_message(bot, order_id)
    await callback.message.edit_text(f"✅ Yetkazib berish #{order_id} muvaffaqiyatli yakunlandi ({t})!")
    await callback.answer()
    
    from app.sheets import get_sheets_service
    from app.config import GOOGLE_SHEET_ID
    try:
        sheets = get_sheets_service()
        if sheets:
            res = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='orders!A:F').execute()
            vals = res.get('values', [])
            for i, row in enumerate(vals):
                if len(row) > 0 and row[0].strip() == order_id:
                    update_order_status(i + 1, 'DONE')
                    break
    except Exception as e:
        logger.error(f"Error updating sheet to DONE: {e}")
