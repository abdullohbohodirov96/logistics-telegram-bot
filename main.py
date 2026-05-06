import asyncio
import logging
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import BOT_TOKEN, POLL_INTERVAL_SECONDS, is_sheets_configured
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

    # Reverted to Google Sheets as primary scheduler
    if is_sheets_configured():
        try:
            from core.scheduler import check_sheets_job
            logger.info("Starting Google Sheets polling job...")
            scheduler = AsyncIOScheduler()
            scheduler.add_job(check_sheets_job, 'interval', seconds=POLL_INTERVAL_SECONDS, args=[bot])
            scheduler.start()
        except Exception as e:
            logger.error(f"Failed to start Google Sheets scheduler: {e}")
    else:
        logger.warning("Google Sheets credentials are MISSING. Scheduler NOT started.")
        # Admin alert would be handled here if needed, but for now we just log it.

    logger.info("Deleting old webhook and starting polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Critical bot error: {e}")
    finally:
        await bot.session.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution stopped.")
