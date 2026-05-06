import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, supabase
from core.sheets import update_order_status_by_order_id, update_driver_status_sheet
from core.states import DeliveryStates
import core.keyboards as kb
from core.utils import get_now, format_duration, parse_dt

router = Router()
logger = logging.getLogger(__name__)

def get_status_icon(status):
    if not status: return "⚪️"
    if status in ["ORTDI", "OLDI"]: return "✅"
    if status in ["ORTMADI", "OLMADI"]: return "❌"
    return "⚪️"

def build_group_report_text(order):
    order_id = order.get('order_id', '-')
    
    a_icon = get_status_icon(order.get('a_block_status'))
    b_icon = get_status_icon(order.get('b_block_status'))
    c_icon = get_status_icon(order.get('c_block_status'))
    d_icon = get_status_icon(order.get('d_block_status'))
    tr_icon = get_status_icon(order.get('transit_status'))

    acc_at = parse_dt(order.get('accepted_at'))
    fin_at = parse_dt(order.get('finished_at'))
    
    acc_time = acc_at.strftime('%H:%M') if acc_at else "—"
    fin_time = fin_at.strftime('%H:%M') if fin_at else "—"
    
    duration = "—"
    if acc_at:
        end = fin_at or get_now()
        total_min = int((end - acc_at).total_seconds() / 60)
        duration = format_duration(total_min)

    text = (
        f"🚚 **LOGISTIKA HISOBOTI #{order_id}**\n"
        f"📍 **Manzil:** {order.get('address', '-')}\n"
        f"📦 **Yuk:** {order.get('cargo', '-')}\n"
        f"📝 **Izoh:** {order.get('comment', '-')}\n"
        f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏗 **Yuklash:**\n"
        f"A: {a_icon}  B: {b_icon}  C: {c_icon}  D: {d_icon}  Transit: {tr_icon}\n\n"
        f"📊 **Status:** {order.get('current_status', 'NEW')}\n"
        f"⏰ **Boshlandi:** {acc_time}\n"
        f"🏁 **Tugadi:** {fin_time}\n"
        f"⏳ **Ketgan vaqt:** {duration}\n"
    )
    
    if order.get('current_status') == 'YAKUNLANDI':
        text += f"\n🟢 **Mashina bo'shadi:** {order.get('car_number', '-')}"
        
    return text

async def update_group_report(bot: Bot, order_id: str):
    if not GROUP_CHAT_ID or str(GROUP_CHAT_ID) == "0": return
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    text = build_group_report_text(order)
    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")
        else:
            msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
            asyncio.create_task(asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)}))
    except Exception as e: logger.error(f"Group report error: {e}")

# 1. Take Delivery
@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split("_")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {
        'current_status': 'QABUL_QILINDI', 'accepted_at': now,
        'driver_telegram_id': callback.from_user.id, 'driver_name': callback.from_user.full_name
    })
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'QABUL_QILINDI'))
    asyncio.create_task(asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BAND (QABUL QILDI)', order_id))
    
    await state.update_data(order_id=order_id)
    await state.set_state(DeliveryStates.A_BLOCK)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id} qabul qilindi.**\n\nSavol: **A-blokdan narsa ortdingizmi?**", 
                                    reply_markup=kb.get_block_kb("A", order_id))
    asyncio.create_task(update_group_report(bot, order_id))

# Blocks A, B, C, D
@router.callback_query(F.data.startswith("block_"))
async def handle_blocks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    parts = callback.data.split("_")
    letter, status, order_id = parts[1], parts[2].upper(), parts[3]
    now = get_now().isoformat()
    
    update_data = {f'{letter.lower()}_block_at': now, f'{letter.lower()}_block_status': status}
    await asyncio.to_thread(update_order, order_id, update_data)
    
    next_map = {"A": ("B", DeliveryStates.B_BLOCK), "B": ("C", DeliveryStates.C_BLOCK), "C": ("D", DeliveryStates.D_BLOCK)}
    
    if letter in next_map:
        next_letter, next_state = next_map[letter]
        await state.set_state(next_state)
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **{next_letter}-blokdan narsa ortdingizmi?**", 
                                        reply_markup=kb.get_block_kb(next_letter, order_id))
    else:
        await state.set_state(DeliveryStates.TRANSIT)
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **Transitdan narsa oldingizmi?**", 
                                        reply_markup=kb.get_transit_kb(order_id))
    
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    status = "OLDI" if "tr_oldi_" in callback.data else "OLMADI"
    await asyncio.to_thread(update_order, order_id, {'transit_at': now, 'transit_status': status})
    await state.set_state(DeliveryStates.LOADED_PHOTO)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmni yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.LOADED_PHOTO, F.photo)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    file_id = message.photo[-1].file_id
    await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': file_id, 'loaded_photo_at': now})
    
    # Send photo to group
    if GROUP_CHAT_ID:
        try: await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=file_id, caption=f"📸 Yuk ortilgan rasm #{order_id}")
        except Exception as e: logger.error(f"Group photo error: {e}")

    await state.set_state(DeliveryStates.ON_WAY)
    order = await asyncio.to_thread(get_order, order_id)
    addr = order.get('address', '-')
    await message.answer(f"✅ Rasm qabul qilindi.\n\n📍 **Manzil:** {addr}\n\n**Yo'lga chiqdingizmi?**", 
                         reply_markup=kb.get_step_kb("🚚 Yo'lga chiqdim", f"step_way_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    asyncio.create_task(asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'YOLDA'}))
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'YOLDA'))
    asyncio.create_task(asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BAND (YOLDA)', order_id))
    await state.set_state(DeliveryStates.ACT_PHOTO) # Skip arrived button/delivered photo, go to ACT
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nMijoz manziliga yetib borgach akt rasmini yuboring:\n\n📄 **Qo'l qo'ydirilgan akt rasmini yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.ACT_PHOTO, F.photo)
async def handle_act_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    file_id = message.photo[-1].file_id
    await asyncio.to_thread(update_order, order_id, {'act_photo_file_id': file_id, 'act_photo_at': now})
    
    # Send photo to group
    if GROUP_CHAT_ID:
        try: await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=file_id, caption=f"📄 Qo'l qo'ydirilgan akt rasmi #{order_id}")
        except Exception as e: logger.error(f"Group photo error: {e}")

    await state.set_state(DeliveryStates.DELIVERED_LOC)
    await message.answer(f"✅ Akt qabul qilindi.\n\n📍 **Yetkazilgan joy lokatsiyasini yuboring**", 
                         reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.DELIVERED_LOC, F.location)
async def handle_delivered_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {
        'delivered_lat': message.location.latitude,
        'delivered_lng': message.location.longitude,
        'delivered_location_at': now
    })
    await state.set_state(DeliveryStates.WAITING_FINISH)
    await message.answer(f"✅ Lokatsiya qabul qilindi.\n\nBuyurtmani yakunlash uchun tugmani bosing:", 
                         reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("final_done_"))
async def handle_final_done(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'current_status': 'YAKUNLANDI'})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI'))
    
    async def release_driver():
        tid = callback.from_user.id
        res = await asyncio.to_thread(lambda: supabase.table('orders').select('id').eq('driver_telegram_id', tid).neq('current_status', 'YAKUNLANDI').execute())
        if not res.data: await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BO\'SH', "")
    
    asyncio.create_task(release_driver())
    
    # Simple finish message for driver
    await callback.message.edit_text(f"✅ **Buyurtma yakunlandi!**\n\nID: {order_id}\n\n🟢 Mashina bo'shadi: {order.get('car_number', '-')}")
    await state.clear(); await callback.answer("✅ Yakunlandi")
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates())
async def handle_wrong_input(message: Message, state: FSMContext):
    curr = await state.get_state()
    if curr in [DeliveryStates.LOADED_PHOTO, DeliveryStates.ACT_PHOTO]:
        await message.answer("❌ Iltimos, rasm yuboring.")
    elif curr == DeliveryStates.DELIVERED_LOC:
        await message.answer("❌ Iltimos, lokatsiyani yuboring.", reply_markup=kb.get_location_kb())
    else:
        await message.answer("❌ Iltimos, yuqoridagi tugmalardan foydalaning.")
