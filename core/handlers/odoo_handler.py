import logging
from aiogram import Router, F
from aiogram.types import Message
from core.odoo_client import odoo_client
from core.config import ADMIN_IDS

router = Router()
logger = logging.getLogger(__name__)

@router.message(F.text == "/odoo_test")
async def cmd_odoo_test(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    # Send initial message
    status_msg = await message.answer("Odoo bilan ulanish tekshirilmoqda...")
    success, report = await odoo_client.test_connection()
    await status_msg.edit_text(report, parse_mode="Markdown")

@router.message(F.text == "/dostavka")
async def cmd_dostavka(message: Message):
    try:
        loading_msg = await message.answer("Odoo'dan dostavka ma'lumotlari olinmoqda...")
        
        # Ensure authentication
        if not odoo_client.uid:
            auth_success = await odoo_client.authenticate()
            if not auth_success:
                await loading_msg.edit_text("❌ Odoo auth xatosi. Iltimos login/parolni tekshiring.")
                return

        deliveries = await odoo_client.get_delivery_orders(limit=10)
        
        if not deliveries:
            await loading_msg.edit_text("Hozircha dostavkalar topilmadi.")
            return

        text = "📦 **Oxirgi 10 ta dostavka (Odoo):**\n\n"
        for d in deliveries:
            name = d.get('name', 'Noma\'lum')
            partner = d.get('partner_id')
            partner_name = partner[1] if partner and len(partner) > 1 else "Noma'lum"
            date = d.get('scheduled_date', 'Noma\'lum')
            state = d.get('state', 'Noma\'lum')
            origin = d.get('origin', 'Noma\'lum')
            
            text += f"🔹 **Nomer:** {name}\n"
            text += f"👤 **Mijoz:** {partner_name}\n"
            text += f"📅 **Sana:** {date}\n"
            text += f"📊 **Holat:** {state}\n"
            text += f"📍 **Manba:** {origin}\n"
            text += "────────────────────\n"
            
        await loading_msg.edit_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in /dostavka handler: {e}")
        await message.answer("⚠️ Dostavkalarni olishda xatolik yuz berdi. Odoo ulanishini tekshiring.")
