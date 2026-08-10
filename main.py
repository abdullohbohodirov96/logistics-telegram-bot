import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import (
    BOT_TOKEN, POLL_INTERVAL_SECONDS, is_sheets_configured,
    WEBHOOK_URL, PORT, SHEET_WEBHOOK_SECRET,
)
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

    # Instant-dispatch webhook (Google Sheets Apps Script -> here) — this is
    # now the PRIMARY way new SEND orders get dispatched. Needs this service
    # to be a Render Web Service (bound to $PORT), not a Background Worker.
    if SHEET_WEBHOOK_SECRET:
        try:
            from core.webhook import create_webhook_app
            webhook_app = create_webhook_app(bot)
            runner = web.AppRunner(webhook_app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", PORT)
            await site.start()
            logger.info(f"✅ Webhook server listening on 0.0.0.0:{PORT} (POST /webhook/sheet-edit)")
        except Exception as e:
            logger.error(f"Failed to start webhook server: {e}")
    else:
        logger.warning(
            "SHEET_WEBHOOK_SECRET not set — instant dispatch webhook disabled. "
            "Orders will only be picked up by the periodic poll."
        )

    # Google Sheets scheduler (safety net — see process_one_order docstring)
    if is_sheets_configured():
        try:
            from core.scheduler import check_sheets_job, send_daily_report_job, send_driver_reminders

            # Now a safety net, not the primary dispatch path — the webhook
            # handles new SEND orders instantly. Controlled by
            # POLL_INTERVAL_SECONDS (render.yaml), minimum 60s.
            interval = max(POLL_INTERVAL_SECONDS, 60)
            logger.info(f"Starting scheduler (interval: {interval}s)...")

            scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")

            # max_instances=1 — overlap bo'lmaydi (lock bilan ham himoyalangan)
            scheduler.add_job(
                check_sheets_job, 'interval',
                seconds=interval,
                args=[bot],
                max_instances=1,
                misfire_grace_time=30
            )

            # Kunlik hisobot 22:00
            scheduler.add_job(
                send_daily_report_job, 'cron',
                hour=22, minute=0,
                args=[bot],
                max_instances=1
            )

            # Eslatmalar: har 15 daqiqada tekshirish (1 soatlik chegalni to'g'ri ushlab qolish)
            scheduler.add_job(
                send_driver_reminders, 'interval',
                minutes=15,
                args=[bot],
                max_instances=1
            )

            scheduler.start()
            logger.info("✅ All scheduler jobs started.")
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
