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

def build_interim_report(order, lang="uz_latin"):
    order_id = order.get('order_id', '-')
    stage_history = order.get('stage_history') or []
    
    # Create a map for quick access
    history_map = {item['stage']: item for item in stage_history}
    
    def get_stage_status(label_key):
        label = _(label_key, lang)
        item = history_map.get(label) # History map keys are actual labels saved in DB
        # However, it's safer to check for common keys or translated keys
        # For blocks, we usually save "A-blok", "B-blok" etc. 
        # I'll check both keys.
        item = history_map.get(label) or history_map.get(label_key.replace("stage_", "").capitalize() + "-blok")
        
        if item:
            return f"{item.get('emoji', '✅')} {item.get('status', 'ORTDI')} ({item.get('completed_at', '-')})"
        return _("not_selected", lang)

    # Build transits section
    transits_text = ""
    transits = [item for item in stage_history if item['stage'].startswith("Transit") or item['stage'].startswith("Транзит")]
    if not transits:
        transits_text = f"🚚 {_('transit', lang)}: {get_status_icon(order.get('transit_status'))}"
    else:
        for t in transits:
            transits_text += f"{t.get('emoji', '✅')} {t['stage']}: {t['status']} ({t['completed_at']})\n"
        transits_text = transits_text.strip()

    text = (
        f"🚚 **LOGISTIKA HISOBOTI #{order_id}**\n"
        f"📍 **{_('address', lang)}:** {order.get('address', '-')}\n"
        f"📦 **{_('cargo', lang)}:** {order.get('cargo', '-')}\n"
        f"👤 **{_('driver', lang)}:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏗 **Yuklash:**\n"
        f"🅰️ {_('stage_a', lang)}: {get_stage_status('stage_a')}\n"
        f"🅱️ {_('stage_b', lang)}: {get_stage_status('stage_b')}\n"
        f"©️ {_('stage_c', lang)}: {get_stage_status('stage_c')}\n"
        f"🇩 {_('stage_d', lang)}: {get_stage_status('stage_d')}\n"
        f"{transits_text}\n\n"
        f"📊 **Status:** {order.get('current_status', 'NEW')}\n"
        f"⏰ **{_('started', lang)}:** {parse_dt(order.get('accepted_at')).strftime('%H:%M') if order.get('accepted_at') else '—'}\n"
    )
    return text

async def update_group_report(bot: Bot, order_id: str):
    if not GROUP_CHAT_ID or str(GROUP_CHAT_ID) == "0": return
    order = await asyncio.to_thread(get_order, order_id)
    if not order or order.get('current_status') == 'YAKUNLANDI': return
    
    # Group report always in Latin or default
    text = build_interim_report(order, lang="uz_latin")
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
    
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await state.update_data(order_id=order_id, stage_history=[])
    await state.set_state(DeliveryStates.BLOCK_MENU)
    
    try:
        await callback.message.edit_text(
            f"📦 **{_('order_accepted', lang)}**\n\n🆔 #{order_id}\n\n📦 **{_('stages', lang)}**\n\n"
            f"🅰️ {_('stage_a', lang)}: {_('not_selected', lang)}\n"
            f"🅱️ {_('stage_b', lang)}: {_('not_selected', lang)}\n"
            f"©️ {_('stage_c', lang)}: {_('not_selected', lang)}\n"
            f"🇩 {_('stage_d', lang)}: {_('not_selected', lang)}",
            reply_markup=kb.get_block_menu_kb(order_id, {}, lang)
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
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
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
        label = _(f"stage_{letter.lower()}", lang)
        await callback.message.edit_text(
            f"📦 **{_('id', lang)} #{order_id}**\n\n"
            f"**{label}**\n\n"
            f"Ushbu blokdan yuk oldingizmi?",
            reply_markup=kb.get_block_selection_kb(letter, order_id, lang)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("block_act_"))
async def handle_block_action(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    parts = callback.data.split("_")
    letter, status_type, order_id = parts[2], parts[3], parts[4]
    
    status = "ORTDI" if status_type == "ortdi" else "ORTMADI"
    emoji = "✅" if status_type == "ortdi" else "❌"
    color = "green" if status_type == "ortdi" else "red"
    
    now = get_now()
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    # Calculate duration
    if not stage_history:
        order = await asyncio.to_thread(get_order, order_id)
        start_time = parse_dt(order.get('accepted_at')) or now
    else:
        start_time = data.get('last_block_time') or now

    duration_seconds = int((now - start_time).total_seconds())
    label = _(f"stage_{letter.lower()}", lang)
    
    # Re-append if exists
    stage_history = [item for item in stage_history if item['stage'] != label]
    
    new_entry = {
        "stage": label,
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
    def get_stage_status(label_key):
        l = _(label_key, lang)
        item = history_map.get(l)
        if item:
            return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
        return _("not_selected", lang)

    try:
        await callback.message.edit_text(
            f"📦 **{_('id', lang)} #{order_id}**\n\n📦 **{_('stages', lang)}**\n\n"
            f"🅰️ {_('stage_a', lang)}: {get_stage_status('stage_a')}\n"
            f"🅱️ {_('stage_b', lang)}: {get_stage_status('stage_b')}\n"
            f"©️ {_('stage_c', lang)}: {get_stage_status('stage_c')}\n"
            f"🇩 {_('stage_d', lang)}: {get_stage_status('stage_d')}",
            reply_markup=kb.get_block_menu_kb(order_id, history_map, lang)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("block_back_"))
async def handle_block_back(callback: CallbackQuery, state: FSMContext):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    order_id = callback.data.split("_")[2]
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    await state.set_state(DeliveryStates.BLOCK_MENU)
    
    history_map = {item['stage']: item for item in stage_history}
    def get_stage_status(label_key):
        l = _(label_key, lang)
        item = history_map.get(l)
        if item:
            return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
        return _("not_selected", lang)

    try:
        await callback.message.edit_text(
            f"📦 **{_('id', lang)} #{order_id}**\n\n📦 **{_('stages', lang)}**\n\n"
            f"🅰️ {_('stage_a', lang)}: {get_stage_status('stage_a')}\n"
            f"🅱️ {_('stage_b', lang)}: {get_stage_status('stage_b')}\n"
            f"©️ {_('stage_c', lang)}: {get_stage_status('stage_c')}\n"
            f"🇩 {_('stage_d', lang)}: {get_stage_status('stage_d')}",
            reply_markup=kb.get_block_menu_kb(order_id, history_map, lang)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("confirm_blocks_"))
@router.callback_query(F.data.startswith("tr_start_"))
async def handle_tr_start(callback: CallbackQuery, state: FSMContext):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    order_id = callback.data.split("_")[2]
    await state.set_state(DeliveryStates.TRANSIT)
    try:
        await callback.message.edit_text(f"📦 **{_('id', lang)} #{order_id}**\n\nSavol: **{_('transit', lang)} yuk oldingizmi?**", reply_markup=kb.get_transit_kb(order_id, lang))
    except Exception: pass

@router.callback_query(F.data.startswith("tr_oldi_") | F.data.startswith("tr_olmadim_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
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
        label = f"{_('transit', lang)} 1"
        
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
                f"📦 **{_('id', lang)} #{order_id}**\n\n"
                f"🚚 **2-{_('transit', lang)}ingiz bormi?**",
                reply_markup=kb.get_transit_extra_kb(2, order_id, lang)
            )
        except Exception: pass
        
    else: # OLMADI
        status = "OLMADI"
        emoji = "❌"
        label = _('transit', lang)
        
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
            await callback.message.edit_text(f"📦 **{_('id', lang)} #{order_id}**\n\n📸 **{_('send_photo', lang)}**")
        except Exception: pass
        
    async def process_tr():
        await asyncio.to_thread(update_order, order_id, {'stage_history': stage_history, 'transit_at': now.isoformat(), 'transit_status': status})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_tr())

@router.callback_query(F.data.startswith("tr_oldi_") | F.data.startswith("tr_stop_"))
async def handle_transit_extra(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    parts = callback.data.split("_")
    action, num, order_id = parts[2], int(parts[3]), parts[4]
    
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    now = get_now()
    
    if action == "stop":
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        try:
            await callback.message.edit_text(f"📦 **{_('id', lang)} #{order_id}**\n\n📸 **{_('send_photo', lang)}**")
        except Exception: pass
        return

    # If action is "ha" (tr_oldi_)
    last_block_time = data.get('last_block_time') or now
    duration_seconds = int((now - last_block_time).total_seconds())
    
    status = "OLDI"
    emoji = "✅"
    label = f"{_('transit', lang)} {num}"
    
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
                f"📦 **{_('id', lang)} #{order_id}**\n\n"
                f"🚚 **{next_num}-{_('transit', lang)}ingiz bormi?**",
                reply_markup=kb.get_transit_extra_kb(next_num, order_id, lang)
            )
        except Exception: pass
    else:
        # Limit reached (4 transits)
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        try:
            await callback.message.edit_text(f"📦 **{_('id', lang)} #{order_id}**\n\n📸 **{_('send_photo', lang)}**")
        except Exception: pass

    async def process_tr_extra():
        await asyncio.to_thread(update_order, order_id, {'stage_history': stage_history, 'transit_at': now.isoformat(), 'transit_status': 'OLDI'})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_tr_extra())

@router.message(DeliveryStates.LOADED_PHOTO, F.photo | F.document)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await message.answer(_('order_not_found', lang))
        return
    await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': file_id, 'loaded_photo_at': now})
    await state.set_state(DeliveryStates.ON_WAY)
    addr = order.get('address', '-')
    
    msg_text = f"✅ {_('lang_saved', lang).replace('Til ', 'Rasm ')}\n\n📍 **{_('address', lang)}:** {addr}\n\n**{_('on_way', lang)}mi?**"
    await message.answer(msg_text, reply_markup=kb.get_step_kb(_('on_way_btn', lang), f"step_way_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.LOADED_PHOTO)
async def loaded_photo_wrong_input(message: Message):
    await message.answer("❌ Iltimos, faqat yuk rasmini yuboring.")

@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    await state.set_state(DeliveryStates.ACT_PHOTO)
    try: 
        msg_text = f"📦 **{_('id', lang)} #{order_id}**\n\nMijoz manziliga yetib borgach akt rasmini yuboring:\n\n📄 **{_('act_photo', lang)}ни yuboring**"
        await callback.message.edit_text(msg_text)
    except Exception: pass
    
    async def process_way():
        await asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'YOLDA'})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YOLDA')
        await update_group_report(bot, order_id)
    asyncio.create_task(process_way())

@router.message(DeliveryStates.ACT_PHOTO, F.photo | F.document)
async def handle_act_photo(message: Message, state: FSMContext, bot: Bot):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await message.answer(_('order_not_found', lang))
        return
    await asyncio.to_thread(update_order, order_id, {'act_photo_file_id': file_id, 'act_photo_at': now})
    await state.set_state(DeliveryStates.DELIVERED_LOC)
    await message.answer(f"✅ {_('act_photo', lang)} {_('lang_saved', lang).split('muvaf')[0].lower()}ilindi.\n\n📍 **{_('delivered_loc', lang)}ni yuboring**", reply_markup=kb.get_location_kb(lang))
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.ACT_PHOTO)
async def act_photo_wrong_input(message: Message):
    await message.answer("❌ Iltimos, faqat rasm yuboring (kamera yoki gallery).")

@router.message(DeliveryStates.DELIVERED_LOC, F.location)
async def handle_delivered_location(message: Message, state: FSMContext, bot: Bot):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    
    m = await message.answer("✅", reply_markup=ReplyKeyboardRemove())
    await m.delete()
    await state.set_state(DeliveryStates.WAITING_FINISH)
    await message.answer(f"✅ {_('location', lang)} {_('lang_saved', lang).split('muvaf')[0].lower()}ilindi.\n\n🏁 **{_('finish_order', lang)}**", reply_markup=kb.get_finish_kb(order_id, lang))
    
    async def process_loc():
        await asyncio.to_thread(update_order, order_id, {'delivered_lat': message.location.latitude, 'delivered_lng': message.location.longitude, 'delivered_location_at': now})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_loc())

@router.message(DeliveryStates.DELIVERED_LOC)
async def delivered_loc_wrong_input(message: Message):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    await message.answer(_('error_wrong_input', lang), reply_markup=kb.get_location_kb(lang))

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
    def get_emoji_line(label_key, status, dt1, dt2, success_val, emoji_ok="🟩", emoji_fail="🟥", ok_icon="✅", fail_icon="❌"):
        label = _(label_key, lang)
        if dt2:
            d_str = format_duration_detailed(get_seconds_diff(dt1, dt2))
            dt_formatted = parse_dt(dt2).strftime('%H:%M') if parse_dt(dt2) else ""
            is_ok = (status == success_val) if status else True
            st_text = status if status else ("YUBORILDI" if "location" in label_key else "OLINDI" if "photo" in label_key else "BOSILDI")
            icon = ok_icon if is_ok else fail_icon
            color = emoji_ok if is_ok else emoji_fail
            return f"{color} {label}: {icon} {st_text} — {d_str} ({dt_formatted})"
        else:
            return f"{emoji_fail} {label}: ❌ YUBORILMADI"

    # Last action time for next duration calculation
    last_action_at = stage_history[-1]['full_at'] if stage_history else order.get('accepted_at')
    
    line_yuk = get_emoji_line("loaded_photo", "", last_action_at, order.get('loaded_photo_at'), "", "📸", "📸")
    line_way = get_emoji_line("on_way", "", order.get('loaded_photo_at'), order.get('on_way_at'), "", "🛣", "🛣")
    line_act = get_emoji_line("act_photo", "", order.get('on_way_at'), order.get('act_photo_at'), "", "🧾", "🧾")
    line_loc = get_emoji_line("location", "", order.get('act_photo_at'), order.get('delivered_location_at'), "", "📍", "🟥")

    history_lines.extend([line_yuk, line_way, line_act, line_loc])
    etaplar_text = "\n".join(history_lines)

    drv_msg = (f"✅ **{_('finished', lang)}**\n\n🆔 {_('id', lang)}: #{order_id}\n⏰ {_('started', lang)}: {parse_dt(acc_at).strftime('%H:%M') if acc_at else '-'}\n🏁 {_('finished', lang)}: {parse_dt(fin_at).strftime('%H:%M')}\n⏳ {_('total_time', lang)}: {d_total}\n\n"
               f"📋 **{_('stages', lang)}:**\n"
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
            
            grp_text = (f"✅ **{_('finished', 'uz_latin')}**\n\n🆔 {_('id', 'uz_latin')}: #{order_id}\n⏰ {_('started', 'uz_latin')}: {parse_dt(acc_at).strftime('%H:%M') if acc_at else '-'}\n🏁 {_('finished', 'uz_latin')}: {parse_dt(fin_at).strftime('%H:%M')}\n⏳ {_('total_time', 'uz_latin')}: {d_total}\n\n"
                        f"👤 **{_('driver', 'uz_latin')}:** {order.get('driver_name', '-')}\n🚘 **{_('car', 'uz_latin')}:** {order.get('car_number', '-')}\n"
                        f"📍 **{_('address', 'uz_latin')}:** {order.get('address', '-')}\n📍 **{_('delivered_loc', 'uz_latin')}:** [Google Maps]({maps_url})\n\n📦 **{_('cargo', 'uz_latin')}:** {order.get('cargo', '-')}\n📝 **{_('comment', 'uz_latin')}:** {order.get('comment', '-')}\n\n"
                        f"📋 **{_('stages', 'uz_latin')}:**\n"
                        f"{etaplar_text}\n\n"
                        f"🟢 **{_('car_free', 'uz_latin')}:** {order.get('car_number', '-')}")
            
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
