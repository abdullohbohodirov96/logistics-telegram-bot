from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from core.i18n import _

def get_language_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇿 O'zbek lotin", callback_data="set_lang_uz_latin")],
        [InlineKeyboardButton(text="🇺🇿 Ўзбек кирилл", callback_data="set_lang_uz_cyrillic")]
    ])

def get_main_menu_kb(is_admin: bool = False, lang: str = "uz_latin"):
    keyboard = [[KeyboardButton(text=_("my_tasks", lang))]]
    keyboard.append([KeyboardButton(text=_("my_history", lang))])
    if is_admin: 
        keyboard.append([KeyboardButton(text=_("admin_panel", lang))])
    keyboard.append([KeyboardButton(text=_("change_lang", lang))])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_take_delivery_kb(order_id: str, lang: str = "uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("take_order", lang), callback_data=f"take_{order_id}")]
    ])

def get_step_kb(text, callback_data):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=callback_data)]
    ])

def get_block_menu_kb(order_id, history_map):
    builder = InlineKeyboardBuilder()
    for letter in ['A', 'B', 'C', 'D']:
        status = history_map.get(f"{letter}-blok", {}).get('status')
        icon = "✅" if status == "ORTDI" else "❌" if status == "ORTMADI" else "⏳"
        builder.button(text=f"{icon} {letter}-blok", callback_data=f"sel_block_{letter}_{order_id}")
    builder.adjust(2)
    
    if len(history_map) >= 4:
        builder.row(InlineKeyboardButton(text="➡️ Transitga o'tish", callback_data=f"tr_start_{order_id}"))
    
    return builder.as_markup()

def get_block_selection_kb(letter, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Oldim (ORTDI)", callback_data=f"block_act_{letter}_ortdi_{order_id}")],
        [InlineKeyboardButton(text="❌ Olmadim (ORTMADI)", callback_data=f"block_act_{letter}_ortmadi_{order_id}")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"back_to_blocks_{order_id}")]
    ])

def get_transit_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Oldim", callback_data=f"tr_oldi_1_{order_id}")],
        [InlineKeyboardButton(text="❌ Olmadim", callback_data=f"tr_olmadim_{order_id}")]
    ])

def get_transit_extra_kb(num, order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha (Oldim)", callback_data=f"tr_oldi_{num}_{order_id}")],
        [InlineKeyboardButton(text="❌ Yo'q", callback_data=f"tr_stop_{order_id}")]
    ])

def get_finish_kb(order_id, lang: str = "uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("finish_order", lang), callback_data=f"final_done_{order_id}")]
    ])

def get_location_kb(text):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=text, request_location=True)]], resize_keyboard=True)

def remove_kb():
    return ReplyKeyboardRemove()

# Admin Panel Keyboards
def get_admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚚 Haydovchilar holati", callback_data="adm_cars_status")],
        [InlineKeyboardButton(text="📊 Aktiv buyurtmalar", callback_data="adm_active")],
        [InlineKeyboardButton(text="📋 Tarix (Mashina bo'yicha)", callback_data="adm_hist_car")],
        [InlineKeyboardButton(text="👤 Tarix (Haydovchi bo'yicha)", callback_data="adm_hist_drv")],
        [InlineKeyboardButton(text="📅 Tarix (Hammasi)", callback_data="adm_hist_all")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm_refresh")],
        [InlineKeyboardButton(text="❌ Yopish", callback_data="adm_close")]
    ])

def get_cars_kb(cars):
    builder = InlineKeyboardBuilder()
    for c in cars:
        builder.button(text=c, callback_data=f"car_{c}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back"))
    return builder.as_markup()

def get_drivers_kb(drivers):
    builder = InlineKeyboardBuilder()
    for tid, name in drivers.items():
        builder.button(text=name, callback_data=f"drv_{tid}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back"))
    return builder.as_markup()

def get_date_range_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Bugun", callback_data="dt_today")],
        [InlineKeyboardButton(text="Kecha", callback_data="dt_yesterday")],
        [InlineKeyboardButton(text="Oxirgi 7 kun", callback_data="dt_7days")],
        [InlineKeyboardButton(text="Oxirgi 30 kun", callback_data="dt_30days")],
        [InlineKeyboardButton(text="📅 Tanlash (manual)", callback_data="dt_manual")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]
    ])

def get_order_detail_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔍 Batafsil", callback_data=f"detail:{order_id}")]])

def get_pagination_kb(page, total):
    btns = []
    if page > 1: btns.append(InlineKeyboardButton(text="⬅️", callback_data=f"h:p:{page-1}"))
    if page < total: btns.append(InlineKeyboardButton(text="➡️", callback_data=f"h:n:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[btns, [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]])

def get_driver_pagination_kb(page, total):
    btns = []
    if page > 1: btns.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"m:p:{page-1}"))
    if page < total: btns.append(InlineKeyboardButton(text="➡️ Keyingi", callback_data=f"m:n:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[btns])
