from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

def get_take_delivery_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yetkazib berishni oldim", callback_data=f"take_{order_id}")]
    ])

def get_zone_kb(zone: str, order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Oldim", callback_data=f"z_{zone}_y_{order_id}"),
            InlineKeyboardButton(text="❌ Olmadim", callback_data=f"z_{zone}_n_{order_id}")
        ]
    ])

def get_driving_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚚 Yo'lga chiqdim", callback_data=f"start_drive_{order_id}")]
    ])

def get_arrived_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Manzilga yetib keldim", callback_data=f"arrived_{order_id}")]
    ])

def get_request_location_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def remove_reply_kb():
    return ReplyKeyboardRemove()

def get_finish_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tushirib bo'ldim", callback_data=f"finish_{order_id}")]
    ])
