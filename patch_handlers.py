import re

with open("core/handlers/delivery.py", "r") as f:
    content = f.read()

# 1. Add `await callback.answer()` to the beginning of all callback handlers
content = re.sub(r'(@router\.callback_query\(.*?\)[\s\n]*async def .*?\(.*?\):[\s\n]*)', r'\1    await callback.answer()\n', content)

# 2. Fix handle_take_delivery to prevent double-taking without blocking
take_old = """@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    order_id = callback.data.split("_")[1]; order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.answer("❌ Buyurtma topilmadi yoki o'chirib yuborilgan.", show_alert=True)
        return
    now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {'current_status': 'QABUL_QILINDI', 'accepted_at': now, 'driver_telegram_id': callback.from_user.id, 'driver_name': callback.from_user.full_name})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'QABUL_QILINDI'))
    asyncio.create_task(asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'BUSY', order_id))
    await state.update_data(order_id=order_id); await state.set_state(DeliveryStates.A_BLOCK)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id} qabul qilindi.**\\n\\nSavol: **A-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb("A", order_id))
    asyncio.create_task(update_group_report(bot, order_id))"""

take_new = """@router.callback_query(F.data.startswith("take_"))
async def handle_take_delivery(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    if await state.get_state() is not None: return
    order_id = callback.data.split("_")[1]
    
    # edit UI immediately for speed
    try:
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id} qabul qilindi.**\\n\\nSavol: **A-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb("A", order_id))
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
    asyncio.create_task(process_take())"""

content = content.replace(take_old, take_new)

# 3. Fix handle_blocks to edit message quickly and background DB update
block_old = """@router.callback_query(F.data.startswith("block_"))
async def handle_blocks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_"); letter, status, order_id = parts[1], parts[2].upper(), parts[3]; now = get_now().isoformat()
    await asyncio.to_thread(update_order, order_id, {f'{letter.lower()}_block_at': now, f'{letter.lower()}_block_status': status})
    next_map = {"A": ("B", DeliveryStates.B_BLOCK), "B": ("C", DeliveryStates.C_BLOCK), "C": ("D", DeliveryStates.D_BLOCK)}
    if letter in next_map:
        next_letter, next_state = next_map[letter]; await state.set_state(next_state)
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\nSavol: **{next_letter}-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb(next_letter, order_id))
    else:
        await state.set_state(DeliveryStates.TRANSIT)
        await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\nSavol: **Transitdan narsa oldingizmi?**", reply_markup=kb.get_transit_kb(order_id))
    asyncio.create_task(update_group_report(bot, order_id))"""

block_new = """@router.callback_query(F.data.startswith("block_"))
async def handle_blocks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_"); letter, status, order_id = parts[1], parts[2].upper(), parts[3]; now = get_now().isoformat()
    next_map = {"A": ("B", DeliveryStates.B_BLOCK), "B": ("C", DeliveryStates.C_BLOCK), "C": ("D", DeliveryStates.D_BLOCK)}
    if letter in next_map:
        next_letter, next_state = next_map[letter]; await state.set_state(next_state)
        try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\nSavol: **{next_letter}-blokdan narsa ortdingizmi?**", reply_markup=kb.get_block_kb(next_letter, order_id))
        except Exception: pass
    else:
        await state.set_state(DeliveryStates.TRANSIT)
        try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\nSavol: **Transitdan narsa oldingizmi?**", reply_markup=kb.get_transit_kb(order_id))
        except Exception: pass
    
    async def process_block():
        await asyncio.to_thread(update_order, order_id, {f'{letter.lower()}_block_at': now, f'{letter.lower()}_block_status': status})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_block())"""

content = content.replace(block_old, block_new)

# 4. Fix handle_transit
tr_old = """@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    status = "OLDI" if "tr_oldi_" in callback.data else "OLMADI"
    await asyncio.to_thread(update_order, order_id, {'transit_at': now, 'transit_status': status})
    await state.set_state(DeliveryStates.LOADED_PHOTO)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\n📸 **Yuk ortilgan rasmni yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))"""

tr_new = """@router.callback_query(F.data.startswith("tr_"))
async def handle_transit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    status = "OLDI" if "tr_oldi_" in callback.data else "OLMADI"
    await state.set_state(DeliveryStates.LOADED_PHOTO)
    try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\n📸 **Yuk ortilgan rasmni yuboring**")
    except Exception: pass
    
    async def process_tr():
        await asyncio.to_thread(update_order, order_id, {'transit_at': now, 'transit_status': status})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_tr())"""

content = content.replace(tr_old, tr_new)

# 5. Fix step_way
step_old = """@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.answer("❌ Buyurtma topilmadi.", show_alert=True)
        return
    asyncio.create_task(asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'YOLDA'}))
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'YOLDA'))
    await state.set_state(DeliveryStates.ACT_PHOTO)
    await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\nMijoz manziliga yetib borgach akt rasmini yuboring:\\n\\n📄 **Qo'l qo'ydirilgan akt rasmini yuboring**")
    asyncio.create_task(update_group_report(bot, order_id))"""

step_new = """@router.callback_query(F.data.startswith("step_way_"))
async def step_way(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    await state.set_state(DeliveryStates.ACT_PHOTO)
    try: await callback.message.edit_text(f"📦 **Buyurtma #{order_id}**\\n\\nMijoz manziliga yetib borgach akt rasmini yuboring:\\n\\n📄 **Qo'l qo'ydirilgan akt rasmini yuboring**")
    except Exception: pass
    
    async def process_way():
        await asyncio.to_thread(update_order, order_id, {'on_way_at': now, 'current_status': 'YOLDA'})
        await asyncio.to_thread(update_order_status_by_order_id, order_id, 'YOLDA')
        await update_group_report(bot, order_id)
    asyncio.create_task(process_way())"""

content = content.replace(step_old, step_new)

# 6. Fix handle_delivered_location to remove keyboard immediately
# Wait, handle_delivered_location is a message handler.
loc_old = """@router.message(DeliveryStates.DELIVERED_LOC, F.location)
async def handle_delivered_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        return
    await asyncio.to_thread(update_order, order_id, {'delivered_lat': message.location.latitude, 'delivered_lng': message.location.longitude, 'delivered_location_at': now})
    await state.set_state(DeliveryStates.WAITING_FINISH)
    await message.answer(f"✅ Lokatsiya qabul qilindi.\\n\\nBuyurtmani yakunlash uchun tugmani bosing:", reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}"))
    asyncio.create_task(update_group_report(bot, order_id))"""

loc_new = """@router.message(DeliveryStates.DELIVERED_LOC, F.location)
async def handle_delivered_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data(); order_id = data.get('order_id'); now = get_now().isoformat()
    if not order_id: return
    
    m = await message.answer("✅", reply_markup=ReplyKeyboardRemove())
    await m.delete()
    await state.set_state(DeliveryStates.WAITING_FINISH)
    await message.answer(f"✅ Lokatsiya qabul qilindi.\\n\\nBuyurtmani yakunlash uchun tugmani bosing:", reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}"))
    
    async def process_loc():
        await asyncio.to_thread(update_order, order_id, {'delivered_lat': message.location.latitude, 'delivered_lng': message.location.longitude, 'delivered_location_at': now})
        await update_group_report(bot, order_id)
    asyncio.create_task(process_loc())"""

content = content.replace(loc_old, loc_new)

# 7. handle_final_done UI fix + background DB updates
final_old = """@router.callback_query(F.data.startswith("final_done_"))
async def handle_final_done(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    order_id = (await state.get_data()).get('order_id'); now = get_now().isoformat(); order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.answer("❌ Buyurtma topilmadi.")
        return
    await asyncio.to_thread(update_order, order_id, {'finished_at': now, 'current_status': 'YAKUNLANDI'})
    asyncio.create_task(asyncio.to_thread(update_order_status_by_order_id, order_id, 'YAKUNLANDI'))
    acc_at = order.get('accepted_at'); fin_at = now
    d_total = format_duration_detailed(get_seconds_diff(acc_at, fin_at))
    d_a = format_duration_detailed(get_seconds_diff(acc_at, order.get('a_block_at')))
    d_b = format_duration_detailed(get_seconds_diff(order.get('a_block_at'), order.get('b_block_at')))
    d_c = format_duration_detailed(get_seconds_diff(order.get('b_block_at'), order.get('c_block_at')))
    d_d = format_duration_detailed(get_seconds_diff(order.get('c_block_at'), order.get('d_block_at')))
    d_tr = format_duration_detailed(get_seconds_diff(order.get('d_block_at'), order.get('transit_at')))
    d_yuk = format_duration_detailed(get_seconds_diff(order.get('transit_at'), order.get('loaded_photo_at')))
    d_way = format_duration_detailed(get_seconds_diff(order.get('loaded_photo_at'), order.get('on_way_at')))
    d_act = format_duration_detailed(get_seconds_diff(order.get('on_way_at'), order.get('act_photo_at')))
    d_loc = format_duration_detailed(get_seconds_diff(order.get('act_photo_at'), order.get('delivered_location_at')))
    drv_msg = (f"✅ **Buyurtma yakunlandi**\\n\\n🆔 Buyurtma: #{order_id}\\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M')}\\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\\n⏳ Umumiy vaqt: {d_total}\\n\\n"
               f"**Etaplar:**\\n"
               f"A-blok: {order.get('a_block_status','—')} | {d_a}\\n"
               f"B-blok: {order.get('b_block_status','—')} | {d_b}\\n"
               f"C-blok: {order.get('c_block_status','—')} | {d_c}\\n"
               f"D-blok: {order.get('d_block_status','—')} | {d_d}\\n"
               f"Transit: {order.get('transit_status','—')} | {d_tr}\\n"
               f"Yuk rasmi: {d_yuk}\\n"
               f"Yo'lga chiqish: {d_way}\\n"
               f"Akt rasmi: {d_act}\\n"
               f"Lokatsiya: {d_loc}\\n")
    await callback.message.edit_text(drv_msg, parse_mode="Markdown")
    if GROUP_CHAT_ID:
        try:
            msg_id = order.get('group_message_id')
            if msg_id: await bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=int(msg_id))
        except: pass
        a_i, b_i, c_i, d_i, t_i = get_status_icon(order.get('a_block_status')), get_status_icon(order.get('b_block_status')), get_status_icon(order.get('c_block_status')), get_status_icon(order.get('d_block_status')), get_status_icon(order.get('transit_status'))
        maps_url = f"https://maps.google.com/?q={order.get('delivered_lat')},{order.get('delivered_lng')}"
        grp_text = (f"🚚 **LOGISTIKA YAKUNI #{order_id}**\\n\\n📍 **Manzil:** {order.get('address', '-')}...""" # We'll replace the whole function

final_match = re.search(r'(@router\.callback_query\(F\.data\.startswith\("final_done_"\)\).*?)(?=@router\.message|\Z)', content, re.DOTALL)
if final_match:
    final_old_full = final_match.group(1)
    
    final_new = """@router.callback_query(F.data.startswith("final_done_"))
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
    line_tr = get_emoji_line("🚚 Transit", order.get('transit_status'), order.get('d_block_at'), order.get('transit_at'), "OLDI", "🚚", "🚚")
    line_yuk = get_emoji_line("📸 Yuk rasmi", "", order.get('transit_at'), order.get('loaded_photo_at'), "", "📸", "📸")
    line_way = get_emoji_line("🛣 Yo'lga chiqish", "", order.get('loaded_photo_at'), order.get('on_way_at'), "", "🛣", "🛣")
    line_act = get_emoji_line("🧾 Akt rasmi", "", order.get('on_way_at'), order.get('act_photo_at'), "", "🧾", "🧾")
    line_loc = get_emoji_line("📍 Lokatsiya", "", order.get('act_photo_at'), order.get('delivered_location_at'), "", "📍", "🟥")

    drv_msg = (f"✅ **Buyurtma yakunlandi**\\n\\n🆔 Buyurtma: #{order_id}\\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M')}\\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\\n⏳ Umumiy vaqt: {d_total}\\n\\n"
               f"📋 **Etaplar:**\\n"
               f"{line_a}\\n{line_b}\\n{line_c}\\n{line_d}\\n{line_tr}\\n{line_yuk}\\n{line_way}\\n{line_act}\\n{line_loc}\\n")
    
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
            
            grp_text = (f"✅ **Buyurtma yakunlandi**\\n\\n🆔 Buyurtma: #{order_id}\\n⏰ Boshlandi: {parse_dt(acc_at).strftime('%H:%M')}\\n🏁 Tugadi: {parse_dt(fin_at).strftime('%H:%M')}\\n⏳ Umumiy vaqt: {d_total}\\n\\n"
                        f"👤 **Haydovchi:** {order.get('driver_name', '-')}\\n🚘 **Mashina:** {order.get('car_number', '-')}\\n"
                        f"📍 **Manzil:** {order.get('address', '-')}\\n📍 **Yetkazilgan lokatsiya:** [Google Maps]({maps_url})\\n\\n📦 **Yuk:** {order.get('cargo', '-')}\\n📝 **Izoh:** {order.get('comment', '-')}\\n\\n"
                        f"📋 **Etaplar:**\\n"
                        f"{line_a}\\n{line_b}\\n{line_c}\\n{line_d}\\n{line_tr}\\n{line_yuk}\\n{line_way}\\n{line_act}\\n{line_loc}\\n\\n"
                        f"🟢 **Mashina bo'shadi:** {order.get('car_number', '-')}")
            
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=grp_text, parse_mode="Markdown", disable_web_page_preview=False)
            media = []
            if order.get('loaded_photo_file_id'): media.append(InputMediaPhoto(media=order['loaded_photo_file_id'], caption=f"📸 Buyurtma rasmlari #{order_id}\\n1) Yuk ortilgan rasm\\n2) Qo'l qo'ydirilgan akt rasmi"))
            if order.get('act_photo_file_id'): media.append(InputMediaPhoto(media=order['act_photo_file_id']))
            if media:
                try: await bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media)
                except: pass
        tid = callback.from_user.id
        res = await asyncio.to_thread(lambda: supabase.table('orders').select('id').eq('driver_telegram_id', tid).neq('current_status', 'YAKUNLANDI').execute())
        if not res.data: await asyncio.to_thread(update_driver_status_sheet, order.get('car_number'), 'IDLE', "")
    
    asyncio.create_task(finish_background())
"""
    content = content.replace(final_old_full, final_new)


with open("core/handlers/delivery.py", "w") as f:
    f.write(content)
