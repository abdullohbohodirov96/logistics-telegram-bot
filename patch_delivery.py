import re

with open("core/handlers/delivery.py", "r") as f:
    content = f.read()

# Add TelegramBadRequest to imports
if "TelegramBadRequest" not in content:
    content = content.replace("from aiogram.types import", "from aiogram.exceptions import TelegramBadRequest\nfrom aiogram.types import")

# Fix update_group_report
content = content.replace('''    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")''', '''    try:
        msg_id = order.get('group_message_id')
        if msg_id:
            try:
                await bot.edit_message_text(chat_id=GROUP_CHAT_ID, message_id=int(msg_id), text=text, parse_mode="Markdown")
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    logger.error(f"Group report edit error: {e}")''')

with open("core/handlers/delivery.py", "w") as f:
    f.write(content)
