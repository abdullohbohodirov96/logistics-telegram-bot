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
    a_icon = get_status_icon(order.get('a_block_status'))
    b_icon = get_status_icon(order.get('b_block_status'))
    c_icon = get_status_icon(order.get('c_block_status'))
    d_icon = get_status_icon(order.get('d_block_status'))
    tr_icon = get_status_icon(order.get('transit_status'))
    acc_at = parse_dt(order.get('accepted_at'))
    acc_time = acc_at.strftime('%H:%M') if acc_at else "—"
    text = (
        f"🚚 **LOGISTIKA HISOBOTI #{order_id}**\n"
        f"📍 **Manzil:** {order.get('address', '-')}\n"
        f"📦 **Yuk:** {order.get('cargo', '-')}\n"
        f"👤 **Haydovchi:** {order.get('driver_name', '-')} ({order.get('car_number', '-')})\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏗 **Yuklash:**\n"
        f"A: {a_icon}  B: {b_icon}  C: {c_icon}  D: {d_icon}  Transit: {tr_icon}\n\n"
        f"📊 **Status:** {order.get('current_status', 'NEW')}\n"
        f"⏰ **Boshlandi:** {acc_time}\n"
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
    
    try:
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id} qabul qilindi.**\n\nSavol: **A-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb("A", order_id))
    except Exception: pass
    
    await state.update_data(order_id=order_id); await state.set_state(DeliveryStates.A_BLOCK)
    
    async def process_take():
        order = await asyncio.to_thread(get_order, order_id)
        if not order: return
        now = get_now().isoformat()
        await asyncio.to_thread(update_order, order_id, {'current_status': 'QABUL_QILINDI', 'accepted_at': now, 'driver_telegram_id': callback.from_user.id, 'driver_name': callback.from_user.full_name})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'QABUL_QILINDI')
        await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BUSY', order_id)
        await update_group_report(bot, order_id)
    asyncio.create_task(process_take())

@router.callback_query(F.data.startswith("block_"))
async def handle_blocks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_"); letter, status, order_id = parts[1], parts[2].upper(), parts[3]; now = get_now().isoformat()
    next_map = {"A": ("B", DeliveryStates.B_BLOCK), "B": ("C", DeliveryStates.C_BLOCK), "C": ("D", DeliveryStates.D_BLOCK)}
    if letter in next_map:
        next_letter, next_state = next_map[letter]; await state.set_state(next_state)
        try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **{next_letter}-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb(next_letter, order_id))
        except Exception: pass
    else:
        await state.set_state(DeliveryStates.TRANSIT)
        try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\nSavol: **Transitdan narsa oldingizmi?**", reply_markup=kb.get_transit_kb(order_id))
        except Exception: pass
    
    async def process_block():
        await asyncio.to_thread(update_order, order_id, {f'{letter.lower()}_block_at': now, f'{letter.lower()}_block_status': status})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_block())

@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    status = "OLDI" if "tr_oldi_" in callback.data else "OLMADI"
    await state.set_state(DeliveryStates.LOADED_PHOTO)
    try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\n\n📸 **Yuk ortilgan rasmni yuboring**")
    except Exception: pass
    
    async def process_tr():
        await asyncio.to_thread(update_order, order_id, {'transit_at': now, 'transit_status': status})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_tr())

@router.message(DeliveryStates.LOADED_PHOTO, F.photo | F.document)
async def handle_loaded_photo(message: Message, state: FSMContext, bot: Bot):
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await message.answer("❌ Buyurtma topilmadi. Iltimos, qaytadan urinib ko'ring.")
        return
    await asyncio.to_thread(update_order, order_id, {'loaded_photo_file_id': file_id, 'loaded_photo_at': now})
    if GROUP_CHAT_ID:
        try: await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=file_id, caption=f"📸 Yuk ortilgan rasm #{order_id}")
        except: pass
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
    if GROUP_CHAT_ID:
        try: await bot.send_photo(chat_id=GROUP_CHAT_ID, photo=file_id, caption=f"📄 Qo'l qo'ydirilgan akt rasmi #{order_id}")
        except: pass
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
    await callback.answer()
    order_id = (await state.get_data()).get('order_id'); now = get_now().isoformat()
    if not order_id: return
    
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        try: await callback.message.edit_text("❌ Buyurtma topilmadi.")
        except Exception: pass
        return

    await state.clear()
    
    acc_at = order.get('accepted_at'); fin_at = now
    d_total = format_duration_detailed(get_seconds_diff(acc_at, fin_at))
    
    def get_emoji_line(label, status, dt1, dt2, success_val, emoji_ok="🟩", emoji_fail="🟥", ok_icon="✅", fail_icon="❌"):
        if dt2:
            d_str = format_duration_detailed(get_seconds_diff(dt1, dt2))
            is_ok = (status == success_val) if status else True
            if not status and label in ["📸 Yuk rasmi", "🛣 Yo'lga chiqish", "🧾 Akt rasmi", "📍 Lokatsiya"]: is_ok = True
            
            # Use specific status text or default
            st_text = status if status else ("YUBORILDI" if "Lokatsiya" in label else "OLINDI" if "rasmi" in label else "BOSILDI")
            
            if is_ok:
                return f"{emoji_ok} {label}: {ok_icon} {st_text} — {d_str}"
            else:
                return f"{emoji_fail} {label}: {fail_icon} {st_text} — {d_str}"
        else:
            return f"{emoji_fail} {label}: {fail_icon} YUBORILMADI"

    line_a = get_emoji_line("A-blok", order.get('a_block_status'), acc_at, order.get('a_block_at'), "ORTDI")
    line_b = get_emoji_line("B-blok", order.get('b_block_status'), order.get('a_block_at'), order.get('b_block_at'), "ORTDI")
    line_c = get_emoji_line("C-blok", order.get('c_block_status'), order.get('b_block_at'), order.get('c_block_at'), "ORTDI")
    line_d = get_emoji_line("D-blok", order.get('d_block_status'), order.get('c_block_at'), order.get('d_block_at'), "ORTDI")
    line_tr = get_emoji_line("Transit", order.get('transit_status'), order.get('d_block_at'), order.get('transit_at'), "OLDI", "🚚", "🚚")
    line_yuk = get_emoji_line("Yuk rasmi", "", order.get('transit_at'), order.get('loaded_photo_at'), "", "📸", "📸")
    line_way = get_emoji_line("Yo'lga chiqish", "", order.get('loaded_photo_at'), order.get('on_way_at'), "", "🛣", "🛣")
    line_act = get_emoji_line("Akt rasmi", "", order.get('on_way_at'), order.get('act_photo_at'), "", "🧾", "🧾")
    line_loc = get_emoji_line("Lokatsiya", "", order.get('act_photo_at'), order.get('delivered_location_at'), "", "📍", "🟥")

    drv_msg = (f"✅ **Buyurtma yakunlandi**\n\n🆔 Buyurtma: #{order_id}\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M')}\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\n⏳ Umumiy vaqt: {d_total}\n\n"
               f"📋 **Etaplar:**\n"
               f"{line_a}\n{line_b}\n{line_c}\n{line_d}\n{line_tr}\n{line_yuk}\n{line_way}\n{line_act}\n{line_loc}\n")
    
    try: await callback.message.edit_text(drv_msg, parse_mode="Markdown")
    except Exception: pass
    
    async def finish_background():
        await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'current_status': 'YAKUNLANDI'})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI')
        if GROUP_CHAT_ID:
            try:
                msg_id = order.get('group_message_id')
                if msg_id: await bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=int(msg_id))
            except: pass
            
            maps_url = f"https://maps.google.com/?q={order.get('delivered_lat')},{order.get('delivered_lng')}"
            
            grp_text = (f"✅ **Buyurtma yakunlandi**\n\n🆔 Buyurtma: #{order_id}\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M')}\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\n⏳ Umumiy vaqt: {d_total}\n\n"
                        f"👤 **Haydovchi:** {order.get('driver_name', '-')}\n🚘 **Mashina:** {order.get('car_number', '-')}\n"
                        f"📍 **Manzil:** {order.get('address', '-')}\n📍 **Yetkazilgan lokatsiya:** [Google Maps]({maps_url})\n\n📦 **Yuk:** {order.get('cargo', '-')}\n📝 **Izoh:** {order.get('comment', '-')}\n\n"
                        f"📋 **Etaplar:**\n"
                        f"{line_a}\n{line_b}\n{line_c}\n{line_d}\n{line_tr}\n{line_yuk}\n{line_way}\n{line_act}\n{line_loc}\n\n"
                        f"🟢 **Mashina bo'shadi:** {order.get('car_number', '-')}")
            
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=grp_text, parse_mode="Markdown", disable_web_page_preview=False)
            media = []
            if order.get('loaded_photo_file_id'): media.append(InputMediaPhoto(media=order['loaded_photo_file_id'], caption=f"📸 Buyurtma rasmlari #{order_id}\n1) Yuk ortilgan rasm\n2) Qo'l qo'ydirilgan akt rasmi"))
            if order.get('act_photo_file_id'): media.append(InputMediaPhoto(media=order['act_photo_file_id']))
            if media:
                try: await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media)
                except: pass
        tid = callback.from_user.id
        res = await asyncio.to_thread(lambda: supabase.table('orders').select('id').eq('driver_telegram_id', tid).neq('current_status', 'YAKUNLANDI').execute())
        if not res.data: await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'IDLE', "")
    
    asyncio.create_task(finish_background())
@router.message(DeliveryStates())
async def handle_wrong_input(message: Message, state: FSMContext):
    curr = await state.get_state()
    if curr == DeliveryStates.ON_WAY: await message.answer("⚠️ Iltimos, avval yuqoridagi **🚚 Yo'lga chiqdim** tugmasini bosing!")
    else: await message.answer("❌ Iltimos, yuqoridagi tugmalardan foydalaning.")
