import logging
from datetime import datetime
import pytz
import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from core.config import TIMEZONE, GROUP_CHAT_ID
from core.db import get_order, update_order, save_order_step, get_order_steps
from core.sheets import update_order_status, update_driver_status_sheet
from core.states import DeliveryProcess
import core.keyboards as kb

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
        
    order = await asyncio.to_thread(get_order, order_id)
    if not order or not order.get('group_message_id'):
        return

    steps = await asyncio.to_thread(get_order_steps, order_id)
    
    start_time = "Noma'lum"
    end_time = "Noma'lum"
    last_step_text = ""
    loc_lat = None
    loc_lng = None
    photo_load = "yo'q"
    photo_obj = "yo'q"
    
    is_done = order.get('current_status') == 'DONE'
    
    for s in steps:
        name = s['step_name']
        t = s.get('time_text', '')
        if name == 'take_delivery':
            start_time = t
            last_step_text = f"Vazifani oldi — {t}"
        elif name.startswith('zone_'):
            z = name.split('_')[1]
            last_step_text = f"{z}-blok {'oldi' if s.get('step_value') == 'y' else 'olmadi'} — {t}"
        elif name == 'photo_load':
            photo_load = "bor"
            last_step_text = f"Yuklangan rasm yuborildi — {t}"
        elif name == 'start_drive':
            last_step_text = f"Yo‘lga chiqdi — {t}"
        elif name == 'arrived':
            last_step_text = f"Manzilga yetdi — {t}"
        elif name == 'location':
            loc_lat = s.get('location_lat')
            loc_lng = s.get('location_lng')
            last_step_text = f"Lokatsiya yuborildi — {t}"
        elif name == 'photo_obj':
            photo_obj = "bor"
            last_step_text = f"Manzildagi rasm yuborildi — {t}"
        elif name == 'finish':
            end_time = t
            is_done = True
            
    text = f"🚚 Yetkazib berish #{order_id}\n"
    text += f"Haydovchi: {order['driver_name']}\n"
    text += f"Mashina: {order['car_number']}\n"
    text += f"Manzil: {order['address']}\n"
    text += f"Yuk: {order['cargo']}\n"
    
    if order.get('transit_status'):
        text += f"Transit: {order['transit_status']}\n"

    text += "\n"

    if is_done:
        text += f"Holat: ✅ Yakunlandi\n"
        text += f"Oldi: {start_time}\n"
        text += f"Yetkazdi: {end_time}\n"
        if loc_lat and loc_lng:
            text += f"Lokatsiya: https://maps.google.com/?q={loc_lat},{loc_lng}\n"
        text += f"\nRasmlar:\n📸 Yuklangan rasm: {photo_load}\n📸 Manzildagi rasm: {photo_obj}\n"
    else:
        text += f"Holat: 🚚 Yo‘lda\n"
        text += f"Oldi: {start_time}\n"
        text += f"Oxirgi bosqich: {last_step_text}\n"

    try:
        await bot.edit_message_text(
            chat_id=GROUP_CHAT_ID,
            message_id=order['group_message_id'],
            text=text,
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Error editing group message: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, bot: Bot):
    await callback.answer("✅ Qabul qilindi")
    order_id = callback.data.split("_")[1]
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.message.answer("Buyurtma topilmadi.")
        return

    t = get_current_time()
    await asyncio.to_thread(save_order_step, {
        'order_id': order_id,
        'step_name': 'take_delivery',
        'time_text': t
    })

    # Run background tasks (Sheets + Group notification)
    async def bg_task():
        await asyncio.to_thread(update_driver_status_sheet, order['car_number'], order['driver_name'], order['driver_telegram_id'], 'BAND', order_id)
        
        if should_send_to_group():
            text = f"🚚 Yetkazib berish #{order_id}\n"
            text += f"Haydovchi: {order['driver_name']}\n"
            text += f"Mashina: {order['car_number']}\n"
            text += f"Manzil: {order['address']}\n"
            text += f"Yuk: {order['cargo']}\n\n"
            text += f"Holat: 🚚 Yo‘lda\n"
            text += f"Oldi: {t}\n"
            text += f"Oxirgi bosqich: Vazifani oldi — {t}\n"

            try:
                msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, disable_web_page_preview=True)
                await asyncio.to_thread(update_order, order_id, {'group_message_id': msg.message_id, 'current_status': 'take_delivery', 'start_time': datetime.now(tz).isoformat()})
            except Exception as e:
                logger.error(f"Error sending to group: {e}")
        else:
            await asyncio.to_thread(update_order, order_id, {'current_status': 'take_delivery', 'start_time': datetime.now(tz).isoformat()})

    asyncio.create_task(bg_task())

    await callback.message.edit_text("✅ Yetkazib berish qabul qilindi.")
    await callback.message.answer("A-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb("A", order_id))

ZONES = ["A", "B", "C", "D"]

@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await callback.answer("✅ Qabul qilindi")
    parts = callback.data.split("_")
    zone = parts[1]
    val = parts[2]
    order_id = parts[3]
    
    t = get_current_time()
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {
            'order_id': order_id,
            'step_name': f'zone_{zone}',
            'step_value': val,
            'time_text': t
        })
        order = await asyncio.to_thread(get_order, order_id)
        if order:
            await asyncio.to_thread(update_driver_status_sheet, order['car_number'], order['driver_name'], order['driver_telegram_id'], 'YUK ORTYAPTI', order_id)

    asyncio.create_task(bg_task())
    
    action_text = "✅ Oldim" if val == 'y' else "❌ Olmadim"
    await callback.message.edit_text(f"{zone}-blok: {action_text}")
    
    current_index = ZONES.index(zone)
    if current_index + 1 < len(ZONES):
        next_zone = ZONES[current_index + 1]
        await callback.message.answer(f"{next_zone}-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb(next_zone, order_id))
    else:
        # After D-blok, ask for transit
        await callback.message.answer("Transit bormi?", reply_markup=kb.get_transit_kb(order_id))

@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext):
    await callback.answer("✅ Qabul qilindi")
    parts = callback.data.split("_")
    val = parts[1] # y or n
    order_id = parts[2]
    
    transit_exists = (val == 'y')
    transit_status = "Ha, bor" if transit_exists else "Yo'q"
    
    await asyncio.to_thread(update_order, order_id, {
        'transit_exists': transit_exists,
        'transit_status': transit_status
    })
    
    await callback.message.edit_text(f"Transit: {transit_status}")
    
    await state.update_data(order_id=order_id)
    await state.set_state(DeliveryProcess.waiting_for_load_photo)
    await callback.message.answer("📸 Mashinaga ortilgan yuk rasmini yuboring.")

@router.message(DeliveryProcess.waiting_for_load_photo, F.photo)
async def process_load_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    photo_id = message.photo[-1].file_id
    t = get_current_time()
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {
            'order_id': order_id,
            'step_name': 'photo_load',
            'time_text': t,
            'photo_file_id': photo_id
        })
        await update_group_message(bot, order_id)
        
    asyncio.create_task(bg_task())
    
    await message.answer("Rasm qabul qilindi. Yo'lga chiqqaningizda tugmani bosing.", reply_markup=kb.get_driving_kb(order_id))
    await state.clear()

@router.callback_query(F.data.startswith("start_drive_"))
async def start_drive(callback: CallbackQuery, bot: Bot):
    await callback.answer("✅ Qabul qilindi")
    order_id = callback.data.split("start_drive_")[1]
    t = get_current_time()
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'start_drive', 'time_text': t})
        await update_group_message(bot, order_id)
        order = await asyncio.to_thread(get_order, order_id)
        if order:
            await asyncio.to_thread(update_driver_status_sheet, order['car_number'], order['driver_name'], order['driver_telegram_id'], 'YO‘LDA', order_id)
            
    asyncio.create_task(bg_task())
    
    await callback.message.edit_text("Siz yo'ldasiz. Manzilga yetib kelgach tugmani bosing.", reply_markup=kb.get_arrived_kb(order_id))

@router.callback_query(F.data.startswith("arrived_"))
async def arrived(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await callback.answer("✅ Qabul qilindi")
    order_id = callback.data.split("arrived_")[1]
    t = get_current_time()
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'arrived', 'time_text': t})
        await update_group_message(bot, order_id)
        order = await asyncio.to_thread(get_order, order_id)
        if order:
            await asyncio.to_thread(update_driver_status_sheet, order['car_number'], order['driver_name'], order['driver_telegram_id'], 'YETIB BORDI', order_id)
            
    asyncio.create_task(bg_task())
    
    await callback.message.edit_text("📍 Manzilga yetib keldingiz.")
    await state.update_data(order_id=order_id)
    await state.set_state(DeliveryProcess.waiting_for_location)
    await callback.message.answer("📍 Iltimos, lokatsiyani yuboring.", reply_markup=kb.get_request_location_kb())

@router.message(DeliveryProcess.waiting_for_location, F.location)
async def process_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    lat = message.location.latitude
    lng = message.location.longitude
    t = get_current_time()
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {
            'order_id': order_id,
            'step_name': 'location',
            'time_text': t,
            'location_lat': lat,
            'location_lng': lng
        })
        
    asyncio.create_task(bg_task())
    
    await state.set_state(DeliveryProcess.waiting_for_unload_photo)
    await message.answer("Lokatsiya qabul qilindi. 📸 Manzilga yetib kelganingizdagi yuk/mashina rasmini yuboring.", reply_markup=kb.remove_reply_kb())

@router.message(DeliveryProcess.waiting_for_unload_photo, F.photo)
async def process_obj_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    photo_id = message.photo[-1].file_id
    t = get_current_time()
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {
            'order_id': order_id,
            'step_name': 'photo_obj',
            'time_text': t,
            'photo_file_id': photo_id
        })
        
    asyncio.create_task(bg_task())
    
    await message.answer("Rasm qabul qilindi. Yuk tushirib bo'lingach tugmani bosing.", reply_markup=kb.get_finish_kb(order_id))
    await state.clear()

@router.callback_query(F.data.startswith("finish_"))
async def finish_delivery(callback: CallbackQuery, bot: Bot):
    await callback.answer("✅ Qabul qilindi")
    order_id = callback.data.split("finish_")[1]
    t = get_current_time()
    
    await callback.message.edit_text(f"✅ Yetkazib berish #{order_id} muvaffaqiyatli yakunlandi ({t})!")
    
    async def bg_task():
        now = datetime.now(tz)
        
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'finish', 'time_text': t})
        order = await asyncio.to_thread(get_order, order_id)
        
        # Calculate duration
        start_time_obj = None
        duration_minutes = None
        if order and order.get('start_time'):
            try:
                start_time_obj = datetime.fromisoformat(order['start_time'])
                duration_td = now - start_time_obj
                duration_minutes = int(duration_td.total_seconds() / 60)
            except:
                pass

        update_payload = {
            'current_status': 'DONE', 
            'completed_at': now.isoformat()
        }
        if duration_minutes is not None:
            update_payload['duration_minutes'] = duration_minutes
            
        await asyncio.to_thread(update_order, order_id, update_payload)
        
        if order:
            await asyncio.to_thread(update_driver_status_sheet, order['car_number'], order['driver_name'], order['driver_telegram_id'], 'BO‘SH', '')

        await update_group_message(bot, order_id)
        
        if should_send_to_group():
            steps = await asyncio.to_thread(get_order_steps, order_id)
            photos = []
            for s in steps:
                if s['step_name'] in ['photo_load', 'photo_obj'] and s.get('photo_file_id'):
                    caption = "Yuklangan rasm" if s['step_name'] == 'photo_load' else "Manzildagi rasm"
                    photos.append(InputMediaPhoto(media=s['photo_file_id'], caption=f"{caption} (#{order_id})"))
            
            if photos:
                try:
                    await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=photos)
                except Exception as e:
                    logger.error(f"Error sending media group: {e}")
        
        from core.sheets import get_sheets_service
        from core.config import GOOGLE_SHEET_ID
        try:
            sheets = await asyncio.to_thread(get_sheets_service)
            if sheets:
                request = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range='orders!A:F')
                res = await asyncio.to_thread(request.execute)
                vals = res.get('values', [])
                for i, row in enumerate(vals):
                    if len(row) > 0 and row[0].strip() == order_id:
                        await asyncio.to_thread(update_order_status, i + 1, 'DONE')
                        break
        except Exception as e:
            logger.error(f"Error updating sheet to DONE: {e}")

    asyncio.create_task(bg_task())
