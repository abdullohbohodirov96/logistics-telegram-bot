import logging
import time
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, save_order_step, get_order_steps
from core.sheets import update_order_status, update_driver_status_sheet, get_sheets_service
from core.states import DeliveryProcess
import core.keyboards as kb
from core.utils import get_now, parse_dt, format_time, format_duration, get_order_start_time

router = Router()
logger = logging.getLogger(__name__)

def should_send_to_group():
    return bool(GROUP_CHAT_ID and str(GROUP_CHAT_ID) != "0")

async def update_group_message(bot: Bot, order_id: str, order: dict = None, steps: list = None):
    """Updates the group message. Optimized to take order/steps if already available."""
    if not should_send_to_group(): return
    
    if order is None:
        order = await asyncio.to_thread(get_order, order_id)
    if not order or not order.get('group_message_id'): return

    if steps is None:
        steps = await asyncio.to_thread(get_order_steps, order_id)
    
    start_time_dt = get_order_start_time(order, steps)
    start_time_text = format_time(start_time_dt)
    
    end_time_dt = parse_dt(order.get('completed_at'))
    end_time_text = format_time(end_time_dt)
    
    last_step_text = ""
    loc_lat = None
    loc_lng = None
    photo_load = "yo'q"
    photo_obj = "yo'q"
    is_done = order.get('current_status') == 'DONE'
    
    for s in steps:
        name = s['step_name']
        t = s.get('time_text', '')
        if name == 'take_delivery': last_step_text = f"Vazifani oldi — {t}"
        elif name.startswith('zone_'):
            z = name.split('_')[1]
            last_step_text = f"{z}-blok {'oldi' if s.get('step_value') == 'y' else 'olmadi'} — {t}"
        elif name == 'photo_load':
            photo_load = "bor"
            last_step_text = f"Yuklangan rasm yuborildi — {t}"
        elif name == 'start_drive': last_step_text = f"Yo‘lga chiqdi — {t}"
        elif name == 'arrived': last_step_text = f"Manzilga yetdi — {t}"
        elif name == 'location':
            loc_lat, loc_lng = s.get('location_lat'), s.get('location_lng')
            last_step_text = f"Lokatsiya yuborildi — {t}"
        elif name == 'photo_obj':
            photo_obj = "bor"
            last_step_text = f"Manzildagi rasm yuborildi — {t}"
        elif name == 'finish':
            end_time_text = t
            is_done = True
            
    text = f"🚚 Yetkazib berish #{order_id}\n"
    text += f"Haydovchi: {order['driver_name']}\n"
    text += f"Mashina: {order['car_number']}\n"
    text += f"Manzil: {order['address']}\n"
    text += f"Yuk: {order['cargo']}\n"
    if order.get('transit_exists'): text += f"Transit: Ha\n"
    text += "\n"

    if is_done:
        text += f"Holat: ✅ Yakunlandi\n"
        text += f"Oldi: {start_time_text}\n"
        text += f"Yetkazdi: {end_time_text}\n"
        if order.get('duration_minutes'):
            text += f"Sarflangan vaqt: {format_duration(order['duration_minutes'])}\n"
        if loc_lat and loc_lng:
            text += f"Lokatsiya: https://maps.google.com/?q={loc_lat},{loc_lng}\n"
        text += f"\nRasmlar:\n📸 Yuklangan rasm: {photo_load}\n📸 Manzildagi rasm: {photo_obj}\n"
    else:
        text += f"Holat: 🚚 Yo‘lda\n"
        text += f"Oldi: {start_time_text}\n"
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
async def handle_take_delivery(callback: CallbackQuery, bot: Bot, state: FSMContext):
    t0 = time.time()
    await callback.answer() # Immediate answer
    order_id = callback.data.split("_")[1]
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    
    # Store order in state to avoid redundant GET calls
    await state.update_data(order=order, order_id=order_id)

    now = get_now()
    t = format_time(now)
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'take_delivery', 'time_text': t})
        await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BAND', order_id)
        
        if should_send_to_group():
            text = f"🚚 Yetkazib berish #{order_id}\nHaydovchi: {order['driver_name']}\nMashina: {order['car_number']}\nManzil: {order['address']}\nYuk: {order['cargo']}\n\nHolat: 🚚 Yo‘lda\nOldi: {t}\nOxirgi bosqich: Vazifani oldi — {t}\n"
            try:
                msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, disable_web_page_preview=True)
                await asyncio.to_thread(update_order, order_id, {
                    'group_message_id': msg.message_id, 
                    'current_status': 'take_delivery', 
                    'start_time': now.isoformat()
                })
            except Exception as e: logger.error(f"Error sending group msg: {e}")
        else:
            await asyncio.to_thread(update_order, order_id, {'current_status': 'take_delivery', 'start_time': now.isoformat()})

    asyncio.create_task(bg_task())
    await callback.message.edit_text("✅ Yetkazib berish qabul qilindi.")
    await callback.message.answer("A-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb("A", order_id))
    logger.info(f"start_order took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot, state: FSMContext):
    t0 = time.time()
    await callback.answer()
    parts = callback.data.split("_")
    zone, val, order_id = parts[1], parts[2], parts[3]
    t = format_time(get_now())
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': f'zone_{zone}', 'step_value': val, 'time_text': t})
        # We NO LONGER update Sheets here to save time
        await update_group_message(bot, order_id)

    asyncio.create_task(bg_task())
    await callback.message.edit_text(f"{zone}-blok: {'✅ Oldim' if val == 'y' else '❌ Olmadim'}")
    
    ZONES = ["A", "B", "C", "D"]
    idx = ZONES.index(zone)
    if idx + 1 < len(ZONES):
        next_z = ZONES[idx + 1]
        await callback.message.answer(f"{next_z}-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb(next_z, order_id))
    else:
        await callback.message.answer("Transit bormi?", reply_markup=kb.get_transit_kb(order_id))
    logger.info(f"zone_{zone} took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    t0 = time.time()
    await callback.answer()
    parts = callback.data.split("_")
    val, order_id = parts[1], parts[2]
    transit_exists = (val == 'y')
    
    async def bg_task():
        await asyncio.to_thread(update_order, order_id, {'transit_exists': transit_exists})
        await update_group_message(bot, order_id)

    asyncio.create_task(bg_task())
    transit_text = "Ha" if transit_exists else "Yo'q"
    await callback.message.edit_text(f"Transit: {transit_text}")
    await state.update_data(order_id=order_id)
    await state.set_state(DeliveryProcess.waiting_for_load_photo)
    await callback.message.answer("📸 Mashinaga ortilgan yuk rasmini yuboring.")
    logger.info(f"transit took {time.time()-t0:.2f}s")

@router.message(DeliveryProcess.waiting_for_load_photo, F.photo)
async def process_load_photo(message: Message, state: FSMContext, bot: Bot):
    t0 = time.time()
    data = await state.get_data()
    order_id = data.get('order_id')
    photo_id = message.photo[-1].file_id
    t = format_time(get_now())
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'photo_load', 'time_text': t, 'photo_file_id': photo_id})
        await update_group_message(bot, order_id)
        
    asyncio.create_task(bg_task())
    await message.answer("Rasm qabul qilindi. Yo'lga chiqqaningizda tugmani bosing.", reply_markup=kb.get_driving_kb(order_id))
    await state.clear()
    logger.info(f"photo_load took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("start_drive_"))
async def start_drive(callback: CallbackQuery, bot: Bot):
    t0 = time.time()
    await callback.answer()
    order_id = callback.data.split("start_drive_")[1]
    t = format_time(get_now())
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'start_drive', 'time_text': t})
        await update_group_message(bot, order_id)
            
    asyncio.create_task(bg_task())
    await callback.message.edit_text("Siz yo'ldasiz. Manzilga yetib kelgach tugmani bosing.", reply_markup=kb.get_arrived_kb(order_id))
    logger.info(f"start_drive took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("arrived_"))
async def arrived(callback: CallbackQuery, bot: Bot, state: FSMContext):
    t0 = time.time()
    await callback.answer()
    order_id = callback.data.split("arrived_")[1]
    t = format_time(get_now())
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'arrived', 'time_text': t})
        await update_group_message(bot, order_id)
            
    asyncio.create_task(bg_task())
    await callback.message.edit_text("📍 Manzilga yetib keldingiz.")
    await state.update_data(order_id=order_id)
    await state.set_state(DeliveryProcess.waiting_for_location)
    await callback.message.answer("📍 Iltimos, lokatsiyani yuboring.", reply_markup=kb.get_request_location_kb())
    logger.info(f"arrived took {time.time()-t0:.2f}s")

@router.message(DeliveryProcess.waiting_for_location, F.location)
async def process_location(message: Message, state: FSMContext, bot: Bot):
    t0 = time.time()
    data = await state.get_data()
    order_id = data.get('order_id')
    lat, lng = message.location.latitude, message.location.longitude
    t = format_time(get_now())
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'location', 'time_text': t, 'location_lat': lat, 'location_lng': lng})
        
    asyncio.create_task(bg_task())
    await state.set_state(DeliveryProcess.waiting_for_unload_photo)
    await message.answer("Lokatsiya qabul qilindi. 📸 Manzilga yetib kelganingizdagi yuk/mashina rasmini yuboring.", reply_markup=kb.remove_reply_kb())
    logger.info(f"location took {time.time()-t0:.2f}s")

@router.message(DeliveryProcess.waiting_for_unload_photo, F.photo)
async def process_obj_photo(message: Message, state: FSMContext, bot: Bot):
    t0 = time.time()
    data = await state.get_data()
    order_id = data.get('order_id')
    photo_id = message.photo[-1].file_id
    t = format_time(get_now())
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'photo_obj', 'time_text': t, 'photo_file_id': photo_id})
        
    asyncio.create_task(bg_task())
    await message.answer("Rasm qabul qilindi. Yuk tushirib bo'lingach tugmani bosing.", reply_markup=kb.get_finish_kb(order_id))
    await state.clear()
    logger.info(f"photo_obj took {time.time()-t0:.2f}s")

@router.callback_query(F.data.startswith("finish_"))
async def finish_delivery(callback: CallbackQuery, bot: Bot):
    t0 = time.time()
    await callback.answer()
    order_id = callback.data.split("finish_")[1]
    now = get_now()
    t = format_time(now)
    
    async def bg_task():
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'finish', 'time_text': t})
        order = await asyncio.to_thread(get_order, order_id)
        steps = await asyncio.to_thread(get_order_steps, order_id)
        
        start_time_dt = get_order_start_time(order, steps)
        duration_minutes = None
        if start_time_dt:
            try: duration_minutes = int((now - start_time_dt).total_seconds() / 60)
            except: pass

        update_payload = {'current_status': 'DONE', 'completed_at': now.isoformat()}
        if duration_minutes is not None: update_payload['duration_minutes'] = duration_minutes
        await asyncio.to_thread(update_order, order_id, update_payload)
        
        if order:
            await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BO‘SH', '')
            # Find and update row in ORDERS sheet
            try:
                from core.config import GOOGLE_SHEET_ID, ORDERS_SHEET_NAME
                sheets = await asyncio.to_thread(get_sheets_service)
                if sheets:
                    res = await asyncio.to_thread(sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f'{ORDERS_SHEET_NAME}!A:A').execute)
                    vals = res.get('values', [])
                    for i, row in enumerate(vals):
                        if row and row[0].strip() == order_id:
                            await asyncio.to_thread(update_order_status, i + 1, 'DONE')
                            break
            except Exception as e: logger.error(f"Error updating sheet: {e}")

        await update_group_message(bot, order_id, order, steps)
        
        finish_text = f"✅ Yetkazib berish #{order_id} yakunlandi\nBoshlagan vaqti: {format_time(start_time_dt)}\nTugagan vaqti: {t}\n"
        if duration_minutes is not None: finish_text += f"Ketgan vaqt: {format_duration(duration_minutes)}"
        await callback.message.edit_text(finish_text)
        
        if should_send_to_group():
            photos = [InputMediaPhoto(media=s['photo_file_id'], caption=f"{'Yuklangan' if s['step_name']=='photo_load' else 'Manzildagi'} rasm (#{order_id})") 
                      for s in steps if s['step_name'] in ['photo_load', 'photo_obj'] and s.get('photo_file_id')]
            if photos:
                try: await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=photos)
                except Exception as e: logger.error(f"Error sending photos: {e}")

    asyncio.create_task(bg_task())
    logger.info(f"finish took {time.time()-t0:.2f}s")
