from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from core.config import ADMIN_IDS
import core.keyboards as kb
from core.db import get_user, upsert_user
from core.i18n import _

router = Router()

@router.message(CommandStart())
async def start_cmd(message: Message):
    tid = message.from_user.id
    user = get_user(tid)
    
    if not user or not user.get('language'):
        # Save user initially
        upsert_user(tid, message.from_user.full_name, message.from_user.username)
        await message.answer("🇺🇿 Iltimos, tilni tanlang / Илтимос, тилни танланг:", reply_markup=kb.get_language_kb())
        return

    lang = user.get('language')
    is_admin = tid in ADMIN_IDS
    text = (
        f"{_('welcome', lang)}\n\n"
        "Men Dunyabunya logistika nazorat botiman.\n\n"
        "Bu yerda sizga biriktirilgan yetkazib berish vazifalarini qabul qilasiz, yuk ortish bosqichlarini belgilaysiz, rasm va lokatsiya yuborasiz."
    )
    await message.answer(text, reply_markup=kb.get_main_menu_kb(is_admin, lang))

@router.callback_query(F.data.startswith("set_lang_"))
async def handle_language_selection(callback: CallbackQuery):
    lang = callback.data.split("_")[2]
    tid = callback.from_user.id
    upsert_user(tid, callback.from_user.full_name, callback.from_user.username, lang)
    
    await callback.answer(_('lang_saved', lang))
    await callback.message.delete()
    
    is_admin = tid in ADMIN_IDS
    await callback.message.answer(_('welcome', lang), reply_markup=kb.get_main_menu_kb(is_admin, lang))

@router.message(F.text.in_({"⚙️ Tilni o'zgartirish", "⚙️ Тилни ўзгартириш"}))
@router.message(Command("lang"))
async def change_language(message: Message):
    await message.answer("🇺🇿 Iltimos, tilni tanlang / Илтимос, тилни танланг:", reply_markup=kb.get_language_kb())

@router.message(F.text.in_({"🚚 Mening vazifalarim", "🚚 Менинг вазифаларим"}))
async def my_tasks(message: Message):
    tid = message.from_user.id
    user = get_user(tid)
    lang = user.get('language') if user else 'uz_latin'
    await message.answer("Sizga biriktirilgan yangi vazifalar avtomatik ravishda shu yerga yuboriladi.")
