with open("core/handlers/delivery.py", "r") as f:
    content = f.read()

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

content = content.replace(take_old, take_new)
content = content.replace(block_old, block_new)
content = content.replace(tr_old, tr_new)
content = content.replace(step_old, step_new)

with open("core/handlers/delivery.py", "w") as f:
    f.write(content)
