from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import ADMIN_IDS
import core.keyboards as kb

router = Router()

@router.message(CommandStart())
async def start_cmd(message: Message):
    is_admin = message.from_user.id in ADMIN_IDS
    text = (
        "Assalomu alaykum! 👋\n"
        "Men Dunyabunya logistika nazorat botiman.\n\n"
        "Bu yerda sizga biriktirilgan yetkazib berish vazifalarini qabul qilasiz, yuk ortish bosqichlarini belgilaysiz, rasm va lokatsiya yuborasiz.\n\n"
        "Kerakli bo'limni tanlang:"
    )
    await message.answer(text, reply_markup=kb.get_main_menu_kb(is_admin))

@router.message(F.text == "🚚 Mening vazifalarim")
async def my_tasks(message: Message):
    await message.answer("Sizga biriktirilgan yangi vazifalar avtomatik ravishda shu yerga yuboriladi.")
