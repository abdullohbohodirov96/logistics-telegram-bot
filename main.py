import asyncio
import logging
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import BOT_TOKEN, POLL_INTERVAL_SECONDS, ODOO_USE_SHEETS
from core.handlers import router
from core.scheduler import check_sheets_job

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    dp.include_router(router)

    # Start scheduler ONLY if ODOO_USE_SHEETS is True
    if ODOO_USE_SHEETS:
        logger.info("Google Sheets scheduler starting...")
        scheduler = AsyncIOScheduler()
        scheduler.add_job(check_sheets_job, 'interval', seconds=POLL_INTERVAL_SECONDS, args=[bot])
        scheduler.start()
    else:
        logger.info("Google Sheets scheduler is DISABLED (ODOO_USE_SHEETS=false)")

    logger.info("Deleting old webhook and starting polling...")
    try:
        # drop_pending_updates=True is critical to avoid old message storms
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Start polling
        # If another instance is running, this might throw TelegramConflictError
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot execution error: {e}")
    finally:
        await bot.session.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
