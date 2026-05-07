import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, supabase
from core.sheets import update_order_status_by_order_id, update_driver_status_sheet
from core.states import DeliveryStates
import core.keyboards as kb
from core.utils import get_now, format_duration_detailed, parse_dt

router = Router()
logger = logging.getLogger(__name__)

def get_status_icon(status):
    if not status: return "⚪️"
    if status in ["ORTDI", "OLDI"]: return "✅"
    if status in ["ORTMADI", "OLMADI"]: return "❌"
    return "⚪️"

def get_seconds_diff(start_iso, end_iso):
    if not start_iso or not end_iso: return None
    try:
        s = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
        e = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
        return int((e - s).total_seconds())
    except: return None

def build_interim_report(order):
    order_id = order.get('order_id', '-')
    a_icon = get_status_icon(order.get('a_block_status'))
    b_icon = get_status_icon(order.get('b_block_status'))
    c_icon = get_status_icon(order.get('c_block_status'))
    d_icon = get_status_icon(order.get('d_block_status'))
    tr_icon = get_status_icon(order.get('transit_status'))
    
    acc_at = parse_dt(order.get('accepted_at'))
    acc_time = acc_at.strftime('%H:%M') if acc_at else "—"
    
    # Calculate stage durations for interim report
    now_iso = get_now().isoformat()
    d_a = format_duration_detailed(get_seconds_diff(order.get('accepted_at'), order.get('a_block_at')))
    d_b = format_duration_detailed(get_seconds_diff(order.get('a_block_at'), order.get('b_block_at')))
    d_c = format_duration_detailed(get_seconds_diff(order.get('b_block_at'), order.get('c_block_at')))
    d_d = format_duration_detailed(get_seconds_diff(order.get('c_block_at'), order.get('d_block_at')))
    d_tr = format_duration_detailed(get_seconds_diff(order.get('d_block_at'), order.get('transit_at')))
    
    # Calculate total elapsed time
    d_total = format_duration_detailed(get_seconds_diff(order.get('accepted_at'), now_iso))
    
    text = (
        f"🚚 **LOGISTIKA HISOBOTI #{order_id}**\n"
        f"📍 **Manzil:** {order.get('address', '-')}\n"
        f"📦 **Yuk:** {order.get('cargo', '-')}\n"
        f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏗 **Yuklash:**\n"
        f"A: {a_icon} ({d_a})  B: {b_icon} ({d_b})  C: {c_icon} ({d_c})  D: {d_icon} ({d_d})  Transit: {tr_icon} ({d_tr})\n\n"
        f"📊 **Status:** {order.get('current_status', 'NEW')}\n"
        f"⏰ **Boshlandi:** {acc_time}\n"
        f"⏳ **Ketgan vaqt:** {d_total}\n"
    )
    return text

async def update_group_report(bot: Bot, order_id: str):
    if not GROUP_CHAT_ID or str(GROUP_CHAT_ID) == "0": return
    order = await asyncio.to_thread(get_order, order_id)
    if not order or order.get('current_status') == 'YAKUNLANDI': return
    
    text = build_interim_report(order)
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
    order_id = callback.data.split("_")[1]; order = await asyncio.to_thread(get_order, order_id)
    if not order: return
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'current_status': 'QABUL_QILINDI', 'accepted_at': now, 'driver_telegram_id': callback.from_user.id, 'driver_name': callback.from_user.full_name})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'QABUL_QILINDI'))
    asyncio.create_task(asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BAND (QABUL QILDI)', order_id))
    await state.update_data(order_id=order_id); await state.set_state(DeliveryStates.A_BLOCK)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id} qabul qilindi.**\n\nSavol: **A-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb("A", order_id))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("block_"))
async def handle_blocks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    parts = callback.data.split("_"); letter, status, order_id = parts[1], parts[2].upper(), parts[3]; now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {f'{letter.lower()}_block_at': now, f'{letter.lower()}_block_status': status})
    next_map = {"A": ("B", DeliveryStates.B_BLOCK), "B": ("C", DeliveryStates.C_BLOCK), "C": ("D", DeliveryStates.D_BLOCK)}
    if letter in next_map:
        next_letter, next_state = next_map[letter]; await state.set_state(next_state)
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **{next_letter}-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb(next_letter, order_id))
    else:
        await state.set_state(DeliveryStates.TRANSIT)
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **Transitdan narsa oldingizmi?**", reply_markup=kb.get_transit_kb(order_id))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split("_")[-1]
    if not order_id or order_id == "None":
        order_id = (await state.get_data()).get('order_id')
    if not order_id or order_id == "None":
        await callback.answer("Buyurtma ID topilmadi", show_alert=True)
        return
    now = get_now().isoformat()
    status = "OLDI" if callback.data.startswith("tr_oldi_") else "OLMADI"
    await asyncio.to_thread(update_order, order_id, {'transit_at': now, 'transit_status': status})
    await state.set_state(DeliveryStates.LOADED_PHOTO)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmni yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))
    await callback.answer()

@router.message(DeliveryStates.LOADED_PHOTO, F.photo | F.document)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': file_id, 'loaded_photo_at': now})
    await state.set_state(DeliveryStates.ON_WAY); order = await asyncio.to_thread(get_order, order_id)
    await message.answer(f"✅ Rasm qabul qilindi.\n\n📍 **Manzil:** {order.get('address', '-')}\n\n**Yo'lga chiqdingizmi?**", reply_markup=kb.get_step_kb("🚚 Yo'lga chiqdim", f"step_way_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split("_")[-1]
    if not order_id or order_id == "None":
        order_id = (await state.get_data()).get('order_id')
    if not order_id or order_id == "None":
        await callback.answer("Buyurtma ID topilmadi", show_alert=True)
        return
    now = get_now().isoformat(); order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.answer("Buyurtma topilmadi", show_alert=True)
        return
    asyncio.create_task(asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'YOLDA'}))
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'YOLDA'))
    asyncio.create_task(asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BAND (YOLDA)', order_id))
    await state.set_state(DeliveryStates.ARRIVED_CLIENT)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n**Manzilga yetib borgangizmi?**", reply_markup=kb.get_step_kb("🚚 Borib bo'ldim", f"step_arrived_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))
    await callback.answer()

@router.callback_query(F.data.startswith("step_arrived_"))
async def step_arrived(callback: CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split("_")[-1]
    if not order_id or order_id == "None":
        order_id = (await state.get_data()).get('order_id')
    if not order_id or order_id == "None":
        await callback.answer("Buyurtma ID topilmadi", show_alert=True)
        return
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'arrived_at': now, 'current_status': 'MANZILDA'})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'MANZILDA'))
    await state.set_state(DeliveryStates.DELIVERED_LOC)
    await callback.message.edit_text(f"✅ Manzilga yetib buldingiz.\n\n📍 **Yetkazilgan joy lokatsiyasini yuboring**")
    await callback.message.answer("📍 Lokatsiyani yuboring:", reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish"))
    asyncio.create_task(update_group_report(bot, order_id))
    await callback.answer()

@router.message(DeliveryStates.ACT_PHOTO, F.photo | F.document)
async def handle_act_photo(message: Message, state: FSMContext, bot: Bot):
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'act_photo_file_id': file_id, 'act_photo_at': now})
    await state.set_state(DeliveryStates.WAITING_FINISH)
    await message.answer(f"✅ Akt rasmi qabul qilindi.\n\nBuyurtmani yakunlash uchun tugmani bosing:", reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.DELIVERED_LOC, F.location)
async def handle_delivered_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'delivered_lat': message.location.latitude, 'delivered_lng': message.location.longitude, 'delivered_location_at': now})
    await state.set_state(DeliveryStates.ACT_PHOTO)
    await message.answer(f"✅ Lokatsiya qabul qilindi.\n\n📄 **Qo'l qo'ydirilgan akt rasmini yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))

@router.callback_query(F.data.startswith("final_done_"))
async def handle_final_done(callback: CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split("_")[-1]
    if not order_id or order_id == "None":
        order_id = (await state.get_data()).get('order_id')
    if not order_id or order_id == "None":
        await callback.answer("Buyurtma ID topilmadi", show_alert=True)
        return
    now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.answer("Buyurtma topilmadi", show_alert=True)
        return
    
    # Update DB
    await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'current_status': 'YAKUNLANDI'})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI'))
    
    # 1. Driver Report
    acc_at = order.get('accepted_at')
    fin_at = now
    
    # Durations
    d_total = format_duration_detailed(get_seconds_diff(acc_at, fin_at))
    d_a = format_duration_detailed(get_seconds_diff(acc_at, order.get('a_block_at')))
    d_b = format_duration_detailed(get_seconds_diff(order.get('a_block_at'), order.get('b_block_at')))
    d_c = format_duration_detailed(get_seconds_diff(order.get('b_block_at'), order.get('c_block_at')))
    d_d = format_duration_detailed(get_seconds_diff(order.get('c_block_at'), order.get('d_block_at')))
    d_tr = format_duration_detailed(get_seconds_diff(order.get('d_block_at'), order.get('transit_at')))
    d_yuk = format_duration_detailed(get_seconds_diff(order.get('transit_at'), order.get('loaded_photo_at')))
    d_way = format_duration_detailed(get_seconds_diff(order.get('loaded_photo_at'), order.get('on_way_at')))
    d_arrived = format_duration_detailed(get_seconds_diff(order.get('on_way_at'), order.get('arrived_at')))
    d_loc = format_duration_detailed(get_seconds_diff(order.get('arrived_at'), order.get('delivered_location_at')))
    d_act = format_duration_detailed(get_seconds_diff(order.get('delivered_location_at'), order.get('act_photo_at')))

    drv_msg = (
        f"✅ **Buyurtma yakunlandi**\n\n"
        f"🆔 Buyurtma: #{order_id}\n"
        f"⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M')}\n"
        f"🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\n"
        f"⏳ Umumiy vaqt: {d_total}\n\n"
        f"**Yuklash etaplari:**\n"
        f"A-blok: {order.get('a_block_status','—')} | {d_a}\n"
        f"B-blok: {order.get('b_block_status','—')} | {d_b}\n"
        f"C-blok: {order.get('c_block_status','—')} | {d_c}\n"
        f"D-blok: {order.get('d_block_status','—')} | {d_d}\n"
        f"Transit: {order.get('transit_status','—')} | {d_tr}\n"
        f"Yuk rasmi: {d_yuk}\n\n"
        f"**Yetkazish etaplari:**\n"
        f"Yo'lga chiqish: {d_way}\n"
        f"Manzilga borish: {d_arrived}\n"
        f"Lokatsiya: {d_loc}\n"
        f"Akt rasmi: {d_act}\n"
    )
    await callback.message.edit_text(drv_msg, parse_mode="Markdown")
    
    # 2. Cleanup Group Message
    if GROUP_CHAT_ID:
        try:
            msg_id = order.get('group_message_id')
            if msg_id: await bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=int(msg_id))
        except Exception as e: logger.warning(f"Failed to delete group msg: {e}")
        
        # 3. Send Final Group Report
        a_i = get_status_icon(order.get('a_block_status'))
        b_i = get_status_icon(order.get('b_block_status'))
        c_i = get_status_icon(order.get('c_block_status'))
        d_i = get_status_icon(order.get('d_block_status'))
        t_i = get_status_icon(order.get('transit_status'))
        
        maps_url = f"https://maps.google.com/?q={order.get('delivered_lat')},{order.get('delivered_lng')}"
        
        grp_text = (
            f"🚚 **LOGISTIKA YAKUNI #{order_id}**\n\n"
            f"📍 **Manzil:** {order.get('address', '-')}\n"
            f"📍 **Yetkazilgan lokatsiya:** [Google Maps]({maps_url})\n\n"
            f"📦 **Yuk:** {order.get('cargo', '-')}\n"
            f"📝 **Izoh:** {order.get('comment', '-')}\n\n"
            f"👤 **Haydovchi:** {order.get('driver_name', '-')}\n"
            f"🚘 **Mashina:** {order.get('car_number', '-')}\n\n"
            f"🏗 **Yuklash:**\n"
            f"A: {a_i}  B: {b_i}  C: {c_i}  D: {d_i}  Transit: {t_i}\n\n"
            f"⏰ **Boshlandi:** {parse_dt(acc_at).strftime('%H:%M')}\n"
            f"🏁 **Tugadi:** {parse_dt(fin_at).strftime('%H:%M')}\n"
            f"⏳ **Ketgan vaqt:** {d_total}\n\n"
            f"📊 **Status:** YAKUNLANDI\n"
            f"🟢 **Mashina bo'shadi:** {order.get('car_number', '-')}"
        )
        await bot.send_message(chat_id=GROUP_CHAT_ID, text=grp_text, parse_mode="Markdown", disable_web_page_preview=False)
        
        # 4. Send Media Group
        media = []
        if order.get('loaded_photo_file_id'):
            media.append(InputMediaPhoto(media=order['loaded_photo_file_id'], caption=f"📸 Buyurtma rasmlari #{order_id}\n1) Yuk ortilgan rasm\n2) Qo'l qo'ydirilgan akt rasmi"))
        if order.get('act_photo_file_id'):
            media.append(InputMediaPhoto(media=order['act_photo_file_id']))
            
        if media:
            try: await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media)
            except Exception as e: logger.error(f"Media group error: {e}")

    # 5. Release Driver
    async def release_driver():
        tid = callback.from_user.id; res = await asyncio.to_thread(lambda: supabase.table('orders').select('id').eq('driver_telegram_id', tid).neq('current_status', 'YAKUNLANDI').execute())
        if not res.data: await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BO\'SH', "")
    asyncio.create_task(release_driver())
    
    await state.clear(); await callback.answer("✅ Yakunlandi")

@router.message(DeliveryStates())
async def handle_wrong_input(message: Message, state: FSMContext):
    curr = await state.get_state()
    if curr == DeliveryStates.ON_WAY:
        await message.answer("⚠️ Iltimos, avval yuqoridagi **🚚 Yo'lga chiqdim** tugmasini bosing!")
    elif curr in [DeliveryStates.LOADED_PHOTO, DeliveryStates.ACT_PHOTO]:
        await message.answer("❌ Iltimos, rasm yuboring.")
    elif curr == DeliveryStates.DELIVERED_LOC:
        await message.answer("❌ Iltimos, lokatsiyani yuboring.", reply_markup=kb.get_location_kb())
    else:
        await message.answer("❌ Iltimos, yuqoridagi tugmalardan foydalaning.")
