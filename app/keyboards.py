from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_take_delivery_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Взял доставку", callback_data=f"take_{order_id}")]
    ])

def get_zones_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ A", callback_data=f"z_A_y_{order_id}"),
         InlineKeyboardButton(text="❌ A", callback_data=f"z_A_n_{order_id}")],
        [InlineKeyboardButton(text="✅ B", callback_data=f"z_B_y_{order_id}"),
         InlineKeyboardButton(text="❌ B", callback_data=f"z_B_n_{order_id}")],
        [InlineKeyboardButton(text="✅ C", callback_data=f"z_C_y_{order_id}"),
         InlineKeyboardButton(text="❌ C", callback_data=f"z_C_n_{order_id}")],
        [InlineKeyboardButton(text="✅ D", callback_data=f"z_D_y_{order_id}"),
         InlineKeyboardButton(text="❌ D", callback_data=f"z_D_n_{order_id}")],
        [InlineKeyboardButton(text="✅ E", callback_data=f"z_E_y_{order_id}"),
         InlineKeyboardButton(text="❌ E", callback_data=f"z_E_n_{order_id}")],
        [InlineKeyboardButton(text="📸 Фото загрузки", callback_data=f"photo_load_{order_id}")]
    ])

def get_driving_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚚 Начал движение", callback_data=f"start_drive_{order_id}")]
    ])

def get_arrived_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Доехал до объекта", callback_data=f"arrived_{order_id}")]
    ])

def get_send_location_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Отправить локацию", callback_data=f"req_loc_{order_id}")]
    ])

def get_object_photo_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Фото на объекте", callback_data=f"req_photo_obj_{order_id}")]
    ])

def get_unload_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Начал разгрузку", callback_data=f"start_unload_{order_id}")]
    ])

def get_finish_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Разгрузил всё", callback_data=f"finish_{order_id}")]
    ])
