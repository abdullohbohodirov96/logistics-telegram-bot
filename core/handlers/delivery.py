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
    await callback.answer()
    if await state.get_state() is not None: return
    order_id = callback.data.split("_")[1]
    
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
    try:
        now = get_now()
        data = await state.get_data()
        stage_history = data.get('stage_history') or []
        
        # Determine status, emoji, color
        if status_type == "ortdi":
            status = "ORTDI"
            emoji = "✅"
            color = "🟩"
        elif status_type == "ortmadi":
            status = "ORTMADI"
            emoji = "❌"
            color = "🟥"
        else:
            status = status_type.upper()
            emoji = "⚪️"
            color = "⚪️"

        # Calculate duration
        if not stage_history:
            order = await asyncio.to_thread(get_order, order_id)
            start_time = parse_dt(order.get('accepted_at')) if order else now
            if not start_time: start_time = now
        else:
            start_time = data.get('last_block_time') or now

        duration_seconds = int((now - start_time).total_seconds())
        
        # Update stage history
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
            try:
                await asyncio.to_thread(update_order, order_id, {
                    'stage_history': stage_history,
                    f'{letter.lower()}_block_status': status,
                    f'{letter.lower()}_block_at': now.isoformat()
                })
                await update_group_report(bot, order_id)
            except Exception as e:
                logger.error(f"Error in background update: {e}")
        
        asyncio.create_task(process_update())
        
        # Show menu again
        history_map = {item['stage']: item for item in stage_history}
        def get_stage_status(label):
            item = history_map.get(label)
            if item:
                return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
            return "⏳ Tanlanmagan"

        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\n📦 **Bloklardan yuk olish**\n\n"
            f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
            f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
            f"©️ C-blok: {get_stage_status('C-blok')}\n"
            f"🇩 D-blok: {get_stage_status('D-blok')}",
            reply_markup=kb.get_block_menu_kb(order_id, stage_history)
        )

    except Exception as e:
        import traceback
        logger.error(f"Error in handle_block_action: {e}\n{traceback.format_exc()}")
        await callback.message.answer("⚠️ Amalda xatolik yuz berdi.")

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
    order_id = callback.data.split("_")[2]
    logger.info(f"[FINISH] Finish button clicked for order_id: {order_id}")
    await callback.answer()
    
    try:
        now = get_now().isoformat()
        order = await asyncio.to_thread(get_order, order_id)
        
        if not order:
            logger.warning(f"[FINISH] Order {order_id} not found in DB")
            await callback.message.answer("❌ Zakaz topilmadi.")
            return

        logger.info(f"[FINISH] Order {order_id} found. Current status: {order.get('current_status')}")

        if order.get('current_status') == 'YAKUNLANDI':
            logger.info(f"[FINISH] Order {order_id} already finished.")
            await callback.message.answer("✅ Bu zakaz allaqachon yakunlangan.")
            await state.clear()
            return

        if not order.get('loaded_photo_file_id') or not order.get('act_photo_file_id'):
            logger.warning(f"[FINISH] Order {order_id} missing photos.")
            await callback.answer("❌ Yakunlash uchun yuk va akt rasmlari yuborilmagan!", show_alert=True)
            return

        logger.info(f"[FINISH] All checks passed for order {order_id}. Finalizing...")
        await state.clear()
        
        acc_at = order.get('accepted_at')
        fin_at = now
        d_total = format_duration_detailed(get_seconds_diff(acc_at, fin_at))
        
        stage_history = order.get('stage_history') or []
        history_lines = []
        
        for item in stage_history:
            d_str = format_duration_detailed(item.get('duration_seconds'))
            line = f"{item.get('color', '🟩')} {item['stage']}: {item.get('emoji', '✅')} {item['status']} — {d_str} ({item.get('completed_at', '-')})"
            history_lines.append(line)
        
        # Last action time for next duration calculation
        last_action_at = stage_history[-1]['full_at'] if stage_history else order.get('accepted_at')
        
        def get_emoji_line(label, status, dt1, dt2, success_val, emoji_ok="🟩", emoji_fail="🟥", ok_icon="✅", fail_icon="❌"):
            if dt2:
                d_str = format_duration_detailed(get_seconds_diff(dt1, dt2))
                dt_formatted = parse_dt(dt2).strftime('%H:%M') if parse_dt(dt2) else ""
                is_ok = (status == success_val) if status else True
                st_text = status if status else ("YUBORILDI" if "Lokatsiya" in label else "OLINDI" if "rasmi" in label else "BOSILDI")
                icon = ok_icon if is_ok else fail_icon
                color = emoji_ok if is_ok else emoji_fail
                return f"{color} {label}: {icon} {st_text} — {d_str} ({dt_formatted})"
            return f"{emoji_fail} {label}: ❌ YUBORILMADI"

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
            try:
                logger.info(f"[FINISH] Updating DB for order {order_id}...")
                await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'completed_at': now, 'current_status': 'YAKUNLANDI'})
                await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI')
                
                if GROUP_CHAT_ID:
                    msg_id = order.get('group_message_id')
                    if msg_id:
                        try: await bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=int(msg_id))
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
                    await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media)
                
                # Check if driver has other active orders to decide car status
                tid = order.get('driver_telegram_id')
                from core.db import get_active_orders_count
                active_left = await asyncio.to_thread(get_active_orders_count, tid)
                if active_left == 0:
                    await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), "BO'SH", "")
                
                logger.info(f"[FINISH] Order {order_id} successfully finalized.")
            except Exception as e:
                import traceback
                logger.error(f"[FINISH] Background task error for order {order_id}: {e}\n{traceback.format_exc()}")
        
        asyncio.create_task(finish_background())

    except Exception as e:
        import traceback
        logger.error(f"[FINISH] Critical error finalizing order {order_id}: {e}\n{traceback.format_exc()}")
        await callback.message.answer("⚠️ Tizimda xatolik yuz berdi. Iltimos, admin bilan bog'laning.")
@router.message(DeliveryStates())
async def handle_wrong_input(message: Message, state: FSMContext):
    curr = await state.get_state()
    if curr == DeliveryStates.ON_WAY: await message.answer("⚠️ Iltimos, avval yuqoridagi **🚚 Yo'lga chiqdim** tugmasini bosing!")
    else: await message.answer("❌ Iltimos, yuqoridagi tugmalardan foydalaning.")
