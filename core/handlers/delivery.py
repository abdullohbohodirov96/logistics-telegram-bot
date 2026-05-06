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

def get_diff_text(start_at, end_at):
    if not start_at or not end_at: return ""
    try:
        s = datetime.fromisoformat(start_at.replace('Z', '+00:00'))
        e = datetime.fromisoformat(end_at.replace('Z', '+00:00'))
        diff = int((e - s).total_seconds() / 60)
        return f" (+{diff} m)"
    except: return ""

def build_full_report(order, now_iso=None):
    order_id = order.get('order_id', '-')
    text = f"📦 **BUYURTMA #{order_id} HISOBOTI**\n"
    text += f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
    text += f"➖➖➖➖➖➖➖➖➖➖\n"
    
    steps = [
        ("accepted_at", "Qabul qilindi"),
        ("a_block_at", "A blok"),
        ("b_block_at", "B blok"),
        ("c_block_at", "C blok"),
        ("d_block_at", "D blok")
    ]
    
    prev_at = None
    for field, label in steps:
        val = order.get(field)
        if val:
            dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
            diff = get_diff_text(prev_at, val) if prev_at else ""
            text += f"✅ {label}: {dt.strftime('%H:%M')}{diff}\n"
            prev_at = val
        else:
            text += f"⚪️ {label}: ⏳ Kutilmoqda\n"

    # Transit Special Handling
    tr_at = order.get('transit_at')
    tr_status = order.get('transit_status')
    if tr_at:
        dt = datetime.fromisoformat(tr_at.replace('Z', '+00:00'))
        diff = get_diff_text(prev_at, tr_at) if prev_at else ""
        icon = "✅" if tr_status == "OLDI" else "❌"
        text += f"{icon} Transit: {tr_status} | {dt.strftime('%H:%M')}{diff}\n"
        prev_at = tr_at
    else:
        text += f"⚪️ Transit: ⏳ Kutilmoqda | —\n"

    rest_steps = [
        ("loaded_photo_at", "Yuk rasmi"),
        ("on_way_at", "Yo'lga chiqdi"),
        ("delivered_photo_at", "Yetkazilgan rasm"),
        ("act_photo_at", "Akt rasmi"),
        ("delivered_location_at", "Lokatsiya"),
        ("finished_at", "Yakunlandi")
    ]
    
    for field, label in rest_steps:
        val = order.get(field)
        if field == "finished_at" and not val: val = now_iso
        
        if val:
            dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
            diff = get_diff_text(prev_at, val) if prev_at else ""
            text += f"✅ {label}: {dt.strftime('%H:%M')}{diff}\n"
            prev_at = val
        else:
            text += f"⚪️ {label}: ⏳ Kutilmoqda\n"

    if order.get('accepted_at'):
        s = datetime.fromisoformat(order['accepted_at'].replace('Z', '+00:00'))
        e = datetime.fromisoformat((order.get('finished_at') or now_iso or get_now().isoformat()).replace('Z', '+00:00'))
        total_min = int((e - s).total_seconds() / 60)
        text += f"\n🏁 **Umumiy vaqt:** {format_duration(total_min)}"
    
    text += f"\n📊 **STATUS:** {order.get('current_status', 'NEW')}"
    return text

async def update_group_report(bot: Bot, order_id: str):
    if not GROUP_CHAT_ID or str(GROUP_CHAT_ID) == "0": return
    order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    text = build_full_report(order)
    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")
        else:
            msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
            asyncio.create_task(asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)}))
    except Exception as e: logger.error(f"Group report error: {e}")

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
                                    reply_markup=kb.get_step_kb("✅ Ha, ortdim", f"step_a_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("step_a_"))
async def step_a(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'a_block_at': now})
    await state.set_state(DeliveryStates.B_BLOCK)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **B-blokdan narsa ortdingizmi?**", 
                                    reply_markup=kb.get_step_kb("✅ Ha, ortdim", f"step_b_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("step_b_"))
async def step_b(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'b_block_at': now})
    await state.set_state(DeliveryStates.C_BLOCK)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **C-blokdan narsa ortdingizmi?**", 
                                    reply_markup=kb.get_step_kb("✅ Ha, ortdim", f"step_c_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("step_c_"))
async def step_c(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'c_block_at': now})
    await state.set_state(DeliveryStates.D_BLOCK)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **D-blokdan narsa ortdingizmi?**", 
                                    reply_markup=kb.get_step_kb("✅ Ha, ortdim", f"step_d_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("step_d_"))
async def step_d(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'d_block_at': now})
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
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmini yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.LOADED_PHOTO, F.photo)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': message.photo[-1].file_id, 'loaded_photo_at': now})
    await state.set_state(DeliveryStates.ON_WAY)
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
    await state.set_state(DeliveryStates.ARRIVED_CLIENT)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSiz yo'ldasiz. Mijoz manziliga yetib borgach bosing:", 
                                    reply_markup=kb.get_step_kb("📍 Yetib keldim", f"step_arr_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("step_arr_"))
async def step_arrived(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id')
    await state.set_state(DeliveryStates.DELIVERED_PHOTO)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yetkazib berilgan mahsulot rasmini yuboring**")
    await callback.answer()

@router.message(DeliveryStates.DELIVERED_PHOTO, F.photo)
async def handle_delivered_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'delivered_photo_file_id': message.photo[-1].file_id, 'delivered_photo_at': now})
    await state.set_state(DeliveryStates.ACT_PHOTO)
    await message.answer(f"✅ Rasm qabul qilindi.\n\n📄 **Akt / qo'l qo'ydirilgan hujjat rasmini yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.ACT_PHOTO, F.photo)
async def handle_act_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'act_photo_file_id': message.photo[-1].file_id, 'act_photo_at': now})
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
    report = build_full_report(order, now)
    await callback.message.edit_text(f"✅ **Buyurtma yakunlandi!**\n\n{report}")
    await state.clear(); await callback.answer("✅ Yakunlandi")
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates())
async def handle_wrong_input(message: Message, state: FSMContext):
    curr = await state.get_state()
    if curr in [DeliveryStates.LOADED_PHOTO, DeliveryStates.DELIVERED_PHOTO, DeliveryStates.ACT_PHOTO]:
        await message.answer("❌ Iltimos, rasm yuboring.")
    elif curr == DeliveryStates.DELIVERED_LOC:
        await message.answer("❌ Iltimos, lokatsiyani yuboring.", reply_markup=kb.get_location_kb())
    else:
        await message.answer("❌ Iltimos, yuqoridagi tugmalardan foydalaning.")
