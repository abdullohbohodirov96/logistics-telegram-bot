import logging
import time
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, save_order_step, get_order_steps
from core.sheets import update_order_status_detailed, update_driver_status_sheet, get_sheets_service, get_driver_by_tid
from core.states import DeliveryProcess
import core.keyboards as kb
from core.utils import get_now, parse_dt, format_time, format_duration, get_order_start_time

router = Router()
logger = logging.getLogger(__name__)

def should_send_to_group():
    return bool(GROUP_CHAT_ID and str(GROUP_CHAT_ID) != "0")

async def update_group_report(bot: Bot, order_id: str, status_text: str, is_finish: bool = False):
    """Sends/Updates a unified report in the logistika group."""
    if not should_send_to_group(): return
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    
    steps = await asyncio.to_thread(get_order_steps, order_id)
    start_time_dt = get_order_start_time(order, steps)
    start_time_text = format_time(start_time_dt) if start_time_dt else "-"
    
    now = get_now()
    finish_time_text = format_time(now) if is_finish else "-"
    
    text = "🚚 **LOGISTIKA HISOBOTI**\n"
    text += f"**Order ID:** #{order_id}\n"
    text += f"**Manzil:** {order['address']}\n"
    text += f"**Yuk:** {order['cargo']}\n"
    if order.get('comment'): text += f"**Izoh:** {order['comment']}\n"
    text += f"**Haydovchi:** {order['driver_name']}\n"
    text += f"**Mashina:** {order['car_number']}\n"
    text += f"**Status:** {status_text}\n"
    text += f"**Start vaqti:** {start_time_text}\n"
    
    if is_finish:
        text += f"**Finish vaqti:** {finish_time_text}\n"
        if start_time_dt:
            dur = int((now - start_time_dt).total_seconds() / 60)
            text += f"**Ketgan vaqt:** {format_duration(dur)}\n"

    try:
        if order.get('group_message_id'):
            await bot.edit_message_text(
                chat_id=GROUP_CHAT_ID,
                message_id=order['group_message_id'],
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        else:
            msg = await bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            await asyncio.to_thread(update_order, order_id, {'group_message_id': msg.message_id})
    except Exception as e:
        logger.error(f"Error updating group report: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await callback.answer()
    order_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    # 1. Check if driver is in the list
    driver = await asyncio.to_thread(get_driver_by_tid, user_id)
    if not driver:
        await callback.message.answer("❌ Siz haydovchilar ro'yxatida topilmadingiz.")
        return
    
    # 2. Check if driver is already busy
    if driver['status'] == 'BAND' and driver['current_order_id'] != order_id:
        await callback.message.answer(f"⚠️ Sizda yakunlanmagan order bor (#{driver['current_order_id']}). Avval uni tugating.")
        return

    # 3. Check if order is already taken
    order = await asyncio.to_thread(get_order, order_id)
    if order and order.get('current_status') == 'JARAYONDA':
        await callback.message.answer("⚠️ Bu order allaqachon boshqa haydovchi tomonidan olingan.")
        return

    now = get_now()
    t = format_time(now)
    
    async def bg_task():
        # Update internal DB
        await asyncio.to_thread(update_order, order_id, {
            'current_status': 'JARAYONDA',
            'start_time': now.isoformat(),
            'driver_name': driver['driver_name'],
            'car_number': driver['car_number']
        })
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'take_delivery', 'time_text': t})
        
        # Update Sheets
        await asyncio.to_thread(update_driver_status_sheet, driver['car_number'], 'BAND', order_id)
        # Find row in ORDERS sheet (this is slightly inefficient, ideally we'd pass row_index)
        # For simplicity, we'll search by order_id or use a helper
        await update_group_report(bot, order_id, "🚚 JARAYONDA")

    asyncio.create_task(bg_task())
    await callback.message.edit_text(f"✅ Order #{order_id} qabul qilindi.")
    await callback.message.answer("A-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb("A", order_id))

@router.callback_query(F.data.startswith("finish_"))
async def finish_delivery(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    order_id = callback.data.split("finish_")[1]
    now = get_now()
    t = format_time(now)
    
    async def bg_task():
        order = await asyncio.to_thread(get_order, order_id)
        if not order: return
        
        steps = await asyncio.to_thread(get_order_steps, order_id)
        start_time_dt = get_order_start_time(order, steps)
        duration_minutes = 0
        if start_time_dt:
            duration_minutes = int((now - start_time_dt).total_seconds() / 60)

        # Update Internal DB
        await asyncio.to_thread(update_order, order_id, {
            'current_status': 'YETKAZILDI',
            'completed_at': now.isoformat(),
            'duration_minutes': duration_minutes
        })
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'finish', 'time_text': t})
        
        # Update Sheets
        await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BO‘SH', '')
        
        # Final Group Report
        await update_group_report(bot, order_id, "✅ YETKAZILDI", is_finish=True)
        
        # Send photos to group
        photos = [InputMediaPhoto(media=s['photo_file_id'], caption=f"{'Yuklangan' if s['step_name']=='photo_load' else 'Manzildagi'} rasm (#{order_id})") 
                  for s in steps if s['step_name'] in ['photo_load', 'photo_obj'] and s.get('photo_file_id')]
        if photos:
            try: await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=photos)
            except: pass

        await callback.message.edit_text(f"✅ Order #{order_id} yakunlandi.\nSarflangan vaqt: {format_duration(duration_minutes)}")

    asyncio.create_task(bg_task())

# Zones, Transit, Photos handlers stay similar but call update_group_report
@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_")
    zone, val, order_id = parts[1], parts[2], parts[3]
    t = format_time(get_now())
    
    await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': f'zone_{zone}', 'step_value': val, 'time_text': t})
    await update_group_report(bot, order_id, f"🚚 {zone}-blok yuklandi")
    
    await callback.message.edit_text(f"{zone}-blok: {'✅ Oldim' if val == 'y' else '❌ Olmadim'}")
    ZONES = ["A", "B", "C", "D"]
    idx = ZONES.index(zone)
    if idx + 1 < len(ZONES):
        await callback.message.answer(f"{ZONES[idx+1]}-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb(ZONES[idx+1], order_id))
    else:
        await callback.message.answer("Transit bormi?", reply_markup=kb.get_transit_kb(order_id))
