from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_main_menu_kb(is_admin: bool = False):
    keyboard = [[KeyboardButton(text="🚚 Mening vazifalarim")]]
    if is_admin: keyboard.append([KeyboardButton(text="🛠 Admin panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_take_delivery_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Buyurtmani qabul qilish", callback_data=f"take_{order_id}")]
    ])

def get_step_kb(text: str, callback_data: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=callback_data)]
    ])

def get_transit_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Oldim", callback_data=f"tr_oldi_{order_id}"),
            InlineKeyboardButton(text="❌ Olmadim", callback_data=f"tr_olmadi_{order_id}")
        ]
    ])

def get_location_kb(text: str = "📍 Lokatsiyani yuborish"):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def remove_kb():
    return ReplyKeyboardRemove()
