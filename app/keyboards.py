from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

def get_main_menu_kb(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="🚚 Mening vazifalarim"), KeyboardButton(text="📋 Mening tarixim")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="🛠 Admin panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Yetkazib berishlar tarixi", callback_data="adm_hist_all")],
        [InlineKeyboardButton(text="🚗 Mashinalar bo'yicha tarix", callback_data="adm_hist_car")],
        [InlineKeyboardButton(text="👤 Haydovchilar bo'yicha tarix", callback_data="adm_hist_drv")],
        [InlineKeyboardButton(text="📊 Bugungi aktiv vazifalar", callback_data="adm_active")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_close")]
    ])

def get_cars_kb(cars: list):
    kb = []
    for car in cars:
        kb.append([InlineKeyboardButton(text=f"🚗 {car}", callback_data=f"car_{car}")])
    kb.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_drivers_kb(drivers: dict):
    kb = []
    for tid, name in drivers.items():
        kb.append([InlineKeyboardButton(text=f"👤 {name}", callback_data=f"drv_{tid}")])
    kb.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_date_range_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Bugun", callback_data="dt_today")],
        [InlineKeyboardButton(text="Kecha", callback_data="dt_yesterday")],
        [InlineKeyboardButton(text="Oxirgi 7 kun", callback_data="dt_7days")],
        [InlineKeyboardButton(text="Oxirgi 30 kun", callback_data="dt_30days")],
        [InlineKeyboardButton(text="Sana oralig'ini kiritish", callback_data="dt_manual")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

def get_order_detail_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Batafsil", callback_data=f"detail:{order_id}")]
    ])

def get_pagination_kb(page: int, total_pages: int):
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"h:p:{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"h:n:{page+1}"))
    
    kb = []
    if buttons:
        kb.append(buttons)
    kb.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_driver_pagination_kb(page: int, total_pages: int):
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"m:p:{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"m:n:{page+1}"))
        
    kb = []
    if buttons:
        kb.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=kb) if kb else None

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
