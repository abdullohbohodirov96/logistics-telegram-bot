import logging
from datetime import datetime
import pytz
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ContentType
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

async def update_group_message(bot: Bot, order_id: str):
    order = get_order(order_id)
    if not order or not order.get('group_message_id'):
        return

    steps = get_order_steps(order_id)
    
    # Determine status
    status_text = "🚚 В процессе"
    if steps:
        last_step = steps[-1].get('step_name')
        if last_step == 'finish':
            status_text = "✅ Доставка завершена"
        elif last_step == 'start_unload':
            status_text = "📦 Разгрузка"
        elif last_step == 'photo_obj':
            status_text = "📍 На объекте"
        elif last_step == 'arrived':
            status_text = "📍 Доехал до объекта"
        elif last_step == 'start_drive':
            status_text = "🚚 В пути"
        elif last_step == 'photo_load':
            status_text = "📦 Загружен"
        elif last_step == 'take_delivery':
            status_text = "📦 Загрузка"

    text = f"🚚 Доставка #{order_id}\n\n"
    text += f"Водитель: {order['driver_name']}\n"
    text += f"Машина: {order['car_number']}\n"
    text += f"Адрес: {order['address']}\n"
    text += f"Груз: {order['cargo']}\n"
    if order.get('comment'):
        text += f"Комментарий: {order['comment']}\n"
    
    text += f"\nСтатус: {status_text}\n\n"

    for step in steps:
        name = step['step_name']
        val = step.get('step_value', '')
        t = step.get('time_text', '')
        
        if name == 'take_delivery':
            text += f"✅ Взял доставку — {t}\n"
        elif name.startswith('zone_'):
            zone_letter = name.split('_')[1]
            sign = "✅" if val == 'y' else "❌"
            text += f"{sign} {zone_letter} зона — {t}\n"
        elif name == 'photo_load':
            text += f"📸 Фото загрузки — {t}\n"
        elif name == 'start_drive':
            text += f"🚚 В пути — {t}\n"
        elif name == 'arrived':
            text += f"📍 Доехал до объекта — {t}\n"
        elif name == 'location':
            text += f"📍 Локация получена — {t}\n"
        elif name == 'photo_obj':
            text += f"📸 Фото на объекте — {t}\n"
        elif name == 'start_unload':
            text += f"📦 Разгрузка началась — {t}\n"
        elif name == 'finish':
            text += f"✅ Доставка завершена — {t}\n"

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
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    t = get_current_time()
    save_order_step({
        'order_id': order_id,
        'step_name': 'take_delivery',
        'time_text': t
    })

    # Send initial message to group
    text = f"🚚 Доставка #{order_id}\n\n"
    text += f"Водитель: {order['driver_name']}\n"
    text += f"Машина: {order['car_number']}\n"
    text += f"Адрес: {order['address']}\n"
    text += f"Груз: {order['cargo']}\n"
    if order.get('comment'):
        text += f"Комментарий: {order['comment']}\n\n"
    text += f"Статус: 📦 Загрузка\n\n"
    text += f"✅ Взял доставку — {t}\n"

    try:
        msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text)
        update_order(order_id, {'group_message_id': msg.message_id, 'current_status': 'take_delivery'})
    except Exception as e:
        logger.error(f"Error sending to group: {e}")

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ Взято в работу: {t}",
        reply_markup=kb.get_zones_kb(order_id)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot):
    # z_A_y_1045
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
    await callback.answer(f"Зона {zone} отмечена")

@router.callback_query(F.data.startswith("photo_load_"))
async def request_load_photo(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split("photo_load_")[1]
    await state.update_data(order_id=order_id, message_id=callback.message.message_id)
    await state.set_state(DeliveryProcess.waiting_for_load_photo)
    await callback.message.answer("Пожалуйста, отправьте фото загруженной машины 📸")
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
    caption = f"📸 Фото загрузки\nДоставка #{order_id}\nВодитель: {order['driver_name']}\nВремя: {t}"
    try:
        await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=photo_id, caption=caption)
    except Exception as e:
        logger.error(f"Error sending photo to group: {e}")
        
    await update_group_message(bot, order_id)
    
    await message.answer("Фото принято. Нажмите кнопку, когда начнете движение.", reply_markup=kb.get_driving_kb(order_id))
    
    # Optional: Edit original message to remove keyboard
    try:
        await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=data.get('message_id'), reply_markup=None)
    except Exception:
        pass
        
    await state.clear()

@router.callback_query(F.data.startswith("start_drive_"))
async def start_drive(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("start_drive_")[1]
    t = get_current_time()
    save_order_step({'order_id': order_id, 'step_name': 'start_drive', 'time_text': t})
    await update_group_message(bot, order_id)
    await callback.message.edit_text("Вы в пути. Нажмите кнопку, когда доедете до объекта.", reply_markup=kb.get_arrived_kb(order_id))
    await callback.answer()

@router.callback_query(F.data.startswith("arrived_"))
async def arrived(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("arrived_")[1]
    t = get_current_time()
    save_order_step({'order_id': order_id, 'step_name': 'arrived', 'time_text': t})
    await update_group_message(bot, order_id)
    await callback.message.edit_text("Вы доехали. Теперь необходимо отправить локацию.", reply_markup=kb.get_send_location_kb(order_id))
    await callback.answer()

@router.callback_query(F.data.startswith("req_loc_"))
async def request_location(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split("req_loc_")[1]
    await state.update_data(order_id=order_id, message_id=callback.message.message_id)
    await state.set_state(DeliveryProcess.waiting_for_location)
    await callback.message.answer("Пожалуйста, отправьте локацию объекта через скрепку 📎 (Местоположение)")
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
    
    try:
        await bot.send_location(chat_id=GROUP_CHAT_ID, latitude=lat, longitude=lng)
    except Exception as e:
        logger.error(f"Error sending location to group: {e}")
        
    await update_group_message(bot, order_id)
    
    await message.answer("Локация принята. Теперь отправьте фото на объекте.", reply_markup=kb.get_object_photo_kb(order_id))
    
    try:
        await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=data.get('message_id'), reply_markup=None)
    except Exception:
        pass
        
    await state.clear()

@router.callback_query(F.data.startswith("req_photo_obj_"))
async def request_obj_photo(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split("req_photo_obj_")[1]
    await state.update_data(order_id=order_id, message_id=callback.message.message_id)
    await state.set_state(DeliveryProcess.waiting_for_unload_photo)
    await callback.message.answer("Пожалуйста, отправьте фото на объекте 📸")
    await callback.answer()

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
    caption = f"📸 Фото на объекте\nДоставка #{order_id}\nВодитель: {order['driver_name']}\nВремя: {t}"
    try:
        await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=photo_id, caption=caption)
    except Exception as e:
        logger.error(f"Error sending photo to group: {e}")
        
    await update_group_message(bot, order_id)
    
    await message.answer("Фото принято. Нажмите кнопку, когда начнете разгрузку.", reply_markup=kb.get_unload_kb(order_id))
    
    try:
        await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=data.get('message_id'), reply_markup=None)
    except Exception:
        pass
        
    await state.clear()

@router.callback_query(F.data.startswith("start_unload_"))
async def start_unload(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("start_unload_")[1]
    t = get_current_time()
    save_order_step({'order_id': order_id, 'step_name': 'start_unload', 'time_text': t})
    await update_group_message(bot, order_id)
    await callback.message.edit_text("Разгрузка началась. Нажмите кнопку, когда всё закончите.", reply_markup=kb.get_finish_kb(order_id))
    await callback.answer()

@router.callback_query(F.data.startswith("finish_"))
async def finish_delivery(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("finish_")[1]
    t = get_current_time()
    save_order_step({'order_id': order_id, 'step_name': 'finish', 'time_text': t})
    await update_group_message(bot, order_id)
    await callback.message.edit_text(f"✅ Доставка #{order_id} завершена в {t}!")
    await callback.answer()
    
    # Update Google Sheet to DONE
    order = get_order(order_id)
    if order:
        # Assuming we need to store row_index in orders table or search for it.
        # It's better to update status using sheets logic but we might not have row_index here.
        # Wait, get_new_orders returns row_index, but we didn't save it to Supabase.
        # Actually, let's search for the row and update it.
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

