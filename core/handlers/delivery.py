import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, save_order_step, get_order_steps
from core.sheets import update_order_status, update_driver_status_sheet, get_driver_by_tid
import core.keyboards as kb
from core.utils import get_now, format_time, format_duration, get_order_start_time

router = Router()
logger = logging.getLogger(__name__)

def should_send_to_group():
    return bool(GROUP_CHAT_ID and str(GROUP_CHAT_ID) != "0")

async def update_group_report(bot: Bot, order_id: str, status_text: str, is_finish: bool = False):
    """
    Updates a single group message per order with full status history.
    """
    if not should_send_to_group(): return
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    
    steps = await asyncio.to_thread(get_order_steps, order_id)
    start_time_dt = get_order_start_time(order, steps)
    now = get_now()
    
    # Zone status summary
    zones = {"A": "⚪️", "B": "⚪️", "C": "⚪️", "D": "⚪️"}
    for s in steps:
        if s['step_name'].startswith("zone_"):
            z = s['step_name'].split("_")[1].upper()
            if z in zones:
                zones[z] = "✅" if s.get('step_value') == 'y' else "❌"

    text = f"🚚 **LOGISTIKA HISOBOTI #{order_id}**\n"
    text += f"📍 **Manzil:** {order.get('address', '-')}\n"
    text += f"📦 **Yuk:** {order.get('cargo', '-')}\n"
    if order.get('comment'): text += f"📝 **Izoh:** {order['comment']}\n"
    text += f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
    text += f"➖➖➖➖➖➖➖➖➖➖\n"
    text += f"🏗 **Yuklash:** A:{zones['A']} B:{zones['B']} C:{zones['C']} D:{zones['D']}\n"
    text += f"📊 **Status:** {status_text}\n"
    text += f"⏰ **Boshlandi:** {format_time(start_time_dt)}\n"
    
    if is_finish:
        text += f"🏁 **Tugadi:** {format_time(now)}\n"
        if start_time_dt:
            dur = int((now - start_time_dt).total_seconds() / 60)
            text += f"⏳ **Ketgan vaqt:** {format_duration(dur)}\n"
        text += f"\n🟢 **Mashina bo'shadi:** {order.get('car_number', '-')}"

    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=GROUP_CHAT_ID,
                    message_id=int(msg_id),
                    text=text,
                    parse_mode="Markdown"
                )
                return
            except Exception:
                pass # Fallback to new message
        
        msg = await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=text,
            parse_mode="Markdown"
        )
        await asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)})
    except Exception as e:
        logger.error(f"Group report error: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    order_id = callback.data.split("_")[1]
    driver = await asyncio.to_thread(get_driver_by_tid, callback.from_user.id)
    if not driver:
        await callback.message.answer("❌ Ro'yxatda yo'qsiz.")
        return
    
    if driver['status'] == 'BAND' and driver['current_order_id'] != order_id:
        await callback.message.answer(f"⚠️ Sizda #{driver['current_order_id']} buyurtma bor.")
        return

    order = await asyncio.to_thread(get_order, order_id)
    if not order or order.get('current_status') == 'OLDI':
        await callback.message.answer("⚠️ Order olingan yoki mavjud emas.")
        return

    now = get_now()
    # Update Sheets & DB
    await asyncio.to_thread(update_order_status, int(order['id']), 'OLDI')
    await asyncio.to_thread(update_driver_status_sheet, driver['car_number'], 'BAND', order_id)
    await asyncio.to_thread(update_order, order_id, {
        'current_status': 'OLDI', 'start_time': now.isoformat(),
        'driver_name': driver['driver_name'], 'car_number': driver['car_number']
    })
    await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'take_delivery', 'time_text': format_time(now)})
    
    await update_group_report(bot, order_id, "🚚 Yuk olinmoqda")
    await callback.message.edit_text(f"✅ Buyurtma #{order_id} qabul qilindi.")
    await callback.message.answer("A-blok bo'yicha yuk oldingizmi?", reply_markup=kb.get_zone_kb("A", order_id))

@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    _, zone, val, order_id = callback.data.split("_")
    
    await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': f'zone_{zone}', 'step_value': val, 'time_text': format_time(get_now())})
    await update_group_report(bot, order_id, f"🏗 {zone}-blok yuklandi")
    
    status_icon = "✅ Ha" if val == 'y' else "❌ Yo'q"
    await callback.message.edit_text(f"{zone}-blok: {status_icon}")
    ZONES = ["A", "B", "C", "D"]
    idx = ZONES.index(zone)
    if idx + 1 < len(ZONES):
        await callback.message.answer(f"{ZONES[idx+1]}-blok yuk oldingizmi?", reply_markup=kb.get_zone_kb(ZONES[idx+1], order_id))
    else:
        await callback.message.answer("Transit bormi?", reply_markup=kb.get_transit_kb(order_id))

@router.callback_query(F.data.startswith("finish_"))
async def finish_delivery(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    order_id = callback.data.split("finish_")[1]
    now = get_now()
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    
    await asyncio.to_thread(update_order_status, int(order['id']), 'DONE')
    await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BO\'SH', '')
    await asyncio.to_thread(update_order, order_id, {'current_status': 'DONE', 'completed_at': now.isoformat()})
    await asyncio.to_thread(save_order_step, {'order_id': order_id, 'step_name': 'finish', 'time_text': format_time(now)})
    
    await update_group_report(bot, order_id, "✅ YETKAZIB BERILDI", is_finish=True)
    
    steps = await asyncio.to_thread(get_order_steps, order_id)
    start_time = get_order_start_time(order, steps)
    dur_text = f"\nKetgan vaqt: {format_duration(int((now - start_time).total_seconds() / 60))}" if start_time else ""
    await callback.message.edit_text(f"✅ Vazifa #{order_id} yakunlandi.{dur_text}")
