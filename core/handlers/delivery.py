import json
import logging
import asyncio
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from core.config import GROUP_CHAT_ID
from core.db import get_order, update_order, supabase
from core.sheets import (
    update_order_status_by_order_id,
    update_driver_status_sheet,
    remove_order_from_driver_sheet
)
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

def escape_html(text):
    if not text: return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def build_interim_report(order):
    order_id = order.get('order_id', '-')
    stage_history = order.get('stage_history') or []
    
    # Create a map for quick access
    history_map = {item['stage']: item for item in stage_history}
    
    def get_stage_status(label):
        item = history_map.get(label)
        if item:
            status_val = item.get('status', 'ORTDI')
            completed_at = item.get('completed_at', '-')
            return f"<b>{item.get('emoji', '✅')} {escape_html(status_val)}</b> ({escape_html(completed_at)})"
        return "<i>⏳ Tanlanmagan</i>"

    # Build transits section
    transits_text = ""
    transits = [item for item in stage_history if item['stage'].startswith("Transit")]
    if not transits:
        transits_text = f"🚚 Transit: {get_status_icon(order.get('transit_status'))}"
    else:
        for t in transits:
            transits_text += f"{t.get('emoji', '✅')} {escape_html(t['stage'])}: <b>{escape_html(t['status'])}</b> ({escape_html(t['completed_at'])})\n"
        transits_text = transits_text.strip()

    text = (
        f"🚚 <b>LOGISTIKA HISOBOTI #{escape_html(order_id)}</b>\n"
        f"📍 <b>Manzil:</b> {escape_html(order.get('address', '-'))}\n"
        f"📦 <b>Yuk:</b> {escape_html(order.get('cargo', '-'))}\n"
        f"👤 <b>Haydovchi:</b> {escape_html(order.get('driver_name', '-'))} ({escape_html(order.get('car_number', '-'))})\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏗 <b>Yuklash:</b>\n"
        f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
        f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
        f"©️ C-blok: {get_stage_status('C-blok')}\n"
        f"🇩 D-blok: {get_stage_status('D-blok')}\n"
        f"{transits_text}\n\n"
        f"📊 <b>Status:</b> {escape_html(order.get('current_status', 'NEW'))}\n"
        f"⏰ <b>Boshlandi:</b> {parse_dt(order.get('accepted_at')).strftime('%H:%M') if order.get('accepted_at') else '—'}\n"
    )
    return text

async def update_group_report(bot: Bot, order_id: str, override_group_id=None):
    """Send/edit interim report to the correct group based on driver's filial."""
    try:
        order = await asyncio.to_thread(get_order, order_id)
        if not order or order.get('current_status') == 'YAKUNLANDI': return

        # Determine correct group
        from core.config import BRANCHES
        from core.sheets import get_drivers
        target_group = override_group_id
        if not target_group:
            filial = order.get('filial', '') or ''
            if not filial:
                car_number = order.get('car_number', '')
                if car_number:
                    drivers = await asyncio.to_thread(get_drivers)
                    dr = drivers.get(car_number)
                    if dr:
                        filial = dr.get('filial', '')
            
            if filial and filial in BRANCHES:
                target_group = BRANCHES[filial].get('group_id') or GROUP_CHAT_ID
            else:
                target_group = GROUP_CHAT_ID

        if not target_group or str(target_group) == "0": return


        text = build_interim_report(order)
        msg_id = order.get('group_message_id')

        if msg_id:
            try:
                await bot.edit_message_text(chat_id=target_group, message_id=int(msg_id), text=text, parse_mode="HTML")
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.error(f"Group report edit error: {e}")
                    try:
                        plain = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
                        await bot.edit_message_text(chat_id=target_group, message_id=int(msg_id), text=plain)
                    except: pass
        else:
            msg = await bot.send_message(chat_id=target_group, text=text, parse_mode="HTML")
            asyncio.create_task(asyncio.to_thread(update_order, order_id, {'group_message_id': str(msg.message_id)}))

    except Exception as e:
        logger.error(f"Group report error: {e}")

@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    order_id = callback.data.split("_")[1]

    # Multi-order: haydovchi 3 tagacha order qabul qilishi mumkin
    # Agar state boshqa order uchun bo'lsa — yangi state open qilamiz
    data = await state.get_data()
    existing_order_id = data.get('order_id')
    if existing_order_id and existing_order_id != order_id:
        # Haydovchi boshqa order ustida ishlayapti — uni saqlab, yangi orderga o'tamiz
        logger.info(f"Driver switching from order {existing_order_id} to new order {order_id}")

    await state.update_data(order_id=order_id, stage_history=[])
    await state.set_state(DeliveryStates.BLOCK_MENU)

    try:
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id} qabul qilindi.**\n\n"
            f"📦 **Bloklardan yuk olish**\n"
            f"⚠️ Hamma bloklarni tanlashingiz kerak!\n"
            f"⚠️ Ҳамма блокларни танлашингиз керак!\n\n"
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
        await asyncio.to_thread(update_order, order_id, {
            'current_status': 'QABUL_QILINDI',
            'accepted_at': now,
            'driver_telegram_id': callback.from_user.id,
            'driver_name': callback.from_user.full_name
        })
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'QABUL_QILINDI')
        car = order.get('car_number', '')
        if car:
            await asyncio.to_thread(update_driver_status_sheet, car, 'YUK OGAN', order_id)
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
    # Explicitly define status variables to prevent NameError
    status = "noma'lum"
    emoji = "⚪️"
    color = "⚪️"

    # Support both old and new callback formats
    if status_type in ["ortdi", "oldim"]:
        status = "oldim"
        emoji = "✅"
        color = "🟩"
    elif status_type in ["ortmadi", "olmadim"]:
        status = "olmadim"
        emoji = "❌"
        color = "🟥"
    else:
        # Fallback if unknown status_type
        await callback.answer("❌ Noto'g'ri amal", show_alert=True)
        return

    try:
        now = get_now()
        data = await state.get_data()
        stage_history = data.get('stage_history') or []

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
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"📦 **Bloklardan yuk olish**\n"
            f"⚠️ Hamma bloklarni tanlashingiz kerak!\n"
            f"⚠️ Ҳамма блокларни танлашингиз керак!\n\n"
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
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"📦 **Bloklardan yuk olish**\n"
            f"⚠️ Hamma bloklarni tanlashingiz kerak!\n"
            f"⚠️ Ҳамма блокларни танлашингиз керак!\n\n"
            f"🅰️ A-blok: {get_stage_status('A-blok')}\n"
            f"🅱️ B-blok: {get_stage_status('B-blok')}\n"
            f"©️ C-blok: {get_stage_status('C-blok')}\n"
            f"🇩 D-blok: {get_stage_status('D-blok')}",
            reply_markup=kb.get_block_menu_kb(order_id, stage_history)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("confirm_blocks_"))
async def handle_confirm_blocks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split("_")[2]
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    
    selected = {item['stage'] for item in stage_history}
    all_blocks = {"A-blok", "B-blok", "C-blok", "D-blok"}
    missing = all_blocks - selected
    
    if missing:
        missing_str = ", ".join(sorted(missing))
        await callback.answer(
            f"⚠️ Belgilanmagan bloklar: {missing_str}\nHamma bloklarni belgilang!",
            show_alert=True
        )
        return
    
    await callback.answer()
    await state.set_state(DeliveryStates.TRANSIT)
    try:
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"🚚 Transitdan yuk oldingizmi?\n"
            f"🚚 Транзитдан юк олдингизми?",
            reply_markup=kb.get_transit_kb(order_id)
        )
    except Exception: pass

@router.callback_query(F.data.startswith("tr_oldi_") | F.data.startswith("tr_olmadi_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data()
    order_id = data.get('order_id')
    if not order_id:
        logger.error("Transit action received but no order_id in state.")
        await callback.message.answer("❌ Xatolik, qayta urinib ko'ring")
        return
    
    logger.info(f"Transit 1 answer received for order {order_id}: {callback.data}")
    now = get_now()
    stage_history = data.get('stage_history') or []
    last_block_time = data.get('last_block_time') or now
    duration_seconds = int((now - last_block_time).total_seconds())
    
    if "tr_oldi_" in callback.data:
        status = "OLDI"
        emoji = "✅"
        label = "Transit 1"
        
        new_entry = {
            "stage": label, "status": status, "emoji": emoji, "color": "🚚",
            "duration_seconds": duration_seconds, "completed_at": now.strftime("%H:%M"),
            "full_at": now.isoformat(), "sequence": len(stage_history) + 1
        }
        stage_history.append(new_entry)
        await state.update_data(stage_history=stage_history, last_block_time=now)
        await state.set_state(DeliveryStates.TRANSIT_EXTRA)
        
        logger.info(f"Transit 1 recorded. Asking for Transit 2 for order {order_id}")
        try:
            await callback.message.edit_text(
                f"📦 **Buyurtma #{order_id}**\n\n🚚 **2-transitingiz bormi?**",
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
            "stage": label, "status": status, "emoji": emoji, "color": "🚚",
            "duration_seconds": duration_seconds, "completed_at": now.strftime("%H:%M"),
            "full_at": now.isoformat(), "sequence": len(stage_history) + 1
        }
        stage_history.append(new_entry)
        await state.update_data(stage_history=stage_history, last_block_time=now)
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        
        logger.info(f"Transit skipped for order {order_id}. Moving to LOADED_PHOTO.")
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
    if len(parts) < 5:
        await callback.message.answer("❌ Xatolik, qayta urinib ko'ring")
        return
        
    action, num, order_id = parts[2], int(parts[3]), parts[4]
    logger.info(f"Transit Extra answer received: {action} for transit {num} (Order {order_id})")
    
    data = await state.get_data()
    stage_history = data.get('stage_history') or []
    now = get_now()
    
    if action == "yoq":
        logger.info(f"Transit {num} declined for order {order_id}. Finishing transits.")
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        try:
            await callback.message.edit_text(
                f"📦 **Buyurtma #{order_id}**\n\n"
                f"📸 Yuk ortilgan rasmni yuboring\n"
                f"📸 Юк ортилган расмни юборинг"
            )
        except Exception: pass
        return

    # action == "ha"
    logger.info(f"Transit {num} confirmed for order {order_id}.")
    last_block_time = data.get('last_block_time') or now
    duration_seconds = int((now - last_block_time).total_seconds())

    new_entry = {
        "stage": f"Transit {num}", "status": "OLDI", "emoji": "✅", "color": "🚚",
        "duration_seconds": duration_seconds, "completed_at": now.strftime("%H:%M"),
        "full_at": now.isoformat(), "sequence": len(stage_history) + 1
    }
    stage_history.append(new_entry)
    await state.update_data(stage_history=stage_history, last_block_time=now)

    if num < 10:  # Transit limit: 10
        next_num = num + 1
        logger.info(f"Asking for Transit {next_num} for order {order_id}")
        try:
            await callback.message.edit_text(
                f"📦 **Buyurtma #{order_id}**\n\n🚚 **{next_num}-transitingiz bormi?**",
                reply_markup=kb.get_transit_extra_kb(next_num, order_id)
            )
        except Exception: pass
    else:
        logger.info(f"Max transits (10) reached for order {order_id}. Moving to LOADED_PHOTO.")
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        try:
            await callback.message.edit_text(
                f"📦 **Buyurtma #{order_id}**\n\n"
                f"📸 Yuk ortilgan rasmni yuboring\n"
                f"📸 Юк ортилган расмни юборинг"
            )
        except Exception: pass

    async def process_tr_extra():
        try:
            await asyncio.to_thread(update_order, order_id, {'stage_history': stage_history, 'transit_at': now.isoformat(), 'transit_status': 'OLDI'})
            await update_group_report(bot, order_id)
        except Exception as e:
            logger.error(f"Error in process_tr_extra: {e}")
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
    await message.answer(
        f"✅ Rasm qabul qilindi.\n✅ Расм қабул қилинди.\n\n"
        f"📍 **Manzil:** {addr}\n\n"
        f"Yo'lga chiqdingizmi?\nЙo'лга чиқдингизми?",
        reply_markup=kb.get_step_kb("🚚 Yo'lga chiqdim", f"step_way_{order_id}")
    )
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.LOADED_PHOTO)
async def loaded_photo_wrong_input(message: Message):
    await message.answer(
        "📸 Iltimos, faqat yuk rasmini yuboring.\n"
        "📸 Илтимос, фақат юк расмини юборинг."
    )

@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    await state.set_state(DeliveryStates.ACT_PHOTO)
    try:
        await callback.message.edit_text(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"Mijoz manziliga yetib borgach akt rasmini yuboring:\n"
            f"Мижоз манзилига етиб борганингиздан сўнг акт расмини юборинг:\n\n"
            f"📄 Qo'l qo'ydirilgan akt rasmini yuboring\n"
            f"📄 Қўл қўйдирилган акт расмини юборинг"
        )
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
    await message.answer(
        f"✅ Akt qabul qilindi.\n✅ Акт қабул қилинди.\n\n"
        f"📍 Yetkazilgan joy lokatsiyasini yuboring\n"
        f"📍 Етказилган жой локациясини юборинг",
        reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish")
    )
    asyncio.create_task(update_group_report(bot, order_id))

@router.message(DeliveryStates.ACT_PHOTO)
async def act_photo_wrong_input(message: Message):
    await message.answer(
        "📄 Iltimos, faqat akt rasmini yuboring.\n"
        "📄 Илтимос, фақат акт расмини юборинг."
    )

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
    await message.answer(
        "📍 Iltimos, faqat lokatsiya yuboring.\n"
        "📍 Илтимос, фақат локация юборинг.",
        reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish")
    )

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
        
        raw_stage_history = order.get("stage_history") or order.get("stages") or []

        if isinstance(raw_stage_history, str):
            try:
                stage_history = json.loads(raw_stage_history)
            except Exception as e:
                logger.warning(f"[FINISH] stage_history parse error: {e}")
                stage_history = []
        elif isinstance(raw_stage_history, list):
            stage_history = raw_stage_history
        else:
            stage_history = []

        logger.info(f"[FINISH] stage_history loaded: {len(stage_history)} items")

        history_lines_full = []
        history_lines_short = []
        
        for item in stage_history:
            d_str = format_duration_detailed(item.get('duration_seconds'))
            status_val = item.get('status', 'oldim')
            emoji_val = item.get('emoji', '✅')
            
            # Full version for group
            full_line = f"{item.get('color', '🟩')} {item['stage']}: {emoji_val} {status_val} — {d_str} ({item.get('completed_at', '-')})"
            history_lines_full.append(full_line)
            
            # Short version for driver
            short_status = "oldi" if status_val.lower() == "oldi" else status_val
            history_lines_short.append(f"{item.get('color', '🟩')} {item['stage']}: {short_status}")
        
        # Last action time for next duration calculation
        last_action_at = stage_history[-1]['full_at'] if stage_history else order.get('accepted_at')
        
        def get_lines(label, status, dt1, dt2, success_val, emoji_ok="🟩", emoji_fail="🟥", ok_icon="✅", fail_icon="❌"):
            if dt2:
                d_str = format_duration_detailed(get_seconds_diff(dt1, dt2))
                dt_formatted = parse_dt(dt2).strftime('%H:%M') if parse_dt(dt2) else ""
                is_ok = (status == success_val) if status else True
                st_text = status if status else ("yuborildi" if "Lokatsiya" in label else "olindi" if "rasmi" in label else "bosildi")
                icon = ok_icon if is_ok else fail_icon
                color = emoji_ok if is_ok else emoji_fail
                
                full = f"{color} {label}: {icon} {st_text.upper()} — {d_str} ({dt_formatted})"
                short = f"{color} {label}: {st_text}"
                return full, short
            return f"{emoji_fail} {label}: ❌ YUBORILMADI", f"{emoji_fail} {label}: yuborilmadi"

        full_yuk, short_yuk = get_lines("Yuk rasmi", "", last_action_at, order.get('loaded_photo_at'), "", "📸", "📸")
        full_way, short_way = get_lines("Yo'lga chiqish", "", order.get('loaded_photo_at'), order.get('on_way_at'), "", "🛣", "🛣")
        full_act, short_act = get_lines("Akt rasmi", "", order.get('on_way_at'), order.get('act_photo_at'), "", "🧾", "🧾")
        full_loc, short_loc = get_lines("Lokatsiya", "", order.get('act_photo_at'), order.get('delivered_location_at'), "", "📍", "🟥")

        history_lines_full.extend([full_yuk, full_way, full_act, full_loc])
        history_lines_short.extend([short_yuk, short_way, short_act, short_loc])
        
        etaplar_full = "\n".join(history_lines_full)
        etaplar_short = "\n".join(history_lines_short)

        acc_str = parse_dt(acc_at).strftime('%H:%M') if acc_at else '-'
        fin_str = parse_dt(fin_at).strftime('%H:%M') if fin_at else '-'
        drv_msg = (
            f"✅ **Buyurtma yakunlandi**\n\n"
            f"🆔 Buyurtma: #{order_id}\n"
            f"⏰ Boshlandi: {acc_str}\n"
            f"🏁 Tugadi: {fin_str}\n"
            f"⏳ Umumiy vaqt: {d_total}\n\n"
            f"📋 **Etaplar:**\n"
            f"{etaplar_short}"
        )
        
        try: await callback.message.edit_text(drv_msg, parse_mode="Markdown")
        except Exception: pass
        
        async def finish_background():
            try:
                logger.info(f"[FINISH] Updating DB for order {order_id}...")
                await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'completed_at': now, 'current_status': 'YAKUNLANDI'})
                await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI')
                
                # Get correct group
                from core.config import BRANCHES, GROUP_CHAT_ID as DEFAULT_GROUP_ID
                from core.sheets import get_drivers
                target_group = DEFAULT_GROUP_ID
                filial = order.get('filial', '') or ''
                if not filial:
                    car_number = order.get('car_number', '')
                    if car_number:
                        drivers = await asyncio.to_thread(get_drivers)
                        dr = drivers.get(car_number)
                        if dr:
                            filial = dr.get('filial', '')
                            
                if filial and filial in BRANCHES:
                    target_group = BRANCHES[filial].get('group_id') or DEFAULT_GROUP_ID

                if target_group:

                    msg_id = order.get('group_message_id')
                    if msg_id:
                        try: await bot.delete_message(chat_id=target_group, message_id=int(msg_id))
                        except: pass
                    
                    maps_url = f"https://maps.google.com/?q={order.get('delivered_lat')},{order.get('delivered_lng')}"
                    grp_text = (f"✅ **Buyurtma yakunlandi**\n\n🆔 Buyurtma: #{order_id}\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M') if acc_at else '-'}\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\n⏳ Umumiy vaqt: {d_total}\n\n"
                                f"👤 **Haydovchi:** {order.get('driver_name', '-')}\n🚘 **Mashina:** {order.get('car_number', '-')}\n"
                                f"📍 **Manzil:** {order.get('address', '-')}\n📍 **Yetkazilgan lokatsiya:** [Google Maps]({maps_url})\n\n📦 **Yuk:** {order.get('cargo', '-')}\n📝 **Izoh:** {order.get('comment', '-')}\n\n"
                                f"📋 **Etaplar:**\n"
                                f"{etaplar_full}\n\n"
                                f"🟢 **Mashina bo'shadi:** {order.get('car_number', '-')}")
                    
                    media = [
                        InputMediaPhoto(media=order['loaded_photo_file_id'], caption=grp_text, parse_mode="Markdown"),
                        InputMediaPhoto(media=order['act_photo_file_id'])
                    ]
                    try:
                        await bot.send_media_group(chat_id=target_group, media=media)
                    except Exception as e:
                        logger.error(f"[FINISH] Failed to send media group to {target_group}: {e}")

                
                # Driver status reset — remove finished order from driver's sheet list
                tid = order.get('driver_telegram_id')
                car_number = order.get('car_number', '')
                try:
                    ok = await asyncio.to_thread(remove_order_from_driver_sheet, car_number, order_id)
                    if ok:
                        logger.info(f"[FINISH] remove_order_from_driver_sheet OK: car={car_number} order={order_id}")
                    else:
                        logger.warning(f"[FINISH] remove_order_from_driver_sheet failed for car={car_number}")
                except Exception as dr_err:
                    logger.error(f"[FINISH] Driver sheet update error for {car_number}: {dr_err}")

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
    try:
        if curr == DeliveryStates.ON_WAY:
            await message.answer(
                "⚠️ Iltimos, avval yuqoridagi **🚚 Yo'lga chiqdim** tugmasini bosing!\n"
                "⚠️ Илтимос, аввал юқоридаги **🚚 Йo'лга чиқдим** тугмасини bosing!"
            )
        elif curr == DeliveryStates.LOADED_PHOTO:
            await message.answer(
                "📸 Iltimos, yuk rasmini yuboring.\n"
                "📸 Илтимос, юк расмини юборинг."
            )
        elif curr == DeliveryStates.ACT_PHOTO:
            await message.answer(
                "📄 Iltimos, akt rasmini yuboring.\n"
                "📄 Илтимос, акт расмини юборинг."
            )
        elif curr == DeliveryStates.DELIVERED_LOC:
            await message.answer(
                "📍 Iltimos, lokatsiya yuboring.\n"
                "📍 Илтимос, локация юборинг.",
                reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish")
            )
        elif curr is not None:
            await message.answer(
                "ℹ️ Iltimos, pastdagi tugmalardan foydalaning.\n"
                "ℹ️ Илтимос, пастдаги тугмалардан фойдаланинг."
            )
    except Exception as e:
        logger.error(f"handle_wrong_input error: {e}")
