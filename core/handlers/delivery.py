import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, save_order_step, get_order_steps, supabase
from core.sheets import update_order_status_by_id, update_driver_status_sheet, get_driver_by_tid
import core.keyboards as kb
from core.utils import get_now, format_time, format_duration, get_order_start_time

router = Router()
logger = logging.getLogger(__name__)

def should_send_to_group():
    return bool(GROUP_CHAT_ID and str(GROUP_CHAT_ID) != "0")

async def update_group_report(bot: Bot, order_id: str, status_text: str = "YUBORILDI", is_finish: bool = False):
    if not should_send_to_group(): return
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    steps = await asyncio.to_thread(get_order_steps, order_id)
    start_time_dt = get_order_start_time(order, steps)
    now = get_now()
    zones = {"A": "⚪️", "B": "⚪️", "C": "⚪️", "D": "⚪️"}
    for s in steps:
        if s['step_name'].startswith("z_"):
            z = s['step_name'].split("_")[1].upper()
            if z in zones: zones[z] = "✅" if s.get('step_value') == 'y' else "❌"

    text = f"🚚 **LOGISTIKA HISOBOTI #{order_id}**\n"
    text += f"📍 **Manzil:** {order.get('address', '-')}\n"
    text += f"📦 **Yuk:** {order.get('cargo', '-')}\n"
    if order.get('comment'): text += f"📝 **Izoh:** {order['comment']}\n"
    text += f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
    text += f"➖➖➖➖➖➖➖➖➖➖\n"
    text += f"🏗 **Yuklash:** A:{zones['A']} B:{zones['B']} C:{zones['C']} D:{zones['D']}\n"
    text += f"📊 **Status:** {status_text}\n"
    if start_time_dt: text += f"⏰ **Boshlandi:** {format_time(start_time_dt)}\n"
    else: text += f"⏰ **Boshlandi:** ⏳ Kutilmoqda...\n"
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
                await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")
                return
            except Exception: pass
        msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
        asyncio.create_task(asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)}))
    except Exception as e: logger.error(f"Error in update_group_report: {e}")

@router.message(F.text == "🚚 Mening vazifalarim")
async def show_my_tasks(message: Message, bot: Bot):
    tid = message.from_user.id
    try:
        res = await asyncio.to_thread(lambda: supabase.table('orders').select('*').eq('driver_telegram_id', tid).neq('current_status', 'DONE').execute())
        active_orders = res.data
        if not active_orders:
            await message.answer("Sizda hozircha faol vazifalar yo'q.")
            return
        await message.answer(f"Sizda {len(active_orders)} ta faol vazifa bor:")
        for order in active_orders:
            order_id = order['order_id']
            status = order.get('current_status', 'NEW')
            msg = f"📦 **Buyurtma #{order_id}**\n📍 Manzil: {order['address']}\n📦 Yuk: {order['cargo']}\n📊 Status: {status}"
            kb_markup = None
            if status in ['NEW', 'SENT', 'YUBORILGAN']: kb_markup = kb.get_take_delivery_kb(order_id)
            elif status == 'OLDI' or status.startswith('z_') or status.startswith('zone_'):
                steps = await asyncio.to_thread(get_order_steps, order_id)
                kb_markup = kb.get_delivery_zones_kb(order_id, steps)
            elif status in ['TRANZIT', 'YETKAZISH']: kb_markup = kb.get_finish_kb(order_id)
            await message.answer(msg, reply_markup=kb_markup, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error showing tasks: {e}")
        await message.answer("Xatolik yuz berdi.")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("_")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.answer("Buyurtma topilmadi", show_alert=True)
        return
    now = get_now()
    asyncio.create_task(asyncio.to_thread(update_order, order_id, {'current_status': 'OLDI', 'start_time': now.isoformat()}))
    asyncio.create_task(asyncio.to_thread(update_order_status_by_id, order_id, 'OLDI'))
    asyncio.create_task(asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BAND', order_id))
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nYuklash bosqichlarini belgilang:",
        reply_markup=kb.get_delivery_zones_kb(order_id, [])
    )
    await callback.answer("Qabul qilindi!")
    asyncio.create_task(update_group_report(bot, order_id, "OLDI"))

@router.callback_query(F.data.startswith("zone_"))
async def handle_zones(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    zone, order_id = parts[1], parts[2]
    steps = await asyncio.to_thread(get_order_steps, order_id)
    current_val = next((s['step_value'] for s in steps if s['step_name'] == f"z_{zone}"), 'n')
    new_val = 'y' if current_val == 'n' else 'n'
    asyncio.create_task(asyncio.to_thread(save_order_step, order_id, f"z_{zone}", new_val))
    updated_steps = [s for s in steps if s['step_name'] != f"z_{zone}"]
    updated_steps.append({'step_name': f"z_{zone}", 'step_value': new_val})
    y_count = sum(1 for s in updated_steps if s['step_name'].startswith('z_') and s['step_value'] == 'y')
    await callback.message.edit_reply_markup(
        reply_markup=kb.get_delivery_zones_kb(order_id, updated_steps, show_transit=(y_count >= 1))
    )
    await callback.answer(f"{zone.upper()} yangilandi")
    asyncio.create_task(update_group_report(bot, order_id, f"{zone.upper()} blok {'✅' if new_val=='y' else '⚪️'}"))

@router.callback_query(F.data.startswith("transit_"))
async def handle_transit(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("_")[1]
    asyncio.create_task(asyncio.to_thread(update_order, order_id, {'current_status': 'TRANZIT'}))
    asyncio.create_task(asyncio.to_thread(update_order_status_by_id, order_id, 'TRANZIT'))
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nHolat: **TRANZIT**\n\nManzilga yetib borgach tugatishni bosing.",
        reply_markup=kb.get_finish_kb(order_id)
    )
    await callback.answer("Tranzitda")
    asyncio.create_task(update_group_report(bot, order_id, "TRANZIT"))

@router.callback_query(F.data.startswith("finish_"))
async def handle_finish(callback: CallbackQuery, bot: Bot):
    order_id = callback.data.split("_")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    now = get_now()
    asyncio.create_task(asyncio.to_thread(update_order, order_id, {'current_status': 'DONE', 'completed_at': now.isoformat()}))
    asyncio.create_task(asyncio.to_thread(update_order_status_by_id, order_id, 'DONE'))
    async def release_driver():
        tid = callback.from_user.id
        res = await asyncio.to_thread(lambda: supabase.table('orders').select('id').eq('driver_telegram_id', tid).neq('current_status', 'DONE').execute())
        if not res.data:
            await asyncio.to_thread(update_driver_status_sheet, order['car_number'], 'BO\'SH', "")
    asyncio.create_task(release_driver())
    await callback.message.edit_text(f"✅ **Buyurtma #{order_id} yakunlandi!**")
    await callback.answer("Tugallandi!")
    asyncio.create_task(update_group_report(bot, order_id, "DONE", is_finish=True))
