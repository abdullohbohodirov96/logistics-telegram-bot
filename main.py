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

    # Webhook tozalash (sleep yo'q — keraksiz)
    logger.info("Clearing webhooks...")
    await bot.delete_webhook(drop_pending_updates=True)

    # Scheduler — har doim ishlaydigan joblar + sheets polling (agar konfiguratsiya bo'lsa)
    try:
        from core.scheduler import send_daily_report_job, send_driver_reminders

        scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

        if is_sheets_configured():
            from core.scheduler import check_sheets_job
            interval = max(POLL_INTERVAL_SECONDS, 60)
            logger.info(f"Starting sheets polling (interval: {interval}s)...")
            scheduler.add_job(
                check_sheets_job, 'interval',
                seconds=interval,
                args=[bot],
                max_instances=1,
                misfire_grace_time=30
            )

        # Kunlik hisobot 22:00 — sheets konfiguratsiyasidan mustaqil ishlaydi
        scheduler.add_job(
            send_daily_report_job, 'cron',
            hour=22, minute=0,
            args=[bot],
            max_instances=1
        )

        # 30 daqiqalik eslatmalar
        scheduler.add_job(
            send_driver_reminders, 'interval',
            minutes=30,
            args=[bot],
            max_instances=1
        )

        scheduler.start()
        logger.info("✅ Scheduler started.")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

    logger.info("Starting Bot in Polling mode...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Critical error: {e}")
    finally:
        await bot.session.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
