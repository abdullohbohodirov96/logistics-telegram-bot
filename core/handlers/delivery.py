import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, save_order_step, get_order_steps
from core.sheets import update_order_status, update_driver_status_sheet, get_driver_by_tid
from core.states import DeliveryProcess
import core.keyboards as kb
from core.utils import get_now, format_time, format_duration, get_order_start_time

router = Router()
logger = logging.getLogger(__name__)

def should_send_to_group():
    return bool(GROUP_CHAT_ID and str(GROUP_CHAT_ID) != "0")

async def update_group_report(bot: Bot, order_id: str, status_text: str, is_finish: bool = False):
    """
    Updates the group report using persistent Supabase data.
    """
    if not should_send_to_group(): return
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    
    # Recover start time from DB or steps
    steps = await asyncio.to_thread(get_order_steps, order_id)
    start_time_dt = get_order_start_time(order, steps)
    start_time_text = format_time(start_time_dt) if start_time_dt else "-"
    
    now = get_now()
    finish_time_text = format_time(now) if is_finish else "-"
    
    text = "🚚 **LOGISTIKA HISOBOTI**\n"
    text += f"**Order ID:** #{order_id}\n"
    text += f"**Manzil:** {order.get('address', '-')}\n"
    text += f"**Yuk:** {order.get('cargo', '-')}\n"
    if order.get('comment'): text += f"**Izoh:** {order['comment']}\n"
    text += f"**Haydovchi:** {order.get('driver_name', '-')}\n"
    text += f"**Mashina:** {order.get('car_number', '-')}\n"
    text += f"**Status:** {status_text}\n"
    text += f"**Start vaqti:** {start_time_text}\n"
    
    if is_finish:
        text += f"**Finish vaqti:** {finish_time_text}\n"
        if start_time_dt:
            dur = int((now - start_time_dt).total_seconds() / 60)
            text += f"**Ketgan vaqt:** {format_duration(dur)}\n"
        text += f"\n✅ **Mashina bo'shadi:** {order.get('car_number', '-')}"

    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=GROUP_CHAT_ID,
                    message_id=int(msg_id),
                    text=text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                return
            except Exception as e:
                logger.warning(f"Could not edit message {msg_id}, sending new one: {e}")
        
        # Send new if no ID or edit failed
        msg = await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        await asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)})
        
    except Exception as e:
        logger.error(f"Error in group report: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await callback.answer()
    order_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    driver = await asyncio.to_thread(get_driver_by_tid, user_id)
    if not driver:
        await callback.message.answer("❌ Siz ro'yxatda yo'qsiz.")
        return
    
    if driver['status'] == 'BAND' and driver['current_order_id'] != order_id:
        await callback.message.answer(f"⚠️ Sizda yakunlanmagan vazifa bor (#{driver['current_order_id']}).")
        return

    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.message.answer("❌ Order topilmadi.")
        return
        
    if order.get('current_status') == 'OLDI':
        await callback.message.answer("⚠️ Bu order allaqachon olingan.")
        return

    now = get_now()
    t_str = format_time(now)
    
    async def bg_task():
        # Update Sheets (6 columns limit for ORDERS)
        await asyncio.to_thread(update_order_status, int(order['id']), 'OLDI')
        await asyncio.to_thread(update_driver_status_sheet, driver['car_number'], 'BAND', order_id)
        
        # Persistent Update
        await asyncio.to_thread(update_order, order_id, {
            'current_status': 'OLDI',
            'start_time': now.isoformat(),
            'driver_name': driver['driver_name'],
            'car_number': driver['car_number']
        })
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'take_delivery', 'time_text': t_str})
        
        await update_group_report(bot, order_id, "🚚 OLDI")

    asyncio.create_task(bg_task())
    await callback.message.edit_text(f"✅ Vazifa #{order_id} qabul qilindi.")
    await callback.message.answer("A-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb("A", order_id))

@router.callback_query(F.data.startswith("finish_"))
async def finish_delivery(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    order_id = callback.data.split("finish_")[1]
    now = get_now()
    
    async def bg_task():
        order = await asyncio.to_thread(get_order, order_id)
        if not order: return
        
        # Update Sheets
        await asyncio.to_thread(update_order_status, int(order['id']), 'DONE')
        await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BO\'SH', '')
        
        # Final Group Report
        await update_group_report(bot, order_id, "✅ DONE", is_finish=True)
        
        # Cleanup
        await asyncio.to_thread(update_order, order_id, {
            'current_status': 'DONE',
            'completed_at': now.isoformat()
        })
        await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'finish', 'time_text': format_time(now)})

        # Duration calc for driver message
        steps = await asyncio.to_thread(get_order_steps, order_id)
        start_time_dt = get_order_start_time(order, steps)
        dur_text = ""
        if start_time_dt:
            dur = int((now - start_time_dt).total_seconds() / 60)
            dur_text = f"\nSarflangan vaqt: {format_duration(dur)}"
        
        await callback.message.edit_text(f"✅ Vazifa #{order_id} yakunlandi.{dur_text}")

    asyncio.create_task(bg_task())

@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_")
    zone, val, order_id = parts[1], parts[2], parts[3]
    
    await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': f'zone_{zone}', 'step_value': val, 'time_text': format_time(get_now())})
    await update_group_report(bot, order_id, f"🚚 {zone}-blok yuklandi")
    
    await callback.message.edit_text(f"{zone}-blok: {'✅ Oldim' if val == 'y' else '❌ Olmadim'}")
    ZONES = ["A", "B", "C", "D"]
    idx = ZONES.index(zone)
    if idx + 1 < len(ZONES):
        await callback.message.answer(f"{ZONES[idx+1]}-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb(ZONES[idx+1], order_id))
    else:
        await callback.message.answer("Transit bormi?", reply_markup=kb.get_transit_kb(order_id))
