from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
        [InlineKeyboardButton(text="🚗 Mashinalar holati", callback_data="adm_cars_status")],
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

def get_delivery_zones_kb(order_id: str, steps: list, show_transit: bool = False):
    builder = InlineKeyboardBuilder()
    
    zones = ["A", "B", "C", "D"]
    # We use 'z_a', 'z_b' format from the handler
    active_zones = {s['step_name'].split("_")[1].upper() for s in steps if s.get('step_value') == 'y' and s['step_name'].startswith('z_')}
    
    for z in zones:
        label = f"{z} ✅" if z in active_zones else f"{z} ⚪️"
        builder.button(text=label, callback_data=f"zone_{z.lower()}_{order_id}")
    
    builder.adjust(2)
    
    if show_transit:
        builder.row(InlineKeyboardButton(text="🚛 Tranzitga chiqish", callback_data=f"transit_{order_id}"))
    else:
        builder.row(InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"refresh_{order_id}"))
        
    return builder.as_markup()

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

def get_transit_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Ha, transit bor", callback_data=f"tr_y_{order_id}"),
            InlineKeyboardButton(text="Yo‘q, transit yo‘q", callback_data=f"tr_n_{order_id}")
        ]
    ])

def get_finish_kb(order_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Tushirib bo'ldim", callback_data=f"finish_{order_id}")]
    ])
