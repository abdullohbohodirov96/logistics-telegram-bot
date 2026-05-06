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
from core.utils import get_now, format_duration

router = Router()
logger = logging.getLogger(__name__)

# Helper to calculate differences between steps
def get_diff_text(start_at, end_at):
    if not start_at or not end_at: return ""
    try:
        s = datetime.fromisoformat(start_at.replace('Z', '+00:00'))
        e = datetime.fromisoformat(end_at.replace('Z', '+00:00'))
        diff = int((e - s).total_seconds() / 60)
        return f" (+{diff} daq)"
    except: return ""

async def update_group_report(bot: Bot, order_id: str):
    if not GROUP_CHAT_ID or str(GROUP_CHAT_ID) == "0": return
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return

    text = f"📦 **LOGISTIKA HISOBOTI #{order_id}**\n"
    text += f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
    text += f"➖➖➖➖➖➖➖➖➖➖\n"
    
    steps = [
        ("taken_at", "Olingan vaqt"),
        ("a_block_at", "A blok"),
        ("b_block_at", "B blok"),
        ("c_block_at", "C blok"),
        ("d_block_at", "D blok"),
        ("transit_at", "Transit"),
        ("loaded_photo_at", "Yuk rasmi"),
        ("on_way_at", "Yo'lga chiqdi"),
        ("arrived_at", "Yetib bordi"),
        ("delivered_photo_at", "Yetkazilgan rasm"),
        ("act_photo_at", "Akt rasmi"),
        ("final_proof_at", "Final proof"),
        ("finished_at", "Yakunlandi")
    ]
    
    prev_at = None
    for field, label in steps:
        val = order.get(field)
        if val:
            dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
            diff = get_diff_text(prev_at, val) if prev_at else ""
            text += f"✅ {label}: {dt.strftime('%H:%M')}{diff}\n"
            prev_at = val

    text += f"\n📊 **STATUS:** {order.get('current_status', 'NEW')}"
    
    if order.get('finished_at') and order.get('taken_at'):
        s = datetime.fromisoformat(order['taken_at'].replace('Z', '+00:00'))
        e = datetime.fromisoformat(order['finished_at'].replace('Z', '+00:00'))
        total_min = int((e - s).total_seconds() / 60)
        text += f"\n\n🏁 **Umumiy vaqt:** {format_duration(total_min)}"

    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")
        else:
            msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
            await asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)})
    except Exception as e:
        logger.error(f"Group report error: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split("_")[1]
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {
        'current_status': 'OLDI',
        'taken_at': now,
        'driver_telegram_id': callback.from_user.id,
        'driver_name': callback.from_user.full_name
    })
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'OLDI'))
    
    await state.update_data(order_id=order_id)
    await state.set_state(DeliveryStates.A_BLOCK)
    
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id} olingan.**\n\nSavol: **A blok mahsulot olindimi?**",
        reply_markup=kb.get_step_kb("✅ A blok olindi", f"step_a_{order_id}")
    )
    await callback.answer("✅ Buyurtma olindi")
    await update_group_report(bot, order_id)

@router.callback_query(F.data.startswith("step_a_"))
async def step_a(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'a_block_at': now})
    await state.set_state(DeliveryStates.B_BLOCK)
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nSavol: **B blok mahsulot olindimi?**",
        reply_markup=kb.get_step_kb("✅ B blok olindi", f"step_b_{order_id}")
    )
    await update_group_report(bot, order_id)

@router.callback_query(F.data.startswith("step_b_"))
async def step_b(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'b_block_at': now})
    await state.set_state(DeliveryStates.C_BLOCK)
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nSavol: **C blok mahsulot olindimi?**",
        reply_markup=kb.get_step_kb("✅ C blok olindi", f"step_c_{order_id}")
    )
    await update_group_report(bot, order_id)

@router.callback_query(F.data.startswith("step_c_"))
async def step_c(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'c_block_at': now})
    await state.set_state(DeliveryStates.D_BLOCK)
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nSavol: **D blok mahsulot olindimi?**",
        reply_markup=kb.get_step_kb("✅ D blok olindi", f"step_d_{order_id}")
    )
    await update_group_report(bot, order_id)

@router.callback_query(F.data.startswith("step_d_"))
async def step_d(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'d_block_at': now})
    await state.set_state(DeliveryStates.TRANSIT)
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\nSavol: **🚚 Transit qilindimi?**",
        reply_markup=kb.get_step_kb("✅ Transit qilindi", f"step_tr_{order_id}")
    )
    await update_group_report(bot, order_id)

@router.callback_query(F.data.startswith("step_tr_"))
async def step_tr(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'transit_at': now, 'current_status': 'TRANSIT'})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'TRANSIT'))
    await state.set_state(DeliveryStates.LOADED_PHOTO)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmni yuboring**")
    await update_group_report(bot, order_id)

@router.message(DeliveryStates.LOADED_PHOTO, F.photo)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    file_id = message.photo[-1].file_id
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': file_id, 'loaded_photo_at': now})
    await state.set_state(DeliveryStates.ON_WAY)
    await message.answer(
        f"✅ Rasm qabul qilindi.\n\n🚚 **Yo'lga chiqdingizmi?**",
        reply_markup=kb.get_step_kb("✅ Yo'lga chiqdim", f"step_way_{order_id}")
    )
    await update_group_report(bot, order_id)

@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'ON_WAY'})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'ON_WAY'))
    await state.set_state(DeliveryStates.ARRIVED_LOC)
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\n📍 **Yetib borgan lokatsiyani yuboring**",
        reply_markup=kb.get_location_kb()
    )
    await update_group_report(bot, order_id)

@router.message(DeliveryStates.ARRIVED_LOC, F.location)
async def handle_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    lat, lng = message.location.latitude, message.location.longitude
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {
        'arrived_location_lat': lat,
        'arrived_location_lng': lng,
        'arrived_at': now,
        'current_status': 'ARRIVED'
    })
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'ARRIVED'))
    await state.set_state(DeliveryStates.DELIVERED_PHOTO)
    await message.answer(
        f"✅ Lokatsiya qabul qilindi.\n\n📸 **Yetkazib berilgan mahsulot rasmini yuboring**",
        reply_markup=ReplyKeyboardRemove()
    )
    await update_group_report(bot, order_id)

@router.message(DeliveryStates.DELIVERED_PHOTO, F.photo)
async def handle_delivered_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    file_id = message.photo[-1].file_id
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'delivered_photo_file_id': file_id, 'delivered_photo_at': now})
    await state.set_state(DeliveryStates.ACT_PHOTO)
    await message.answer(f"✅ Rasm qabul qilindi.\n\n📄 **Akt rasmini yuboring**")
    await update_group_report(bot, order_id)

@router.message(DeliveryStates.ACT_PHOTO, F.photo)
async def handle_act_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    file_id = message.photo[-1].file_id
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'act_photo_file_id': file_id, 'act_photo_at': now})
    await state.set_state(DeliveryStates.FINAL_PROOF)
    await message.answer(f"✅ Akt qabul qilindi.\n\n🚛 **Moshina rasmi yoki qo'l qo'ydirilgan hujjat rasmini yuboring**")
    await update_group_report(bot, order_id)

@router.message(DeliveryStates.FINAL_PROOF, F.photo)
async def handle_final_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    file_id = message.photo[-1].file_id
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'final_proof_photo_file_id': file_id, 'final_proof_at': now})
    await state.set_state(DeliveryStates.WAITING_FINISH)
    await message.answer(
        f"✅ Barcha hujjatlar qabul qilindi.\n\n🏁 **Buyurtmani yakunlashni bosing**",
        reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}")
    )
    await update_group_report(bot, order_id)

@router.callback_query(F.data.startswith("final_done_"))
async def handle_final_done(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get('order_id')
    order = await asyncio.to_thread(get_order, order_id)
    now = get_now().isoformat()
    
    await asyncio.to_thread(update_order, order_id, {
        'finished_at': now,
        'current_status': 'DONE'
    })
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'DONE'))
    
    # Release driver
    tid = callback.from_user.id
    res = await asyncio.to_thread(lambda: supabase.table('orders').select('id').eq('driver_telegram_id', tid).neq('current_status', 'DONE').execute())
    if not res.data:
        await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BO\'SH', "")

    await state.clear()
    
    # Calculate final stats
    s_dt = datetime.fromisoformat(order['taken_at'].replace('Z', '+00:00'))
    e_dt = datetime.fromisoformat(now.replace('Z', '+00:00'))
    total_min = int((e_dt - s_dt).total_seconds() / 60)
    
    await callback.message.edit_text(
        f"✅ **Buyurtma yakunlandi**\n\n"
        f"🆔 Buyurtma ID: {order_id}\n"
        f"⏰ Olingan vaqt: {s_dt.strftime('%H:%M')}\n"
        f"🏁 Yakunlangan vaqt: {e_dt.strftime('%H:%M')}\n"
        f"⏳ Umumiy vaqt: {format_duration(total_min)}"
    )
    await callback.answer("✅ Muvaffaqiyatli yakunlandi")
    await update_group_report(bot, order_id)

# Fallback for wrong input
@router.message(DeliveryStates())
async def handle_wrong_input(message: Message, state: FSMContext):
    curr = await state.get_state()
    if curr == DeliveryStates.LOADED_PHOTO or curr == DeliveryStates.DELIVERED_PHOTO or \
       curr == DeliveryStates.ACT_PHOTO or curr == DeliveryStates.FINAL_PROOF:
        await message.answer("❌ Iltimos, rasm yuboring.")
    elif curr == DeliveryStates.ARRIVED_LOC:
        await message.answer("❌ Iltimos, lokatsiyani yuboring.", reply_markup=kb.get_location_kb())
    else:
        await message.answer("❌ Iltimos, yuqoridagi tugmalardan foydalaning.")
