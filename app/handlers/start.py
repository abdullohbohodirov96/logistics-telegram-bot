from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from app.config import ADMIN_IDS
import app.keyboards as kb

router = Router()

@router.message(CommandStart())
async def start_cmd(message: Message):
    is_admin = message.from_user.id in ADMIN_IDS
    text = (
        "Assalomu alaykum!\n"
        "Bu logistika nazorat boti.\n\n"
        "Sizga biriktirilgan yetkazib berish vazifalari shu yerga keladi.\n"
        "Kerakli tugmani tanlang:"
    )
    await message.answer(text, reply_markup=kb.get_main_menu_kb(is_admin))

@router.message(F.text == "🚚 Mening vazifalarim")
async def my_tasks(message: Message):
    await message.answer("Sizga biriktirilgan yangi vazifalar avtomatik ravishda shu yerga yuboriladi.")
