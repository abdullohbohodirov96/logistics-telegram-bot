import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
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
    stage_history = order.get('stage_history') or []
    
    # Create a map for quick access
    history_map = {item['stage']: item for item in stage_history}
    
    def get_stage_status(label):
        item = history_map.get(label)
        if item:
            return f"{item.get('emoji', '✅')} {item.get('status', 'ORTDI')} ({item.get('completed_at', '-')})"
        return "⏳ Tanlanmagan"

    # Build transits section
    transits_text = ""
    transits = [item for item in stage_history if item['stage'].startswith("Transit")]
    if not transits:
        transits_text = f"🚚 Transit: {get_status_icon(order.get('transit_status'))}"
    else:
        for t in transits:
            transits_text += f"{t.get('emoji', '✅')} {t['stage']}: {t['status']} ({t['completed_at']})\n"
        transits_text = transits_text.strip()

    text = (
        f"🚚 **LOGISTIKA HISOBOTI #{order_id}**\n"
        f"📍 **Manzil:** {order.get('address', '-')}\n"
        f"📦 **Yuk:** {order.get('cargo', '-')}\n"
        f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏗 **Yuklash:**\n"
        f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
        f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
        f"©️ C-blok: {get_stage_status('C-blok')}\n"
        f"🇩 D-blok: {get_stage_status('D-blok')}\n"
        f"{transits_text}\n\n"
        f"📊 **Status:** {order.get('current_status', 'NEW')}\n"
        f"⏰ **Boshlandi:** {parse_dt(order.get('accepted_at')).strftime('%H:%M') if order.get('accepted_at') else '—'}\n"
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
            try:
                await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.error(f"Group report edit error: {e}")
        else:
            msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
            asyncio.create_task(asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)}))
    except Exception as e: logger.error(f"Group report error: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    
    # Check active orders limit
    from core.db import count_active_orders
    active_count = await asyncio.to_thread(count_active_orders, tid)
    if active_count >= 3:
        await callback.message.answer(_('too_many_active', lang))
        return

    if await state.get_state() is not None:
        # User might be in the middle of another order flow in FSM
        # But we now allow multiple orders. So we should probably allow 
        # starting another flow if they finished the previous one or if we manage multiple states.
        # However, Aiogram 3 FSM is usually per-user. 
        # To support multiple concurrent orders, we would need order_id in the state or separate states.
        # Given the current structure, we'll allow it but warn if they are in middle of one.
        pass

    order_id = callback.data.split("_")[1]
    logger.info(f"Take delivery clicked: order_id={order_id}, user={tid}, active={active_count}")
    
    await state.update_data(order_id=order_id, stage_history=[])
    await state.set_state(DeliveryStates.BLOCK_MENU)
    
    try:
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id} qabul qilindi.**\n\n📦 **Bloklardan yuk olish**\n\n"
            f"🅰️ A-blok: ⏳ Tanlanmagan\n"
            f"🅱️ B-blok: ⏳ Tanlanmagan\n"
            f"©️ C-blok: ⏳ Tanlanmagan\n"
            f"🇩 D-blok: ⏳ Tanlanmagan",
            reply_markup=kb.get_block_menu_kb(order_id, [])
        )
    except Exception: pass
    
    async def process_take():
        order = await asyncio.to_thread(get_order, order_id)
        if not order: return
        now = get_now().isoformat()
        await asyncio.to_thread(update_order, order_id, {'current_status': 'QABUL_QILINDI', 'accepted_at': now, 'driver_telegram_id': callback.from_user.id, 'driver_name': callback.from_user.full_name})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'QABUL_QILINDI')
        await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'YUK OGAN', order_id)
        await update_group_report(bot, order_id)
    asyncio.create_task(process_take())

@router.callback_query(F.data.startswith("sel_block_"))
async def handle_sel_block(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split("_")
    letter, order_id = parts[2], parts[3]
    
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    # Allow re-selection as per user request
    # if any(item['stage'] == f"{letter}-blok" for item in stage_history):
    #     await callback.answer("Bu blok allaqachon belgilangan", show_alert=True)
    #     return
        
    await state.set_state(DeliveryStates.BLOCK_SUBMENU)
    try:
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"**{letter}-blok**\n\n"
            f"Ushbu blokdan yuk oldingizmi?",
            reply_markup=kb.get_block_selection_kb(letter, order_id)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("block_act_"))
async def handle_block_action(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_")
    letter, status_type, order_id = parts[2], parts[3], parts[4]
    now = get_now()
    
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    # Calculate duration
    if not stage_history:
        # First block: from accepted_at
        order = await asyncio.to_thread(get_order, order_id)
        start_time = parse_dt(order.get('accepted_at')) or now
    else:
        # Subsequent blocks: from previous block in history
        last_item = stage_history[-1]
        # We need the full timestamp of the last item. We'll store it in FSM data.
        start_time = data.get('last_block_time') or now

    duration_seconds = int((now - start_time).total_seconds())
    
    # If block already exists, remove it (we'll re-append it to the end as "latest")
    stage_history = [item for item in stage_history if item['stage'] != f"{letter}-blok"]
    
    new_entry = {
        "stage": f"{letter}-blok",
        "status": status,
        "emoji": emoji,
        "color": color,
        "duration_seconds": duration_seconds,
        "completed_at": now.strftime("%H:%M"),
        "full_at": now.isoformat(),
        "sequence": len(stage_history) + 1
    }
    
    stage_history.append(new_entry)
    await state.update_data(stage_history=stage_history, last_block_time=now)
    await state.set_state(DeliveryStates.BLOCK_MENU)
    
    # Update Supabase
    async def process_update():
        await asyncio.to_thread(update_order, order_id, {
            'stage_history': stage_history,
            f'{letter.lower()}_block_status': status,
            f'{letter.lower()}_block_at': now.isoformat()
        })
        await update_group_report(bot, order_id)
    asyncio.create_task(process_update())
    
    # Show menu again
    history_map = {item['stage']: item for item in stage_history}
    def get_stage_status(label):
        item = history_map.get(label)
        if item:
            return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
        return "⏳ Tanlanmagan"

    try:
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\n📦 **Bloklardan yuk olish**\n\n"
            f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
            f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
            f"©️ C-blok: {get_stage_status('C-blok')}\n"
            f"🇩 D-blok: {get_stage_status('D-blok')}",
            reply_markup=kb.get_block_menu_kb(order_id, stage_history)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("block_back_"))
async def handle_block_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    order_id = callback.data.split("_")[2]
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    await state.set_state(DeliveryStates.BLOCK_MENU)
    
    history_map = {item['stage']: item for item in stage_history}
    def get_stage_status(label):
        item = history_map.get(label)
        if item:
            return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
        return "⏳ Tanlanmagan"

    try:
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\n📦 **Bloklardan yuk olish**\n\n"
            f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
            f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
            f"©️ C-blok: {get_stage_status('C-blok')}\n"
            f"🇩 D-blok: {get_stage_status('D-blok')}",
            reply_markup=kb.get_block_menu_kb(order_id, stage_history)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("confirm_blocks_"))
async def handle_confirm_blocks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    order_id = callback.data.split("_")[2]
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    if len(stage_history) < 4:
        await callback.answer("Avval A/B/C/D bloklarning hammasini belgilang", show_alert=True)
        return
        
    await state.set_state(DeliveryStates.TRANSIT)
    try:
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **Transitdan narsa oldingizmi?**", reply_markup=kb.get_transit_kb(order_id))
    except Exception: pass

@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data()
    order_id = data.get('order_id')
    if not order_id: return
    
    now = get_now()
    stage_history = data.get('stage_history') or []
    last_block_time = data.get('last_block_time') or now
    duration_seconds = int((now - last_block_time).total_seconds())
    
    if "tr_oldi_" in callback.data:
        status = "OLDI"
        emoji = "✅"
        label = "Transit 1"
        
        new_entry = {
            "stage": label,
            "status": status,
            "emoji": emoji,
            "color": "🚚",
            "duration_seconds": duration_seconds,
            "completed_at": now.strftime("%H:%M"),
            "full_at": now.isoformat(),
            "sequence": len(stage_history) + 1
        }
        stage_history.append(new_entry)
        await state.update_data(stage_history=stage_history, last_block_time=now)
        await state.set_state(DeliveryStates.TRANSIT_EXTRA)
        
        try:
            await callback.message.edit_text(
                f"📦 **Buyurtma #{order_id}**\n\n"
                f"🚚 **2-transitingiz bormi?**",
                reply_markup=kb.get_transit_extra_kb(2, order_id)
            )
        except Exception: pass
        
        async def process_tr():
            await asyncio.to_thread(update_order, order_id, {'stage_history': stage_history, 'transit_at': now.isoformat(), 'transit_status': status})
            await update_group_report(bot, order_id)
        asyncio.create_task(process_tr())
        
    else: # OLMADI
        status = "OLMADI"
        emoji = "❌"
        label = "Transit"
        
        new_entry = {
            "stage": label,
            "status": status,
            "emoji": emoji,
            "color": "🚚",
            "duration_seconds": duration_seconds,
            "completed_at": now.strftime("%H:%M"),
            "full_at": now.isoformat(),
            "sequence": len(stage_history) + 1
        }
        stage_history.append(new_entry)
        await state.update_data(stage_history=stage_history, last_block_time=now)
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        
        try:
            await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmni yuboring**")
        except Exception: pass
        
        async def process_tr():
            await asyncio.to_thread(update_order, order_id, {'stage_history': stage_history, 'transit_at': now.isoformat(), 'transit_status': status})
            await update_group_report(bot, order_id)
        asyncio.create_task(process_tr())

@router.callback_query(F.data.startswith("tr_extra_"))
async def handle_transit_extra(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_")
    action, num, order_id = parts[2], int(parts[3]), parts[4]
    
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    now = get_now()
    
    if action == "yoq":
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        try:
            await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmni yuboring**")
        except Exception: pass
        return

    # If action is "ha"
    last_block_time = data.get('last_block_time') or now
    duration_seconds = int((now - last_block_time).total_seconds())
    
    status = "OLDI"
    emoji = "✅"
    label = f"Transit {num}"
    
    new_entry = {
        "stage": label,
        "status": status,
        "emoji": emoji,
        "color": "🚚",
        "duration_seconds": duration_seconds,
        "completed_at": now.strftime("%H:%M"),
        "full_at": now.isoformat(),
        "sequence": len(stage_history) + 1
    }
    stage_history.append(new_entry)
    await state.update_data(stage_history=stage_history, last_block_time=now)
    
    if num < 4:
        # Ask for next transit
        next_num = num + 1
        try:
            await callback.message.edit_text(
                f"📦 **Buyurtma #{order_id}**\n\n"
                f"🚚 **{next_num}-transitingiz bormi?**",
                reply_markup=kb.get_transit_extra_kb(next_num, order_id)
            )
        except Exception: pass
    else:
        # Limit reached (4 transits)
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        try:
            await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmni yuboring**")
        except Exception: pass

    async def process_tr_extra():
        await asyncio.to_thread(update_order, order_id, {'stage_history': stage_history, 'transit_at': now.isoformat(), 'transit_status': 'OLDI'})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_tr_extra())

@router.message(DeliveryStates.LOADED_PHOTO, F.photo | F.document)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await message.answer("❌ Buyurtma topilmadi. Iltimos, qaytadan urinib ko'ring.")
        return
    await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': file_id, 'loaded_photo_at': now})
    await state.set_state(DeliveryStates.ON_WAY)
    addr = order.get('address', '-')
    await message.answer(f"✅ Rasm qabul qilindi.\n\n📍 **Manzil:** {addr}\n\n**Yo'lga chiqdingizmi?**", reply_markup=kb.get_step_kb("🚚 Yo'lga chiqdim", f"step_way_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.LOADED_PHOTO)
async def loaded_photo_wrong_input(message: Message):
    await message.answer("❌ Iltimos, faqat yuk rasmini yuboring.")

@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    await state.set_state(DeliveryStates.ACT_PHOTO)
    try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nMijoz manziliga yetib borgach akt rasmini yuboring:\n\n📄 **Qo'l qo'ydirilgan akt rasmini yuboring**")
    except Exception: pass
    
    async def process_way():
        await asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'YOLDA'})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YOLDA')
        await update_group_report(bot, order_id)
    asyncio.create_task(process_way())

@router.message(DeliveryStates.ACT_PHOTO, F.photo | F.document)
async def handle_act_photo(message: Message, state: FSMContext, bot: Bot):
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        return
    await asyncio.to_thread(update_order, order_id, {'act_photo_file_id': file_id, 'act_photo_at': now})
    await state.set_state(DeliveryStates.DELIVERED_LOC)
    await message.answer(f"✅ Akt qabul qilindi.\n\n📍 **Yetkazilgan joy lokatsiyasini yuboring**", reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.ACT_PHOTO)
async def act_photo_wrong_input(message: Message):
    await message.answer("❌ Iltimos, faqat rasm yuboring (kamera yoki gallery).")

@router.message(DeliveryStates.DELIVERED_LOC, F.location)
async def handle_delivered_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    
    m = await message.answer("✅", reply_markup=ReplyKeyboardRemove())
    await m.delete()
    await state.set_state(DeliveryStates.WAITING_FINISH)
    await message.answer(f"✅ Lokatsiya qabul qilindi.\n\nBuyurtmani yakunlash uchun tugmani bosing:", reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}"))
    
    async def process_loc():
        await asyncio.to_thread(update_order, order_id, {'delivered_lat': message.location.latitude, 'delivered_lng': message.location.longitude, 'delivered_location_at': now})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_loc())

@router.message(DeliveryStates.DELIVERED_LOC)
async def delivered_loc_wrong_input(message: Message):
    await message.answer("❌ Iltimos, faqat lokatsiya yuboring.", reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish"))

@router.callback_query(F.data.startswith("final_done_"))
async def handle_final_done(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    data = await state.get_data()
    order_id = data.get('order_id') or callback.data.split("_")[2]
    now = get_now().isoformat()
    
    logger.info(f"🏁 Finish button clicked: order_id={order_id}, user={tid}")
    
    if not order_id:
        logger.error(f"❌ Finish failed: No order_id found in state or callback for user {tid}")
        await callback.message.answer(_('order_not_found', lang))
        return
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        logger.error(f"❌ Finish failed: Order {order_id} not found in database")
        await callback.message.answer(_('order_not_found', lang))
        return

    logger.info(f"✅ Order found: {order_id}, current_status={order.get('current_status')}")

    if order.get('current_status') == 'YAKUNLANDI':
        logger.warning(f"⚠️ Finish skipped: Order {order_id} already finished")
        await callback.message.answer(_('order_already_finished', lang))
        await state.clear()
        return

    if not order.get('loaded_photo_file_id') or not order.get('act_photo_file_id'):
        logger.warning(f"⚠️ Finish blocked: Missing photos for order {order_id}")
        await callback.answer("❌ Yakunlash uchun yuk va akt rasmlari yuborilmagan!", show_alert=True)
        return

    await state.clear()
    logger.info(f"✅ State cleared for user {tid}, finishing order {order_id}")
    
    acc_at = order.get('accepted_at')
    fin_at = now
    d_total = format_duration_detailed(get_seconds_diff(acc_at, fin_at))
    
    def format_stage_line(label, status, emoji, color, duration_sec, comp_at):
        d_str = format_duration_detailed(duration_sec)
        return f"{color} {label}: {emoji} {status} — {d_str} ({comp_at})"

    stage_history = order.get('stage_history') or []
    history_lines = []
    
    for item in stage_history:
        line = format_stage_line(
            item['stage'], item['status'], item['emoji'], 
            item['color'], item['duration_seconds'], item['completed_at']
        )
        history_lines.append(line)
    
    # Other stages (sequential after blocks and transits)
    def get_emoji_line(label, status, dt1, dt2, success_val, emoji_ok="🟩", emoji_fail="🟥", ok_icon="✅", fail_icon="❌"):
        if dt2:
            d_str = format_duration_detailed(get_seconds_diff(dt1, dt2))
            dt_formatted = parse_dt(dt2).strftime('%H:%M') if parse_dt(dt2) else ""
            is_ok = (status == success_val) if status else True
            st_text = status if status else ("YUBORILDI" if "Lokatsiya" in label else "OLINDI" if "rasmi" in label else "BOSILDI")
            icon = ok_icon if is_ok else fail_icon
            color = emoji_ok if is_ok else emoji_fail
            return f"{color} {label}: {icon} {st_text} — {d_str} ({dt_formatted})"
        else:
            return f"{emoji_fail} {label}: ❌ YUBORILMADI"

    # Last action time for next duration calculation
    last_action_at = stage_history[-1]['full_at'] if stage_history else order.get('accepted_at')
    
    line_yuk = get_emoji_line("Yuk rasmi", "", last_action_at, order.get('loaded_photo_at'), "", "📸", "📸")
    line_way = get_emoji_line("Yo'lga chiqish", "", order.get('loaded_photo_at'), order.get('on_way_at'), "", "🛣", "🛣")
    line_act = get_emoji_line("Akt rasmi", "", order.get('on_way_at'), order.get('act_photo_at'), "", "🧾", "🧾")
    line_loc = get_emoji_line("Lokatsiya", "", order.get('act_photo_at'), order.get('delivered_location_at'), "", "📍", "🟥")

    history_lines.extend([line_yuk, line_way, line_act, line_loc])
    etaplar_text = "\n".join(history_lines)

    drv_msg = (f"✅ **Buyurtma yakunlandi**\n\n🆔 Buyurtma: #{order_id}\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M') if acc_at else '-'}\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\n⏳ Umumiy vaqt: {d_total}\n\n"
               f"📋 **Etaplar:**\n"
               f"{etaplar_text}\n")
    
    try: await callback.message.edit_text(drv_msg, parse_mode="Markdown")
    except Exception: pass
    
    async def finish_background():
        await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'current_status': 'YAKUNLANDI'})
        logger.info(f"💾 Database updated: Order {order_id} set to YAKUNLANDI")
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI')
        if GROUP_CHAT_ID:
            try:
                msg_id = order.get('group_message_id')
                if msg_id: await bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=int(msg_id))
            except: pass
            
            maps_url = f"https://maps.google.com/?q={order.get('delivered_lat')},{order.get('delivered_lng')}"
            
            grp_text = (f"✅ **Buyurtma yakunlandi**\n\n🆔 Buyurtma: #{order_id}\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M') if acc_at else '-'}\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\n⏳ Umumiy vaqt: {d_total}\n\n"
                        f"👤 **Haydovchi:** {order.get('driver_name', '-')}\n🚘 **Mashina:** {order.get('car_number', '-')}\n"
                        f"📍 **Manzil:** {order.get('address', '-')}\n📍 **Yetkazilgan lokatsiya:** [Google Maps]({maps_url})\n\n📦 **Yuk:** {order.get('cargo', '-')}\n📝 **Izoh:** {order.get('comment', '-')}\n\n"
                        f"📋 **Etaplar:**\n"
                        f"{etaplar_text}\n\n"
                        f"🟢 **Mashina bo'shadi:** {order.get('car_number', '-')}")
            
            media = [
                InputMediaPhoto(media=order['loaded_photo_file_id'], caption=grp_text, parse_mode="Markdown"),
                InputMediaPhoto(media=order['act_photo_file_id'])
            ]
            try: await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media)
            except Exception as e: logger.error(f"Failed to send media group: {e}")
        tid = callback.from_user.id
        res = await asyncio.to_thread(lambda: supabase.table('orders').select('id').eq('driver_telegram_id', tid).neq('current_status', 'YAKUNLANDI').execute())
        if not res.data: await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), "BO'SH", "")
    
    asyncio.create_task(finish_background())
@router.message(DeliveryStates())
async def handle_wrong_input(message: Message, state: FSMContext):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    curr = await state.get_state()
    if curr == DeliveryStates.ON_WAY: 
        await message.answer("⚠️ Iltimos, avval yuqoridagi **🚚 Yo'lga chiqdim** tugmasini bosing!")
    else: 
        await message.answer(_('error_wrong_input', lang))
