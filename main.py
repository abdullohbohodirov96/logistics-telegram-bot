import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import BOT_TOKEN, POLL_INTERVAL_SECONDS, is_sheets_configured, WEBHOOK_URL, PORT
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

    # Forceful cleanup
    logger.info("Aggressive cleanup: clearing webhooks and waiting 5s for zombie processes...")
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.sleep(5)

    # Start Google Sheets scheduler
    if is_sheets_configured():
        try:
            from core.scheduler import check_sheets_job, send_daily_report_job, send_driver_reminders
            logger.info(f"Starting Google Sheets polling job (interval: {POLL_INTERVAL_SECONDS}s)...")
            scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
            scheduler.add_job(check_sheets_job, 'interval', seconds=POLL_INTERVAL_SECONDS, args=[bot])

            # Daily report at 22:00 Tashkent
            scheduler.add_job(send_daily_report_job, 'cron', hour=22, minute=0, args=[bot])

            # 30-minute driver reminders
            scheduler.add_job(send_driver_reminders, 'interval', minutes=30, args=[bot])

            scheduler.start()
            logger.info("✅ All scheduler jobs started.")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")


    # Polling mode (Standard for Worker)
    logger.info("Starting Bot in Polling mode (Worker style)...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Critical Worker error: {e}")
    finally:
        await bot.session.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution stopped.")
