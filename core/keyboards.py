from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from core.i18n import _

def get_language_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇿 O'zbek lotin", callback_data="set_lang_uz_latin")],
        [InlineKeyboardButton(text="🇺🇿 Ўзбек кирилл", callback_data="set_lang_uz_cyrillic")]
    ])

def get_main_menu_kb(is_admin: bool = False, lang: str = "uz_latin"):
    keyboard = [
        [KeyboardButton(text=_("my_tasks", lang))],
        [KeyboardButton(text=_("change_lang", lang))],
        [KeyboardButton(text=_("my_history", lang))]
    ]
    if is_admin: 
        keyboard.append([KeyboardButton(text=_("admin_panel", lang))])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_take_delivery_kb(order_id: str, lang: str = "uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("take_order", lang), callback_data=f"take_{order_id}")]
    ])

def get_step_kb(text, callback_data):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=callback_data)]
    ])

def get_block_menu_kb(order_id, history_map, lang="uz_latin"):
    builder = InlineKeyboardBuilder()
    for letter in ['A', 'B', 'C', 'D']:
        # Check in history_map (stage names in DB are translated labels)
        label_key = f"stage_{letter.lower()}"
        label = _(label_key, lang)
        
        # In history_map, key might be translated or literal
        item = history_map.get(label) or history_map.get(f"{letter}-blok")
        status = item.get('status') if item else None
        
        icon = "✅" if status == "ORTDI" else "❌" if status == "ORTMADI" else "⏳"
        builder.button(text=f"{icon} {label}", callback_data=f"sel_block_{letter}_{order_id}")
    builder.adjust(2)
    
    if len(history_map) >= 4:
        builder.row(InlineKeyboardButton(text="➡️ " + _("transit", lang), callback_data=f"tr_start_{order_id}"))
    
    return builder.as_markup()

def get_block_selection_kb(letter, order_id, lang="uz_latin"):
    label = _(f"stage_{letter.lower()}", lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("yes", lang) + " (ORTDI)", callback_data=f"block_act_{letter}_ortdi_{order_id}")],
        [InlineKeyboardButton(text=_("no", lang) + " (ORTMADI)", callback_data=f"block_act_{letter}_ortmadi_{order_id}")],
        [InlineKeyboardButton(text=_("back", lang), callback_data=f"back_to_blocks_{order_id}")]
    ])

def get_transit_kb(order_id, lang="uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("yes", lang), callback_data=f"tr_oldi_1_{order_id}")],
        [InlineKeyboardButton(text=_("no", lang), callback_data=f"tr_olmadim_{order_id}")]
    ])

def get_transit_extra_kb(num, order_id, lang="uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("yes", lang), callback_data=f"tr_oldi_{num}_{order_id}")],
        [InlineKeyboardButton(text=_("no", lang), callback_data=f"tr_stop_{order_id}")]
    ])

def get_finish_kb(order_id, lang: str = "uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("finish_order", lang), callback_data=f"final_done_{order_id}")]
    ])

def get_location_kb(lang="uz_latin"):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=_("send_location", lang), request_location=True)]], resize_keyboard=True)

def remove_kb():
    return ReplyKeyboardRemove()

# Admin Panel Keyboards
def get_admin_panel_kb(lang="uz_latin"):
    # Since admin panel is mostly used by admin, I'll translate buttons too
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚚 " + _("my_tasks", lang).replace("Mening ", "Barcha "), callback_data="adm_cars_status")],
        [InlineKeyboardButton(text="📊 " + _("admin_panel", lang), callback_data="adm_active")],
        [InlineKeyboardButton(text="📋 " + _("my_history", lang), callback_data="adm_hist_car")],
        [InlineKeyboardButton(text="👤 " + _("driver", lang), callback_data="adm_hist_drv")],
        [InlineKeyboardButton(text="🔄 " + _("confirm", lang), callback_data="adm_refresh")],
        [InlineKeyboardButton(text="❌ " + _("no", lang), callback_data="adm_close")]
    ])

def get_cars_kb(cars, lang="uz_latin"):
    builder = InlineKeyboardBuilder()
    for c in cars:
        builder.button(text=c, callback_data=f"car_{c}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 " + _("back", lang), callback_data="adm_back"))
    return builder.as_markup()

def get_drivers_kb(drivers, lang="uz_latin"):
    builder = InlineKeyboardBuilder()
    for tid, name in drivers.items():
        builder.button(text=name, callback_data=f"drv_{tid}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 " + _("back", lang), callback_data="adm_back"))
    return builder.as_markup()

def get_date_range_kb(lang="uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Bugun", callback_data="dt_today")],
        [InlineKeyboardButton(text="Kecha", callback_data="dt_yesterday")],
        [InlineKeyboardButton(text="Oxirgi 7 kun", callback_data="dt_7days")],
        [InlineKeyboardButton(text="Oxirgi 30 kun", callback_data="dt_30days")],
        [InlineKeyboardButton(text="📅 Tanlash (manual)", callback_data="dt_manual")],
        [InlineKeyboardButton(text="🔙 " + _("back", lang), callback_data="adm_back")]
    ])

def get_order_detail_kb(order_id, lang="uz_latin"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔍 " + _("details", lang), callback_data=f"detail:{order_id}")]])

def get_pagination_kb(page, total):
    btns = []
    if page > 1: btns.append(InlineKeyboardButton(text="⬅️", callback_data=f"h:p:{page-1}"))
    if page < total: btns.append(InlineKeyboardButton(text="➡️", callback_data=f"h:n:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[btns, [InlineKeyboardButton(text="🔙 Orqaga", callback_data="adm_back")]])

def get_driver_pagination_kb(page, total, lang="uz_latin"):
    btns = []
    if page > 1: btns.append(InlineKeyboardButton(text="⬅️", callback_data=f"m:p:{page-1}"))
    if page < total: btns.append(InlineKeyboardButton(text="➡️", callback_data=f"m:n:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[btns])
