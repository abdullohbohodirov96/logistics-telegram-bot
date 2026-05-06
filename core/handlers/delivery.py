import logging
import asyncio
from datetime import datetime
import pytz
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID, TIMEZONE
from core.db import get_order, update_order, save_order_step, get_order_steps
from core.sheets import update_order_status, update_driver_status_sheet, get_driver_by_tid
import core.keyboards as kb
from core.utils import get_now, format_time, format_duration, get_order_start_time

router = Router()
logger = logging.getLogger(__name__)

def should_send_to_group():
    return bool(GROUP_CHAT_ID and str(GROUP_CHAT_ID) != "0")

async def update_group_report(bot: Bot, order_id: str, status_text: str = "YUBORILDI", is_finish: bool = False):
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
        if s['step_name'].startswith("z_"):
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
    
    if start_time_dt:
        text += f"⏰ **Boshlandi:** {format_time(start_time_dt)}\n"
    else:
        text += f"⏰ **Boshlandi:** ⏳ Kutilmoqda...\n"
    
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
                pass
        
        msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
        await asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)})
    except Exception as e:
        logger.error(f"Error in update_group_report: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("_")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.answer("Buyurtma topilmadi", show_alert=True)
        return

    now = get_now()
    await asyncio.to_thread(update_order, order_id, {
        'current_status': 'OLDI',
        'start_time': now.isoformat()
    })
    
    # Update Sheets
    await asyncio.to_thread(update_order_status, order['row_index'], 'OLDI')
    await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BAND', order_id)

    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nYuklash bosqichlarini belgilang:",
        reply_markup=kb.get_zone_kb("A", order_id)
    )
    await callback.answer("Buyurtma qabul qilindi!")
    await update_group_report(bot, order_id, "YUKLANDI (OLDI)")

@router.callback_query(F.data.startswith("z_"))
async def handle_zones(callback: CallbackQuery, bot: Bot):
    # z_{zone}_{val}_{order_id}
    parts = callback.data.split("_")
    zone = parts[1].upper()
    val = parts[2]
    order_id = parts[3]
    
    await asyncio.to_thread(save_order_step, order_id, f"z_{zone.lower()}", val)
    
    next_zones = {"A": "B", "B": "C", "C": "D"}
    if zone in next_zones:
        next_z = next_zones[zone]
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\nKeyingi bosqich: **{next_z}-blok**",
            reply_markup=kb.get_zone_kb(next_z, order_id)
        )
    else:
        # After D, ask for transit
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\nHamma bloklar yakunlandi. Transit bormi?",
            reply_markup=kb.get_transit_kb(order_id)
        )
    
    await callback.answer(f"{zone}-blok belgilandi")
    await update_group_report(bot, order_id, f"{zone}-blok {('✅' if val=='y' else '❌')}")

@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, bot: Bot):
    # tr_{val}_{order_id}
    parts = callback.data.split("_")
    val = parts[1]
    order_id = parts[2]
    
    status = "TRANZIT" if val == "y" else "YETKAZISH"
    order = await asyncio.to_thread(get_order, order_id)
    
    await asyncio.to_thread(update_order, order_id, {'current_status': status})
    if order:
        await asyncio.to_thread(update_order_status, order['row_index'], status)
    
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nHolat: **{status}**\n\nManzilga yetib borib, tushirib bo'lgach tugatishni bosing.",
        reply_markup=kb.get_finish_kb(order_id)
    )
    await callback.answer(f"Holat: {status}")
    await update_group_report(bot, order_id, status)

@router.callback_query(F.data.startswith("finish_"))
async def handle_finish(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("_")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return

    now = get_now()
    await asyncio.to_thread(update_order, order_id, {
        'current_status': 'DONE',
        'completed_at': now.isoformat()
    })
    
    # Sheets
    await asyncio.to_thread(update_order_status, order['row_index'], 'DONE')
    await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BO\'SH', "")

    await callback.message.edit_text(f"✅ **Buyurtma #{order_id} yakunlandi!**\n\nRahmat!")
    await callback.answer("Buyurtma yakunlandi!")
    await update_group_report(bot, order_id, "YAKUNLANDI (DONE)", is_finish=True)
