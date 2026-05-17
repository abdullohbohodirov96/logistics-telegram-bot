import logging
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import ADMIN_IDS
from core.db import get_active_orders_by_driver
import core.keyboards as kb

router = Router()
logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "QABUL_QILINDI": "📥 Qabul qilindi",
    "BLOCK_MENU":    "🏗 Blok tanlayapti",
    "TRANSIT":       "🚚 Transit",
    "YOLDA":         "🛣 Yo'lda",
    "LOADED_PHOTO":  "📸 Yuk rasmi kerak",
    "ACT_PHOTO":     "🧾 Akt rasmi kerak",
    "DELIVERED_LOC": "📍 Lokatsiya kerak",
    "WAITING_FINISH":"✅ Yakunlash kerak",
    "NEW":           "🆕 Yangi",
}

def _order_status_label(order) -> str:
    st = order.get("current_status", "")
    return STATUS_LABELS.get(st, st)

def _active_orders_kb(orders: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for o in orders:
        label = f"📦 #{o['order_id']} — {_order_status_label(o)}"
        # Truncate label to 60 chars for button safety
        builder.button(text=label[:60], callback_data=f"open_order_{o['order_id']}")
    builder.adjust(1)
    return builder.as_markup()


@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    is_admin = message.from_user.id in ADMIN_IDS
    tid = message.from_user.id

    # Check for active orders
    try:
        active = await asyncio.to_thread(get_active_orders_by_driver, tid)
    except Exception as e:
        logger.error(f"start_cmd: get_active_orders_by_driver error: {e}")
        active = []

    if active:
        text = (
            f"Assalomu alaykum! 👋\n\n"
            f"📦 Sizda {len(active)} ta aktiv buyurtma bor.\n"
            f"Davom ettirish uchun quyidagi buyurtmani tanlang:"
        )
        await message.answer(text, reply_markup=_active_orders_kb(active))
    else:
        text = (
            "Assalomu alaykum! 👋\n"
            "Men Dunyabunya logistika nazorat botiman.\n\n"
            "Bu yerda sizga biriktirilgan yetkazib berish vazifalarini qabul qilasiz.\n\n"
            "Kerakli bo'limni tanlang:"
        )
        await message.answer(text, reply_markup=kb.get_main_menu_kb(is_admin))


@router.message(F.text == "🚚 Mening vazifalarim")
async def my_tasks(message: Message, state: FSMContext):
    tid = message.from_user.id
    try:
        active = await asyncio.to_thread(get_active_orders_by_driver, tid)
    except Exception as e:
        logger.error(f"my_tasks: {e}")
        active = []

    if active:
        text = f"📦 Sizning aktiv buyurtmalaringiz ({len(active)} ta):\n\nDavom ettirish uchun tanlang:"
        await message.answer(text, reply_markup=_active_orders_kb(active))
    else:
        await message.answer(
            "✅ Hozirda aktiv buyurtmangiz yo'q.\n"
            "Yangi buyurtmalar avtomatik yuboriladi."
        )


@router.callback_query(F.data.startswith("open_order_"))
async def open_active_order(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Resume driver flow for a specific active order."""
    from aiogram import Bot
    await callback.answer()
    order_id = callback.data[len("open_order_"):]

    from core.db import get_order
    from core.states import DeliveryStates
    from core.handlers.delivery import update_group_report

    order = await asyncio.to_thread(get_order, order_id)
    if not order:
        await callback.message.answer(f"❌ Buyurtma #{order_id} topilmadi.")
        return

    status = order.get("current_status", "")

    # Set FSM state based on order's current status
    await state.update_data(order_id=order_id)

    if status in ("NEW", "QABUL_QILINDI", "BLOCK_MENU", ""):
        # Restore block menu
        stage_history = order.get("stage_history") or []
        if isinstance(stage_history, str):
            import json
            try: stage_history = json.loads(stage_history)
            except: stage_history = []
        await state.update_data(stage_history=stage_history)
        await state.set_state(DeliveryStates.BLOCK_MENU)

        history_map = {item["stage"]: item for item in stage_history}
        def gs(label):
            item = history_map.get(label)
            if item: return f"{item.get('emoji')} {item.get('status')} ({item.get('completed_at')})"
            return "⏳ Tanlanmagan"

        await callback.message.answer(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"📦 **Bloklardan yuk olish**\n"
            f"⚠️ Hamma bloklarni tanlashingiz kerak!\n\n"
            f"🅰️ A-blok: {gs('A-blok')}\n"
            f"🅱️ B-blok: {gs('B-blok')}\n"
            f"©️ C-blok: {gs('C-blok')}\n"
            f"🇩 D-blok: {gs('D-blok')}",
            reply_markup=kb.get_block_menu_kb(order_id, stage_history),
            parse_mode="Markdown"
        )

    elif status == "TRANSIT":
        await state.set_state(DeliveryStates.TRANSIT)
        await callback.message.answer(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"🚚 Transitdan yuk oldingizmi?\n"
            f"🚚 Транзитдан юк олдингизми?",
            reply_markup=kb.get_transit_kb(order_id),
            parse_mode="Markdown"
        )

    elif status == "LOADED_PHOTO" or not order.get("loaded_photo_file_id"):
        await state.set_state(DeliveryStates.LOADED_PHOTO)
        await callback.message.answer(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"📸 Yuk ortilgan rasmni yuboring\n"
            f"📸 Юк ортилган расмни юборинг",
            parse_mode="Markdown"
        )

    elif status == "YOLDA" or not order.get("on_way_at"):
        await state.set_state(DeliveryStates.ON_WAY)
        addr = order.get("address", "-")
        await callback.message.answer(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"📍 Manzil: {addr}\n\n"
            f"Yo'lga chiqdingizmi?",
            reply_markup=kb.get_step_kb("🚚 Yo'lga chiqdim", f"step_way_{order_id}"),
            parse_mode="Markdown"
        )

    elif not order.get("act_photo_file_id"):
        await state.set_state(DeliveryStates.ACT_PHOTO)
        await callback.message.answer(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"📄 Akt rasmini yuboring\n"
            f"📄 Акт расмини юборинг",
            parse_mode="Markdown"
        )

    elif not order.get("delivered_location_at"):
        await state.set_state(DeliveryStates.DELIVERED_LOC)
        await callback.message.answer(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"📍 Lokatsiyani yuboring\n"
            f"📍 Локацияни юборинг",
            reply_markup=kb.get_location_kb("📍 Lokatsiyani yuborish"),
            parse_mode="Markdown"
        )

    else:
        await state.set_state(DeliveryStates.WAITING_FINISH)
        await callback.message.answer(
            f"📦 **Buyurtma #{order_id}**\n\n"
            f"✅ Buyurtmani yakunlash uchun tugmani bosing:",
            reply_markup=kb.get_step_kb("✅ Buyurtmani yakunlash", f"final_done_{order_id}"),
            parse_mode="Markdown"
        )
