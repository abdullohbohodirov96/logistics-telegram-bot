import asyncio
import logging
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import BOT_TOKEN, POLL_INTERVAL_SECONDS
from app.handlers import router
from app.scheduler import check_sheets_job

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

    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_sheets_job, 'interval', seconds=POLL_INTERVAL_SECONDS, args=[bot])
    scheduler.start()

    logger.info("Deleting old webhook if exists...")
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Bot started and scheduler is running.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
