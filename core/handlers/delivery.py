import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, supabase, get_user
from core.sheets import update_order_status_by_order_id, update_driver_status_sheet
from core.states import DeliveryStates
import core.keyboards as kb
from core.utils import get_now, format_duration_detailed, parse_dt, get_seconds_diff
from core.i18n import _

router = Router()
logger = logging.getLogger(__name__)

def get_status_icon(status):
    if not status: return "⚪️"
    if status == "NEW": return "🆕"
    if status == "SENT": return "📩"
    if status == "QABUL_QILINDI": return "🤝"
    if status == "YUK_OLINDI": return "📸"
    if status == "YO'LDA": return "🛣"
    if status == "YAKUNLANDI": return "✅"
    return "🔹"

async def update_group_report(bot: Bot, order_id: str):
    order = await asyncio.to_thread(get_order, order_id)
    if not order or not GROUP_CHAT_ID: return
    
    icon = get_status_icon(order.get('current_status'))
    text = (f"{icon} **BUYURTMA #{order_id} HOLATI**\n\n"
            f"👤 **Haydovchi:** {order.get('driver_name', '-')}\n"
            f"🚘 **Mashina:** {order.get('car_number', '-')}\n"
            f"📍 **Manzil:** {order.get('address', '-')}\n"
            f"📦 **Yuk:** {order.get('cargo', '-')}\n"
            f"📊 **Holat:** {order.get('current_status', 'NEW')}\n")
            
    if order.get('comment'): text += f"📝 **Izoh:** {order['comment']}\n"
    
    sh = order.get('stage_history') or []
    if sh:
        text += "\n📋 **Bosqichlar:**\n"
        for item in sh:
            text += f"{item.get('emoji', '✅')} {item['stage']}: {item['status']} ({item.get('completed_at')})\n"

    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            try: await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")
            except TelegramBadRequest: pass
        else:
            msg = await bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="Markdown")
            await asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)})
    except Exception as e:
        logger.error(f"Error updating group report: {e}")

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
            reply_markup=kb.get_block_menu_kb(order_id, [], lang)
        )
    except Exception: pass
    
    async def process_take():
        order = await asyncio.to_thread(get_order, order_id)
        if not order: return
        now = get_now().isoformat()
        await asyncio.to_thread(update_order, order_id, {'current_status': 'QABUL_QILINDI', 'accepted_at': now, 'driver_telegram_id': tid, 'driver_name': callback.from_user.full_name})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'QABUL_QILINDI')
        await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'YUK OGAN', order_id)
        await update_group_report(bot, order_id)
    asyncio.create_task(process_take())

@router.callback_query(DeliveryStates.BLOCK_MENU, F.data.startswith("sel_block_"))
async def handle_block_select(callback: CallbackQuery, state: FSMContext):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    parts = callback.data.split("_")
    letter = parts[2]
    order_id = parts[3]
    
    await state.set_state(DeliveryStates.BLOCK_SUBMENU)
    await state.update_data(current_letter=letter)
    
    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\n**{letter}-blok**\n\nUshbu blokdan yuk oldingizmi?",
        reply_markup=kb.get_block_selection_kb(letter, order_id, lang)
    )

@router.callback_query(DeliveryStates.BLOCK_SUBMENU, F.data.startswith("block_act_"))
async def handle_block_action(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    parts = callback.data.split("_")
    letter, status_type, order_id = parts[2], parts[3], parts[4]
    now = get_now()
    
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    status = "ORTDI" if status_type == "ortdi" else "ORTMADI"
    emoji = "✅" if status == "ORTDI" else "❌"
    color = "🟩" if status == "ORTDI" else "🟥"
    
    # Calculate duration
    if not stage_history:
        order = await asyncio.to_thread(get_order, order_id)
        start_time = parse_dt(order.get('accepted_at')) or now
    else:
        start_time = data.get('last_block_time')
        if not start_time:
            # Fallback to previous history item
            start_time = parse_dt(stage_history[-1]['full_at']) or now
        else:
            if isinstance(start_time, str): start_time = parse_dt(start_time)
            
    duration_seconds = int((now - start_time).total_seconds())
    
    # Replace existing block if re-selected
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
    await state.update_data(stage_history=stage_history, last_block_time=now.isoformat())
    await state.set_state(DeliveryStates.BLOCK_MENU)
    
    async def process_update():
        await asyncio.to_thread(update_order, order_id, {
            'stage_history': stage_history,
            f'{letter.lower()}_block_status': status,
            f'{letter.lower()}_block_at': now.isoformat()
        })
        await update_group_report(bot, order_id)
    asyncio.create_task(process_update())
    
    history_map = {item['stage']: item for item in stage_history}
    def get_stage_status(lbl):
        item = history_map.get(lbl)
        if item: return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
        return "⏳ Tanlanmagan"

    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\n📦 **Bloklardan yuk olish**\n\n"
        f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
        f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
        f"©️ C-blok: {get_stage_status('C-blok')}\n"
        f"🇩 D-blok: {get_stage_status('D-blok')}",
        reply_markup=kb.get_block_menu_kb(order_id, history_map, lang)
    )

@router.callback_query(F.data.startswith("back_to_blocks_"))
async def handle_back_to_blocks(callback: CallbackQuery, state: FSMContext):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    order_id = callback.data.split("_")[3]
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    history_map = {item['stage']: item for item in stage_history}
    
    await state.set_state(DeliveryStates.BLOCK_MENU)
    
    def get_stage_status(lbl):
        item = history_map.get(lbl)
        if item: return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
        return "⏳ Tanlanmagan"

    await callback.message.edit_text(
        f"📦 **Buyurtma #{order_id}**\n\n📦 **Bloklardan yuk olish**\n\n"
        f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
        f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
        f"©️ C-blok: {get_stage_status('C-blok')}\n"
        f"🇩 D-blok: {get_stage_status('D-blok')}",
        reply_markup=kb.get_block_menu_kb(order_id, history_map, lang)
    )

@router.callback_query(DeliveryStates.BLOCK_MENU, F.data.startswith("tr_start_"))
async def handle_transit_start(callback: CallbackQuery, state: FSMContext):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    order_id = callback.data.split("_")[2]
    await state.set_state(DeliveryStates.TRANSIT_1)
    await callback.message.edit_text("🚚 Transit yuk oldingizmi?", reply_markup=kb.get_transit_kb(order_id, lang))

@router.callback_query(DeliveryStates.TRANSIT_1, F.data.startswith("tr_oldi_"))
async def handle_transit_oldi(callback: CallbackQuery, state: FSMContext):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    parts = callback.data.split("_")
    num, order_id = int(parts[2]), parts[3]
    
    await asyncio.to_thread(update_order, order_id, {f'transit_{num}_status': 'OLDI', 'transit_status': f'OLDI ({num})'})
    
    if num < 4:
        next_num = num + 1
        await state.set_state(getattr(DeliveryStates, f"TRANSIT_{next_num}"))
        await callback.message.edit_text(f"🚚 {next_num}-transitingiz bormi?", reply_markup=kb.get_transit_extra_kb(next_num, order_id, lang))
    else:
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        await callback.message.edit_text("📸 Yuklangan yuk rasmini yuboring (Yuk va rasm bitta kadrda bo'lsin).")

@router.callback_query(F.data.startswith("tr_olmadim_") | F.data.startswith("tr_stop_"))
async def handle_transit_stop(callback: CallbackQuery, state: FSMContext):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    order_id = callback.data.split("_")[2]
    
    if "olmadim" in callback.data:
        await asyncio.to_thread(update_order, order_id, {'transit_status': 'OLMADI'})
    
    await state.set_state(DeliveryStates.LOADED_PHOTO)
    await callback.message.edit_text("📸 Yuklangan yuk rasmini yuboring (Yuk va rasm bitta kadrda bo'lsin).")

@router.message(DeliveryStates.LOADED_PHOTO, F.photo)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    
    photo_id = message.photo[-1].file_id
    await state.set_state(DeliveryStates.ON_WAY)
    await message.answer("✅ Yuk rasmi qabul qilindi.\n\nEndi yo'lga chiqqanda tugmani bosing:", reply_markup=kb.get_step_kb("🚚 Yo'lga chiqdim", f"on_way_{order_id}"))
    
    async def process_photo():
        await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': photo_id, 'loaded_photo_at': now, 'current_status': 'YUK_OLINDI'})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YUK_OLINDI')
        await update_group_report(bot, order_id)
    asyncio.create_task(process_photo())

@router.callback_query(DeliveryStates.ON_WAY, F.data.startswith("on_way_"))
async def handle_on_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    tid = callback.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    await callback.answer()
    order_id = callback.data.split("_")[2]; now = get_now().isoformat()
    
    await state.set_state(DeliveryStates.ACT_PHOTO)
    await callback.message.edit_text("🛣 Oq yo'l!\n\nManzilga yetib borgach, imzolangan **AKT rasmini** yuboring.")
    
    async def process_way():
        await asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'YO\'LDA'})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YO\'LDA')
        await update_group_report(bot, order_id)
    asyncio.create_task(process_way())

@router.message(DeliveryStates.ACT_PHOTO, F.photo)
async def handle_act_photo(message: Message, state: FSMContext, bot: Bot):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    
    photo_id = message.photo[-1].file_id
    await state.set_state(DeliveryStates.DELIVERED_LOC)
    await message.answer("✅ AKT rasmi qabul qilindi.\n\nEndi yuk tushirilgan joy **LOKATSIYASINI** yuboring (Telegram'dan Share Location orqali):", reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish"))
    
    async def process_act():
        await asyncio.to_thread(update_order, order_id, {'act_photo_file_id': photo_id, 'act_photo_at': now})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_act())

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
    await message.answer(f"✅ Lokatsiya qabul qilindi.\n\nBuyurtmani yakunlash uchun tugmani bosing:", reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}"))
    
    async def process_loc():
        await asyncio.to_thread(update_order, order_id, {'delivered_lat': message.location.latitude, 'delivered_lng': message.location.longitude, 'delivered_location_at': now})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_loc())

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
        await callback.answer(_('send_photo', lang) + " (Yuk & Akt)", show_alert=True)
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
    
    # Other stages
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
        try:
            await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'current_status': 'YAKUNLANDI', 'completed_at': now})
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
            
            from core.db import count_active_orders
            active_left = await asyncio.to_thread(count_active_orders, tid)
            if active_left == 0:
                await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), "BO'SH", "")
            
            logger.info(f"🏁 Final finish process completed for order {order_id}")
        except Exception as e:
            logger.error(f"❌ Error in finish_background for {order_id}: {e}", exc_info=True)
    
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
