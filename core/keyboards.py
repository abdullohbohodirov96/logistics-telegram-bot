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

def get_block_menu_kb(order_id: str, stage_history: list):
    builder = InlineKeyboardBuilder()
    selected_stages = [item['stage'] for item in stage_history]
    
    stages = ["A-blok", "B-blok", "C-blok", "D-blok"]
    for stage in stages:
        text = stage
        if stage in selected_stages:
            text = f"✅ {stage}"
        builder.button(text=text, callback_data=f"sel_block_{stage[0]}_{order_id}")
    
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"confirm_blocks_{order_id}"))
    return builder.as_markup()

def get_block_selection_kb(block_letter: str, order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Oldim", callback_data=f"block_act_{block_letter}_ortdi_{order_id}"),
            InlineKeyboardButton(text="❌ Olmadim", callback_data=f"block_act_{block_letter}_ortmadi_{order_id}")
        ],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"block_back_{order_id}")]
    ])

def get_transit_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Oldim", callback_data=f"tr_oldi_{order_id}"),
            InlineKeyboardButton(text="❌ Olmadim", callback_data=f"tr_olmadi_{order_id}")
        ]
    ])

def get_transit_extra_kb(transit_number: int, order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ha", callback_data=f"tr_extra_ha_{transit_number}_{order_id}"),
            InlineKeyboardButton(text="❌ Yo'q", callback_data=f"tr_extra_yoq_{transit_number}_{order_id}")
        ]
    ])

def get_step_kb(text: str, callback_data: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=callback_data)]
    ])

def get_location_kb(text: str = "📍 Lokatsiyani yuborish"):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def remove_kb():
    return ReplyKeyboardRemove()

def get_admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Aktiv buyurtmalar", callback_data="adm_active")],
        [InlineKeyboardButton(text="🚚 Haydovchilar holati", callback_data="adm_cars_status")],
        [InlineKeyboardButton(text="📊 Bugungi hisobot", callback_data="adm_hist_all")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_refresh")],
        [InlineKeyboardButton(text="❌ Yopish", callback_data="adm_close")]
    ])

def get_date_range_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Bugun", callback_data="dt_today"), InlineKeyboardButton(text="Kecha", callback_data="dt_yesterday")],
        [InlineKeyboardButton(text="Oxirgi 7 kun", callback_data="dt_7days"), InlineKeyboardButton(text="Oxirgi 30 kun", callback_data="dt_30days")],
        [InlineKeyboardButton(text="Qo'lda kiritish", callback_data="dt_manual")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

def get_pagination_kb(page: int, total: int):
    row = []
    if page > 1: row.append(InlineKeyboardButton(text="⬅️", callback_data=f"h:p:{page-1}"))
    if page < total: row.append(InlineKeyboardButton(text="➡️", callback_data=f"h:n:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[row, [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]])

def get_order_detail_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Batafsil", callback_data=f"ord_det_{order_id}")]])

def get_cars_kb(cars):
    kb = InlineKeyboardBuilder()
    for car in cars: kb.button(text=car, callback_data=f"car_{car}")
    kb.button(text="🔙 Orqaga", callback_data="adm_back")
    kb.adjust(2)
    return kb.as_markup()

def get_drivers_kb(drivers_dict):
    kb = InlineKeyboardBuilder()
    for tid, name in drivers_dict.items(): kb.button(text=name, callback_data=f"drv_{tid}")
    kb.button(text="🔙 Orqaga", callback_data="adm_back")
    kb.adjust(1)
    return kb.as_markup()
