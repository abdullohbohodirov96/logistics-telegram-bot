
TEXTS = {
    "uz_latin": {
        "choose_lang": "🇺🇿 Iltimos, tilni tanlang:",
        "main_menu": "Asosiy menyu",
        "my_tasks": "🚚 Mening vazifalarim",
        "my_history": "📋 Mening tarixim",
        "admin_panel": "🛠 Admin panel",
        "settings": "⚙️ Sozlamalar",
        "change_lang": "⚙️ Tilni o'zgartirish",
        "welcome": "Xush kelibsiz!",
        "lang_saved": "Til muvaffaqiyatli saqlandi! ✅",
        "order_accepted": "✅ Buyurtma qabul qilindi.",
        "order_already_finished": "✅ Bu zakaz allaqachon yakunlangan.",
        "order_not_found": "❌ Zakaz topilmadi.",
        "too_many_active": "❌ Sizda 3 ta aktiv zakaz bor. Avval ularni yakunlang.",
        "admin_too_many_active": "❌ Bu haydovchida 3 ta aktiv zakaz bor. Boshqa haydovchini tanlang.",
        "take_order": "✅ Qabul qilish",
        "finish_order": "🏁 Yakunlash",
        "send_location": "📍 Lokatsiyani yuborish",
        "send_photo": "📸 Rasm yuborish",
        "error_wrong_input": "❌ Iltimos, yuqoridagi tugmalardan foydalaning.",
        "ranking_title": "🏆 Kunlik reyting",
        "no_orders_today": "Bugun yakunlangan reyslar yo'q.",
    },
    "uz_cyrillic": {
        "choose_lang": "🇺🇿 Илтимос, тилни танланг:",
        "main_menu": "Асосий меню",
        "my_tasks": "🚚 Менинг вазифаларим",
        "my_history": "📋 Менинг тарихим",
        "admin_panel": "🛠 Админ панел",
        "settings": "⚙️ Созламалар",
        "change_lang": "⚙️ Тилни ўзгартириш",
        "welcome": "Хуш келибсиз!",
        "lang_saved": "Тил мувафaqқиятли сақланди! ✅",
        "order_accepted": "✅ Буюртма қабул қилинди.",
        "order_already_finished": "✅ Бу заказ аллақачон якунланган.",
        "order_not_found": "❌ Заказ топилмади.",
        "too_many_active": "❌ Сизда 3 та актив заказ бор. Аввал уларни якунланг.",
        "admin_too_many_active": "❌ Бу ҳайдовчида 3 та актив заказ бор. Бошқа ҳайдовчини танланг.",
        "take_order": "✅ Қабул қилиш",
        "finish_order": "🏁 Якунлаш",
        "send_location": "📍 Локацияни юborиш",
        "send_photo": "📸 Расм юborиш",
        "error_wrong_input": "❌ Илтимос, юқоридаги тугмалардан фойдаланинг.",
        "ranking_title": "🏆 Кунлик рейтинг",
        "no_orders_today": "Бугун якунланган рейслар йўқ.",
    }
}

def _(key, lang="uz_latin"):
    if not lang: lang = "uz_latin"
    return TEXTS.get(lang, TEXTS["uz_latin"]).get(key, key)
