import asyncio
import logging
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import BOT_TOKEN, POLL_INTERVAL_SECONDS, IS_SHEETS_ENABLED
from core.handlers import router

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

    # Completely disable Sheets scheduler if flag is false
    if IS_SHEETS_ENABLED:
        try:
            from core.scheduler import check_sheets_job
            logger.info("Google Sheets scheduler starting...")
            scheduler = AsyncIOScheduler()
            scheduler.add_job(check_sheets_job, 'interval', seconds=POLL_INTERVAL_SECONDS, args=[bot])
            scheduler.start()
        except Exception as e:
            logger.error(f"Failed to start Sheets scheduler: {e}")
    else:
        logger.info("Google Sheets scheduler is DISABLED via USE_SHEETS/ODOO_USE_SHEETS flag.")

    logger.info("Deleting old webhook and starting polling...")
    try:
        # Clear webhook and drops pending updates to avoid multiple instance issues
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Start polling. One bot instance will keep connection.
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Critical bot error: {e}")
    finally:
        await bot.session.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution stopped by user/system.")
